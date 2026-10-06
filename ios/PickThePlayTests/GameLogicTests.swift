import XCTest
@testable import PickThePlay

final class GameLogicTests: XCTestCase {
    // MARK: - Scoring (mirrors models.score_prediction on the server)

    func testScoringMatchesServerRulesForEveryCombination() {
        let picks: [Yardage?] = Yardage.allCases + [nil]  // nil = a pick made before the distance pick existed
        let actuals: [YardageOutcome?] = YardageOutcome.allCases + [nil]
        var checked = 0
        for type in PlayType.allCases {
            for direction in Direction.allCases {
                for yardage in picks {
                    for actualType in PlayType.allCases {
                        for actualDirection in Direction.allCases {
                            for actualYardage in actuals {
                                let r = ScoreRules.score(type: type, direction: direction, yardage: yardage,
                                                         actualType: actualType, actualDirection: actualDirection,
                                                         actualYardage: actualYardage)
                                let typeOK = type == actualType
                                let dirOK = direction == actualDirection
                                let yardsOK = yardage != nil && actualYardage != nil && actualYardage != .loss
                                    && yardage?.rawValue == actualYardage?.rawValue
                                let expected = (typeOK ? 10 : 0) + (dirOK ? 10 : 0) + (yardsOK ? 10 : 0)
                                let context = "\(type) \(direction) \(String(describing: yardage)) vs \(actualType) \(actualDirection) \(String(describing: actualYardage))"
                                XCTAssertEqual(r.points, expected, context)
                                XCTAssertEqual(r.typeCorrect, typeOK, context)
                                XCTAssertEqual(r.directionCorrect, dirOK, context)
                                XCTAssertEqual(r.yardageCorrect, yardsOK, context)
                                XCTAssertEqual(r.exact, typeOK && dirOK && yardsOK, context)
                                XCTAssertEqual(r.correctParts * 10, r.points, context)
                                checked += 1
                            }
                        }
                    }
                }
            }
        }
        XCTAssertEqual(checked, 2 * 3 * 4 * 2 * 3 * 5)
    }

    func testOwnersExamples() {
        // "Picked Run Left Short, it was Run Left Medium -> 20 points."
        let twenty = ScoreRules.score(type: .run, direction: .left, yardage: .short,
                                      actual: PlayOutcome(playType: .run, direction: .left, yards: 7))
        XCTAssertEqual(twenty.points, 20)
        XCTAssertFalse(twenty.exact)
        XCTAssertEqual(ScoreRules.label(for: twenty), "Two of three")

        let thirty = ScoreRules.score(type: .run, direction: .left, yardage: .short,
                                      actual: PlayOutcome(playType: .run, direction: .left, yards: 3))
        XCTAssertEqual(thirty.points, 30)
        XCTAssertTrue(thirty.exact)
        XCTAssertEqual(ScoreRules.label(for: thirty), "Perfect call!")

        let ten = ScoreRules.score(type: .pass, direction: .left, yardage: .long,
                                   actual: PlayOutcome(playType: .run, direction: .right, yards: 25))
        XCTAssertEqual(ten.points, 10)
        XCTAssertEqual(ScoreRules.label(for: ten), "One of three")
    }

    func testLossNeverScoresDistance() {
        for yardage in Yardage.allCases {
            let r = ScoreRules.score(type: .pass, direction: .right, yardage: yardage,
                                     actual: PlayOutcome(playType: .pass, direction: .right, yards: -4))
            XCTAssertFalse(r.yardageCorrect)
            XCTAssertEqual(r.points, 20, "type and direction still score on a loss")
            XCTAssertFalse(r.exact)
            XCTAssertFalse(YardageOutcome.loss.matches(yardage))
        }
        XCTAssertNil(YardageOutcome.loss.pick)
        XCTAssertFalse(YardageOutcome.short.matches(nil))
    }

    func testPickWithoutDistanceScoresNoDistancePoints() {
        let legacy = ScoreRules.score(type: .run, direction: .center, yardage: nil,
                                      actual: PlayOutcome(playType: .run, direction: .center, yards: 2))
        XCTAssertEqual(legacy.points, 20)
        XCTAssertFalse(legacy.exact)
    }

    func testYardsToBucketBoundaries() {
        let cases: [(Int, YardageOutcome)] = [(-99, .loss), (-1, .loss), (0, .short), (5, .short), (6, .medium),
                                              (10, .medium), (11, .long), (99, .long)]
        for (yards, bucket) in cases {
            XCTAssertEqual(YardageOutcome(yards: yards), bucket, "\(yards) yds")
        }
    }

