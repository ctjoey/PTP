import SwiftUI

/// The "Run" tab: start a game, open and lock each play, score it (or let the live feed), end the game.
struct HostRunView: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    /// Start another game while one is still showing.
    @State private var startingAnother = false

    var body: some View {
        if let state = host.snapshot {
            VStack(spacing: 14) {
                if let game = state.game {
                    ScorebugView(game: game, play: state.play)
                    HostCrowdLine(state: state)
                }
                switch state.stage {
                case .noGame:
                    StartGameCard(replacing: nil)
                case .gameOver:
                    StartGameCard(replacing: nil, finished: state.game)
                case .ready, .open, .locked:
                    if startingAnother {
                        StartGameCard(replacing: state.game, onDone: { startingAnother = false })
                    } else {
                        PlayControlCard(state: state)
                    }
                }
                if state.game != nil && state.stage != .gameOver && !startingAnother {
                    if state.lockedPlayIsLeftToHost {
                        // Live data can't score this play, so the way to do it comes first.
                        ScoreItYourselfCard(state: state)
                        HostLiveDataCard()
                    } else {
                        HostLiveDataCard()
                        if state.hasActivePlay { ScoreItYourselfCard(state: state) }
                    }
                    GameFooterCard(state: state, startAnother: { startingAnother = true })
                }
            }
            .onChange(of: state.game?.id) { _, _ in startingAnother = false }
            // A play was opened (here or on another console) while "Start a different game" was showing: back to the play.
            .onChange(of: state.hasActivePlay) { _, active in if active { startingAnother = false } }
        }
    }
}

// MARK: - Who is here

/// "12 playing · 3 watching", and a warning when a second host is connected.
private struct HostCrowdLine: View {
    var state: AdminState

    var body: some View {
        VStack(spacing: 6) {
            Text("\(state.playersOnline) playing · \(state.spectatorsOnline) watching")
                .font(.system(size: 13, weight: .bold)).foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity, alignment: .center)
            if state.adminsOnline > 1 {
                Text("\(state.adminsOnline) hosts are connected. Only one of you should open and score plays.")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.warn)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
            }
        }
    }
}

// MARK: - Starting a game

