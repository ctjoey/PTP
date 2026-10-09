import XCTest
@testable import PickThePlay

/// The player screens' own words (the points table, the bonus line) and the live score beside each team.
final class PlayerScreenTests: XCTestCase {
    // MARK: - Live score on the game

    private func game(_ extra: String = "") throws -> Game {
        // (##: the colours' "#" would end a plain #"..."# string.)
        let json = ##"{"id": 1, "home_name": "Detroit", "home_primary": "#0076B6", "home_secondary": "#B0B7BC", "away_name": "Chicago", "away_primary": "#0B162A", "away_secondary": "#C83803", "status": "LIVE"\##(extra)}"##
        return try JSON.decoder.decode(Game.self, from: Data(json.utf8))
    }

    func testAGameWithoutAScoreStillDecodes() throws {
        // A server from before the score, and one that has no check to report yet (nulls).
        for extra in ["", #", "home_score": null, "away_score": null, "score_at": null"#] {
            let g = try game(extra)
            XCTAssertEqual(g.homeName, "Detroit")
            XCTAssertEqual(g.status, .live)
            XCTAssertNil(g.homeScore)
            XCTAssertNil(g.awayScore)
            XCTAssertNil(g.scoreAt)
            XCTAssertNil(g.liveScore, "nothing to draw beside the teams")
        }
    }

