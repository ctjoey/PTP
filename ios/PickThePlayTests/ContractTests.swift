import XCTest
@testable import PickThePlay

/// Every fixture is a real message captured from the Python server (see Fixtures/), so these tests
/// prove the Swift models match what the server actually sends.
final class ContractTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle(for: ContractTests.self).url(forResource: name, withExtension: "json"),
                                "missing fixture \(name).json")
        return try Data(contentsOf: url)
    }

    private func snapshot(_ name: String) throws -> StateSnapshot {
        guard case .state(let snapshot) = try ServerMessage.decode(fixture(name)) else {
            XCTFail("\(name) is not a state message")
            throw CancellationError()
        }
        return snapshot
    }

    private func object(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    func testEveryStateFixtureDecodes() throws {
        // The server only ever sends MIDDLE.
        for name in ["state_sync_nogame", "state_game_created", "state_play_opened", "state_play_locked",
                     "state_play_resolved", "state_play_resolved_loss", "state_play_voided", "state_final",
                     "msg_prediction_saved", "rest_me"] {
            XCTAssertFalse(String(decoding: try fixture(name), as: UTF8.self).contains("CENTER"), name)
        }
        for name in ["state_sync_nogame", "state_game_created", "state_play_opened", "state_play_locked",
                     "state_play_resolved", "state_play_resolved_loss", "state_play_voided", "state_final"] {
            let s = try snapshot(name)
            // Every snapshot carries the 10/10/10 scoring plus the 10 bonus, so all three = 40.
            XCTAssertEqual(s.scoring, .standard, name)
            let wire = try object(fixture(name))["scoring"].flatMap { $0 as? [String: Int] }
            XCTAssertEqual(wire?["yardage"], 10, name)
            XCTAssertEqual(wire?["bonus"], 10, name)
            XCTAssertEqual(wire?["exact"], 40, name)
        }
    }

    func testNoGame() throws {
        let s = try snapshot("state_sync_nogame")
        XCTAssertNil(s.game)
        XCTAssertNil(s.play)
        XCTAssertEqual(s.me?.username, "JoeyC")
        XCTAssertEqual(s.lounge?.name, "Sunday Crew")
    }

    func testGameUsesCityNamesAndRealColors() throws {
        let game = try XCTUnwrap(try snapshot("state_game_created").game)
        XCTAssertEqual(game.status, .scheduled)
        // Chicago at Detroit, from the admin's team presets.
        XCTAssertEqual(game.awayName, "Chicago")
        XCTAssertEqual(game.awayPrimary.uppercased(), "#0B162A")
        XCTAssertEqual(game.awaySecondary.uppercased(), "#C83803")
        XCTAssertEqual(game.homeName, "Detroit")
        XCTAssertEqual(game.homePrimary.uppercased(), "#0076B6")
        XCTAssertEqual(game.homeSecondary.uppercased(), "#B0B7BC")
        XCTAssertEqual(Football.abbreviation(game.awayName), "CHI")
        XCTAssertEqual(Football.abbreviation(game.homeName), "DET")
    }

    func testOpenPlayHidesCrowdAndHasTimer() throws {
        let s = try snapshot("state_play_opened")
        let play = try XCTUnwrap(s.play)
        XCTAssertEqual(play.state, .open)
        XCTAssertEqual(play.label, "Play 1 · 3rd & 7")
        XCTAssertEqual(play.locksAt - play.openedAt, 15, accuracy: 0.01)
        XCTAssertNil(play.correctYardage)
        XCTAssertNil(play.yardsGained)
        XCTAssertNil(play.outcome)
        XCTAssertNil(s.crowd)
        XCTAssertEqual(s.game?.status, .live)
        // The new play fields are on the wire (as null) before the play is resolved.
        let wire = try XCTUnwrap(try object(fixture("state_play_opened"))["play"] as? [String: Any])
        XCTAssertTrue(wire.keys.contains("correct_yardage"))
        XCTAssertTrue(wire.keys.contains("yards_gained"))
    }

    func testLockedShowsCrowdAndMyPick() throws {
        let s = try snapshot("state_play_locked")
        XCTAssertEqual(s.play?.state, .locked)
        let crowd = try XCTUnwrap(s.crowd)
        XCTAssertEqual(crowd.total, 2)
        XCTAssertEqual(crowd.count(Direction.left) + crowd.count(Direction.middle) + crowd.count(Direction.right), crowd.total)
        XCTAssertEqual(crowd.count(PlayType.run) + crowd.count(PlayType.pass), crowd.total)
        // The distance split rides along with the type and direction split.
        XCTAssertTrue(crowd.hasYardage)
        let yardageTotal = Yardage.allCases.compactMap { crowd.count($0) }.reduce(0, +)
        XCTAssertEqual(yardageTotal, crowd.total)
        XCTAssertEqual(crowd.count(Yardage.medium), 2)
        // Sam's old app sent "CENTER"; the server counts it (and sends it) as MIDDLE.
        XCTAssertEqual(crowd.count(Direction.middle), 2)
        XCTAssertEqual(crowd.exact, 0)
        let wireCrowd = try XCTUnwrap(try object(fixture("state_play_locked"))["crowd"] as? [String: Any])
        XCTAssertNotNil(wireCrowd["MIDDLE"])
        XCTAssertNil(wireCrowd["CENTER"])
        let mine = try XCTUnwrap(s.myPrediction)
        XCTAssertEqual(mine.playType, .pass)
        XCTAssertEqual(mine.direction, .middle)
        XCTAssertEqual(mine.yardage, .medium)
        XCTAssertNil(mine.pointsEarned)
        XCTAssertNil(mine.yardageCorrect)
        XCTAssertNil(s.play?.outcome, "nothing is revealed while the play runs")
    }

    func testResolvedScoresAndRanks() throws {
        let s = try snapshot("state_play_resolved")
        let play = try XCTUnwrap(s.play)
        XCTAssertEqual(play.correctPlayType, .pass)
        XCTAssertEqual(play.correctDirection, .middle)
        XCTAssertEqual(play.correctYardage, .medium)
        XCTAssertEqual(play.yardsGained, 7)
        let outcome = try XCTUnwrap(play.outcome)
        XCTAssertEqual(outcome.summary, "Pass · Middle · Medium (7 yds)")
        XCTAssertEqual(YardageOutcome(yards: 7), play.correctYardage, "server and app bucket yards the same way")
        // The server's score for my pick is exactly what the app's own rules give.
        let mine = try XCTUnwrap(s.myPrediction)
        let yardage = try XCTUnwrap(mine.yardage)
        let local = ScoreRules.score(type: mine.playType, direction: mine.direction, yardage: yardage, actual: outcome,
                                     scoring: s.scoring)
        XCTAssertEqual(mine.pointsEarned, local.points)
        XCTAssertEqual(mine.typeCorrect, local.typeCorrect)
        XCTAssertEqual(mine.directionCorrect, local.directionCorrect)
        XCTAssertEqual(mine.yardageCorrect, local.yardageCorrect)
        // A perfect call: 10 + 10 + 10 + the 10 bonus.
        XCTAssertEqual(mine.pointsEarned, 40)
        XCTAssertEqual(mine.pointsEarned, s.scoring.exact)
        XCTAssertEqual(mine.direction, .middle)
        XCTAssertEqual(mine.yardage, .medium)
        XCTAssertEqual(mine.yardageCorrect, true)
        XCTAssertEqual(ScoreRules.label(pick: mine, scoring: s.scoring), "Perfect call!")
        XCTAssertEqual(ScoreRules.label(points: mine.pointsEarned, scoring: s.scoring), "Perfect call!")
        XCTAssertTrue(ScoreRules.isPerfect(mine, scoring: s.scoring))
        XCTAssertEqual(s.me?.rank, 1)
        XCTAssertEqual(s.me?.exactHits, 1)
        XCTAssertEqual(s.me?.gameScore, 40)
        // Sam (Run · Middle · Medium, sent as the old CENTER) got two of three: 20 points, no bonus.
        XCTAssertEqual(s.leaderboard.map(\.username), ["JoeyC", "Sam"])
        XCTAssertEqual(s.leaderboard.map(\.score), [40, 20])
        XCTAssertEqual(s.leaderboard.map(\.exactHits), [1, 0])
        let crowd = try XCTUnwrap(s.crowd)
        XCTAssertEqual(crowd.exact, 1)
        XCTAssertEqual(crowd.scored, 2)
        let lounge = try XCTUnwrap(s.lounge?.leaderboard)
        XCTAssertEqual(lounge.first?.isHost, true)
        XCTAssertEqual(lounge.last?.score, 20)
    }

    func testResolvedLossScoresNoDistance() throws {
        let s = try snapshot("state_play_resolved_loss")
        let play = try XCTUnwrap(s.play)
        XCTAssertEqual(play.correctYardage, .loss)
        XCTAssertEqual(play.yardsGained, -4)
        let outcome = try XCTUnwrap(play.outcome)
        XCTAssertEqual(outcome.summary, "Pass · Right · Loss (-4 yds)")
        let mine = try XCTUnwrap(s.myPrediction)
        XCTAssertEqual(mine.yardage, .short)
        XCTAssertEqual(mine.yardageCorrect, false)
        XCTAssertEqual(mine.typeCorrect, true)
        XCTAssertEqual(mine.directionCorrect, true)
        XCTAssertEqual(mine.pointsEarned, 20)
        let local = ScoreRules.score(type: mine.playType, direction: mine.direction, yardage: mine.yardage, actual: outcome)
        XCTAssertEqual(local.points, mine.pointsEarned)
        XCTAssertFalse(local.yardageCorrect)
        XCTAssertEqual(ScoreRules.label(points: mine.pointsEarned), "Two of three")
        XCTAssertEqual(ScoreRules.label(pick: mine), "Two of three")
        XCTAssertFalse(ScoreRules.isPerfect(mine), "a loss means no distance points, so no bonus")
        XCTAssertEqual(s.crowd?.exact, 0, "nobody can call a loss")
        XCTAssertEqual(s.me?.gameScore, 60)
        XCTAssertEqual(s.leaderboard.map(\.score), [60, 30])
    }

    func testVoidedAndFinal() throws {
        let voided = try snapshot("state_play_voided").play
        XCTAssertEqual(voided?.voided, true)
        XCTAssertNil(voided?.outcome)
        XCTAssertEqual(try snapshot("state_final").game?.status, .final)
    }

    func testOtherSocketMessages() throws {
        guard case .predictionSaved(let p) = try ServerMessage.decode(fixture("msg_prediction_saved")) else {
            return XCTFail("prediction_saved")
        }
        XCTAssertEqual(p.direction, .middle)
        XCTAssertEqual(p.yardage, .medium, "the server echoes the distance pick")
        XCTAssertNil(p.pointsEarned)
        guard case .error(let code, let message) = try ServerMessage.decode(fixture("msg_bad_token")) else {
            return XCTFail("bad_token")
        }
        XCTAssertEqual(code, "bad_token")
        XCTAssertFalse(message.isEmpty)
        guard case .error(nil, _) = try ServerMessage.decode(fixture("msg_error")) else { return XCTFail("error") }
        guard case .pong(let t) = try ServerMessage.decode(fixture("msg_pong")) else { return XCTFail("pong") }
        XCTAssertGreaterThan(t, 0)
    }

    /// The host's banner and removal, as the real server sends them.
    func testHostMessagesDecode() throws {
        guard case .announcement(let id, let text) = try ServerMessage.decode(fixture("msg_announcement")) else {
            return XCTFail("announcement")
        }
        XCTAssertGreaterThan(id, 0)
        XCTAssertEqual(text, "Halftime! Back in about 15 minutes.")
        // Told on connect when nothing is showing, and when the host clears it: empty text.
        guard case .announcement(_, let none) = try ServerMessage.decode(fixture("msg_announcement_none")) else {
            return XCTFail("announcement_none")
        }
        XCTAssertEqual(none, "")
        guard case .announcement(let clearedID, let cleared) = try ServerMessage.decode(fixture("msg_announcement_cleared")) else {
            return XCTFail("announcement_cleared")
        }
        XCTAssertEqual(cleared, "")
        XCTAssertGreaterThan(clearedID, id)
        guard case .error(let code, let message) = try ServerMessage.decode(fixture("msg_account_removed")) else {
            return XCTFail("account_removed")
        }
        XCTAssertEqual(code, "account_deleted")
        XCTAssertEqual(message, "You were removed by the host.")
    }

    func testRestPayloads() throws {
        let user = try JSON.decoder.decode(UserAccount.self, from: fixture("rest_create_user"))
        XCTAssertEqual(user.username, "JoeyC")
        XCTAssertNotNil(user.token)
        let me = try JSON.decoder.decode(MeResponse.self, from: fixture("rest_me"))
        XCTAssertEqual(me.lounges.first?.memberCount, 2)
        let lounge = try JSON.decoder.decode(Lounge.self, from: fixture("rest_lounge"))
        XCTAssertEqual(lounge.id.count, 4)
        XCTAssertEqual(APIClient.detail(from: try fixture("rest_error")), "That username is taken. Try another.")
    }

    func testOutgoingMessagesUseServerFieldNames() throws {
        let pick = PredictMessage(playId: 7, playType: .pass, direction: .middle, yardage: .short)
        let wire = try object(JSON.encoder.encode(pick))
        XCTAssertEqual(wire["type"] as? String, "predict")
        XCTAssertEqual(wire["play_id"] as? Int, 7)
        XCTAssertEqual(wire["play_type"] as? String, "PASS")
        XCTAssertEqual(wire["direction"] as? String, "MIDDLE")
        XCTAssertEqual(wire["yardage"] as? String, "SHORT")
        XCTAssertEqual(wire.count, 5)
        // The HTTP fallback posts the same fields (no "type").
        XCTAssertEqual(pick.body["yardage"] as? String, "SHORT")
        XCTAssertEqual(pick.body["direction"] as? String, "MIDDLE")
        XCTAssertEqual(pick.body["play_id"] as? Int, 7)
        XCTAssertEqual(Set(pick.body.keys), ["play_id", "play_type", "direction", "yardage"])
        let hello = try object(JSON.encoder.encode(HelloMessage(token: "t", lounge: nil)))
        XCTAssertEqual(hello["type"] as? String, "hello")
        XCTAssertEqual(hello["token"] as? String, "t")
    }

    /// A game.db or server from before the distance pick: null/missing fields must still decode.
    func testLegacyPayloadsDecode() throws {
        let json = """
        {"type": "state", "event": "sync", "server_time": 1.5, "game": null,
         "play": {"id": 3, "game_id": 1, "play_number": 3, "down": 2, "distance": "4", "state": "RESOLVED",
                  "voided": false, "opened_at": 1, "locks_at": 16, "correct_play_type": "RUN",
                  "correct_direction": "CENTER"},
         "my_prediction": {"user_id": 1, "play_id": 3, "play_type": "RUN", "direction": "CENTER", "yardage": null,
                           "points_earned": 20, "type_correct": true, "direction_correct": true},
         "me": null, "leaderboard": [], "ranked_players": 0,
         "crowd": {"total": 1, "RUN": 1, "PASS": 0, "LEFT": 0, "CENTER": 1, "RIGHT": 0, "exact": 0, "scored": 1},
         "lounge": null, "scoring": {"type": 10, "direction": 10, "exact": 30}}
        """
        guard case .state(let s) = try ServerMessage.decode(Data(json.utf8)) else { return XCTFail("state") }
        XCTAssertNil(s.play?.correctYardage)
        XCTAssertEqual(s.play?.outcome, PlayOutcome(playType: .run, direction: .middle, yardage: nil))
        XCTAssertEqual(s.myPrediction?.direction, .middle, "CENTER from an old server reads as MIDDLE")
        XCTAssertEqual(s.crowd?.count(Direction.middle), 1)
        XCTAssertNil(s.myPrediction?.yardage)
        XCTAssertNil(s.myPrediction?.yardageCorrect)
        XCTAssertEqual(s.crowd?.hasYardage, false)
        XCTAssertNil(s.crowd?.count(Yardage.short))
        XCTAssertEqual(s.scoring.yardage, 10, "missing scoring.yardage falls back to 10")
        XCTAssertEqual(s.scoring.bonus, 0, "a server where all three = 30 pays no bonus")

        let loss = """
        {"id": 4, "game_id": 1, "play_number": 4, "down": null, "distance": null, "state": "RESOLVED", "voided": false,
         "opened_at": 1, "locks_at": 16, "correct_play_type": "PASS", "correct_direction": "RIGHT",
         "correct_yardage": "LOSS", "yards_gained": -4}
        """
        let play = try JSON.decoder.decode(Play.self, from: Data(loss.utf8))
        XCTAssertEqual(play.outcome?.summary, "Pass · Right · Loss (-4 yds)")
    }
}
