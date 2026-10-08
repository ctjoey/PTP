import SwiftUI

// The host console's "Players" tab: who has signed up, find someone by name, remove them (and block their name),
// and give a blocked name back. Ports the Players card of the website console (static/js/admin-players.js).
//
// What carries over from the website:
//   - typing narrows the list at once, and 250 ms after the last key the server is asked as well (the server
//     list is capped, so a name that isn't loaded yet can still be found);
//   - the list refreshes by itself every 20 s while this tab is open, and at once when the number of sign-ups
//     changes (here or on another console);
//   - at most 200 rows are drawn, newest first, with a note when there are more;
//   - Remove always asks first: "Remove and block name", "Remove only" or "Cancel";
//   - blocked names are listed with an Unblock button each;
//   - a failed load shows its error instead of the list, and a rejected key stops the automatic refresh.

// MARK: - The Players tab

struct HostPlayersView: View {
    @EnvironmentObject var host: HostState
    @StateObject private var list = PlayerList()
    /// The player the host tapped Remove on; the confirmation sheet is open while this is set.
    @State private var asking: HostPlayer?
    @FocusState private var searching: Bool

    var body: some View {
        VStack(spacing: 14) {
            playersCard
            if !list.blocked.isEmpty { blockedCard }
        }
        // Loads now, then every 20 seconds. SwiftUI cancels this when the tab goes away, which ends the loop.
        .task { await list.keepFresh(host: host) }
        .onChange(of: host.snapshot?.registeredPlayers) { _, count in
            // Someone signed up or was removed (here or elsewhere): bring the list up to date.
            let model = self.list, host = self.host
            guard let count, count != model.total else { return }
            Task { @MainActor in await model.load(host: host) }
        }
        .onChange(of: list.query) { _, _ in
            asking = nil
            list.queryChanged(host: host)
        }
        .onChange(of: list.players) { _, players in
            // The player being asked about is gone (removed from another console): drop the question.
            if let asking, !players.contains(where: { $0.id == asking.id }) { self.asking = nil }
        }
        .onDisappear { list.stop() }
        .confirmationDialog("Remove \(asking?.username ?? "this player")?", isPresented: askingBinding,
                            titleVisibility: .visible, presenting: asking) { player in
            Button("Remove and block name", role: .destructive) { remove(player, block: true) }
            Button("Remove only", role: .destructive) { remove(player, block: false) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("This deletes their account, picks and points. Blocking the name stops anyone using it again.")
        }
    }

    private var askingBinding: Binding<Bool> {
        Binding(get: { asking != nil }, set: { shown in if !shown { asking = nil } })
    }

    /// Something is waiting for the server (any action), or one of our own removals is: hold off on new ones.
    private var busy: Bool { host.isBusy || list.working }

    // MARK: Players card

    private var playersCard: some View {
        HostCard("Players", systemImage: "person.2.fill") {
            HStack(spacing: 10) {
                Text(verbatim: countText)
                    .font(.system(size: 20, weight: .black))
                    .foregroundStyle(Theme.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                refreshButton
            }
            searchField
            HostHint("Newest sign-ups first. Remove deletes a person's account, picks and points.")
            listContent
        }
    }

    /// "12 signed up": the server's live count when it sends one, else the count from the last list.
    private var countText: String {
        if let count = host.snapshot?.registeredPlayers ?? list.total { return "\(count) signed up" }
        return "Players"
    }

    private var refreshButton: some View {
        Button {
            searching = false
            let model = self.list, host = self.host
            Task { @MainActor in await model.load(host: host) }
        } label: {
            HStack(spacing: 6) {
                if list.loading {
                    ProgressView()
                } else {
                    Image(systemName: "arrow.clockwise")
                }
                Text("Refresh")
            }
            .font(.system(size: 15, weight: .bold))
            .frame(minHeight: 44)
        }
        .buttonStyle(.bordered)
        .tint(Theme.text)
        .disabled(list.loading)
        .fixedSize()
        .accessibilityLabel("Refresh the player list")
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.muted)
                .accessibilityHidden(true)
            TextField("Find a name", text: $list.query)
                .font(.system(size: 17))
                .foregroundStyle(Theme.text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .focused($searching)
                .onSubmit { searching = false }
                .accessibilityLabel("Find a player by name")
            if !list.query.isEmpty {
                Button {
                    list.query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(Theme.muted)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Clear the search")
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, list.query.isEmpty ? 14 : 2)
        .frame(minHeight: 48)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .stroke(searching ? Theme.accent : Theme.border, lineWidth: 1))
    }

    // MARK: The list

    /// What is loaded, narrowed by what is typed (the same test as the website: the name contains the text).
    private var rows: [HostPlayer] {
        let needle = list.currentQuery.lowercased()
        guard !needle.isEmpty else { return list.players }
        return list.players.filter { $0.username.lowercased().contains(needle) }
    }

    @ViewBuilder
    private var listContent: some View {
        let all = rows
        let shown = Array(all.prefix(PlayerList.showRows))
        if let message = list.error {
            VStack(alignment: .leading, spacing: 6) {
                Text(verbatim: message)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Theme.danger)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                HostHint(list.keyRejected
                    ? "Tap the three-dot button at the top and sign out. Then enter the key again."
                    : "Tap Refresh to try again.")
            }
        } else if !list.loaded {
            HStack(spacing: 10) {
                ProgressView()
                Text("Loading players…")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.muted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else if all.isEmpty {
            Text(emptyText)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            LazyVStack(spacing: 8) {
                ForEach(shown) { player in
                    PlayerRow(player: player, disabled: busy) {
                        searching = false
                        asking = player
                    }
                }
            }
            if all.count > shown.count {
                HostHint("Showing \(shown.count) of \(all.count). Type a name to narrow it down.")
            }
        }
    }

    private var emptyText: String {
        if list.currentQuery.isEmpty { return "No one has signed up yet." }
        // The server hasn't answered for this text yet: the name may just not be loaded.
        return list.isSearching ? "Searching…" : "No one by that name."
    }

    // MARK: Blocked names card

    private var blockedCard: some View {
        HostCard("Blocked names", systemImage: "nosign") {
            HostHint("No one can sign up with these names. Unblock a name to let someone use it again.")
            VStack(spacing: 8) {
                ForEach(Array(list.blocked.enumerated()), id: \.offset) { entry in
                    BlockedNameRow(name: entry.element, disabled: busy) { unblock(entry.element) }
                }
            }
        }
    }

    // MARK: Removing

    private func remove(_ player: HostPlayer, block: Bool) {
        searching = false
        let list = self.list, host = self.host
        Task { @MainActor in await list.remove(player, block: block, host: host) }
    }

    private func unblock(_ name: String) {
        let list = self.list, host = self.host
        Task { @MainActor in await list.unblock(name, host: host) }
    }
}

// MARK: - Rows

/// One player: name (with a green dot when online), what they scored, and a Remove button.
private struct PlayerRow: View {
    var player: HostPlayer
    var disabled: Bool
    var onRemove: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(verbatim: player.username)
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(Theme.text)
                        .lineLimit(2)
                    if player.online {
                        Circle().fill(Theme.accent).frame(width: 8, height: 8)
                            .accessibilityHidden(true)
                    }
                }
                Text(verbatim: player.summary)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            Button(action: onRemove) {
                Text("Remove")
                    .font(.system(size: 15, weight: .bold))
                    .frame(minWidth: 52, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(Theme.danger)
            .disabled(disabled)
            .fixedSize()
            .accessibilityLabel("Remove \(player.username)")
        }
        .padding(.leading, 12).padding(.trailing, 8).padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    /// "Sam, online now. 3 picks · 60 game pts · 120 season"
    private var spoken: String {
        player.online ? "\(player.username), online now. \(player.summary)" : "\(player.username). \(player.summary)"
    }
}

/// A blocked name with its Unblock button.
private struct BlockedNameRow: View {
    var name: String
    var disabled: Bool
    var onUnblock: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Text(verbatim: name)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(Theme.text)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: onUnblock) {
                Text("Unblock")
                    .font(.system(size: 15, weight: .bold))
                    .frame(minWidth: 52, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .tint(Theme.text)
            .disabled(disabled)
            .fixedSize()
            .accessibilityLabel("Unblock \(name)")
        }
        .padding(.leading, 14).padding(.trailing, 8).padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

// MARK: - The list's state

/// Loads the players from the server and keeps the replies in order. A reply is used only if it answers what is
/// typed now and is not older than one already shown, so a slow answer can never overwrite a newer one.
@MainActor
private final class PlayerList: ObservableObject {
    static let refreshEvery: UInt64 = 20_000_000_000
    static let searchDelay: UInt64 = 250_000_000
    static let showRows = 200
    /// The server looks at no more than this many characters of a search.
    static let longestQuery = 40

    /// The search box.
    @Published var query = ""
    @Published private(set) var players: [HostPlayer] = []
    @Published private(set) var blocked: [String] = []
    /// Everyone signed up, whatever the search (from the last good reply).
    @Published private(set) var total: Int?
    @Published private(set) var error: String?
    /// The server refused the admin key: the automatic refresh stops until a load works again.
    @Published private(set) var keyRejected = false
    /// A first answer (or error) has arrived.
    @Published private(set) var loaded = false
    @Published private(set) var loading = false
    /// A removal or unblock is waiting for the server.
    @Published private(set) var working = false
    /// The search text the list on screen is the server's answer to.
    @Published private(set) var settledQuery: String?

    private var inFlight = 0
    private var issued = 0
    private var applied = 0
    private var searchTask: Task<Void, Never>?

    /// What the server is asked: the box without surrounding spaces, cut to the longest search it reads.
    var currentQuery: String {
        String(query.trimmingCharacters(in: .whitespacesAndNewlines).prefix(Self.longestQuery))
    }

    /// The search text has changed since the last answer that was used.
    var isSearching: Bool { settledQuery != currentQuery }

    // MARK: Loading

    /// Loads now, then again every 20 s until the tab goes away (the caller's task is cancelled).
    func keepFresh(host: HostState) async {
        await load(host: host)
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.refreshEvery)
            if Task.isCancelled { break }
            if keyRejected { continue }
            await load(host: host)
        }
    }

    func load(host: HostState) async {
        let asked = currentQuery
        issued += 1
        let number = issued
        inFlight += 1
        loading = true
        defer {
            inFlight -= 1
            loading = inFlight > 0
        }
        do {
            let reply = try await host.loadPlayers(query: asked)
            guard accept(number, asked) else { return }
            players = reply.players
            blocked = reply.blockedNames
            total = reply.count
            error = nil
            keyRejected = false
            loaded = true
        } catch {
            // Cancelled because the tab closed or the text changed: nobody is waiting for this answer.
            if Task.isCancelled { return }
            guard accept(number, asked) else { return }
            if let failure = error as? APIError {
                if failure.isUnauthorized {
                    keyRejected = true
                    self.error = "The admin key was rejected."
                } else {
                    self.error = failure.localizedDescription
                }
            } else {
                self.error = "Couldn't load the players."
            }
            loaded = true
        }
    }

    /// True if this answer should be shown: it is for what is typed now, and nothing newer has been shown.
    private func accept(_ number: Int, _ asked: String) -> Bool {
        guard asked == currentQuery, number > applied else { return false }
        applied = number
        settledQuery = asked
        return true
    }

    /// The search box changed: ask the server once typing has paused for 250 ms.
    func queryChanged(host: HostState) {
        if query.count > Self.longestQuery {
            query = String(query.prefix(Self.longestQuery))   // changes the box again, which comes back here
            return
        }
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.searchDelay)
            guard !Task.isCancelled, let self else { return }
            await self.load(host: host)
        }
    }

    /// The tab is going away.
    func stop() {
        searchTask?.cancel()
        searchTask = nil
    }

    // MARK: Actions

    func remove(_ player: HostPlayer, block: Bool, host: HostState) async {
        guard !working else { return }
        working = true
        let done = await host.removePlayer(id: player.id, block: block)
        working = false
        if done {
            host.show(block ? "Removed \(player.username) and blocked the name." : "Removed \(player.username).")
            players.removeAll { $0.id == player.id }
        }
        await load(host: host)
    }

    func unblock(_ name: String, host: HostState) async {
        guard !working else { return }
        working = true
        let done = await host.unblock(name: name)
        working = false
        if done {
            host.show("Unblocked \(name).")
            blocked.removeAll { $0 == name }
        }
        await load(host: host)
    }
}