    func testTheScoreIsReadAsSent() throws {
        let g = try game(#", "home_score": 24, "away_score": 17, "score_at": 1791476820.5"#)
        XCTAssertEqual(g.homeScore, 24)
        XCTAssertEqual(g.awayScore, 17)
        XCTAssertEqual(g.scoreAt, 1791476820.5)
        XCTAssertEqual(g.liveScore?.home, 24)
        XCTAssertEqual(g.liveScore?.away, 17)
        // 0-0 is a score too.
        XCTAssertEqual(try game(#", "home_score": 0, "away_score": 0, "score_at": 5"#).liveScore?.home, 0)
        // A JSON writer that prints whole numbers with a decimal point.
        XCTAssertEqual(try game(#", "home_score": 21.0, "away_score": 3.0"#).liveScore?.home, 21)
    }

    func testOddScoreValuesNeverBreakTheGame() throws {
        let odd = [
            #", "home_score": "24", "away_score": true, "score_at": "now""#,   // wrong types
            #", "home_score": 21.5, "away_score": -3, "score_at": -1"#,         // not whole, negative
            #", "home_score": 5000, "away_score": 1e300, "score_at": 0"#,      // absurd, and no time to speak of
            #", "home_score": {"a": 1}, "away_score": [], "score_at": {}"#,     // containers
        ]
        for extra in odd {
            let g = try game(extra)
            XCTAssertEqual(g.awayName, "Chicago", extra)
            XCTAssertNil(g.liveScore, extra)
            XCTAssertNil(g.homeScore, extra)
            XCTAssertNil(g.awayScore, extra)
            XCTAssertNil(g.scoreAge(now: 1_791_476_900), extra)
        }
    }

    func testOneSidedScoreIsHiddenAndTheOtherFieldsSurvive() throws {
        // One team's number alone would mislead: nothing is drawn, but the good value is kept.
        let g = try game(#", "home_score": 14, "away_score": "x", "score_at": 100"#)
        XCTAssertEqual(g.homeScore, 14)
        XCTAssertNil(g.awayScore)
        XCTAssertNil(g.liveScore)
        XCTAssertEqual(g.scoreAt, 100)
        // The in-memory model never lets a bad value through either.
        var direct = g
        direct.homeScore = -5
        direct.awayScore = 7
        XCTAssertNil(direct.liveScore)
    }

    func testHowOldTheScoreIs() throws {
        let g = try game(#", "home_score": 21, "away_score": 17, "score_at": 1000"#)
        XCTAssertEqual(g.scoreAge(now: 1040), 40)
        XCTAssertEqual(g.scoreAge(now: 1000.4), 0)
        XCTAssertEqual(g.scoreAge(now: 990), 0, "a clock a little behind the server's never reads as negative")
        XCTAssertEqual(g.scoreAge(now: 1000 + 10 * 86_400), 86_400, "capped at a day")
        XCTAssertNil(g.scoreAge(now: .nan))
        XCTAssertNil(try game(#", "home_score": 21, "away_score": 17"#).scoreAge(now: 1040), "no time, no age")
        XCTAssertNil(try game(#", "score_at": 1000"#).scoreAge(now: 1040), "no score, no age")
        var g2 = g
        g2.scoreAt = .infinity
        XCTAssertNil(g2.scoreAge(now: 1040))
    }

    func testAGameWithAScoreRoundTrips() throws {
        let g = try game(#", "home_score": 24, "away_score": 17, "score_at": 1791476820.5"#)
        let wire = try XCTUnwrap(JSONSerialization.jsonObject(with: JSON.encoder.encode(g)) as? [String: Any])
        XCTAssertEqual(wire["home_score"] as? Int, 24)
        XCTAssertEqual(wire["away_score"] as? Int, 17)
        XCTAssertEqual(try JSON.decoder.decode(Game.self, from: JSON.encoder.encode(g)), g)
        // Built by hand (previews, screenshots, tests), the score is optional.
        let plain = Game(id: 2, homeName: "Dallas", homePrimary: "#003594", homeSecondary: "#869397",
                         awayName: "Tampa Bay", awayPrimary: "#D50A0A", awaySecondary: "#FF7900", status: .scheduled)
        XCTAssertNil(plain.liveScore)
        XCTAssertNotNil(ScreenshotMode.sample(for: .open).snapshot.game?.liveScore, "the store screenshots show the score")
    }

    // MARK: - The points table's words

    func testPointsTableRowsUseThePickWording() {
        let rows = Scoring.standard.rows
        XCTAssertEqual(rows.map(\.title), ["Pick correct play", "Pick correct direction", "Pick correct distance",
                                           "Pick all 3 correctly"])
        XCTAssertEqual(rows.map(\.detail), ["Run / Pass", "Left / Middle / Right", "Short / Medium / Long", nil])
        XCTAssertEqual(rows.map(\.points), [10, 10, 10, 10])
        XCTAssertEqual(rows.map(\.isBonus), [false, false, false, true], "only the last line is the gold bonus")
        // The rows follow whatever scoring the server sends.
        let custom = Scoring(type: 5, direction: 6, yardage: 7, bonus: 22, exact: 40)
        XCTAssertEqual(custom.rows.map(\.points), [5, 6, 7, 22])
    }

    func testThePerfectCallNote() {
        XCTAssertEqual(ScoreRules.perfectNote, "You picked the play")
        XCTAssertEqual(ScoreRules.label(correctParts: 3), "Perfect call!", "the headline stays; the note rides under it")
    }

    func testBonusLine() {
        XCTAssertEqual(Scoring.standard.bonusLine, "Pick all 3 correctly: +10 bonus = 40")
        XCTAssertEqual(Scoring.standard.bonusSpoken, "Pick all 3 correctly adds a 10 point bonus, 40 in all")
        // A server from before the bonus: all three = 30.
        let old = Scoring(type: 10, direction: 10, yardage: 10, bonus: 0, exact: 30)
        XCTAssertEqual(old.bonusLine, "Pick all 3 correctly = 30")
        XCTAssertEqual(old.bonusSpoken, "Pick all 3 correctly scores 30 points")
        XCTAssertEqual(ScoreRules.summary(),
                       "+10 pick correct play, +10 pick correct direction, +10 pick correct distance, +10 pick all 3 correctly = 40.")
    }

    func testTheOldWordsAreGone() {
        let rows = Scoring.standard.rows
        let all = rows.flatMap { [$0.title, $0.detail ?? ""] }
            + [Scoring.standard.bonusLine, Scoring.standard.bonusSpoken, ScoreRules.summary(), ScoreRules.perfectNote]
        for old in ["play type right", "direction right", "distance right", "all three right", "bonus: all", "bonus for all three"] {
            for text in all {
                XCTAssertFalse(text.lowercased().contains(old), "“\(text)” still says “\(old)”")
            }
        }
    }
}
