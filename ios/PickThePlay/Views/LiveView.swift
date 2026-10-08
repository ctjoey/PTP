import SwiftUI

struct LiveView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let compact = geo.size.height < Theme.compactBelow
                ScrollView {
                    VStack(spacing: compact ? 12 : 14) {
                        // In the page, not the navigation bar: the newest iOS squeezes toolbar items into a small
                        // bubble and cut the logo down to "P…".
                        HStack(alignment: .center, spacing: 8) {
                            BrandMark()
                            Spacer(minLength: 8)
                            ConnectionDot(status: state.connection)
                        }
                        .padding(.horizontal, 2)
                        if let game = state.snapshot?.game {
                            ScorebugView(game: game, play: state.snapshot?.play)
                        }
                        StageView()
                        if let me = state.snapshot?.me {
                            HStack(spacing: 10) {
                                StatTile(label: "Game pts", value: "\(me.gameScore)")
                                StatTile(label: "Rank", value: me.rank.map { "#\($0)" } ?? "—")
                                StatTile(label: "Season pts", value: "\(me.totalScore)")
                            }
                        }
                        Button {
                            state.showPractice = true
                        } label: {
                            Label("Practice mode", systemImage: "figure.american.football")
                                .font(.system(size: 15, weight: .bold))
                                .frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.bordered)
                        .tint(Theme.muted)
                        Disclaimer()
                    }
                    .padding(16)
                }
                .environment(\.compactLayout, compact)
            }
            .background(Theme.bg.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
        }
    }
}

struct BrandMark: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            (Text("PICK THE ").foregroundColor(Theme.text) + Text("PLAY").foregroundColor(Theme.accent))
                .font(.system(size: 20, weight: .black)).italic()
            Text("LIVE PRO FOOTBALL").font(.system(size: 9, weight: .bold)).tracking(2.2).foregroundStyle(Theme.muted)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Pick the Play")
    }
}

struct Disclaimer: View {
    var body: some View {
        Text("Pick the Play is an independent fan prediction game for entertainment only. It is not affiliated with, endorsed by, or sponsored by any professional football league or club. Teams are shown by city or region name and colors only; no club nicknames, logos or league marks are used.")
            .font(.system(size: 11))
            .foregroundStyle(Theme.dim)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 8)
    }
}

// MARK: - Stage

struct StageView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.compactLayout) private var compact

    var body: some View {
        content
            .frame(maxWidth: .infinity, minHeight: compact ? 340 : 380)
            .card(padding: compact ? 14 : 18)
    }

    @ViewBuilder private var content: some View {
        if let snapshot = state.snapshot {
            if let game = snapshot.game {
                if game.status == .final && (snapshot.play == nil || snapshot.play?.state == .resolved) {
                    FinalStage(snapshot: snapshot, game: game)
                } else if let play = snapshot.play {
                    switch play.state {
                    case .open: OpenStage(play: play)
                    case .locked: LockedStage(snapshot: snapshot, play: play)
                    case .resolved: ResultStage(snapshot: snapshot, play: play, game: game)
                    }
                } else {
                    WaitingStage(scheduled: game.status == .scheduled)
                }
            } else {
                NoGameStage()
            }
        } else {
            ConnectingStage()
        }
    }
}

private struct CenteredStage<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(spacing: 14) { content }
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 340)
    }
}

private struct StageTitle: View {
    var text: String
    var body: some View {
        Text(text).font(.system(size: 24, weight: .black)).foregroundStyle(Theme.text).multilineTextAlignment(.center)
    }
}

private struct BouncingBall: View {
    @State private var up = false
    var body: some View {
        Text("🏈").font(.system(size: 54))
            .rotationEffect(.degrees(up ? -8 : 0))
            .offset(y: up ? -8 : 0)
            .onAppear { withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) { up = true } }
            .accessibilityHidden(true)
    }
}

struct ConnectingStage: View {
    @EnvironmentObject var state: AppState
    var body: some View {
        CenteredStage {
            BouncingBall()
            if state.server == nil {
                StageTitle(text: "No game server set")
                Text("Add the game's address in Settings, or try Practice mode.").foregroundStyle(Theme.muted)
                Button("Open Settings") { state.tab = .settings }.buttonStyle(.borderedProminent).tint(Theme.accent)
            } else {
                Text(state.connection == .offline ? "Can't reach the game. Retrying…" : "Connecting to the stadium…")
                    .foregroundStyle(Theme.muted)
            }
        }
    }
}

struct NoGameStage: View {
    @EnvironmentObject var state: AppState
    var body: some View {
        CenteredStage {
            Text("🏟️").font(.system(size: 54)).accessibilityHidden(true)
            StageTitle(text: "No game on right now")
            Text("Keep the app open. When a game starts, it shows up here instantly.").foregroundStyle(Theme.muted)
            Button {
                state.showPractice = true
            } label: {
                Text("Try a practice round").font(.system(size: 16, weight: .bold)).frame(minHeight: 36)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.accent)
            .foregroundStyle(Theme.accentInk)
        }
    }
}

struct WaitingStage: View {
    var scheduled: Bool
    var body: some View {
        CenteredStage {
            BouncingBall()
            Text(scheduled ? "Kickoff soon" : "Get ready").kicker()
            StageTitle(text: scheduled ? "The game hasn't started yet" : "Waiting for the next play…")
            Text("You get 15 seconds to call it once the play opens.").foregroundStyle(Theme.muted)
        }
    }
}

