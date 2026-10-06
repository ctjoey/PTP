import SwiftUI

struct LeaderboardView: View {
    @EnvironmentObject var state: AppState
    @State private var board: Board = .global

    enum Board: Hashable { case global, lounge }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let lounge = state.snapshot?.lounge {
                        Picker("Leaderboard", selection: $board) {
                            Text("Live Game").tag(Board.global)
                            Text(lounge.name).tag(Board.lounge)
                        }
                        .pickerStyle(.segmented)
                    }
                    Text(title).kicker()
                    let rows = currentRows
                    if rows.isEmpty {
                        Text(state.snapshot?.game == nil ? "The leaderboard opens at kickoff." : "No scores yet. Make your first pick!")
                            .foregroundStyle(Theme.muted)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 24)
                    } else {
                        LazyVStack(spacing: 6) {
                            ForEach(rows) { row in
                                BoardRowView(row: row, isMe: row.userId == state.snapshot?.me?.id,
                                             move: state.rankMoves[showingLounge ? "lounge" : "global"]?[row.userId] ?? 0)
                            }
                            if let me = state.snapshot?.me, let rank = me.rank, !showingLounge,
                               !rows.contains(where: { $0.userId == me.id }) {
                                Text("⋯").foregroundStyle(Theme.dim)
                                BoardRowView(row: BoardRow(userId: me.id, username: me.username, score: me.gameScore, rank: rank,
                                                           exactHits: me.exactHits, picks: 0, isHost: nil, totalScore: nil),
                                             isMe: true, move: 0, showPicks: false)
                            }
                        }
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Leaderboard")
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .onAppear { if ScreenshotMode.screen == .board, state.snapshot?.lounge != nil { board = .lounge } }
        }
    }

    private var showingLounge: Bool { board == .lounge && state.snapshot?.lounge != nil }

    private var currentRows: [BoardRow] {
        showingLounge ? (state.snapshot?.lounge?.leaderboard ?? []) : (state.snapshot?.leaderboard ?? [])
    }

    private var title: String {
        if showingLounge, let lounge = state.snapshot?.lounge { return "\(lounge.name) · Head-to-Head" }
        let count = state.snapshot?.rankedPlayers ?? 0
        return count > 0 ? "Live game · \(count) players" : "Live game"
    }
}

struct BoardRowView: View {
    var row: BoardRow
    var isMe: Bool
    var move: Int
    var showPicks = true

    var body: some View {
        HStack(spacing: 10) {
            Text("\(row.rank)")
                .font(.system(size: 15, weight: .black).monospacedDigit())
                .foregroundStyle(row.rank == 1 ? Theme.gold : Theme.muted)
                .frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    if row.isHost == true { Text("👑").accessibilityLabel("Host") }
                    Text(row.username).font(.system(size: 16, weight: .heavy)).foregroundStyle(Theme.text).lineLimit(1)
                    if isMe {
                        Text("YOU").font(.system(size: 9, weight: .black)).tracking(1)
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Theme.accent, in: RoundedRectangle(cornerRadius: 4))
                            .foregroundStyle(Theme.accentInk)
                    }
                }
                Text(showPicks ? "\(row.picks) \(row.picks == 1 ? "pick" : "picks") · \(row.exactHits) perfect" : "\(row.exactHits) perfect")
                    .font(.system(size: 11)).foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 4)
            if move != 0 {
                Text("\(move > 0 ? "▲" : "▼")\(abs(move))")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(move > 0 ? Theme.accent : Theme.danger)
            }
            Text("\(row.score)").font(.system(size: 17, weight: .black).monospacedDigit()).foregroundStyle(Theme.text)
        }
        .padding(.horizontal, 10).padding(.vertical, 9)
        .background(isMe ? Theme.accent.opacity(0.14) : Theme.surface2, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(isMe ? Theme.accent.opacity(0.45) : .clear, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}
