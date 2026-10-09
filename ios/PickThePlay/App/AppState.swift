import SwiftUI

/// Single source of truth for the app: account, server, live snapshot, the player's pick and lounges.
@MainActor
final class AppState: ObservableObject {
    /// The tab bar, in order: Live, Leaderboard, Head to Head (the lounges), Rules, Settings.
    enum Tab: Hashable { case live, board, lounges, rules, settings }

    struct Notice: Identifiable, Equatable {
        let id = UUID()
        var text: String
        var isError: Bool
        /// How long the banner stays up.
        var seconds: Double = 3.5
    }

    /// A message from the host, shown as a banner to everyone until they dismiss it or the host clears it.
    struct Announcement: Equatable {
        var id: Int
        var text: String
    }

    private enum Key {
        static let token = "ptp.token"
        static let username = "ptp.username"
        static let lounge = "ptp.activeLounge"
        static let dismissedAnnouncement = "ptp.dismissedAnnouncement"
    }

    @Published private(set) var server: URL? = ServerConfig.current
    @Published private(set) var token: String? = UserDefaults.standard.string(forKey: Key.token)
    @Published private(set) var username: String? = UserDefaults.standard.string(forKey: Key.username)
    @Published private(set) var snapshot: StateSnapshot?
    @Published private(set) var connection: LiveConnection.Status = .offline
    @Published private(set) var lounges: [Lounge] = []
    @Published private(set) var activeLoungeID: String? = UserDefaults.standard.string(forKey: Key.lounge)
    @Published private(set) var pickType: PlayType?
    @Published private(set) var pickDirection: Direction?
    @Published private(set) var pickYardage: Yardage?
    @Published private(set) var savedPick: String?
    @Published private(set) var saving = false
    /// The host's banner, or nil when none is showing (cleared, or this player dismissed it).
    @Published private(set) var announcement: Announcement?
    /// True while the welcome screen is waiting for the game server to answer.
    @Published private(set) var waking = false
    @Published private(set) var rankMoves: [String: [Int: Int]] = [:]
    @Published var notice: Notice?
    @Published var tab: Tab = .live
    @Published var showPractice = false

    private let live = LiveConnection()
    private var clockOffset: Double = 0
    /// Store screenshots freeze the clock so the countdown always reads the same.
    private var frozenClock: Double?
    private var pickPlayID: Int?
    private var ranks: [String: [Int: Int]] = [:]
    private var rankGameID: Int?

    var isSignedIn: Bool { token != nil }
    var activeLounge: Lounge? { lounges.first { $0.id == activeLoungeID } }

    init() {
        live.onMessage = { [weak self] message in self?.handle(message) }
        live.onStatus = { [weak self] status in self?.connection = status }
        live.onRejected = { [weak self] _ in
            // (The server sends the reason, then closes the socket; don't replace the reason with a generic line.)
            guard let self, self.isSignedIn else { return }
            self.forgetAccount(message: "Please pick a username again.")
        }
        if let screen = ScreenshotMode.screen { stage(screen) }
    }

    /// Server clock, so every phone counts down to the same instant.
    func now() -> Double { frozenClock ?? Date().timeIntervalSince1970 + clockOffset }

    // MARK: - Connection

    func connect() {
        guard ScreenshotMode.screen == nil, isSignedIn, let server, let url = ServerConfig.socketURL(for: server) else { return }
        live.start(url: url, hello: HelloMessage(token: token, lounge: activeLoungeID))
    }

    func disconnect() {
        guard ScreenshotMode.screen == nil else { return }
        live.stop()
    }

    /// Waits up to `seconds` for the game server to answer. The free hosting plan puts an idle server to sleep and
    /// waking it takes up to a minute, longer than a normal request waits, so the first sign-up after a quiet
    /// spell used to fail with "Can't reach the game server".
    @discardableResult
    func wakeServer(seconds: TimeInterval = 90, announce: Bool = false) async -> Bool {
        guard ScreenshotMode.screen == nil, let server else { return false }
        let api = APIClient(server: server)
        let deadline = Date().addingTimeInterval(seconds)
        defer { if announce { waking = false } }
        while !Task.isCancelled {
            if await api.isAwake() { return true }
            if Date() >= deadline { return false }
            if announce { waking = true }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
        return false
    }

    // MARK: - Account

    func signUp(username: String) async throws {
        guard let server else { throw APIError(status: 0, message: "Enter the game server address first.") }
        guard await wakeServer(announce: true) else {
            throw APIError(status: 0, message: "Can't reach the game server. Check your connection and try again in a minute.")
        }
        let account = try await APIClient(server: server).createUser(username: username)
        token = account.token
        self.username = account.username
        persist()
        connect()
        await refreshLounges()
    }

    func deleteAccount() async throws {
        guard let server, let token else { return }
        try await APIClient(server: server, token: token).deleteAccount()
        forgetAccount(message: "Your account was deleted.")
    }

    /// Back to the welcome screen (account deleted, or the server no longer knows this device).
    func forgetAccount(message: String?, isError: Bool = false, seconds: Double = 3.5) {
        live.stop()
        announcement = nil
        token = nil
        username = nil
        lounges = []
        activeLoungeID = nil
        snapshot = nil
        pickType = nil
        pickDirection = nil
        pickYardage = nil
        savedPick = nil
        pickPlayID = nil
        tab = .live
        persist()
        if let message { notice = Notice(text: message, isError: isError, seconds: seconds) }
    }

    /// Returns false if the address can't be understood. An empty string restores the built-in server.
    @discardableResult
    func setServer(_ raw: String) -> Bool {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            UserDefaults.standard.removeObject(forKey: ServerConfig.overrideKey)
        } else {
            guard let url = ServerConfig.normalize(trimmed) else { return false }
            UserDefaults.standard.set(url.absoluteString, forKey: ServerConfig.overrideKey)
        }
        let changed = ServerConfig.current != server
        server = ServerConfig.current
        // Accounts live on a server; on a different one this device needs a new username.
        if changed && isSignedIn { forgetAccount(message: "Server changed. Pick a username for this game.") }
        return true
    }

