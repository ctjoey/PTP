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
                     "state_play_resolved", "state_play_resolved_loss", "state_play_voided", "state_final",
                     "state_game_score"] {
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

    func testGameCarriesTheLiveScoreWhenThereIsOne() throws {
        // Until live data has made a check the three score fields are on the wire as null (never missing).
        for name in ["state_game_created", "state_play_opened", "state_play_locked", "state_play_resolved",
                     "state_play_resolved_loss", "state_play_voided", "state_final"] {
            let wire = try XCTUnwrap(try object(fixture(name))["game"] as? [String: Any], name)
            for key in ["home_score", "away_score", "score_at"] {
                XCTAssertTrue(wire[key] is NSNull, "\(name) \(key)")
            }
            let game = try XCTUnwrap(try snapshot(name).game, name)
            XCTAssertNil(game.homeScore, name)
            XCTAssertNil(game.awayScore, name)
            XCTAssertNil(game.scoreAt, name)
            XCTAssertNil(game.liveScore, name)
        }
        // With a score (an open play whose game the live data has read: Detroit 24, Chicago 17, 20 seconds ago).
        let s = try snapshot("state_game_score")
        let game = try XCTUnwrap(s.game)
        XCTAssertEqual(game.homeScore, 24)
        XCTAssertEqual(game.awayScore, 17)
        XCTAssertEqual(game.liveScore?.home, 24)
        XCTAssertEqual(game.liveScore?.away, 17)
        XCTAssertEqual(game.scoreAge(now: s.serverTime), 20)
        XCTAssertEqual(game.awayName, "Chicago", "the rest of the game is unchanged")
        XCTAssertEqual(s.play?.state, .open)
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

// MARK: - Host console (admin socket and REST)

extension ContractTests {
    private func adminState(_ name: String) throws -> AdminState {
        guard case .state(let state) = try AdminServerMessage.decode(fixture(name)) else {
            XCTFail("\(name) is not an admin_state message")
            throw CancellationError()
        }
        return state
    }

    private func ack(_ name: String) throws -> AdminAck {
        guard case .ack(let ack) = try AdminServerMessage.decode(fixture(name)) else {
            XCTFail("\(name) is not an admin_ack message")
            throw CancellationError()
        }
        return ack
    }

    func testAdminFixturesNeverUseTheOldCenterName() throws {
        for name in ["admin_state_nogame", "admin_state_game_demo", "admin_state_play_open", "admin_state_suggestion",
                     "admin_state_suggestion_held", "admin_state_play_resolved", "admin_state_play_voided"] {
            XCTAssertFalse(String(decoding: try fixture(name), as: UTF8.self).contains("CENTER"), name)
        }
    }

    func testAdminNoGame() throws {
        let s = try adminState("admin_state_nogame")
        XCTAssertNil(s.game)
        XCTAssertNil(s.play)
        XCTAssertEqual(s.stage, .noGame)
        XCTAssertEqual(s.registeredPlayers, 2)
        XCTAssertEqual(s.windowSeconds, 15)
        XCTAssertTrue(s.history.isEmpty)
        XCTAssertNil(s.announcement)
        XCTAssertEqual(s.feed?.linked, false)
        XCTAssertEqual(s.feed?.state, "off")
        XCTAssertFalse(s.canEndGame)
    }

    func testAdminGameWithTheRecordedFeed() throws {
        let s = try adminState("admin_state_game_demo")
        XCTAssertEqual(s.game?.awayName, "Carolina")
        XCTAssertEqual(s.game?.homeName, "Washington")
        XCTAssertEqual(s.game?.status, .scheduled)
        XCTAssertEqual(s.stage, .ready)
        XCTAssertTrue(s.canEndGame)
        let feed = try XCTUnwrap(s.feed)
        XCTAssertTrue(feed.linked)
        XCTAssertTrue(feed.isRecorded)
        XCTAssertEqual(feed.gameId, "demo")
        XCTAssertEqual(feed.state, "idle")
        XCTAssertTrue(feed.autoScore)
        XCTAssertFalse(feed.autoOpen)
        XCTAssertEqual(feed.requests?.gameCap, 900)
        XCTAssertNil(feed.requests?.planRemaining)
        XCTAssertNil(feed.suggestion)
        XCTAssertNil(feed.waiting)
    }

    func testAdminPlayOpenCountsThePicks() throws {
        let s = try adminState("admin_state_play_open")
        XCTAssertEqual(s.stage, .open)
        XCTAssertEqual(s.play?.state, .open)
        XCTAssertTrue(s.hasActivePlay)
        XCTAssertFalse(s.canEndGame, "not while a play is open")
        XCTAssertEqual(s.pickStats?.total, 2)
        XCTAssertEqual(s.pickStats?.run, 1)
        XCTAssertEqual(s.pickStats?.pass, 1)
        XCTAssertEqual(s.history.first?.state, .open)
        XCTAssertEqual(s.history.first?.picks, 2)
    }

    func testAdminSuggestionIsReadyAndCountsDown() throws {
        let s = try adminState("admin_state_suggestion")
        XCTAssertEqual(s.stage, .locked)
        let sg = try XCTUnwrap(s.feed?.suggestion)
        XCTAssertEqual(sg.status, .ready)
        XCTAssertEqual(sg.badge, "FEED SUGGESTS")
        XCTAssertEqual(sg.playType, .pass)
        XCTAssertEqual(sg.direction, .right)
        XCTAssertEqual(sg.yardage, .medium)
        XCTAssertEqual(sg.yards, 7)
        XCTAssertEqual(sg.downAndDistance, "1st & 10 at CAR 14")
        XCTAssertEqual(sg.clock, "Q1 14:55")
        XCTAssertTrue(sg.text.contains("Dalton"))
        XCTAssertTrue(sg.missingParts.isEmpty)
        XCTAssertFalse(sg.isVoid)
        XCTAssertTrue(sg.countsDown)
        let left = try XCTUnwrap(sg.secondsUntilAuto(at: s.serverTime))
        XCTAssertGreaterThan(left, 0)
        XCTAssertLessThanOrEqual(left, 31)
        XCTAssertNil(sg.note(missing: [], paused: false), "a countdown speaks for itself")
        XCTAssertEqual(s.feed?.label(at: s.serverTime), "READY")
        XCTAssertEqual(s.feed?.tone(at: s.serverTime), .good)
    }

    func testAdminHeldSuggestionStopsCountingDown() throws {
        let sg = try XCTUnwrap(try adminState("admin_state_suggestion_held").feed?.suggestion)
        XCTAssertEqual(sg.status, .held)
        XCTAssertEqual(sg.badge, "ON HOLD")
        XCTAssertFalse(sg.countsDown)
        XCTAssertNil(sg.secondsUntilAuto(at: 0))
        XCTAssertEqual(sg.note(missing: [], paused: false), "On hold. Nothing is scored until you tap Score now.")
    }

    func testAdminScoredPlayShowsInTheLog() throws {
        let s = try adminState("admin_state_play_resolved")
        XCTAssertEqual(s.stage, .ready)
        let row = try XCTUnwrap(s.history.first)
        XCTAssertEqual(row.playNumber, 1)
        XCTAssertEqual(row.downAndDistance, "1st & 10")
        XCTAssertEqual(row.outcome?.summary, "Pass · Right · Medium (7 yds)")
        XCTAssertEqual(row.resultText, "Pass · Right · Medium (7 yds)")
        XCTAssertEqual(row.resolvedBy, "feed")
        XCTAssertTrue(row.feedText?.contains("Dalton") == true)
        XCTAssertTrue(row.isFixable)
        XCTAssertEqual(row.picks, 2)
        XCTAssertEqual(s.feed?.lastScored?.summary, "PASS - RIGHT - MEDIUM (7 yds)")
        XCTAssertEqual(s.feed?.lastScored?.by, "feed")
        XCTAssertEqual(s.feed?.lastScored?.auto, false)
        XCTAssertNil(s.feed?.suggestion)
    }

    func testAdminVoidedPlayIsStoredAsZeroOrOne() throws {
        let wire = String(decoding: try fixture("admin_state_play_voided"), as: UTF8.self)
        XCTAssertTrue(wire.contains("\"voided\": 1"), "the history rows really do carry SQLite's 0/1")
        let s = try adminState("admin_state_play_voided")
        XCTAssertEqual(s.history.count, 2)
        XCTAssertTrue(s.history[0].voided)
        XCTAssertFalse(s.history[1].voided)
        XCTAssertEqual(s.history[0].resultText, "VOID")
        XCTAssertFalse(s.history[0].isFixable)
        XCTAssertNil(s.history[0].outcome)
        XCTAssertEqual(s.history[0].resolvedBy, "void")
        XCTAssertEqual(s.stage, .ready)
    }

    func testAdminAnnouncementAndPause() throws {
        XCTAssertEqual(try adminState("admin_state_announcement").announcement?.text, "Halftime! Back in about 15 minutes.")
        let paused = try XCTUnwrap(try adminState("admin_state_feed_paused").feed)
        XCTAssertTrue(paused.paused)
        XCTAssertEqual(paused.state, "paused")
        XCTAssertEqual(paused.label(at: 0), "PAUSED")
        XCTAssertEqual(paused.tone(at: 0), .wait)
    }

    func testAdminAcksAndAuthError() throws {
        let ok = try ack("admin_ack_ok")
        XCTAssertTrue(ok.ok)
        XCTAssertEqual(ok.action, "create_game")
        XCTAssertNil(ok.error)
        XCTAssertEqual(try ack("admin_ack_announce").sentTo, 1)
        let bad = try ack("admin_ack_error")
        XCTAssertFalse(bad.ok)
        XCTAssertEqual(bad.error, "down: Input should be less than or equal to 4")
        XCTAssertTrue(try ack("admin_ack_feed_accept").ok)
        guard case .authError(let text) = try AdminServerMessage.decode(fixture("admin_auth_error")) else {
            return XCTFail("auth_error")
        }
        XCTAssertEqual(text, "Invalid admin key.")
        guard case .pong = try AdminServerMessage.decode(fixture("msg_pong")) else { return XCTFail("pong") }
    }

    func testAdminRestPayloads() throws {
        let games = try JSON.decoder.decode(FeedGames.self, from: fixture("rest_admin_feed_games"))
        XCTAssertTrue(games.games.isEmpty)
        XCTAssertEqual(games.available, false)
        XCTAssertNotNil(games.error)
        let demo = try XCTUnwrap(games.demo)
        XCTAssertTrue(demo.isRecorded)
        XCTAssertEqual(demo.away.name, "Carolina")
        XCTAssertEqual(demo.home.abbr, "WSH")
        XCTAssertTrue(demo.label.hasPrefix("CAR at WSH"))
        XCTAssertEqual(TeamChoice(demo.home), TeamChoice(name: "Washington", primary: "#5a1414", secondary: "#ffb612"))

        let players = try JSON.decoder.decode(HostPlayers.self, from: fixture("rest_admin_players"))
        XCTAssertEqual(players.count, 2)
        XCTAssertEqual(players.players.map(\.username), ["Sam", "JoeyC"], "newest first")
        XCTAssertEqual(players.players.first?.online, false)
        XCTAssertEqual(players.players.first?.summary, "0 picks · 0 game pts · 0 season")
        XCTAssertTrue(players.blockedNames.isEmpty)
    }

    /// The console must keep working when a small field changes type, and must not mistake a broken game for none.
    func testAdminStateIsLenientExceptForTheGameAndPlay() throws {
        let json = """
        {"type": "admin_state", "event": "stats", "server_time": 100.5,
         "game": {"id": 1, "home_name": "Dallas", "home_primary": "#003594", "home_secondary": "#869397",
                  "away_name": "Tampa Bay", "away_primary": "#D50A0A", "away_secondary": "#FF7900", "status": "LIVE"},
         "play": {"id": 7, "game_id": 1, "play_number": 7, "down": 3, "distance": "7", "state": "LOCKED",
                  "voided": false, "opened_at": 1, "locks_at": 16},
         "pick_stats": "oops", "players_online": 3, "spectators_online": 1, "admins_online": 1,
         "leaderboard": "nope", "ranked_players": 0,
         "history": [{"id": 7, "play_number": 7, "down": 3, "distance": 7, "state": "LOCKED", "voided": false,
                      "picks": 2, "exact_hits": 0},
                     {"id": "bad row"}],
         "window_seconds": 15,
         "feed": {"state": "waiting", "available": true, "linked": true, "source": "tank01", "game_id": "20261008_TB@DAL",
                  "message": "Waiting for the result of play #7 (check 2).", "paused": false, "auto_score": true,
                  "auto_open": false, "requests": {"game": 12, "game_cap": 500, "plan_remaining": 900},
                  "waiting": {"play_id": 7, "checks": 1, "since": 90.0, "next_check_at": 105.0},
                  "suggestion": {"play_id": 7, "status": "review", "text": "T.Brady sacked at DAL 20 for -8 yards",
                                 "play_type": null, "direction": null, "yards": -8, "yardage": "LOSS",
                                 "flags": ["sack"], "warning": "Feed shows 2nd & 3 but this play is 3rd & 7",
                                 "auto_at": null, "kind": "review"},
                  "next_down": {"down": 4, "distance": 2}}}
        """
        guard case .state(let s) = try AdminServerMessage.decode(Data(json.utf8)) else { return XCTFail("state") }
        XCTAssertEqual(s.stage, .locked)
        XCTAssertNil(s.pickStats, "an unreadable part is left out")
        XCTAssertTrue(s.leaderboard.isEmpty)
        XCTAssertEqual(s.history.count, 1, "one bad row doesn't empty the log")
        XCTAssertEqual(s.history.first?.distance, "7", "a number where text was expected is read as text")
        XCTAssertEqual(s.playersOnline, 3)
        let feed = try XCTUnwrap(s.feed)
        XCTAssertEqual(feed.nextDown, NextDown(down: 4, distance: "2"))
        XCTAssertEqual(feed.requests?.percentUsed, 2)
        XCTAssertEqual(feed.requests?.summary, "Requests this game 12 / 500 · plan has 900 left")
        XCTAssertFalse(feed.isQuiet(at: 100))
        XCTAssertTrue(feed.isQuiet(at: 300), "waiting more than three minutes")
        XCTAssertEqual(feed.label(at: 300), "QUIET")
        let sg = try XCTUnwrap(feed.suggestion)
        XCTAssertEqual(sg.status, .review)
        XCTAssertEqual(sg.yardage, .loss)
        XCTAssertEqual(sg.missingParts, [.playType, .direction])
        XCTAssertEqual(sg.flags, ["sack"])
        XCTAssertEqual(sg.note(missing: sg.missingParts, paused: false), "Pick the play type and direction above, then Score.")
        XCTAssertEqual(sg.note(missing: [.direction], paused: false), "Pick the direction above, then Score.")

        let brokenGame = json.replacingOccurrences(of: "\"status\": \"LIVE\"", with: "\"status\": \"SOMETHING_NEW\"")
        XCTAssertThrowsError(try AdminServerMessage.decode(Data(brokenGame.utf8)), "never show a broken game as 'no game'")
    }
}
