import SwiftUI

/// Offline practice round: same timer, three-part pick and scoring as the live game, with simulated plays.
struct PracticeView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var game = PracticeGame()

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                let compact = geo.size.height < Theme.compactBelow
                ScrollView {
                    VStack(spacing: compact ? 12 : 14) {
                        HStack(spacing: 10) {
                            StatTile(label: "Practice pts", value: "\(game.score)")
                            StatTile(label: "Plays", value: "\(game.played)")
                            StatTile(label: "Perfect", value: "\(game.exactHits)")
                        }
                        stage(compact: compact)
                            .frame(maxWidth: .infinity, minHeight: compact ? 340 : 380)
                            .card(padding: compact ? 14 : 18)
                        Text("Practice plays are simulated and don't count toward the live leaderboard.")
                            .font(.footnote).foregroundStyle(Theme.dim).multilineTextAlignment(.center)
                    }
                    .padding(16)
                }
                .environment(\.compactLayout, compact)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Practice")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        game.stop()
                        dismiss()
                    }
                }
            }
            .onAppear { if game.playNumber == 0 { game.nextPlay() } }
            .onDisappear { game.stop() }
        }
    }

    @ViewBuilder private func stage(compact: Bool) -> some View {
        switch game.phase {
        case .open(let deadline):
            TimelineView(.periodic(from: .now, by: 0.1)) { context in
                let remaining = max(0, deadline.timeIntervalSince(context.date))
                VStack(alignment: .leading, spacing: compact ? 10 : 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(game.label).kicker()
                            Text("Call the play!").font(.system(size: 24, weight: .black)).foregroundStyle(Theme.text)
                        }
                        Spacer()
                        CountdownRing(remaining: remaining, total: PracticeGame.window, size: compact ? 62 : 84)
                    }
                    PickPanel(type: game.pickType, direction: game.pickDirection, yardage: game.pickYardage,
                              enabled: remaining > 0,
                              onType: { game.pickType = $0 }, onDirection: { game.pickDirection = $0 },
                              onYardage: { game.pickYardage = $0 })
                    Button {
                        game.snap()
                    } label: {
                        Text(game.hasFullPick ? "Snap the ball" : "Pick all three, then snap")
                            .font(.system(size: 17, weight: .black))
                            .frame(maxWidth: .infinity, minHeight: compact ? 44 : 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.blue)
                    .disabled(!game.hasFullPick)
                    BonusChip()
                }
            }
        case .locked:
            VStack(spacing: 14) {
                Image(systemName: "lock.fill").font(.system(size: 30, weight: .bold)).foregroundStyle(Theme.warn)
                    .frame(width: 76, height: 76).background(Theme.warn.opacity(0.16), in: Circle())
                Text(game.label).kicker()
                Text("Predictions Locked — Play in Progress").font(.system(size: 22, weight: .black))
                    .foregroundStyle(Theme.text).multilineTextAlignment(.center)
                ProgressView().tint(Theme.warn)
            }
            .frame(maxWidth: .infinity, minHeight: compact ? 300 : 340)
        case .result(let outcome, let scored):
            VStack(spacing: compact ? 12 : 14) {
                Text("\(game.label) — Result").kicker()
                ResultReveal(outcome: outcome, points: scored?.points, label: ScoreRules.label(for: scored),
                             exact: scored?.exact == true, animationKey: game.playNumber)
                if let pick = game.gradedPick {
                    PickChips(pick: pick, graded: true)
                }
                Button {
                    game.nextPlay()
                } label: {
                    Text("Next play").font(.system(size: 17, weight: .black)).frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .foregroundStyle(Theme.accentInk)
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .overlay {
                if scored?.exact == true {
                    ConfettiView(colors: [Theme.gold, Theme.accent, Theme.blue]).id(game.playNumber)
                }
            }
        }
    }
}
