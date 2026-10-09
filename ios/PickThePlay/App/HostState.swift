import SwiftUI

/// Where the admin key is remembered between launches (the Keychain on the phone).
protocol KeyStore {
    func read() -> String?
    func write(_ key: String)
    func delete()
}

/// Remembers nothing beyond this run (tests, store screenshots).
final class MemoryKeyStore: KeyStore {
    private var key: String?
    init(_ key: String? = nil) { self.key = key }
    func read() -> String? { key }
    func write(_ key: String) { self.key = key }
    func delete() { key = nil }
}

/// The host console's single source of truth: the admin socket, the latest `admin_state`, and one method per
/// thing the host can do. Every action method waits for the server's answer, shows its error (if any) as a
/// notice, and returns whether it worked. Screens only read `snapshot` and call these methods.
@MainActor
final class HostState: ObservableObject {
    enum Tab: Hashable { case run, log, players, message }

    struct Notice: Identifiable, Equatable {
        let id = UUID()
        var text: String
        var isError: Bool
    }

    /// The full-screen host console is showing.
    @Published var isPresented = false
    @Published var tab: Tab = .run
    /// The latest `admin_state`; nil until the key has been accepted (and again after sign-out).
    @Published private(set) var snapshot: AdminState?
    @Published private(set) var connection: AdminConnection.Status = .offline
    /// The key was sent and the server hasn't answered yet (it may be waking up).
    @Published private(set) var signingIn = false
    /// Why sign-in failed ("Invalid admin key."), shown on the key screen.
    @Published private(set) var authError: String?
    /// Actions waiting for the server's answer; screens disable their buttons while it is above zero.
    @Published private(set) var pending = 0
    @Published var notice: Notice?
    /// A key is saved on this phone.
    @Published private(set) var hasSavedKey: Bool
    /// The host's half-finished entries; they outlive the tabs (and the console being closed for a moment).
    let drafts = HostDrafts()

    private let link: AdminLink
    private let keys: KeyStore
    /// The one game server the admin key may be sent to: the address built into the app. A tester can point the
    /// app at another server in Settings; the key must never follow.
    private let trustedServer: () -> URL?
    private var key: String?
    private var remember = true
    private var clockOffset: Double = 0
    private var sequence = 0
    private var waiters: [Int: CheckedContinuation<AdminAck?, Never>] = [:]

    var isBusy: Bool { pending > 0 }
    /// A key is being used (typed, or saved) and the server hasn't answered yet: show "Connecting…", not the key form.
    var isConnecting: Bool { key != nil && snapshot == nil && serverIsTrusted }
    var isSignedIn: Bool { snapshot != nil }
    var game: Game? { snapshot?.game }
    var play: Play? { snapshot?.play }
    var feed: FeedState? { snapshot?.feed }
    var server: URL? { ServerConfig.current }
    /// The app is pointed at its built-in game server, so the admin key may be used.
    var serverIsTrusted: Bool { ServerConfig.allowsAdminKey(for: server, builtIn: trustedServer()) }
    /// The app is pointed at some other server (a tester changed it in Settings): the console says so and sends
    /// nothing. (With no address at all there is nothing to refuse; the key screen says the address isn't set.)
    var isOtherServer: Bool { server != nil && !serverIsTrusted }

    /// `trustedServer` is the address the key may go to (the built-in one unless a test says otherwise).
    init(keys: KeyStore, link: AdminLink? = nil, trustedServer: (() -> URL?)? = nil) {
        self.keys = keys
        let trusted = trustedServer ?? { ServerConfig.bundled }
        self.trustedServer = trusted
        let channel = link ?? AdminConnection(trustedServer: trusted())
        self.link = channel
        let saved = keys.read()
        key = saved
        hasSavedKey = saved != nil
        channel.onStatus = { [weak self] status in self?.statusChanged(status) }
        channel.onMessage = { [weak self] message in self?.handle(message) }
        channel.onRejected = { [weak self] in self?.rejected("Invalid admin key.") }
        if let screen = ScreenshotMode.screen { stage(screen) }
    }

    /// Store/CI screenshots: show the console on sample data, without a server.
    private func stage(_ screen: ScreenshotMode.Screen) {
        switch screen {
        case .hostSignIn, .hostRun, .hostLive, .hostLog:
            snapshot = ScreenshotMode.hostSample(for: screen)
            connection = .online
            tab = screen == .hostLog ? .log : .run
            isPresented = true
        default:
            break
        }
    }

    /// Server clock, so every countdown on this screen matches the players'.
    func now() -> Double { Date().timeIntervalSince1970 + clockOffset }

    // MARK: - Opening, signing in and out

    /// Show the console; with a saved key, connect right away.
    func present() {
        isPresented = true
        if key != nil { connect() }
    }

    /// Hide the console and close the connection (the key stays saved unless the host signed out).
    func dismiss() {
        isPresented = false
        link.stop()
        failAll()
        snapshot = nil
        signingIn = false
    }

