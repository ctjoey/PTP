import XCTest
@testable import PickThePlay

final class GameLogicTests: XCTestCase {
    // MARK: - Scoring (mirrors models.score_prediction on the server: +10 a part, +10 bonus for all three)

    func testScoringMatchesServerRulesForEveryCombination() {
        let picks: [Yardage?] = Yardage.allCases + [nil]  // nil = a pick made before the distance pick existed
        let actuals: [YardageOutcome?] = YardageOutcome.allCases + [nil]
        var checked = 0
        var totals: Set<Int> = []
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
                                let allThree = typeOK && dirOK && yardsOK
                                let expected = (typeOK ? 10 : 0) + (dirOK ? 10 : 0) + (yardsOK ? 10 : 0) + (allThree ? 10 : 0)
                                let context = "\(type) \(direction) \(String(describing: yardage)) vs \(actualType) \(actualDirection) \(String(describing: actualYardage))"
                                XCTAssertEqual(r.points, expected, context)
                                XCTAssertEqual(r.typeCorrect, typeOK, context)
                                XCTAssertEqual(r.directionCorrect, dirOK, context)
                                XCTAssertEqual(r.yardageCorrect, yardsOK, context)
                                XCTAssertEqual(r.exact, allThree, context)
                                XCTAssertEqual(r.correctParts * 10 + (r.exact ? 10 : 0), r.points, context)
                                totals.insert(r.points)
                                checked += 1
                            }
                        }
                    }
                }
            }
        }
        XCTAssertEqual(checked, 2 * 3 * 4 * 2 * 3 * 5)
        XCTAssertEqual(totals, [0, 10, 20, 40], "30 is impossible: the third right part brings the bonus")
    }

    func testBonusOnlyForAllThree() {
        let actual = PlayOutcome(playType: .run, direction: .middle, yards: 8)  // Run · Middle · Medium
        let cases: [(PlayType, Direction, Yardage, Int, String)] = [
            (.run, .middle, .medium, 40, "Perfect call!"),
            (.run, .middle, .short, 20, "Two of three"),
            (.run, .left, .medium, 20, "Two of three"),
            (.pass, .middle, .medium, 20, "Two of three"),
            (.run, .left, .short, 10, "One of three"),
            (.pass, .middle, .long, 10, "One of three"),
            (.pass, .right, .medium, 10, "One of three"),
            (.pass, .right, .long, 0, "No points this time"),
        ]
        for (type, direction, yardage, points, label) in cases {
            let r = ScoreRules.score(type: type, direction: direction, yardage: yardage, actual: actual)
            XCTAssertEqual(r.points, points, "\(type) \(direction) \(yardage)")
            XCTAssertEqual(r.exact, points == 40)
            XCTAssertEqual(ScoreRules.label(for: r), label)
            XCTAssertEqual(ScoreRules.label(points: r.points), label)
        }
        // The bonus comes from the scoring the server sends.
        let custom = Scoring(type: 5, direction: 5, yardage: 5, bonus: 25, exact: 40)
        let r = ScoreRules.score(type: .run, direction: .middle, yardage: .medium, actual: actual, scoring: custom)
        XCTAssertEqual(r.points, 40)
        let noBonus = Scoring(type: 10, direction: 10, yardage: 10, bonus: 0, exact: 30)
        XCTAssertEqual(ScoreRules.score(type: .run, direction: .middle, yardage: .medium, actual: actual,
                                        scoring: noBonus).points, 30)
    }

    func testOwnersExamples() {
        // "Picked Run Left Short, it was Run Left Medium -> 20 points."
        let twenty = ScoreRules.score(type: .run, direction: .left, yardage: .short,
                                      actual: PlayOutcome(playType: .run, direction: .left, yards: 7))
        XCTAssertEqual(twenty.points, 20)
        XCTAssertFalse(twenty.exact)
        XCTAssertEqual(ScoreRules.label(for: twenty), "Two of three")

        // "Call Run Left Medium: 40" (10 + 10 + 10 + the 10 bonus).
        let forty = ScoreRules.score(type: .run, direction: .left, yardage: .medium,
                                     actual: PlayOutcome(playType: .run, direction: .left, yards: 7))
        XCTAssertEqual(forty.points, 40)
        XCTAssertTrue(forty.exact)
        XCTAssertEqual(ScoreRules.label(for: forty), "Perfect call!")

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
            XCTAssertEqual(r.points, 20, "type and direction still score on a loss, but no bonus")
            XCTAssertFalse(r.exact)
            XCTAssertFalse(YardageOutcome.loss.matches(yardage))
        }
        XCTAssertNil(YardageOutcome.loss.pick)
        XCTAssertFalse(YardageOutcome.short.matches(nil))
    }

    func testPickWithoutDistanceScoresNoDistancePoints() {
        let legacy = ScoreRules.score(type: .run, direction: .middle, yardage: nil,
                                      actual: PlayOutcome(playType: .run, direction: .middle, yards: 2))
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
        XCTAssertEqual(ScoreRules.label(points: 40), "Perfect call!")
        XCTAssertEqual(ScoreRules.label(points: 30), "Perfect call!", "a perfect call scored before the bonus existed")
        XCTAssertEqual(ScoreRules.label(points: 20), "Two of three")
        XCTAssertEqual(ScoreRules.label(points: 10), "One of three")
        XCTAssertEqual(ScoreRules.label(points: 0), "No points this time")
        XCTAssertEqual(ScoreRules.label(for: nil), "You didn't pick this play")
        XCTAssertEqual(ScoreRules.label(pick: nil), "You didn't pick this play")
    }

    func testLabelsFollowTheFlagsNotThePoints() {
        var pick = Prediction(userId: 1, playId: 1, playType: .run, direction: .middle, yardage: .short, pointsEarned: 40,
                              typeCorrect: true, directionCorrect: true, yardageCorrect: true)
        XCTAssertEqual(ScoreRules.label(pick: pick), "Perfect call!")
        XCTAssertTrue(ScoreRules.isPerfect(pick))
        // Resolved before the bonus: still 30 on the server, still a perfect call.
        pick.pointsEarned = 30
        XCTAssertEqual(ScoreRules.label(pick: pick), "Perfect call!")
        XCTAssertTrue(ScoreRules.isPerfect(pick))
        pick.yardageCorrect = false
        pick.pointsEarned = 20
        XCTAssertEqual(ScoreRules.label(pick: pick), "Two of three")
        XCTAssertFalse(ScoreRules.isPerfect(pick))
        pick.directionCorrect = false
        pick.pointsEarned = 10
        XCTAssertEqual(ScoreRules.label(pick: pick), "One of three")
        pick.typeCorrect = false
        pick.pointsEarned = 0
        XCTAssertEqual(ScoreRules.label(pick: pick), "No points this time")
        // No flags (an old server): fall back to the points.
        let bare = Prediction(userId: 1, playId: 1, playType: .run, direction: .middle, yardage: .short, pointsEarned: 40,
                              typeCorrect: nil, directionCorrect: nil, yardageCorrect: nil)
        XCTAssertEqual(ScoreRules.label(pick: bare), "Perfect call!")
        XCTAssertTrue(ScoreRules.isPerfect(bare))
        var twenty = bare
        twenty.pointsEarned = 20
        XCTAssertEqual(ScoreRules.label(pick: twenty), "Two of three")
        XCTAssertFalse(ScoreRules.isPerfect(twenty))
        XCTAssertFalse(ScoreRules.isPerfect(nil))
    }

    func testScoringCopy() {
        XCTAssertEqual(Scoring.standard, Scoring(type: 10, direction: 10, yardage: 10, bonus: 10, exact: 40))
        XCTAssertEqual(Scoring.standard.allParts + Scoring.standard.bonus, Scoring.standard.exact)
        XCTAssertEqual(ScoreRules.summary(), "+10 play type, +10 direction, +10 distance, +10 bonus for all three = 40.")
    }

    func testScoringDecodesWithAndWithoutBonus() throws {
        let current = try JSON.decoder.decode(Scoring.self, from: Data(#"{"type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40}"#.utf8))
        XCTAssertEqual(current, .standard)
        // A server from before the bonus: all three = 30, so the bonus is 0.
        let old = try JSON.decoder.decode(Scoring.self, from: Data(#"{"type": 10, "direction": 10, "yardage": 10, "exact": 30}"#.utf8))
        XCTAssertEqual(old.bonus, 0)
        // A bonus-era server that left the key out still adds up.
        let implied = try JSON.decoder.decode(Scoring.self, from: Data(#"{"type": 10, "direction": 10, "yardage": 10, "exact": 40}"#.utf8))
        XCTAssertEqual(implied.bonus, 10)
        let wire = try XCTUnwrap(JSONSerialization.jsonObject(with: JSON.encoder.encode(Scoring.standard)) as? [String: Int])
        XCTAssertEqual(wire, ["type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40])
    }

    // MARK: - Left / Middle / Right

    func testDirectionsAreLeftMiddleRight() throws {
        XCTAssertEqual(Direction.allCases, [.left, .middle, .right])
        XCTAssertEqual(Direction.allCases.map(\.rawValue), ["LEFT", "MIDDLE", "RIGHT"])
        XCTAssertEqual(Direction.allCases.map(\.title), ["Left", "Middle", "Right"])
        XCTAssertEqual(Direction.middle.symbol, "arrow.up", "the middle keeps the up arrow")
        XCTAssertNil(Direction(rawValue: "CENTER"), "the app never produces CENTER itself")
    }

    func testCenterDecodesAsMiddleAndMiddleIsSent() throws {
        func decode(_ raw: String) throws -> Direction {
            try JSON.decoder.decode([Direction].self, from: Data("[\"\(raw)\"]".utf8))[0]
        }
        XCTAssertEqual(try decode("CENTER"), .middle)
        XCTAssertEqual(try decode("MIDDLE"), .middle)
        XCTAssertEqual(try decode("LEFT"), .left)
        XCTAssertEqual(try decode("RIGHT"), .right)
        XCTAssertThrowsError(try decode("UP"))
        XCTAssertThrowsError(try decode("middle"))
        XCTAssertEqual(String(decoding: try JSON.encoder.encode([Direction.middle]), as: UTF8.self), #"["MIDDLE"]"#)
        let pick = PredictMessage(playId: 1, playType: .run, direction: .middle, yardage: .short)
        XCTAssertEqual(pick.body["direction"] as? String, "MIDDLE")

        let old = """
        {"user_id": 1, "play_id": 3, "play_type": "RUN", "direction": "CENTER", "yardage": "SHORT"}
        """
        XCTAssertEqual(try JSON.decoder.decode(Prediction.self, from: Data(old.utf8)).direction, .middle)
    }

    func testCrowdReadsMiddleOrTheOldCenterKey() throws {
        let current = """
        {"total": 4, "RUN": 1, "PASS": 3, "LEFT": 1, "MIDDLE": 2, "RIGHT": 1, "SHORT": 1, "MEDIUM": 2, "LONG": 1,
         "exact": 1, "scored": 3}
        """
        let crowd = try JSON.decoder.decode(Crowd.self, from: Data(current.utf8))
        XCTAssertEqual(crowd.count(Direction.middle), 2)
        XCTAssertEqual(crowd.count(Yardage.medium), 2)
        let old = """
        {"total": 4, "RUN": 1, "PASS": 3, "LEFT": 1, "CENTER": 2, "RIGHT": 1, "exact": 1, "scored": 3}
        """
        let legacy = try JSON.decoder.decode(Crowd.self, from: Data(old.utf8))
        XCTAssertEqual(legacy.middle, 2)
        XCTAssertFalse(legacy.hasYardage)
        let neither = #"{"total": 1, "RUN": 1, "PASS": 0, "LEFT": 1, "RIGHT": 0, "exact": 0, "scored": 0}"#
        XCTAssertThrowsError(try JSON.decoder.decode(Crowd.self, from: Data(neither.utf8)))
        let wire = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(crowd)) as? [String: Int])
        XCTAssertEqual(wire["MIDDLE"], 2)
        XCTAssertNil(wire["CENTER"])
    }

    func testWhatHappenedSummary() {
        XCTAssertEqual(PlayOutcome(playType: .run, direction: .left, yards: 7).summary, "Run · Left · Medium (7 yds)")
        XCTAssertEqual(PlayOutcome(playType: .pass, direction: .right, yards: -4).summary, "Pass · Right · Loss (-4 yds)")
        XCTAssertEqual(PlayOutcome(playType: .pass, direction: .middle, yards: 1).summary, "Pass · Middle · Short (1 yd)")
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
        XCTAssertEqual(game.score, 40, "10 + 10 + 10 + the 10 bonus")
        XCTAssertEqual(game.exactHits, 1)
        guard case .result(let outcome, let scored) = game.phase else { return XCTFail("expected a result") }
        XCTAssertEqual(outcome.yardage, .medium)
        XCTAssertEqual(scored?.points, 40)
        XCTAssertEqual(ScoreRules.label(for: scored), "Perfect call!")
        XCTAssertEqual(game.gradedPick?.yardageCorrect, true)
        XCTAssertEqual(game.gradedPick?.pointsEarned, 40)

        // Run Left Short against Pass Left Medium: direction only.
        game.nextPlay()
        game.pickType = .run
        game.pickDirection = .left
        game.pickYardage = .short
        game.resolve()
        XCTAssertEqual(game.score, 50)
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
        XCTAssertEqual(game.score, 70)
        guard case .result(let sack, let sackScore) = game.phase else { return XCTFail("expected a result") }
        XCTAssertEqual(sack.yardage, .loss)
        XCTAssertEqual(sackScore?.points, 20, "no distance points on a loss, so no bonus")
        XCTAssertEqual(game.exactHits, 1)

        // Only two parts picked: like the live game, nothing is sent, so no points and not played.
        game.nextPlay()
        game.pickType = .pass
        game.pickDirection = .left
        XCTAssertFalse(game.hasFullPick)
        game.resolve()
        XCTAssertEqual(game.played, 3)
        XCTAssertEqual(game.score, 70)
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
        game.outcome = { PlayOutcome(playType: .run, direction: .middle, yards: yards) }
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
