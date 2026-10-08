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

        // A play for a loss of yards: type and direction right, no distance points.
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
            (.pass, 0.03, .short), (.pass, 0.2, .short), (.pass, 0.5, .short), (.pass, 0.7, .medium), (.pass, 0.9, .long),
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
        XCTAssertEqual(share(.pass, .loss), 0.0, accuracy: 0.001, "a sack is no play, so practice has none")
        XCTAssertEqual(share(.pass, .short), 0.55, accuracy: 0.04)
        XCTAssertEqual(share(.pass, .long), 0.24, accuracy: 0.04)
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

    // MARK: - Messages from the host

    @MainActor
    func testHostBannerShowsClearsAndStaysDismissed() {
        let key = "ptp.dismissedAnnouncement"
        UserDefaults.standard.removeObject(forKey: key)
        defer { UserDefaults.standard.removeObject(forKey: key) }
        let state = AppState()
        XCTAssertNil(state.announcement)
        state.handle(.announcement(id: 5, text: "  Halftime!  "))
        XCTAssertEqual(state.announcement, AppState.Announcement(id: 5, text: "Halftime!"))
        state.handle(.announcement(id: 6, text: ""))                 // the host cleared it
        XCTAssertNil(state.announcement)
        state.handle(.announcement(id: 7, text: "Back soon"))
        state.dismissAnnouncement()
        XCTAssertNil(state.announcement)
        state.handle(.announcement(id: 7, text: "Back soon"))        // a reconnect repeats it: it stays dismissed
        XCTAssertNil(state.announcement)
        state.handle(.announcement(id: 8, text: "Second half"))      // a new message shows again
        XCTAssertEqual(state.announcement?.text, "Second half")
        state.dismissAnnouncement()
        state.dismissAnnouncement()                                  // nothing showing: harmless
        XCTAssertNil(state.announcement)
    }

    @MainActor
    func testBeingRemovedByTheHostShowsTheReasonAndSignsOut() {
        let state = AppState()
        state.handle(.announcement(id: 9, text: "Hi"))
        state.handle(.error(code: "account_deleted", message: "You were removed by the host."))
        XCTAssertFalse(state.isSignedIn)
        XCTAssertNil(state.announcement, "signed out: the banner goes too")
        XCTAssertEqual(state.notice?.text, "You were removed by the host.")
        XCTAssertEqual(state.notice?.isError, true)
        XCTAssertEqual(state.notice?.seconds, 8, "long enough to read")
        state.handle(.error(code: "account_deleted", message: ""))
        XCTAssertEqual(state.notice?.text, "Your account was deleted.")
        state.handle(.error(code: "bad_token", message: "Session expired. Sign in again."))
        XCTAssertEqual(state.notice?.text, "Please pick a username again.")
        XCTAssertEqual(state.notice?.seconds, 3.5)
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

// MARK: - Host console

extension GameLogicTests {
    private func suggestion(playId: Int = 7, status: String = "ready", playType: String? = "PASS", direction: String? = "LEFT",
                            yards: Int? = 7, yardage: String? = nil, text: String = "pass short left for 7 yards",
                            autoAt: Double? = 110) -> FeedSuggestion {
        var json: [String: Any] = ["play_id": playId, "status": status, "text": text, "flags": [], "kind": "play"]
        json["play_type"] = playType ?? NSNull()
        json["direction"] = direction ?? NSNull()
        json["yards"] = yards ?? NSNull()
        json["yardage"] = yardage ?? NSNull()
        json["auto_at"] = autoAt ?? NSNull()
        // swiftlint:disable:next force_try
        return try! JSON.decoder.decode(FeedSuggestion.self, from: JSONSerialization.data(withJSONObject: json))
    }

    func testResultDraftTypingYardsPicksTheBucketAndPickingABucketClearsAMismatch() {
        var draft = ResultDraft()
        XCTAssertTrue(draft.isEmpty)
        XCTAssertFalse(draft.isComplete)
        draft.choose(.pass)
        draft.choose(.left)
        draft.typeYards("7")
        XCTAssertEqual(draft.yardage, .medium, "typing 7 picks Medium")
        XCTAssertTrue(draft.isComplete)
        draft.choose(YardageOutcome.short)
        XCTAssertEqual(draft.yardsText, "", "7 yards isn't Short, so the typed yards are dropped")
        draft.typeYards("-4")
        XCTAssertEqual(draft.yardage, .loss)
        draft.choose(YardageOutcome.loss)
        XCTAssertEqual(draft.yards, -4, "-4 fits Loss, so it stays")
        draft.typeYards("")
        XCTAssertEqual(draft.yardage, .loss, "clearing the box leaves the bucket")
        XCTAssertTrue(draft.isComplete)
    }

    func testResultDraftRejectsNonsenseYardsAndBuildsTheServersFields() {
        var draft = ResultDraft(playType: .run, direction: .middle, yardage: .short)
        draft.typeYards("abc")
        XCTAssertTrue(draft.yardsAreInvalid)
        XCTAssertFalse(draft.isComplete)
        draft.typeYards("100")
        XCTAssertTrue(draft.yardsAreInvalid, "99 is the most the server accepts")
        draft.typeYards("2.5")
        XCTAssertTrue(draft.yardsAreInvalid)
        draft.typeYards(" 3 ")
        XCTAssertEqual(draft.yards, 3)
        XCTAssertEqual(draft.fields["play_type"] as? String, "RUN")
        XCTAssertEqual(draft.fields["direction"] as? String, "MIDDLE")
        XCTAssertEqual(draft.fields["yardage"] as? String, "SHORT")
        XCTAssertEqual(draft.fields["yards"] as? Int, 3)
        draft.typeYards("")
        XCTAssertNil(draft.fields["yards"], "yards are only sent when typed")
        XCTAssertEqual(draft.summary, "Run · Middle · Short")
        draft.typeYards("-4")
        XCTAssertEqual(draft.summary, "Run · Middle · Loss (-4 yds)")
        draft.clear()
        XCTAssertTrue(draft.isEmpty)
    }

    func testFeedSuggestionFillsGapsWithTheHostsChoicesOnlyForThatPlay() {
        let sack = suggestion(status: "review", playType: "PASS", direction: nil, yards: -7, yardage: "LOSS")
        XCTAssertEqual(sack.missingParts, [.direction])
        var choices = SuggestionChoices()
        choices.sync(to: sack)
        XCTAssertFalse(choices.canScore(sack))
        XCTAssertEqual(choices.missing(sack), [.direction])
        choices.direction = .left
        XCTAssertTrue(choices.canScore(sack))
        XCTAssertEqual(choices.merged(with: sack), ResultDraft(playType: .pass, direction: .left, yardage: .loss, yards: -7))
        let fields = choices.acceptFields(sack)
        XCTAssertEqual(fields["play_id"] as? Int, 7)
        XCTAssertEqual(fields["direction"] as? String, "LEFT")
        XCTAssertNil(fields["play_type"], "only the parts the feed lacked are sent")
        XCTAssertNil(fields["void"])

        // The next play's suggestion starts clean.
        let next = suggestion(playId: 8, status: "review", playType: nil, direction: nil, yards: nil)
        choices.sync(to: next)
        XCTAssertNil(choices.direction)
        XCTAssertEqual(choices.missing(next), [.playType, .direction, .yardage])
        XCTAssertFalse(choices.canScore(next))
        // A suggestion is clean when the feed read everything.
        let clean = suggestion(playId: 9)
        choices.sync(to: clean)
        XCTAssertTrue(choices.canScore(clean))
        XCTAssertEqual(choices.acceptFields(clean).count, 1, "just the play id")
    }

    func testNoPlaySuggestionVoidsAndNeedsNoChoices() {
        let flag = suggestion(playType: nil, direction: nil, yards: nil, text: "No Play. Holding on the offense.")
        let void = suggestion(status: "void", playType: nil, direction: nil, yards: nil, text: "NO PLAY")
        let held = suggestion(status: "held", playType: nil, direction: nil, yards: nil, text: "No Play (penalty)")
        XCTAssertFalse(flag.isVoid, "a ready suggestion without a type is just incomplete")
        XCTAssertTrue(void.isVoid)
        XCTAssertTrue(held.isVoid, "a held 'no play' is still a void")
        let choices = SuggestionChoices()
        XCTAssertTrue(choices.canScore(void))
        XCTAssertEqual(choices.acceptFields(void)["void"] as? Bool, true)
        XCTAssertTrue(void.missingParts.isEmpty)
        XCTAssertEqual(held.note(missing: [], paused: false), "On hold. Tap Void play to confirm it, or score it yourself with Change.")
    }

    func testSuggestionNotesExplainWhatHappensNext() {
        let ready = suggestion(autoAt: nil)
        XCTAssertEqual(ready.note(missing: [], paused: false), "Auto-score is off. Tap Score now when you're happy with it.")
        XCTAssertEqual(ready.note(missing: [], paused: true), "Live data is paused, so this won't score by itself. Tap Score now, or Resume.")
        let review = suggestion(status: "review", autoAt: nil)
        XCTAssertEqual(review.note(missing: [], paused: false), "An unusual play, so it won't score by itself. Check it, then tap Score.")
        XCTAssertEqual(suggestion(autoAt: 110).secondsUntilAuto(at: 100), 10)
        XCTAssertEqual(suggestion(autoAt: 110).secondsUntilAuto(at: 120), 0, "never negative")
    }

    func testDownDraftFillsFromTheFeedUntilTheHostEdits() {
        var dd = DownDraft()
        XCTAssertEqual(dd.down, 1)
        XCTAssertEqual(dd.distance, "10")
        // Between plays the feed says 2nd & 3.
        dd.prefill(from: NextDown(down: 2, distance: "3"), gameID: 1, lastPlayID: 5, hasActivePlay: false)
        XCTAssertEqual(dd.down, 2)
        XCTAssertEqual(dd.distance, "3")
        XCTAssertTrue(dd.showsFeedHint(hasActivePlay: false))
        // The host corrects it; later updates for the same play must not overwrite that.
        dd.setDistance("4")
        XCTAssertFalse(dd.showsFeedHint(hasActivePlay: false))
        dd.prefill(from: NextDown(down: 2, distance: "3"), gameID: 1, lastPlayID: 5, hasActivePlay: false)
        XCTAssertEqual(dd.distance, "4")
        // A new play opens, then resolves: the feed's next down applies again.
        dd.prefill(from: NextDown(down: 2, distance: "3"), gameID: 1, lastPlayID: 5, hasActivePlay: true)
        dd.prefill(from: NextDown(down: 3, distance: "Goal"), gameID: 1, lastPlayID: 6, hasActivePlay: false)
        XCTAssertEqual(dd.down, 3)
        XCTAssertEqual(dd.distance, "Goal")
        XCTAssertEqual(dd.distanceToSend, "Goal")
        dd.setDown(9)
        XCTAssertEqual(dd.down, 4, "only 1st to 4th down")
        dd.setDistance("   ")
        XCTAssertNil(dd.distanceToSend)
        dd.reset()
        XCTAssertEqual(dd, DownDraft())
    }

    func testTeamPresetsAreUsable() {
        XCTAssertGreaterThanOrEqual(TeamPreset.all.count, 32)
        XCTAssertEqual(Set(TeamPreset.all.map(\.label)).count, TeamPreset.all.count, "labels are unique")
        for team in TeamPreset.all {
            XCTAssertTrue(team.primary.range(of: "^#[0-9A-Fa-f]{6}$", options: .regularExpression) != nil, team.label)
            XCTAssertTrue(team.secondary.range(of: "^#[0-9A-Fa-f]{6}$", options: .regularExpression) != nil, team.label)
            XCTAssertLessThanOrEqual(team.name.count, 40)
        }
        let tb = TeamPreset.all.first { $0.name == "Tampa Bay" }
        XCTAssertEqual(tb?.choice, TeamChoice(name: "Tampa Bay", primary: "#D50A0A", secondary: "#FF7900"))
        XCTAssertEqual(tb?.choice.primary, "#d50a0a", "colours go to the server in lower case, like the website")
    }

    func testHostTextHelpers() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/New_York")!
        let date = calendar.date(from: DateComponents(year: 2026, month: 10, day: 8, hour: 19))!
        XCTAssertEqual(HostText.scheduleDate(date, calendar: calendar), "20261008")
        XCTAssertEqual(HostText.count(1, "pick"), "1 pick")
        XCTAssertEqual(HostText.count(2, "pick"), "2 picks")
        XCTAssertEqual(HostText.quickMessages.map(\.title), ["Delayed", "Halftime", "Paused", "Game over"])
        for message in HostText.quickMessages { XCTAssertLessThanOrEqual(message.text.count, HostText.maxMessage) }
    }

    // MARK: HostState

    @MainActor
    func testHostKeyIsSavedOnlyAfterTheServerAcceptsIt() throws {
        UserDefaults.standard.set("http://127.0.0.1:9", forKey: ServerConfig.overrideKey)
        defer { UserDefaults.standard.removeObject(forKey: ServerConfig.overrideKey) }
        let store = MemoryKeyStore()
        let host = HostState(keys: store)
        XCTAssertFalse(host.hasSavedKey)
        XCTAssertFalse(host.beginSignIn(key: "   ", remember: true))
        XCTAssertEqual(host.authError, "Enter the admin key.")
        XCTAssertTrue(host.beginSignIn(key: "  secret  ", remember: true))
        XCTAssertTrue(host.signingIn)
        XCTAssertNil(store.read(), "nothing is kept until the server says the key is right")
        let state = try XCTUnwrap(adminStateFromFixtureJSON())
        host.handle(.state(state))
        XCTAssertFalse(host.signingIn)
        XCTAssertTrue(host.isSignedIn)
        XCTAssertEqual(store.read(), "secret", "spaces around the key are ignored")
        XCTAssertTrue(host.hasSavedKey)
        XCTAssertNil(host.authError)
        host.signOut()
        XCTAssertNil(store.read())
        XCTAssertFalse(host.hasSavedKey)
        XCTAssertFalse(host.isSignedIn)
        XCTAssertFalse(host.isPresented)
    }

    @MainActor
    func testHostKeyIsNotSavedWhenTheHostDoesNotWantItRemembered() throws {
        UserDefaults.standard.set("http://127.0.0.1:9", forKey: ServerConfig.overrideKey)
        defer { UserDefaults.standard.removeObject(forKey: ServerConfig.overrideKey) }
        let store = MemoryKeyStore()
        let host = HostState(keys: store)
        XCTAssertTrue(host.beginSignIn(key: "secret", remember: false))
        host.handle(.state(try XCTUnwrap(adminStateFromFixtureJSON())))
        XCTAssertTrue(host.isSignedIn)
        XCTAssertNil(store.read())
        XCTAssertFalse(host.hasSavedKey)
    }

    @MainActor
    func testARefusedKeyIsForgottenAndExplained() {
        UserDefaults.standard.set("http://127.0.0.1:9", forKey: ServerConfig.overrideKey)
        defer { UserDefaults.standard.removeObject(forKey: ServerConfig.overrideKey) }
        let store = MemoryKeyStore("old-key")
        let host = HostState(keys: store)
        XCTAssertTrue(host.hasSavedKey, "a key from last time")
        host.handle(.authError("Invalid admin key."))
        XCTAssertNil(store.read())
        XCTAssertFalse(host.hasSavedKey)
        XCTAssertEqual(host.authError, "Invalid admin key.")
        XCTAssertFalse(host.signingIn)
        XCTAssertFalse(host.isSignedIn)
        XCTAssertTrue(host.beginSignIn(key: "again", remember: true))
        XCTAssertNil(host.authError, "a fresh attempt clears the old message")
    }

    func testAnActionWithoutAConnectionSaysSoAndSendsNothing() async {
        let host = await MainActor.run { HostState(keys: MemoryKeyStore()) }
        let ack = await host.act("lock_play")
        XCTAssertNil(ack)
        let notice = await MainActor.run { host.notice }
        XCTAssertEqual(notice?.text, "Not connected to the server.")
        XCTAssertEqual(notice?.isError, true)
        let pending = await MainActor.run { host.pending }
        XCTAssertEqual(pending, 0)
        let done = await host.lockPlay()
        XCTAssertFalse(done)
        let incomplete = await host.resolvePlay(ResultDraft(playType: .run))
        XCTAssertFalse(incomplete, "an unfinished result is never sent")
    }

    @MainActor
    func testHostAcksAreMatchedByRequestId() {
        let host = HostState(keys: MemoryKeyStore())
        // An answer nobody is waiting for is ignored.
        host.handle(.ack(AdminAck(requestId: 99, action: "lock_play", ok: true, error: nil, sentTo: nil)))
        XCTAssertEqual(host.pending, 0)
    }

    @MainActor
    private func adminStateFromFixtureJSON() -> AdminState? {
        let json = """
        {"type": "admin_state", "event": "sync", "server_time": 1000.0, "game": null, "play": null,
         "players_online": 0, "spectators_online": 0, "admins_online": 1, "leaderboard": [], "ranked_players": 0,
         "history": [], "window_seconds": 15, "announcement": null, "registered_players": 0}
        """
        guard case .state(let state)? = try? AdminServerMessage.decode(Data(json.utf8)) else { return nil }
        return state
    }
}