struct OpenStage: View {
    @EnvironmentObject var state: AppState
    @Environment(\.compactLayout) private var compact
    var play: Play

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.1)) { _ in
            let remaining = max(0, play.locksAt - state.now())
            let scoring = state.snapshot?.scoring ?? .standard
            VStack(alignment: .leading, spacing: compact ? 10 : 14) {
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(play.label).kicker()
                        StageTitle(text: "Call the play!")
                    }
                    Spacer()
                    CountdownRing(remaining: remaining, total: max(1, play.locksAt - play.openedAt), size: compact ? 62 : 84)
                }
                PickPanel(type: state.pickType, direction: state.pickDirection, yardage: state.pickYardage,
                          enabled: remaining > 0 && state.isSignedIn, scoring: scoring,
                          onType: { state.choose($0) }, onDirection: { state.choose($0) }, onYardage: { state.choose($0) })
                status(remaining: remaining)
                BonusChip(scoring: scoring)
            }
        }
    }

    private func status(remaining: Double) -> some View {
        let (text, color): (String, Color) = {
            if remaining <= 0 { return ("Time! Locking predictions…", Theme.muted) }
            if state.saving { return ("Sending your pick…", Theme.muted) }
            if state.pickIsSaved, let t = state.pickType, let d = state.pickDirection, let y = state.pickYardage {
                return ("✓ Locked in: \(t.rawValue) · \(d.rawValue) · \(y.rawValue). Change it until the clock hits 0.", Theme.accent)
            }
            return (Football.pickPrompt(type: state.pickType, direction: state.pickDirection, yardage: state.pickYardage),
                    Theme.muted)
        }()
        return Text(text)
            .font(.system(size: compact ? 13 : 14, weight: .semibold))
            .foregroundStyle(color)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: compact ? 38 : 44)
            .padding(.horizontal, 10)
            .background(color == Theme.accent ? Theme.accent.opacity(0.12) : Theme.surface2,
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct LockedStage: View {
    var snapshot: StateSnapshot
    var play: Play
    @State private var sweep = false
    @Environment(\.compactLayout) private var compact

    var body: some View {
        VStack(spacing: compact ? 10 : 14) {
            Image(systemName: "lock.fill")
                .font(.system(size: compact ? 24 : 30, weight: .bold))
                .foregroundStyle(Theme.warn)
                .frame(width: compact ? 58 : 76, height: compact ? 58 : 76)
                .background(Theme.warn.opacity(0.16), in: Circle())
            Text(play.label).kicker()
            StageTitle(text: "Predictions Locked — Play in Progress")
            Capsule()
                .fill(Theme.warn.opacity(0.25))
                .frame(height: 8)
                .overlay(alignment: .leading) {
                    GeometryReader { geo in
                        Capsule().fill(Theme.warn).frame(width: geo.size.width * 0.3)
                            .offset(x: sweep ? geo.size.width * 0.7 : 0)
                    }
                }
                .clipShape(Capsule())
                .onAppear { withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { sweep = true } }
            PickChips(pick: snapshot.myPrediction, graded: false)
            if let crowd = snapshot.crowd, crowd.total > 0 {
                CrowdBars(crowd: crowd).padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

struct ResultStage: View {
    var snapshot: StateSnapshot
    var play: Play
    var game: Game

    var body: some View {
        let outcome = play.outcome
        let pick = snapshot.myPrediction.map { ScoreRules.graded($0, outcome: outcome) }
        let points: Int? = pick.map { $0.pointsEarned ?? 0 }
        let exact = ScoreRules.isPerfect(pick, scoring: snapshot.scoring)
        VStack(spacing: 14) {
            Text("\(play.label) — \(play.voided ? "No play" : "Result")").kicker()
            if play.voided {
                Text("VOID").font(.system(size: 60, weight: .black)).foregroundStyle(Theme.dim)
                Text("No play (penalty, sack or QB scramble). No points.").foregroundStyle(Theme.muted)
            } else if let outcome {
                ResultReveal(outcome: outcome, points: points, label: ScoreRules.label(pick: pick, scoring: snapshot.scoring),
                             exact: exact, animationKey: play.id)
                PickChips(pick: pick, graded: true)
                if let crowd = snapshot.crowd, crowd.total > 0 {
                    Text("\(percent(crowd.exact, crowd.total))% of \(crowd.total) \(crowd.total == 1 ? "player" : "players") got all three · \(percent(crowd.scored, crowd.total))% scored")
                        .font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
            }
            Text("Next play coming up…").font(.system(size: 13)).foregroundStyle(Theme.muted)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .overlay {
            if exact && snapshot.event == "play_resolved" {
                ConfettiView(colors: [Color(hex: game.homePrimary), Color(hex: game.homeSecondary),
                                      Color(hex: game.awaySecondary), Theme.gold, Theme.accent])
                    .id(play.id)
            }
        }
    }

    private func percent(_ part: Int, _ total: Int) -> Int {
        total > 0 ? Int((Double(part) * 100 / Double(total)).rounded()) : 0
    }
}

struct FinalStage: View {
    var snapshot: StateSnapshot
    var game: Game

    var body: some View {
        CenteredStage {
            Text("🏆").font(.system(size: 54)).accessibilityHidden(true)
            Text("Final").kicker()
            StageTitle(text: "Final: \(game.awayName) @ \(game.homeName)")
            Text(summary).foregroundStyle(Theme.muted)
        }
    }

    private var summary: String {
        guard let me = snapshot.me else { return "Thanks for watching!" }
        guard let rank = me.rank else { return "You didn't make any picks this game. Catch the next one!" }
        var text = "You finished #\(rank) of \(snapshot.rankedPlayers) with \(me.gameScore) points."
        if let lounge = snapshot.lounge, let row = lounge.leaderboard?.first(where: { $0.userId == me.id }) {
            text += " #\(row.rank) in \(lounge.name)."
        }
        return text
    }
}