    func testLabelsByPoints() {
        XCTAssertEqual(ScoreRules.label(points: nil), "You didn't pick this play")
        XCTAssertEqual(ScoreRules.label(points: 30), "Perfect call!")
        XCTAssertEqual(ScoreRules.label(points: 20), "Two of three")
        XCTAssertEqual(ScoreRules.label(points: 10), "One of three")
        XCTAssertEqual(ScoreRules.label(points: 0), "No points this time")
        XCTAssertEqual(ScoreRules.label(for: nil), "You didn't pick this play")
    }

    func testScoringCopy() {
        XCTAssertEqual(Scoring.standard, Scoring(type: 10, direction: 10, yardage: 10, exact: 30))
        XCTAssertEqual(ScoreRules.summary(),
                       "+10 play type, +10 direction, +10 distance. All three = 30. A loss of yards scores no distance points.")
    }

    func testWhatHappenedSummary() {
        XCTAssertEqual(PlayOutcome(playType: .run, direction: .left, yards: 7).summary, "Run · Left · Medium (7 yds)")
        XCTAssertEqual(PlayOutcome(playType: .pass, direction: .right, yards: -4).summary, "Pass · Right · Loss (-4 yds)")
        XCTAssertEqual(PlayOutcome(playType: .pass, direction: .center, yards: 1).summary, "Pass · Center · Short (1 yd)")
        XCTAssertEqual(PlayOutcome(playType: .run, direction: .right, yardage: .long).summary, "Run · Right · Long")
        XCTAssertEqual(PlayOutcome(playType: .run, direction: .right, yardage: nil).summary, "Run · Right")
        XCTAssertEqual(PlayOutcome(playType: .pass, direction: .left, yards: -4).spokenSummary, "Pass, Left, Loss, -4 yards")
    }

    func testPlayOutcomeFromServerPlay() {
        var play = Play(id: 1, gameId: 1, playNumber: 1, down: 3, distance: "7", state: .resolved, voided: false,
                        openedAt: 0, locksAt: 15, correctPlayType: .run, correctDirection: .left,
                        correctYardage: .medium, yardsGained: 7)
        XCTAssertEqual(play.outcome, PlayOutcome(playType: .run, direction: .left, yardage: .medium, yards: 7))
        play.correctYardage = nil  // yards only: the bucket follows from them
        XCTAssertEqual(play.outcome?.yardage, .medium)
        play.yardsGained = nil  // resolved before the distance pick existed
        XCTAssertNil(play.outcome?.yardage)
        XCTAssertNotNil(play.outcome)
        play.voided = true
        XCTAssertNil(play.outcome)
    }

    func testGradingFillsMissingFlags() {
        let outcome = PlayOutcome(playType: .pass, direction: .left, yards: 12)
        let pick = Prediction(userId: 1, playId: 1, playType: .pass, direction: .right, yardage: .long, pointsEarned: 20,
                              typeCorrect: true, directionCorrect: false, yardageCorrect: nil)
        let graded = ScoreRules.graded(pick, outcome: outcome)
        XCTAssertEqual(graded.typeCorrect, true)
        XCTAssertEqual(graded.directionCorrect, false)
        XCTAssertEqual(graded.yardageCorrect, true)
        // The server's flags win over a local recomputation.
        var serverSaid = pick
        serverSaid.yardageCorrect = false
        XCTAssertEqual(ScoreRules.graded(serverSaid, outcome: outcome).yardageCorrect, false)
        // No recorded distance on the play: nothing to grade the distance against.
        let unknown = ScoreRules.graded(pick, outcome: PlayOutcome(playType: .pass, direction: .left, yardage: nil))
        XCTAssertNil(unknown.yardageCorrect)
    }

    func testPickPrompt() {
        XCTAssertEqual(Football.pickPrompt(type: nil, direction: nil, yardage: nil),
                       "Make your call: type, direction and distance")
        XCTAssertEqual(Football.pickPrompt(type: .run, direction: nil, yardage: nil), "Now pick a direction and a distance")
        XCTAssertEqual(Football.pickPrompt(type: nil, direction: .left, yardage: .short), "Now pick Run or Pass")
        XCTAssertEqual(Football.pickPrompt(type: .pass, direction: .left, yardage: nil), "Now pick a distance")
        XCTAssertEqual(Football.pickPrompt(type: nil, direction: nil, yardage: .long), "Now pick Run or Pass and a direction")
    }