// MARK: - Host drafts (the console's half-finished entries follow the server's state)

extension GameLogicTests {
    private func adminState(game: Int? = 1, play: Int? = nil, playState: String = "OPEN", nextDown: String = "null",
                            history: String = "[]", suggestion: String = "null") -> AdminState {
        let gameJSON = game.map { """
        {"id": \($0), "home_name": "Dallas", "home_primary": "#003594", "home_secondary": "#869397",
         "away_name": "Tampa Bay", "away_primary": "#D50A0A", "away_secondary": "#FF7900", "status": "LIVE"}
        """ } ?? "null"
        let playJSON = play.map { """
        {"id": \($0), "game_id": 1, "play_number": \($0), "down": 1, "distance": "10", "state": "\(playState)",
         "voided": false, "opened_at": 1, "locks_at": 16}
        """ } ?? "null"
        let json = """
        {"type": "admin_state", "event": "sync", "server_time": 100.0, "game": \(gameJSON), "play": \(playJSON),
         "players_online": 0, "spectators_online": 0, "admins_online": 1, "leaderboard": [], "ranked_players": 0,
         "history": \(history), "window_seconds": 15, "announcement": null, "registered_players": 0,
         "feed": {"state": "idle", "available": true, "linked": true, "source": "demo", "paused": false,
                  "auto_score": true, "auto_open": false, "next_down": \(nextDown), "suggestion": \(suggestion)}}
        """
        guard case .state(let state)? = try? AdminServerMessage.decode(Data(json.utf8)) else {
            fatalError("test JSON did not decode")
        }
        return state
    }

