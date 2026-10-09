import Foundation

/// Store-listing capture mode (same approach as GameDial). CI launches the app with
/// SIMCTL_CHILD_SCREENSHOT=<screen>, which the app sees as the SCREENSHOT environment variable, and the
/// app renders that screen from built-in sample data instead of connecting to a server. Real users
/// can't reach this path: the variable only exists when a simulator launch sets it.
enum ScreenshotMode {
    enum Screen: String {
        case open, locked, result, board, lounges, rules, points, directions
        /// The host console on sample data: the key screen, an open play, a feed suggestion, the log.
        case hostSignIn, hostRun, hostLive, hostLog
    }

    static var screen: Screen? {
        if let value = ProcessInfo.processInfo.environment["SCREENSHOT"], let screen = Screen(rawValue: value) { return screen }
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-screenshot"), i + 1 < args.count { return Screen(rawValue: args[i + 1]) }
        return nil
    }

    /// Which part of the Rules tab a store screenshot scrolls to.
    static var rulesAnchor: String? {
        switch screen {
        case .points: return "points"
        case .directions: return "directions"
        default: return nil
        }
    }

    struct Sample {
        var snapshot: StateSnapshot
        var previous: StateSnapshot
        var lounges: [Lounge]
        var tab: AppState.Tab
    }

    static func sample(for screen: Screen) -> Sample {
        let now = Date().timeIntervalSince1970
        // Chicago at Detroit in the clubs' real colours (the same presets the admin console offers), with a score
        // as the live data last read it.
        let game = Game(id: 7, homeName: "Detroit", homePrimary: "#0076B6", homeSecondary: "#B0B7BC",
                        awayName: "Chicago", awayPrimary: "#0B162A", awaySecondary: "#C83803", status: .live,
                        homeScore: 24, awayScore: 17, scoreAt: now - 20)
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
        case .open, .hostSignIn, .hostRun, .hostLive, .hostLog:
            break
        case .locked:
            play.state = .locked
            crowd = Crowd(total: 48, run: 19, pass: 29, left: 22, middle: 9, right: 17, short: 20, medium: 18, long: 10,
                          exact: 0, scored: 0)
            event = "play_locked"
        case .result, .board, .lounges, .rules, .points, .directions:
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
            case .rules, .points, .directions: return .rules
            case .open, .locked, .result, .hostSignIn, .hostRun, .hostLive, .hostLog: return .live
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

// MARK: - Host console samples

extension ScreenshotMode {
    /// What the host console shows for a `host…` screen (nil for every other screen, and for the key screen).
    static func hostSample(for screen: Screen) -> AdminState? {
        let now = Date().timeIntervalSince1970
        let locked = screen == .hostLive || screen == .hostLog
        let suggestion = screen == .hostLive ? """
        {"play_id": 61, "status": "ready", "text": "B.Mayfield pass short left to M.Evans to DAL 31 for 9 yards (T.Diggs).",
         "clock": "Q2 8:41", "down_and_distance": "3rd & 7 at DAL 40", "play_type": "PASS", "direction": "LEFT",
         "yards": 9, "yardage": "MEDIUM", "flags": [], "warning": null, "auto_at": \(now + 6), "kind": "play"}
        """ : "null"
        let json = """
        {"type": "admin_state", "event": "sync", "server_time": \(now),
         "game": {"id": 3, "home_name": "Dallas", "home_primary": "#003594", "home_secondary": "#869397",
                  "away_name": "Tampa Bay", "away_primary": "#D50A0A", "away_secondary": "#FF7900", "status": "LIVE",
                  "home_score": 21, "away_score": 17, "score_at": \(now - 25)},
         "play": {"id": 61, "game_id": 3, "play_number": 12, "down": 3, "distance": "7",
                  "state": "\(locked ? "LOCKED" : "OPEN")", "voided": false, "opened_at": \(now - 4), "locks_at": \(now + 11)},
         "pick_stats": {"total": 31, "RUN": 9, "PASS": 22, "LEFT": 11, "MIDDLE": 6, "RIGHT": 14, "SHORT": 8, "MEDIUM": 15,
                        "LONG": 8, "exact": 0, "scored": 0},
         "players_online": 34, "spectators_online": 5, "admins_online": 1,
         "leaderboard": [
           {"user_id": 5, "username": "Mia", "score": 240, "rank": 1, "exact_hits": 4, "picks": 11},
           {"user_id": 2, "username": "JoeyC", "score": 210, "rank": 2, "exact_hits": 3, "picks": 11},
           {"user_id": 9, "username": "Dre", "score": 200, "rank": 3, "exact_hits": 3, "picks": 10},
           {"user_id": 7, "username": "Sam", "score": 180, "rank": 4, "exact_hits": 2, "picks": 11},
           {"user_id": 8, "username": "Kat", "score": 150, "rank": 5, "exact_hits": 1, "picks": 9}],
         "ranked_players": 31,
         "history": [
           {"id": 60, "play_number": 11, "down": 2, "distance": "4", "state": "RESOLVED", "voided": 0,
            "correct_play_type": "RUN", "correct_direction": "MIDDLE", "correct_yardage": "SHORT", "yards_gained": 3,
            "resolved_by": "feed", "feed_text": "R.White up the middle to TB 38 for 3 yards (L.Vander Esch).",
            "picks": 30, "exact_hits": 4},
           {"id": 59, "play_number": 10, "down": 1, "distance": "10", "state": "RESOLVED", "voided": 0,
            "correct_play_type": "PASS", "correct_direction": "RIGHT", "correct_yardage": "LOSS", "yards_gained": -6,
            "resolved_by": "host-fix", "feed_text": null, "picks": 31, "exact_hits": 0},
           {"id": 58, "play_number": 9, "down": 3, "distance": "2", "state": "RESOLVED", "voided": 1,
            "correct_play_type": null, "correct_direction": null, "correct_yardage": null, "yards_gained": null,
            "resolved_by": "void", "feed_text": "No Play. Offensive holding.", "picks": 29, "exact_hits": 0},
           {"id": 57, "play_number": 8, "down": 2, "distance": "8", "state": "RESOLVED", "voided": 0,
            "correct_play_type": "PASS", "correct_direction": "LEFT", "correct_yardage": "LONG", "yards_gained": 17,
            "resolved_by": "feed", "feed_text": "B.Mayfield pass deep left to C.Godwin for 17 yards.",
            "picks": 33, "exact_hits": 6}],
         "window_seconds": 15,
         "feed": {"available": true, "linked": true, "source": "tank01", "game_id": "20261008_TB@DAL",
                  "state": "\(screen == .hostLive ? "idle" : "waiting")",
                  "message": "\(screen == .hostLive ? "The feed has this play. It will score by itself." : "Connected. It checks the feed only while a play is locked.")",
                  "paused": false, "auto_score": true, "auto_open": false,
                  "requests": {"game": 214, "today": 214, "game_cap": 500, "day_cap": 1000, "plan_remaining": 786, "plan_limit": 1000},
                  "lag": {"median": 18.0, "last": 17.0, "samples": 6}, "waiting": null,
                  "suggestion": \(suggestion), "disagreement": null,
                  "next_down": null, "auto_open_at": null, "last_scored": null},
         "announcement": null, "registered_players": 58}
        """
        guard screen == .hostRun || screen == .hostLive || screen == .hostLog else { return nil }
        return try? JSON.decoder.decode(AdminState.self, from: Data(json.utf8))
    }
}