/// "Pick today's game" (or the recorded practice game) and Create Game, or choose the teams by hand.
private struct StartGameCard: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    /// The game that will be marked FINAL when a new one starts (nil when none is running).
    var replacing: Game?
    var finished: Game?
    var onDone: (() -> Void)?

    @State private var showPicker = false
    @State private var showTeams = false
    @State private var confirm = false
    @State private var working = false

    init(replacing: Game?, finished: Game? = nil, onDone: (() -> Void)? = nil) {
        self.replacing = replacing
        self.finished = finished
        self.onDone = onDone
    }

    var body: some View {
        HostCard(finished == nil ? "Start a game" : "Game over", systemImage: "flag.checkered") {
            if let finished {
                HostHint("\(HostText.gameLine(finished)) is final. Start another game when you're ready.")
            } else if replacing != nil {
                HostHint("Starting a new game marks the current one FINAL.", color: Theme.warn)
            } else {
                HostHint("Pick the game, then tap Create Game. Players see it right away.")
            }
            if let game = drafts.pickedGame {
                picked(game)
            } else {
                HostButton(feedAvailable ? "Pick today's game" : "Practice with a recorded game",
                           systemImage: "sportscourt.fill", kind: .primary) {
                    if feedAvailable { showPicker = true } else { drafts.pickedGame = FeedGame.practice }
                }
                .disabled(host.isBusy || working)
                if feedAvailable {
                    HostButton("Practice with a recorded game", systemImage: "play.rectangle") {
                        drafts.pickedGame = FeedGame.practice
                    }
                    .disabled(host.isBusy || working)
                } else {
                    HostHint("Live data isn't set up on this server (no Tank01 key). Only the recorded practice game is offered.")
                }
                Button {
                    showTeams = true
                } label: {
                    Text("Choose the teams yourself (no live data)")
                        .font(.system(size: 14, weight: .bold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .tint(Theme.muted)
                .disabled(host.isBusy || working)
            }
            if replacing != nil, let onDone {
                Button("Cancel", action: onDone)
                    .font(.system(size: 15, weight: .bold))
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
        }
        .sheet(isPresented: $showPicker) {
            GamePickerSheet { drafts.pickedGame = $0 }
                .environmentObject(host)
                .environmentObject(drafts)
        }
        .sheet(isPresented: $showTeams) {
            TeamPickerSheet { away, home in
                Task { await create(away: away, home: home, feedGameID: nil) }
            }
            .environmentObject(host)
        }
        .confirmationDialog("Start a new game?", isPresented: $confirm, titleVisibility: .visible) {
            Button("Create Game", role: .destructive) { createPicked() }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let replacing { Text("“\(HostText.gameLine(replacing))” will be marked FINAL.") }
        }
    }

    private var feedAvailable: Bool { host.feed?.available == true }

    private func picked(_ game: FeedGame) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(game.isRecorded ? "Practice game" : "Live data").kicker()
            Text(game.isRecorded ? "\(game.away.name) at \(game.home.name)" : game.label)
                .font(.system(size: 20, weight: .black)).foregroundStyle(Theme.text)
            HostHint(game.isRecorded
                     ? "A recorded game, so no requests are used. Tap Create Game to start it."
                     : "The live feed will follow this game. Tap Create Game to start it.")
            HostButton("Create Game", systemImage: "checkmark.circle.fill", kind: .primary, busy: working) {
                if replacing != nil { confirm = true } else { createPicked() }
            }
            .disabled(host.isBusy || working)
            Button("Choose a different game") { drafts.pickedGame = nil }
                .font(.system(size: 15, weight: .bold))
                .frame(maxWidth: .infinity, minHeight: 44)
                .disabled(working)
        }
    }

    private func createPicked() {
        guard let game = drafts.pickedGame else { return }
        Task { await create(away: TeamChoice(game.away), home: TeamChoice(game.home), feedGameID: game.feedGameId) }
    }

    private func create(away: TeamChoice, home: TeamChoice, feedGameID: String?) async {
        working = true
        defer { working = false }
        guard await host.createGame(away: away, home: home, feedGameID: feedGameID) else { return }
        drafts.pickedGame = nil
        let how = feedGameID == nil ? "" : (feedGameID == FeedGame.demoID ? " It is following the recorded practice game." : " It is following the live feed.")
        host.show("Game created.\(how) Open the first play when you're ready.")
        onDone?()
    }
}

/// Two city pickers for a game that doesn't come from the live schedule.
private struct TeamPickerSheet: View {
    @EnvironmentObject var host: HostState
    @Environment(\.dismiss) private var dismiss
    var onCreate: (TeamChoice, TeamChoice) -> Void

    @State private var away = "Chicago"
    @State private var home = "Detroit"

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HostCard("Teams") {
                        Picker("Away team", selection: $away) {
                            ForEach(TeamPreset.all) { team in Text(team.label).tag(team.id) }
                        }
                        Picker("Home team", selection: $home) {
                            ForEach(TeamPreset.all) { team in Text(team.label).tag(team.id) }
                        }
                        if let awayTeam, let homeTeam {
                            Text("\(awayTeam.name) at \(homeTeam.name)")
                                .font(.system(size: 20, weight: .black)).foregroundStyle(Theme.text)
                        }
                        HostHint(sameName
                                 ? "Pick two different teams. For New York against New York or Los Angeles against Los Angeles, use the live schedule instead."
                                 : "You score every play by hand in a game like this.",
                                 color: sameName ? Theme.warn : Theme.muted)
                    }
                    HostButton("Create Game", systemImage: "checkmark.circle.fill", kind: .primary) {
                        guard let awayTeam, let homeTeam else { return }
                        dismiss()
                        onCreate(awayTeam.choice, homeTeam.choice)
                    }
                    .disabled(host.isBusy || awayTeam == nil || homeTeam == nil || sameName)
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Choose the teams")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Close") { dismiss() } }
            }
        }
    }

    private var awayTeam: TeamPreset? { TeamPreset.all.first { $0.id == away } }
    private var homeTeam: TeamPreset? { TeamPreset.all.first { $0.id == home } }
    private var sameName: Bool { awayTeam?.name.lowercased() == homeTeam?.name.lowercased() }
}

// MARK: - The play