    // MARK: - Lounges

    func refreshLounges() async {
        guard ScreenshotMode.screen == nil, let server, let token else { return }
        do {
            let me = try await APIClient(server: server, token: token).me()
            lounges = me.lounges
            username = me.user.username
            if let id = activeLoungeID, !lounges.contains(where: { $0.id == id }) { activate(lounge: nil) }
            persist()
        } catch let error as APIError where error.isUnauthorized {
            forgetAccount(message: "Please pick a username again.")
        } catch {
            // Offline: keep what we have; the socket retries on its own.
        }
    }

    func createLounge(name: String) async throws -> Lounge {
        guard let server, let token else { throw APIError(status: 401, message: "Pick a username first.") }
        let lounge = try await APIClient(server: server, token: token).createLounge(name: name)
        await refreshLounges()
        activate(lounge: lounge.id)
        return lounge
    }

    func joinLounge(code: String) async throws -> Lounge {
        guard let server, let token else { throw APIError(status: 401, message: "Pick a username first.") }
        let lounge = try await APIClient(server: server, token: token).joinLounge(code: code)
        await refreshLounges()
        activate(lounge: lounge.id)
        return lounge
    }

    /// The active lounge's private leaderboard rides along on the live socket.
    func activate(lounge id: String?) {
        guard id != activeLoungeID else { return }
        activeLoungeID = id
        ranks["lounge"] = nil  // arrows belonged to the previous lounge's board
        rankMoves["lounge"] = nil
        persist()
        connect()
    }

    func inviteText(for lounge: Lounge) -> String {
        var text = "Join my Pick the Play lounge \"\(lounge.name)\" with code \(lounge.id)."
        if let server { text += " \(server.absoluteString)/lounge/\(lounge.id)" }
        return text
    }

    // MARK: - Picks

    var secondsLeft: Double {
        guard let play = snapshot?.play, play.state == .open else { return 0 }
        return max(0, play.locksAt - now())
    }

    var canPick: Bool { isSignedIn && snapshot?.play?.state == .open && secondsLeft > 0 }

    func choose(_ type: PlayType) {
        guard canPick else { return }
        pickType = type
        submitIfReady()
    }

    func choose(_ direction: Direction) {
        guard canPick else { return }
        pickDirection = direction
        submitIfReady()
    }

    func choose(_ yardage: Yardage) {
        guard canPick else { return }
        pickYardage = yardage
        submitIfReady()
    }

    /// True when the server has confirmed exactly the three parts on screen.
    var pickIsSaved: Bool { currentKey != nil && savedPick == currentKey }

    private var currentKey: String? {
        guard let pickType, let pickDirection, let pickYardage else { return nil }
        return Self.key(pickType, pickDirection, pickYardage)
    }

    /// Sends the pick once all three parts are chosen; every later change re-sends it (the server
    /// replaces the pick while the play is OPEN).
    private func submitIfReady() {
        guard let play = snapshot?.play, let pickType, let pickDirection, let pickYardage else { return }
        saving = true
        let message = PredictMessage(playId: play.id, playType: pickType, direction: pickDirection, yardage: pickYardage)
        if live.send(message) { return }
        // Socket down: plain HTTP still gets the pick in before the clock runs out.
        guard let server, let token else {
            saving = false
            return
        }
        Task {
            do {
                let saved = try await APIClient(server: server, token: token).predict(message)
                onSaved(saved)
            } catch {
                saving = false
                notice = Notice(text: error.localizedDescription, isError: true)
            }
        }
    }

    private func onSaved(_ prediction: Prediction) {
        guard prediction.playId == pickPlayID else { return }
        var confirmed = prediction
        // A server that predates the distance pick ignores it; nothing more to save there.
        if confirmed.yardage == nil { confirmed.yardage = pickYardage }
        savedPick = Self.key(confirmed)
        // Still sending if this reply confirms an earlier pick and the player has changed it since.
        saving = savedPick != currentKey
        snapshot?.myPrediction = confirmed
    }

