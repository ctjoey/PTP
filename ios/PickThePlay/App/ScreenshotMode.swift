import Foundation

/// Store-listing capture mode (same approach as GameDial). CI launches the app with
/// SIMCTL_CHILD_SCREENSHOT=<screen>, which the app sees as the SCREENSHOT environment variable, and the
/// app renders that screen from built-in sample data instead of connecting to a server. Real users
/// can't reach this path: the variable only exists when a simulator launch sets it.
enum ScreenshotMode {
    enum Screen: String {
        case open, locked, result, board, lounges, rules
    }

    static var screen: Screen? {
        if let value = ProcessInfo.processInfo.environment["SCREENSHOT"], let screen = Screen(rawValue: value) { return screen }
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-screenshot"), i + 1 < args.count { return Screen(rawValue: args[i + 1]) }
        return nil
    }

    struct Sample {
        var snapshot: StateSnapshot
        var previous: StateSnapshot
        var lounges: [Lounge]
        var tab: AppState.Tab
    }

    static func sample(for screen: Screen) -> Sample {
        let now = Date().timeIntervalSince1970
        // Chicago at Detroit in the clubs' real colours (the same presets the admin console offers).
        let game = Game(id: 7, homeName: "Detroit", homePrimary: "#0076B6", homeSecondary: "#B0B7BC",
                        awayName: "Chicago", awayPrimary: "#0B162A", awaySecondary: "#C83803", status: .live)
        var play = Play(id: 42, gameId: 7, playNumber: 23, down: 3, distance: "7", state: .open, voided: false,
                        openedAt: now - 4, locksAt: now + 11, correctPlayType: nil, correctDirection: nil,
                        correctYardage: nil, yardsGained: nil)

        // (name, points, perfect calls); a perfect call is worth 40, so nobody has more than points / 40.
        let before = board([("Mia", 140, 3), ("JoeyC", 120, 3), ("Dre", 120, 2), ("Sam", 110, 2), ("Kat", 90, 1),
                            ("Big Lou", 80, 1), ("Tasha", 60, 1), ("Rico", 40, 0)])
        let after = board([("JoeyC", 160, 4), ("Mia", 140, 3), ("Dre", 130, 2), ("Sam", 110, 2), ("Kat", 100, 1),
                           ("Big Lou", 80, 1), ("Tasha", 70, 1), ("Rico", 40, 0)])
        var myPick: Prediction? = Prediction(userId: 2, playId: 42, playType: .pass, direction: .left, yardage: .medium,
                                              pointsEarned: nil, typeCorrect: nil, directionCorrect: nil,
                                              yardageCorrect: nil)
        var crowd: Crowd?
        var rows = before
        var me = Me(id: 2, username: "JoeyC", gameScore: 120, rank: 2, exactHits: 3, totalScore: 860)
        var event = "play_opened"

        switch screen {
        case .open:
            break
        case .locked:
            play.state = .locked
            crowd = Crowd(total: 48, run: 19, pass: 29, left: 22, middle: 9, right: 17, short: 20, medium: 18, long: 10,
                          exact: 0, scored: 0)
            event = "play_locked"
        case .result, .board, .lounges, .rules:
            // An 8-yard completion to the left: PASS · LEFT · MEDIUM, a perfect call for JoeyC
            // (10 + 10 + 10 + the 10 bonus = 40).
            play.state = .resolved
            play.correctPlayType = .pass
            play.correctDirection = .left
            play.correctYardage = .medium
            play.yardsGained = 8
            myPick?.pointsEarned = 40
            myPick?.typeCorrect = true
            myPick?.directionCorrect = true
            myPick?.yardageCorrect = true
            crowd = Crowd(total: 48, run: 19, pass: 29, left: 22, middle: 9, right: 17, short: 20, medium: 18, long: 10,
                          exact: 6, scored: 41)
            rows = after
            me = Me(id: 2, username: "JoeyC", gameScore: 160, rank: 1, exactHits: 4, totalScore: 900)
            event = "play_resolved"
        }

        let loungeRows = rows.filter { ["JoeyC", "Mia", "Sam", "Big Lou"].contains($0.username) }
            .enumerated().map { index, row -> BoardRow in
                var r = row
                r.rank = index + 1
                r.isHost = row.username == "JoeyC"
                return r
            }
        let lounge = Lounge(id: "4180", name: "Sunday Crew", hostUserId: 2, hostUsername: "JoeyC", memberCount: 4,
                            leaderboard: loungeRows)
        let lounges = [lounge, Lounge(id: "2297", name: "Office League", hostUserId: 9, hostUsername: "Kat",
                                      memberCount: 11, leaderboard: nil)]

        let snapshot = StateSnapshot(event: event, serverTime: now, game: game, play: play, myPrediction: myPick, me: me,
                                     leaderboard: rows, rankedPlayers: 48, crowd: crowd, lounge: lounge, scoring: .standard)
        var previous = snapshot
        previous.event = "sync"
        previous.leaderboard = before
        previous.lounge?.leaderboard = before.filter { ["JoeyC", "Mia", "Sam", "Big Lou"].contains($0.username) }
            .enumerated().map { index, row -> BoardRow in
                var r = row
                r.rank = index + 1
                return r
            }

        let tab: AppState.Tab = {
            switch screen {
            case .board: return .board
            case .lounges: return .lounges
            case .rules: return .rules
            case .open, .locked, .result: return .live
            }
        }()
        return Sample(snapshot: snapshot, previous: previous, lounges: lounges, tab: tab)
    }

    private static let names = ["Mia", "JoeyC", "Dre", "Sam", "Kat", "Big Lou", "Tasha", "Rico"]

    private static func board(_ entries: [(String, Int, Int)]) -> [BoardRow] {
        var rows: [BoardRow] = []
        var rank = 0
        var lastScore: Int?
        for (index, entry) in entries.enumerated() {
            if entry.1 != lastScore { rank = index + 1; lastScore = entry.1 }
            // Stable ids per name so rank movement between the two samples is computed per player.
            let id = entry.0 == "JoeyC" ? 2 : 100 + (names.firstIndex(of: entry.0) ?? index)
            rows.append(BoardRow(userId: id, username: entry.0, score: entry.1, rank: rank, exactHits: entry.2,
                                 picks: 23, isHost: nil, totalScore: nil))
        }
        return rows
    }
}