/// Open the next play, watch the clock, lock it.
private struct PlayControlCard: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    var state: AdminState

    @State private var opening = false
    @FocusState private var timerFocused: Bool

    var body: some View {
        HostCard(title, systemImage: "football.fill") {
            HStack(alignment: .center) {
                Text(state.play?.label ?? "No play yet")
                    .font(.system(size: 22, weight: .black)).foregroundStyle(Theme.text)
                Spacer()
                badge
            }
            switch state.stage {
            case .open: openBody
            case .locked: lockedBody
            default: readyBody
            }
        }
        .animation(.easeInOut(duration: 0.2), value: state.stage)
    }

    private var title: String {
        switch state.stage {
        case .open: return "Players are picking"
        case .locked: return "Picks are locked"
        default: return "Next play"
        }
    }

    private var badge: some View {
        let text: String
        if let play = state.play { text = play.voided ? "VOIDED" : play.state.rawValue } else { text = "IDLE" }
        return Chip(text: text, style: state.stage == .open ? .good : (state.stage == .locked ? .gold : .plain))
    }

    // Between plays: down, to go, Open Next Play.
    private var readyBody: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Down").kicker()
            HStack(spacing: 8) {
                ForEach([1, 2, 3, 4], id: \.self) { down in
                    PickButton(title: Football.ordinal(down).uppercased(), selected: drafts.down.down == down,
                               enabled: !host.isBusy, height: 48, spoken: Football.ordinal(down) + " down") {
                        drafts.down.setDown(down)
                    }
                }
            }
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Yards to go").kicker()
                    TextField("10", text: Binding(get: { drafts.down.distance }, set: { drafts.down.setDistance($0) }))
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.characters)
                        .font(.system(size: 20, weight: .heavy))
                        .padding(14)
                        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("Timer (s)").kicker()
                    timerField
                }
                .frame(width: 104)
            }
            if drafts.down.showsFeedHint(hasActivePlay: false) {
                HostHint("Filled in from the live feed. Change it if the TV says something else.", color: Theme.accent)
            } else {
                HostHint("Match the TV: a number, or Goal.")
            }
            if let warning = drafts.timer.warning { HostHint(warning, color: Theme.warn) }
            HostButton("Open Next Play", systemImage: "play.fill", kind: .primary, busy: opening) {
                Task { await open() }
            }
            .disabled(host.isBusy || opening || state.game?.status == .final)
            HostHint("Open the play just before the snap. Players then have \(drafts.timer.seconds) seconds to pick.")
        }
    }

    /// How many seconds players get to pick (5 to 60). The number pad has no Done key, so the keyboard gets one.
    private var timerField: some View {
        TextField("15", text: Binding(get: { drafts.timer.text }, set: { drafts.timer.type($0) }))
            .keyboardType(.numberPad)
            .focused($timerFocused)
            .font(.system(size: 20, weight: .heavy))
            .padding(14)
            .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(timerFocused ? Theme.accent : Color.clear, lineWidth: 1))
            .accessibilityLabel("Timer in seconds, 5 to 60")
            .onChange(of: timerFocused) { _, focused in if !focused { drafts.timer.normalize() } }
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    // The keyboard toolbar belongs to the whole screen: only offer Done while this box is the one being typed in.
                    if timerFocused { Button("Done") { timerFocused = false } }
                }
            }
    }

    // Players are picking: the clock, who has picked, Lock.
    private var openBody: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let play = state.play {
                TimelineView(.periodic(from: .now, by: 0.1)) { _ in
                    let remaining = max(0, play.locksAt - host.now())
                    HStack(spacing: 16) {
                        CountdownRing(remaining: remaining, total: max(1, play.locksAt - play.openedAt), size: 76)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(HostText.count(state.pickStats?.total ?? 0, "pick") + " so far")
                                .font(.system(size: 18, weight: .heavy)).foregroundStyle(Theme.text)
                            Text(remaining > 0 ? "It locks by itself at 0." : "Locking…")
                                .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.muted)
                        }
                        Spacer()
                    }
                }
            }
            HostButton("Lock Predictions", systemImage: "lock.fill", kind: .warn) {
                Task { _ = await host.lockPlay() }
            }
            .disabled(host.isBusy)
            HostHint("Lock early if the snap already happened.")
        }
    }

    // Locked: waiting for the result. What it says depends on whether live data is really following the play.
    private var lockedBody: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let crowd = state.pickStats, crowd.total > 0 { CrowdBars(crowd: crowd) }
            HostHint(HostText.lockedGuidance(state), color: state.lockedPlayIsLeftToHost ? Theme.warn : Theme.muted)
        }
    }

    private func open() async {
        timerFocused = false
        drafts.timer.normalize()
        opening = true
        defer { opening = false }
        let ok = await host.openPlay(down: drafts.down.down, distance: drafts.down.distanceToSend,
                                     windowSeconds: Double(drafts.timer.seconds))
        if ok { drafts.result.clear() }
    }
}

// MARK: - Scoring by hand