    @MainActor
    func testDraftsStartFreshForANewPlayAndANewGame() {
        let drafts = HostDrafts()
        drafts.update(from: adminState(game: 1, play: 5))
        drafts.result = ResultDraft(playType: .run, direction: .left)
        drafts.update(from: adminState(game: 1, play: 5))
        XCTAssertEqual(drafts.result.playType, .run, "same play: the half-made result stays")
        drafts.update(from: adminState(game: 1, play: 6))
        XCTAssertTrue(drafts.result.isEmpty, "a new play (perhaps opened from another console): start fresh")
        drafts.down.setDown(3)
        drafts.result = ResultDraft(playType: .pass)
        drafts.update(from: adminState(game: 2, play: nil))
        XCTAssertEqual(drafts.down, DownDraft(), "a new game: 1st & 10 again")
        XCTAssertTrue(drafts.result.isEmpty)
        XCTAssertNil(drafts.pickedGame)
    }

    @MainActor
    func testDraftsTakeTheNextDownFromTheFeedBetweenPlaysOnly() {
        let drafts = HostDrafts()
        drafts.update(from: adminState(game: 1, play: 5, playState: "RESOLVED", nextDown: #"{"down": 2, "distance": "3"}"#))
        XCTAssertEqual(drafts.down.down, 2)
        XCTAssertEqual(drafts.down.distance, "3")
        XCTAssertTrue(drafts.down.showsFeedHint(hasActivePlay: false))
        drafts.down.setDistance("4")
        drafts.update(from: adminState(game: 1, play: 5, playState: "RESOLVED", nextDown: #"{"down": 2, "distance": "3"}"#))
        XCTAssertEqual(drafts.down.distance, "4", "the host's edit wins")
    }

    @MainActor
    func testDraftsForgetAFixWhoseRowIsGoneAndKeepsAnOpenOne() throws {
        let row = """
        [{"id": 5, "play_number": 5, "down": 1, "distance": "10", "state": "RESOLVED", "voided": 0,
          "correct_play_type": "RUN", "correct_direction": "LEFT", "correct_yardage": "SHORT", "yards_gained": 2,
          "resolved_by": "host", "picks": 3, "exact_hits": 1}]
        """
        let drafts = HostDrafts()
        let state = adminState(game: 1, play: 5, playState: "RESOLVED", history: row)
        drafts.update(from: state)
        let history = try XCTUnwrap(state.history.first)
        drafts.fix = FixDraft(row: history)
        XCTAssertNotNil(drafts.fix)
        XCTAssertEqual(drafts.fix?.original.summary, "Run · Left · Short (2 yds)")
        drafts.update(from: state)
        XCTAssertNotNil(drafts.fix, "the row is still there")
        drafts.update(from: adminState(game: 1, play: 5, playState: "RESOLVED", history: "[]"))
        XCTAssertNil(drafts.fix, "the row vanished (another console voided it?)")
    }

    @MainActor
    func testChangeLoadsTheSuggestionIntoTheResultBoxes() throws {
        let sg = """
        {"play_id": 5, "status": "review", "text": "sacked", "play_type": "PASS", "direction": null, "yards": -7,
         "yardage": "LOSS", "flags": [], "auto_at": null}
        """
        let state = adminState(game: 1, play: 5, playState: "LOCKED", suggestion: sg)
        let drafts = HostDrafts()
        drafts.update(from: state)
        drafts.choices.direction = .right
        drafts.load(try XCTUnwrap(state.feed?.suggestion))
        XCTAssertEqual(drafts.result.summary, "Pass · Right · Loss (-7 yds)")
        XCTAssertTrue(drafts.result.isComplete)
    }

    func testFixDraftNeedsARealChange() throws {
        let row = HistoryPlay(id: 9, playNumber: 9, down: 2, distance: "5", state: .resolved, voided: false,
                              correctPlayType: .pass, correctDirection: .left, correctYardage: .medium, yardsGained: 7,
                              resolvedBy: "feed", feedText: nil, picks: 4, exactHits: 1)
        var fix = try XCTUnwrap(FixDraft(row: row))
        XCTAssertFalse(fix.isChanged)
        XCTAssertFalse(fix.canSave, "nothing changed")
        fix.result.choose(Direction.right)
        XCTAssertTrue(fix.canSave)
        fix.result.choose(Direction.left)
        XCTAssertFalse(fix.isChanged, "back to how it was")
        fix.result.typeYards("9")
        XCTAssertTrue(fix.isChanged, "different yards count as a change")
        let preset = ResultDraft(playType: .run, direction: nil, yardage: .short, yards: 2)
        let fromFeed = try XCTUnwrap(FixDraft(row: row, preset: preset))
        XCTAssertEqual(fromFeed.result.summary, "Run · Left · Short (2 yds)", "the feed's reading replaces what it knows")
        let voided = HistoryPlay(id: 10, playNumber: 10, down: nil, distance: nil, state: .resolved, voided: true,
                                 correctPlayType: nil, correctDirection: nil, correctYardage: nil, yardsGained: nil,
                                 resolvedBy: "void", feedText: nil, picks: 0, exactHits: 0)
        XCTAssertNil(FixDraft(row: voided), "only a scored play can be fixed")
    }
}