    func testDistanceButtonsCopy() {
        XCTAssertEqual(Yardage.allCases.map(\.title), ["Short", "Medium", "Long"])
        XCTAssertEqual(Yardage.allCases.map(\.range), ["0–5 yds", "6–10 yds", "11+ yds"])
        XCTAssertEqual(Football.yards(0), "0 yds")
        XCTAssertEqual(Football.yards(-1), "-1 yd")
    }

    // MARK: - Practice

    @MainActor
    func testPracticeRoundScoresAllThreePicks() {
        let game = PracticeGame()
        game.outcome = { PlayOutcome(playType: .pass, direction: .left, yards: 8) }
        game.nextPlay()
        game.pickType = .pass
        game.pickDirection = .left
        game.pickYardage = .medium
        XCTAssertTrue(game.hasFullPick)
        game.resolve()
        XCTAssertEqual(game.score, 30)
        XCTAssertEqual(game.exactHits, 1)
        guard case .result(let outcome, let scored) = game.phase else { return XCTFail("expected a result") }
        XCTAssertEqual(outcome.yardage, .medium)
        XCTAssertEqual(scored?.points, 30)
        XCTAssertEqual(game.gradedPick?.yardageCorrect, true)
        XCTAssertEqual(game.gradedPick?.pointsEarned, 30)

        // Run Left Short against Pass Left Medium: direction only.
        game.nextPlay()
        game.pickType = .run
        game.pickDirection = .left
        game.pickYardage = .short
        game.resolve()
        XCTAssertEqual(game.score, 40)
        XCTAssertEqual(game.played, 2)
        XCTAssertEqual(game.exactHits, 1)
        XCTAssertEqual(game.gradedPick?.typeCorrect, false)
        XCTAssertEqual(game.gradedPick?.directionCorrect, true)
        XCTAssertEqual(game.gradedPick?.yardageCorrect, false)

        // A sack: type and direction right, no distance points.
        game.outcome = { PlayOutcome(playType: .pass, direction: .left, yards: -6) }
        game.nextPlay()
        game.pickType = .pass
        game.pickDirection = .left
        game.pickYardage = .short
        game.resolve()
        XCTAssertEqual(game.score, 60)
        guard case .result(let sack, let sackScore) = game.phase else { return XCTFail("expected a result") }
        XCTAssertEqual(sack.yardage, .loss)
        XCTAssertEqual(sackScore?.points, 20)

        // Only two parts picked: like the live game, nothing is sent, so no points and not played.
        game.nextPlay()
        game.pickType = .pass
        game.pickDirection = .left
        XCTAssertFalse(game.hasFullPick)
        game.resolve()
        XCTAssertEqual(game.played, 3)
        XCTAssertEqual(game.score, 60)
        XCTAssertNil(game.gradedPick)

        game.nextPlay()  // no pick at all
        game.resolve()
        XCTAssertEqual(game.played, 3)
        game.stop()
    }

    @MainActor
    func testPracticeDownsFollowTheGain() {
        let game = PracticeGame()
        var yards = 4
        game.outcome = { PlayOutcome(playType: .run, direction: .center, yards: yards) }
        game.nextPlay()
        XCTAssertEqual(game.label, "Practice play 1 · 1st & 10")
        game.resolve()
        game.nextPlay()
        XCTAssertEqual(game.label, "Practice play 2 · 2nd & 6")
        yards = -2
        game.resolve()
        game.nextPlay()
        XCTAssertEqual(game.label, "Practice play 3 · 3rd & 8")
        yards = 9
        game.resolve()
        game.nextPlay()
        XCTAssertEqual(game.label, "Practice play 4 · 1st & 10")
        game.stop()
    }

    func testPracticeYardageOdds() {
        // Each roll band lands in its bucket.
        let bands: [(PlayType, Double, YardageOutcome)] = [
            (.run, 0.05, .loss), (.run, 0.5, .short), (.run, 0.8, .medium), (.run, 0.95, .long),
            (.pass, 0.03, .loss), (.pass, 0.2, .short), (.pass, 0.5, .short), (.pass, 0.7, .medium), (.pass, 0.9, .long),
        ]
        for (type, roll, bucket) in bands {
            for _ in 0..<20 {
                XCTAssertEqual(YardageOutcome(yards: PracticeGame.randomYards(for: type, roll: roll)), bucket, "\(type) \(roll)")
            }
        }
        // An incomplete pass is 0 yards.
        XCTAssertEqual(PracticeGame.randomYards(for: .pass, roll: 0.2), 0)

        // Overall shares are roughly NFL-like (wide margins; 20k samples keep this far from flaky).
        let n = 20_000
        func share(_ type: PlayType, _ bucket: YardageOutcome) -> Double {
            Double((0..<n).filter { _ in YardageOutcome(yards: PracticeGame.randomYards(for: type)) == bucket }.count) / Double(n)
        }
        XCTAssertEqual(share(.run, .loss), 0.10, accuracy: 0.03)
        XCTAssertEqual(share(.run, .short), 0.58, accuracy: 0.04)
        XCTAssertEqual(share(.run, .long), 0.10, accuracy: 0.03)
        XCTAssertEqual(share(.pass, .loss), 0.06, accuracy: 0.02)
        XCTAssertEqual(share(.pass, .short), 0.52, accuracy: 0.04)
        XCTAssertEqual(share(.pass, .long), 0.22, accuracy: 0.04)
        for _ in 0..<2_000 {
            let outcome = PracticeGame.randomOutcome()
            XCTAssertNotNil(outcome.yards)
            XCTAssertTrue((-99...99).contains(outcome.yards ?? 1000))
            XCTAssertEqual(outcome.yardage, outcome.yards.map(YardageOutcome.init(yards:)))
        }
    }