/// Pick what really happened and score the play (also the fallback when the live feed can't).
private struct ScoreItYourselfCard: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    var state: AdminState

    @State private var confirmVoid = false
    @State private var scoring = false

    var body: some View {
        let locked = state.stage == .locked
        HostCard("Score it yourself", systemImage: "checkmark.seal.fill") {
            Text("Run or pass?").kicker()
            HStack(spacing: 10) {
                ForEach(PlayType.allCases) { option in
                    PickButton(title: option.rawValue, caption: option.caption, selected: drafts.result.playType == option,
                               enabled: !host.isBusy, height: 56, spoken: option.title) {
                        edited { drafts.result.choose(option) }
                    }
                }
            }
            Text("Which way? (as the QB looks downfield)").kicker()
            HStack(spacing: 10) {
                ForEach(Direction.allCases) { option in
                    PickButton(title: option.rawValue, symbol: option.symbol, selected: drafts.result.direction == option,
                               enabled: !host.isBusy, height: 54, spoken: option.title) {
                        edited { drafts.result.choose(option) }
                    }
                }
            }
            Text("How far?").kicker()
            HStack(spacing: 8) {
                ForEach(HostText.distanceChoices, id: \.self) { option in
                    PickButton(title: option.rawValue, caption: caption(option), selected: drafts.result.yardage == option,
                               enabled: !host.isBusy, height: 54, spoken: spoken(option)) {
                        edited { drafts.result.choose(option) }
                    }
                }
            }
            Text("Yards gained (optional)").kicker()
            TextField("e.g. 7 or -4", text: Binding(get: { drafts.result.yardsText },
                                                    set: { value in edited { drafts.result.typeYards(value) } }))
                .keyboardType(.numbersAndPunctuation)
                .autocorrectionDisabled()
                .font(.system(size: 20, weight: .heavy))
                .padding(14)
                .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12))
            if drafts.result.yardsAreInvalid {
                HostHint("Yards must be a whole number from -99 to 99.", color: Theme.danger)
            } else if drafts.result.isComplete {
                HostHint("✓ \(drafts.result.summary)", color: Theme.accent)
            } else {
                HostHint("Pick all three, then tap Score Play.")
            }
            HostButton("Score Play", systemImage: "checkmark", kind: .primary, busy: scoring) {
                Task { await score() }
            }
            .disabled(host.isBusy || scoring || !locked || !drafts.result.isComplete)
            if !locked { HostHint("Lock the play first, then score it.") }
            HostButton(HostText.voidPlay, systemImage: "nosign", kind: .secondary) {
                confirmVoid = true
            }
            .disabled(host.isBusy)
            HostHint(HostText.voidHint)
        }
        .confirmationDialog("Void this play?", isPresented: $confirmVoid, titleVisibility: .visible) {
            Button("Void play", role: .destructive) { Task { _ = await host.voidPlay() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Nobody scores and players see “No play”.")
        }
    }

    /// Choosing a result by hand stops the feed suggestion's timer so the two can't race.
    private func edited(_ change: () -> Void) {
        drafts.manualEditStarted(feed: state.feed, host: host)
        change()
    }

    private func score() async {
        scoring = true
        defer { scoring = false }
        let summary = drafts.result.summary
        if await host.resolvePlay(drafts.result) {
            drafts.result.clear()
            host.show("Scored: \(summary)")
        }
    }

    private func caption(_ option: YardageOutcome) -> String {
        option.pick?.range ?? "lost yards"
    }

    private func spoken(_ option: YardageOutcome) -> String {
        if let pick = option.pick { return "\(pick.title), \(pick.spokenRange)" }
        return "Loss of yards"
    }
}

// MARK: - End or change the game

private struct GameFooterCard: View {
    @EnvironmentObject var host: HostState
    var state: AdminState
    var startAnother: () -> Void

    @State private var confirmEnd = false

    var body: some View {
        HostCard("Game", systemImage: "flag.fill") {
            HostButton("End game", systemImage: "flag.checkered", kind: .secondary) { confirmEnd = true }
                .disabled(host.isBusy || !state.canEndGame)
            if state.hasActivePlay { HostHint("Finish or void the current play first.") }
            Button("Start a different game", action: startAnother)
                .font(.system(size: 15, weight: .bold))
                .frame(maxWidth: .infinity, minHeight: 44)
                .tint(Theme.muted)
                .disabled(host.isBusy || state.hasActivePlay)
        }
        .confirmationDialog("End the game?", isPresented: $confirmEnd, titleVisibility: .visible) {
            Button("End game", role: .destructive) { Task { _ = await host.endGame() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This marks it FINAL. Players see the final scores.")
        }
    }
}