    private static func key(_ type: PlayType, _ direction: Direction, _ yardage: Yardage?) -> String {
        "\(type.rawValue)|\(direction.rawValue)|\(yardage?.rawValue ?? "-")"
    }

    private static func key(_ prediction: Prediction) -> String {
        key(prediction.playType, prediction.direction, prediction.yardage)
    }

    // MARK: - Server messages

    func handle(_ message: ServerMessage) {
        switch message {
        case .state(let snapshot):
            apply(snapshot)
        case .predictionSaved(let prediction):
            onSaved(prediction)
        case .announcement(let id, let text):
            showAnnouncement(id: id, text: text)
        case .error(let code, let text):
            if code == "bad_token" {
                forgetAccount(message: "Please pick a username again.")
                return
            }
            if code == "account_deleted" {
                // The server says why: "Your account was deleted." or "You were removed by the host."
                forgetAccount(message: text.isEmpty ? "Your account was deleted." : text, isError: true, seconds: 8)
                return
            }
            saving = false
            notice = Notice(text: text, isError: true)
        case .pong(let serverTime):
            if serverTime > 0 { clockOffset = serverTime - Date().timeIntervalSince1970 }
        case .other:
            break
        }
    }

    // MARK: - Message from the host

    /// Show the host's banner; empty text clears it, and a banner this player already dismissed stays hidden.
    func showAnnouncement(id: Int, text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || id == UserDefaults.standard.integer(forKey: Key.dismissedAnnouncement) {
            announcement = nil
        } else {
            announcement = Announcement(id: id, text: trimmed)
        }
    }

    func dismissAnnouncement() {
        guard let current = announcement else { return }
        UserDefaults.standard.set(current.id, forKey: Key.dismissedAnnouncement)
        announcement = nil
    }

    func apply(_ snapshot: StateSnapshot) {
        clockOffset = snapshot.serverTime - Date().timeIntervalSince1970
        if let me = snapshot.me { username = me.username }
        trackRankMoves(snapshot)
        self.snapshot = snapshot
        guard let play = snapshot.play, play.state == .open else { return }
        if pickPlayID != play.id {
            pickPlayID = play.id
            pickType = snapshot.myPrediction?.playType
            pickDirection = snapshot.myPrediction?.direction
            pickYardage = snapshot.myPrediction?.yardage
            savedPick = snapshot.myPrediction.map(Self.key)
            saving = false
        } else if saving, snapshot.event == "sync" {
            // A reconnect swallowed the reply to our pick: trust the server's copy if it has all three
            // parts we chose, or send it again (the server upserts picks, so a repeat is harmless).
            if let mine = snapshot.myPrediction, Self.key(mine) == currentKey {
                savedPick = currentKey
                saving = false
            } else {
                submitIfReady()
            }
        }
    }

    /// Rank changes since the last scored play (▲2 / ▼1 on the leaderboard).
    private func trackRankMoves(_ snapshot: StateSnapshot) {
        if snapshot.game?.id != rankGameID {
            rankGameID = snapshot.game?.id
            ranks = [:]
            rankMoves = [:]
        }
        let boards: [String: [BoardRow]] = ["global": snapshot.leaderboard, "lounge": snapshot.lounge?.leaderboard ?? []]
        let record = snapshot.event == "play_resolved"
        for (name, rows) in boards {
            let before = ranks[name] ?? [:]
            if record {
                var moves: [Int: Int] = [:]
                for row in rows { if let old = before[row.userId] { moves[row.userId] = old - row.rank } }
                rankMoves[name] = moves
            }
            if record || before.isEmpty {
                var after: [Int: Int] = [:]
                for row in rows { after[row.userId] = row.rank }
                ranks[name] = after
            }
        }
    }

    private func persist() {
        let defaults = UserDefaults.standard
        defaults.set(token, forKey: Key.token)
        defaults.set(username, forKey: Key.username)
        defaults.set(activeLoungeID, forKey: Key.lounge)
    }

    // MARK: - Store screenshots

    private func stage(_ screen: ScreenshotMode.Screen) {
        let sample = ScreenshotMode.sample(for: screen)
        token = "screenshot"
        username = sample.snapshot.me?.username
        lounges = sample.lounges
        activeLoungeID = sample.lounges.first?.id
        connection = .online
        if let pick = sample.snapshot.myPrediction {
            pickType = pick.playType
            pickDirection = pick.direction
            pickYardage = pick.yardage
            savedPick = Self.key(pick)
            pickPlayID = pick.playId
        }
        trackRankMoves(sample.previous)
        snapshot = sample.snapshot
        frozenClock = sample.snapshot.serverTime
        trackRankMoves(sample.snapshot)
        tab = sample.tab
    }
}
