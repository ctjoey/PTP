import SwiftUI

/// Offline practice round: same timer, grid and scoring as the live game, with simulated plays.
struct PracticeView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var game = PracticeGame()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    HStack(spacing: 10) {
                        StatTile(label: "Practice pts", value: "\(game.score)")
                        StatTile(label: "Plays", value: "\(game.played)")
                        StatTile(label: "Exact", value: "\(game.exactHits)")
                    }
                    stage
                        .frame(maxWidth: .infinity, minHeight: 380)
                        .card(padding: 18)
                    Text("Practice plays are simulated and don't count toward the live leaderboard.")
                        .font(.footnote).foregroundStyle(Theme.dim).multilineTextAlignment(.center)
                }
                .padding(16)
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

    @ViewBuilder private var stage: some View {
        switch game.phase {
        case .open(let deadline):
            TimelineView(.periodic(from: .now, by: 0.1)) { context in
                let remaining = max(0, deadline.timeIntervalSince(context.date))
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(game.label).kicker()
                            Text("Call the play!").font(.system(size: 24, weight: .black)).foregroundStyle(Theme.text)
                        }
                        Spacer()
                        CountdownRing(remaining: remaining, total: PracticeGame.window)
                    }
                    PickPanel(type: game.pickType, direction: game.pickDirection, enabled: remaining > 0,
                              onType: { game.pickType = $0 }, onDirection: { game.pickDirection = $0 })
                    Button {
                        game.snap()
                    } label: {
                        Text(game.pickType != nil && game.pickDirection != nil ? "Snap the ball" : "Pick both, then snap")
                            .font(.system(size: 17, weight: .black))
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.blue)
                    .disabled(game.pickType == nil || game.pickDirection == nil)
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
            .frame(maxWidth: .infinity, minHeight: 340)
        case .result(let type, let direction, let outcome):
            VStack(spacing: 14) {
                Text("\(game.label) — Result").kicker()
                ResultReveal(playType: type, direction: direction, points: outcome?.points,
                             label: ScoreRules.label(for: outcome), exact: outcome?.exact == true,
                             animationKey: game.playNumber)
                if let outcome, let pickType = game.pickType, let pickDirection = game.pickDirection {
                    HStack(spacing: 8) {
                        Chip(text: "Your pick")
                        Chip(text: "\(pickType.rawValue) \(outcome.typeCorrect ? "✓" : "✗")", style: outcome.typeCorrect ? .good : .bad)
                        Chip(text: "\(pickDirection.rawValue) \(outcome.directionCorrect ? "✓" : "✗")",
                             style: outcome.directionCorrect ? .good : .bad)
                    }
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
            .frame(maxWidth: .infinity)
            .overlay {
                if outcome?.exact == true {
                    ConfettiView(colors: [Theme.gold, Theme.accent, Theme.blue]).id(game.playNumber)
                }
            }
        }
    }
}
