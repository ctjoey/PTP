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

    func testEveryStateFixtureDecodes() throws {
        for name in ["state_sync_nogame", "state_game_created", "state_play_opened", "state_play_locked",
                     "state_play_resolved", "state_play_voided", "state_final"] {
            XCTAssertNoThrow(try snapshot(name), name)
        }
    }

    func testNoGame() throws {
        let s = try snapshot("state_sync_nogame")
        XCTAssertNil(s.game)
        XCTAssertNil(s.play)
        XCTAssertEqual(s.me?.username, "JoeyC")
        XCTAssertEqual(s.scoring, .standard)
        XCTAssertEqual(s.lounge?.name, "Sunday Crew")
    }

    func testOpenPlayHidesCrowdAndHasTimer() throws {
        let s = try snapshot("state_play_opened")
        let play = try XCTUnwrap(s.play)
        XCTAssertEqual(play.state, .open)
        XCTAssertEqual(play.label, "Play 1 · 3rd & 7")
        XCTAssertEqual(play.locksAt - play.openedAt, 15, accuracy: 0.01)
        XCTAssertNil(s.crowd)
        XCTAssertEqual(s.game?.status, .live)
    }

    func testLockedShowsCrowdAndMyPick() throws {
        let s = try snapshot("state_play_locked")
        XCTAssertEqual(s.play?.state, .locked)
        let crowd = try XCTUnwrap(s.crowd)
        XCTAssertEqual(crowd.total, 2)
        XCTAssertEqual(crowd.count(Direction.left), 2)
        XCTAssertEqual(s.myPrediction?.playType, .pass)
        XCTAssertNil(s.myPrediction?.pointsEarned)
    }

    func testResolvedScoresAndRanks() throws {
        let s = try snapshot("state_play_resolved")
        XCTAssertEqual(s.play?.correctPlayType, .pass)
        XCTAssertEqual(s.play?.correctDirection, .left)
        XCTAssertEqual(s.myPrediction?.pointsEarned, 30)
        XCTAssertEqual(s.myPrediction?.typeCorrect, true)
        XCTAssertEqual(s.me?.rank, 1)
        XCTAssertEqual(s.leaderboard.map(\.username), ["JoeyC", "Sam"])
        let lounge = try XCTUnwrap(s.lounge?.leaderboard)
        XCTAssertEqual(lounge.first?.isHost, true)
        XCTAssertEqual(lounge.last?.score, 10)
    }

    func testVoidedAndFinal() throws {
        XCTAssertEqual(try snapshot("state_play_voided").play?.voided, true)
        XCTAssertEqual(try snapshot("state_final").game?.status, .final)
    }

    func testOtherSocketMessages() throws {
        guard case .predictionSaved(let p) = try ServerMessage.decode(fixture("msg_prediction_saved")) else {
            return XCTFail("prediction_saved")
        }
        XCTAssertEqual(p.direction, .left)
        guard case .error(let code, let message) = try ServerMessage.decode(fixture("msg_bad_token")) else {
            return XCTFail("bad_token")
        }
        XCTAssertEqual(code, "bad_token")
        XCTAssertFalse(message.isEmpty)
        guard case .error(nil, _) = try ServerMessage.decode(fixture("msg_error")) else { return XCTFail("error") }
        guard case .pong(let t) = try ServerMessage.decode(fixture("msg_pong")) else { return XCTFail("pong") }
        XCTAssertGreaterThan(t, 0)
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
        let data = try JSON.encoder.encode(PredictMessage(playId: 7, playType: .run, direction: .center))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["type"] as? String, "predict")
        XCTAssertEqual(object["play_id"] as? Int, 7)
        XCTAssertEqual(object["play_type"] as? String, "RUN")
        XCTAssertEqual(object["direction"] as? String, "CENTER")
        let hello = try XCTUnwrap(JSONSerialization.jsonObject(with: JSON.encoder.encode(HelloMessage(token: "t", lounge: nil)))
            as? [String: Any])
        XCTAssertEqual(hello["type"] as? String, "hello")
        XCTAssertEqual(hello["token"] as? String, "t")
    }
}