    /// Starts a sign-in: the key is held until the server's first update proves it right (then it is saved if the
    /// host asked to remember it). Returns false, with `authError` set, when there is nothing to try.
    @discardableResult
    func beginSignIn(key entered: String, remember: Bool) -> Bool {
        let trimmed = entered.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            authError = "Enter the admin key."
            return false
        }
        guard server != nil else {
            authError = "The game server address isn't set."
            return false
        }
        authError = nil
        signingIn = true
        key = trimmed
        self.remember = remember
        return true
    }

    /// Check the key with the server. A sleeping server is woken first (it can take a minute).
    func signIn(key entered: String, remember: Bool) async {
        guard allowKey() else { return }
        guard beginSignIn(key: entered, remember: remember), let server, let tried = key else { return }
        let awake = await APIClient(server: server).waitUntilAwake()
        guard signingIn, key == tried else { return }  // cancelled or replaced while waiting
        guard awake else {
            signingIn = false
            authError = "Can't reach the game server. Check your connection and try again."
            return
        }
        connect()
    }

    /// Forget the key on this phone and close the console.
    func signOut() {
        forgetKey()
        dismiss()
    }

    /// Forget the key but stay on the console, back at the key screen ("Use a different key").
    func forgetKey() {
        link.stop()
        failAll()
        keys.delete()
        key = nil
        hasSavedKey = false
        authError = nil
        snapshot = nil
        signingIn = false
    }

    /// The app came back to the foreground: reconnect if the console is open.
    func appBecameActive() {
        guard isPresented, key != nil, link.status == .offline else { return }
        connect()
    }

    /// iOS closes sockets in the background; close ours cleanly.
    func appEnteredBackground() {
        guard isPresented else { return }
        link.stop()
        failAll()
    }

    private func connect() {
        guard allowKey() else { return }
        guard let key, let server, let url = ServerConfig.adminSocketURL(for: server) else { return }
        link.start(url: url, key: key)
    }

    /// True when the key may be sent. Otherwise nothing is sent: the key screen explains, and so does a notice.
    private func allowKey() -> Bool {
        if !isOtherServer { return true }
        signingIn = false
        authError = HostText.wrongServer
        show(HostText.wrongServer, isError: true)
        return false
    }

    private func rejected(_ message: String) {
        link.stop()
        failAll()
        keys.delete()
        key = nil
        hasSavedKey = false
        snapshot = nil
        signingIn = false
        authError = message
    }

    private func statusChanged(_ status: AdminConnection.Status) {
        connection = status
        if status == .offline { failAll() }
    }

    // MARK: - Server messages

    func handle(_ message: AdminServerMessage) {
        switch message {
        case .state(let state):
            clockOffset = state.serverTime - Date().timeIntervalSince1970
            snapshot = state
            if signingIn {
                signingIn = false
                authError = nil
                if remember, !hasSavedKey, let key {
                    keys.write(key)
                    hasSavedKey = true
                }
            }
        case .ack(let ack):
            if let id = ack.requestId, let waiter = waiters.removeValue(forKey: id) { waiter.resume(returning: ack) }
        case .authError(let text):
            rejected(text)
        case .pong, .other:
            break
        }
    }

    // MARK: - Sending actions

    /// Sends one admin action and waits (up to 8 s) for the server's answer. Returns nil if there was none.
    @discardableResult
    func act(_ action: String, _ fields: [String: Any] = [:]) async -> AdminAck? {
        guard connection == .online else {
            show("Not connected to the server.", isError: true)
            return nil
        }
        sequence += 1
        let id = sequence
        var message = fields
        message["action"] = action
        message["request_id"] = id
        pending += 1
        let ack: AdminAck? = await withCheckedContinuation { (continuation: CheckedContinuation<AdminAck?, Never>) in
            if link.send(message) {
                waiters[id] = continuation
                Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 8_000_000_000)
                    self?.expire(id)
                }
            } else {
                continuation.resume(returning: nil)
            }
        }
        pending -= 1
        if let ack, !ack.ok { show(ack.error ?? "That didn't work.", isError: true) }
        return ack
    }

    private func expire(_ id: Int) {
        guard let waiter = waiters.removeValue(forKey: id) else { return }
        show("The server didn't answer in time.", isError: true)
        waiter.resume(returning: nil)
    }

    /// The connection dropped: nothing will answer the actions still waiting.
    private func failAll() {
        guard !waiters.isEmpty else { return }
        let all = waiters
        waiters = [:]
        show("The connection dropped. Check the screen, then try again.", isError: true)
        for waiter in all.values { waiter.resume(returning: nil) }
    }

    func show(_ text: String, isError: Bool = false) {
        notice = Notice(text: text, isError: isError)
    }

    /// Runs an action and returns whether the server accepted it.
    private func ok(_ action: String, _ fields: [String: Any] = [:]) async -> Bool {
        await act(action, fields)?.ok == true
    }

    // MARK: - The game

    /// Starts a new game (the current one is marked FINAL by the server). `feedGameID` connects live data
    /// ("demo" for the recorded practice game) in the same step.
    func createGame(away: TeamChoice, home: TeamChoice, feedGameID: String?) async -> Bool {
        var fields: [String: Any] = [
            "away_name": away.name, "away_primary": away.primary, "away_secondary": away.secondary,
            "home_name": home.name, "home_primary": home.primary, "home_secondary": home.secondary,
        ]
        if let feedGameID { fields["feed_game_id"] = feedGameID }
        return await ok("create_game", fields)
    }

    func endGame() async -> Bool { await ok("set_status", ["status": GameStatus.final.rawValue]) }

    // MARK: - Plays

    /// The fields of `open_play`. `windowSeconds` (the Timer box) is held to the server's 5 to 60 and sent as a whole
    /// number; nil leaves it to the server's default.
    nonisolated static func openPlayFields(down: Int?, distance: String?, windowSeconds: Double?) -> [String: Any] {
        var fields: [String: Any] = [:]
        if let down { fields["down"] = down }
        if let distance, !distance.isEmpty { fields["distance"] = distance }
        if let windowSeconds, windowSeconds.isFinite {
            fields["window_seconds"] = TimerDraft.clamp(Int(min(max(windowSeconds, 0), 1000).rounded()))
        }
        return fields
    }

    func openPlay(down: Int?, distance: String?, windowSeconds: Double?) async -> Bool {
        await ok("open_play", Self.openPlayFields(down: down, distance: distance, windowSeconds: windowSeconds))
    }

    func lockPlay() async -> Bool { await ok("lock_play") }

    func resolvePlay(_ result: ResultDraft) async -> Bool {
        guard result.isComplete else { return false }
        return await ok("resolve_play", result.fields)
    }

    func voidPlay() async -> Bool { await ok("void_play") }

    func correctPlay(playID: Int, result: ResultDraft) async -> Bool {
        guard result.isComplete else { return false }
        var fields = result.fields
        fields["play_id"] = playID
        return await ok("correct_play", fields)
    }

    // MARK: - Live data

    /// Connects the current game to a live game ("demo" = the recorded practice game); nil disconnects.
    func feedLink(_ id: String?) async -> Bool {
        var fields: [String: Any] = [:]
        if let id { fields["feed_game_id"] = id } else { fields["feed_game_id"] = NSNull() }
        return await ok("feed_link", fields)
    }

    func feedPause() async -> Bool { await ok("feed_pause") }
    func feedResume() async -> Bool { await ok("feed_resume") }
    func feedCheckNow() async -> Bool { await ok("feed_check_now") }
    func feedHold() async -> Bool { await ok("feed_hold") }
    func feedDismiss() async -> Bool { await ok("feed_dismiss") }
    func feedSkip(playID: Int) async -> Bool { await ok("feed_skip", ["play_id": playID]) }
    func feedAllowMore(_ n: Int = 100) async -> Bool { await ok("feed_allow_more", ["n": n]) }
    func feedSetAutoScore(_ on: Bool) async -> Bool { await ok("feed_set", ["auto_score": on]) }
    func feedSetAutoOpen(_ on: Bool) async -> Bool { await ok("feed_set", ["auto_open": on]) }

    /// Score the feed's suggestion now (or void it). `choices` supplies parts the feed couldn't read.
    func feedAccept(_ suggestion: FeedSuggestion, choices: SuggestionChoices) async -> Bool {
        guard choices.canScore(suggestion) else { return false }
        return await ok("feed_accept", choices.acceptFields(suggestion))
    }

    // MARK: - Players and messages

    /// Shows `text` as a banner on every player's screen. Returns how many screens got it, nil on failure.
    func announce(_ text: String) async -> Int? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= HostText.maxMessage else { return nil }
        guard let ack = await act("announce", ["text": trimmed]), ack.ok else { return nil }
        return ack.sentTo ?? 0
    }

    func clearBanner() async -> Bool { await ok("announce", ["text": ""]) }

    func removePlayer(id: Int, block: Bool) async -> Bool {
        await ok("remove_player", ["user_id": id, "block": block])
    }

    func unblock(name: String) async -> Bool { await ok("unblock_name", ["name": name]) }

    // MARK: - REST

    /// The day's games for "Pick today's game" (`date` = YYYYMMDD).
    func loadGames(date: String) async throws -> FeedGames {
        try await api().adminFeedGames(date: date)
    }

    func loadPlayers(query: String) async throws -> HostPlayers {
        try await api().adminPlayers(query: query)
    }

    private func api() throws -> APIClient {
        guard !isOtherServer else { throw APIError(status: 0, message: HostText.wrongServer) }
        guard let server, let key else { throw APIError(status: 401, message: "Sign in with the admin key first.") }
        return APIClient(server: server, adminKey: key, trustedServer: trustedServer())
    }
}