    // MARK: - Live picks

    @MainActor
    func testSnapshotRestoresTheThreePartPick() throws {
        let state = AppState()
        let mine = Prediction(userId: 1, playId: 9, playType: .run, direction: .right, yardage: .long, pointsEarned: nil,
                              typeCorrect: nil, directionCorrect: nil, yardageCorrect: nil)
        let play = Play(id: 9, gameId: 1, playNumber: 4, down: 1, distance: "10", state: .open, voided: false,
                        openedAt: 100, locksAt: 115, correctPlayType: nil, correctDirection: nil,
                        correctYardage: nil, yardsGained: nil)
        state.apply(StateSnapshot(event: "sync", serverTime: 105, game: nil, play: play, myPrediction: mine, me: nil,
                                  leaderboard: [], rankedPlayers: 0, crowd: nil, lounge: nil, scoring: .standard))
        XCTAssertEqual(state.pickType, .run)
        XCTAssertEqual(state.pickDirection, .right)
        XCTAssertEqual(state.pickYardage, .long)
        XCTAssertTrue(state.pickIsSaved)
        XCTAssertFalse(state.saving)

        // A pick from before the distance pick existed: two parts restored, still needs a distance.
        var legacy = mine
        legacy.playId = 10
        legacy.yardage = nil
        var next = play
        next.id = 10
        state.apply(StateSnapshot(event: "play_opened", serverTime: 105, game: nil, play: next, myPrediction: legacy,
                                  me: nil, leaderboard: [], rankedPlayers: 0, crowd: nil, lounge: nil, scoring: .standard))
        XCTAssertEqual(state.pickType, .run)
        XCTAssertNil(state.pickYardage)
        XCTAssertFalse(state.pickIsSaved)
    }

    // MARK: - Plumbing

    func testServerAddressNormalisation() {
        XCTAssertEqual(ServerConfig.normalize("pick-the-play.onrender.com")?.absoluteString, "https://pick-the-play.onrender.com")
        XCTAssertEqual(ServerConfig.normalize(" 192.168.1.20:8000 ")?.absoluteString, "http://192.168.1.20:8000")
        XCTAssertEqual(ServerConfig.normalize("https://x.example.com/admin?y=1")?.absoluteString, "https://x.example.com")
        XCTAssertNil(ServerConfig.normalize(""))
        XCTAssertNil(ServerConfig.normalize("$(PTP_SERVER_URL)"))
        XCTAssertNil(ServerConfig.normalize("ftp://x.com"))
        let socket = ServerConfig.socketURL(for: URL(string: "https://x.example.com")!)
        XCTAssertEqual(socket?.absoluteString, "wss://x.example.com/ws")
        XCTAssertEqual(ServerConfig.socketURL(for: URL(string: "http://10.0.0.5:8000")!)?.absoluteString, "ws://10.0.0.5:8000/ws")
    }

    func testFootballFormatting() {
        XCTAssertEqual(Football.abbreviation("Green Bay"), "GB")
        XCTAssertEqual(Football.abbreviation("Chicago"), "CHI")
        XCTAssertEqual(Football.abbreviation("Detroit"), "DET")
        XCTAssertEqual(Football.abbreviation("New York"), "NY")
        XCTAssertEqual(Football.abbreviation("Los Angeles"), "LA")
        XCTAssertEqual(Football.abbreviation("Kansas City"), "KC")
        XCTAssertEqual(Football.downAndDistance(down: 4, distance: "Goal"), "4th & Goal")
        XCTAssertNil(Football.downAndDistance(down: nil, distance: "10"))
    }
}
