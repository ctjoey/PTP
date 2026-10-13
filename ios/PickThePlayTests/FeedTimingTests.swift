import XCTest
@testable import PickThePlay

/// The Tank01 vs ESPN timing log in the host console's "Settings and details": `admin_state.feed.timing` decodes
/// without ever breaking the update, and the verdict line and the cells say the same as the website's console.
/// The admin_state_timing*.json fixtures are hand-written to the server's contract for `feed.timing`: ESPN ahead
/// (with ESPN's time of each play), no time of the play (wallclock missing), `"timing": null`, and odd values.
final class FeedTimingTests: XCTestCase {
    // MARK: - Fixtures

    private func fixtureData(_ name: String) throws -> Data {
        #if SWIFT_PACKAGE
        let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        #else
        let url = Bundle(for: FeedTimingTests.self).url(forResource: name, withExtension: "json")
        #endif
        return try Data(contentsOf: try XCTUnwrap(url, "missing fixture \(name).json"))
    }

    private func decode(_ data: Data) throws -> AdminState {
        guard case .state(let state) = try AdminServerMessage.decode(data) else {
            XCTFail("not an admin_state message")
            throw CancellationError()
        }
        return state
    }

    private func adminState(_ name: String) throws -> AdminState {
        try decode(fixtureData(name))
    }

    private func timing(_ name: String) throws -> FeedTiming {
        try XCTUnwrap(try adminState(name).feed?.timing, "\(name) has no feed.timing")
    }

    /// The ESPN-ahead fixture with `feed.timing` replaced, to reach shapes the server should never send.
    private func withTiming(_ value: Any) throws -> AdminState {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData("admin_state_timing")) as? [String: Any])
        var feed = try XCTUnwrap(object["feed"] as? [String: Any])
        feed["timing"] = value
        object["feed"] = feed
        return try decode(JSONSerialization.data(withJSONObject: object))
    }

    private func row(tank01: Double? = nil, espn: Double? = nil, gap: Double? = nil, lead: String? = nil,
                     fair: Bool = true) -> FeedTimingRow {
        FeedTimingRow(key: "2|4:20|1|10", label: "Q2 4:20 · 1st & 10", tank01: tank01, espn: espn, gap: gap,
                      leadText: lead, fair: fair)
    }

    private func texts(_ row: FeedTimingRow) -> [String] {
        [row.cell(.tank01).text, row.cell(.espn).text]
    }

    // MARK: - Decoding

    func testEveryTimingFixtureDecodesAsAWholeUpdate() throws {
        for name in ["admin_state_timing", "admin_state_timing_no_wallclock", "admin_state_timing_null",
                     "admin_state_timing_odd"] {
            let state = try adminState(name)
            XCTAssertEqual(state.game?.awayName, "Buffalo", name)
            XCTAssertEqual(state.play?.state, .resolved, name)
            XCTAssertEqual(state.history.count, 1, name)
            let feed = try XCTUnwrap(state.feed, name)
            XCTAssertEqual(feed.source, "tank01", name)
            XCTAssertEqual(feed.gameId, "20261012_BUF@LAR", name)
            XCTAssertEqual(feed.state, "idle", name)
            XCTAssertEqual(feed.requests?.game, 214, name)
            XCTAssertEqual(feed.nextDown, NextDown(down: 2, distance: "6"), name)
            XCTAssertEqual(feed.timing == nil, name == "admin_state_timing_null", name)
        }
    }

    func testNoTimingWhenTheServerSendsNullOrNothing() throws {
        XCTAssertNil(try adminState("admin_state_timing_null").feed?.timing)
        // Captured before the comparison existed: the key is missing, and everything else still arrives.
        for name in ["admin_state_feed_paused", "admin_state_locked_not_started", "admin_state_suggestion",
                     "admin_state_play_resolved"] {
            let feed = try XCTUnwrap(try adminState(name).feed, name)
            XCTAssertNil(feed.timing, name)
        }
    }

    func testESPNAhead() throws {
        let t = try timing("admin_state_timing")
        XCTAssertEqual(t.espn.phase, .on)
        XCTAssertEqual(t.espn.text, "Comparing with ESPN. It is free and uses none of your Tank01 requests.")
        XCTAssertEqual(t.rows.count, 11)
        XCTAssertEqual(t.summary, FeedTimingSummary(plays: 9, espnFirst: 7, tank01First: 1, ties: 1, avgGap: 31.4,
                                                    avgTank01: 61.2, avgEspn: 24.8))
        XCTAssertEqual(t.summary.verdict, "ESPN was first on 7 of 9 plays, about 31 s sooner on average.")

        // Newest game time first, and only the newest 8 are listed.
        XCTAssertEqual(t.recentRows.count, 8)
        XCTAssertEqual(t.recentRows.map(\.label), [
            "Q2 3:42 · 2nd & 6", "Q2 4:20 · 1st & 10", "Q2 4:51 · 3rd & 2", "Q2 5:30 · 2nd & 7",
            "Q2 6:02 · 1st & 10", "Q2 7:15 · 3rd & 8", "Q2 7:48 · 2nd & 3", "Q2 8:20 · 1st & 10",
        ])

        let newest = t.rows[0]
        XCTAssertEqual(newest.key, "2|3:42|2|6")
        XCTAssertEqual(texts(newest), ["-", "9 s"], "Tank01 hasn't shown it yet")
        XCTAssertNil(newest.lead)
        XCTAssertNil(newest.sooner)

        XCTAssertEqual(t.rows[1], FeedTimingRow(key: "2|4:20|1|10", label: "Q2 4:20 · 1st & 10", tank01: 58, espn: 22,
                                                gap: 36, leadText: "espn", fair: true))
        XCTAssertEqual(texts(t.rows[1]), ["58 s", "22 s"])
        XCTAssertEqual(t.rows[1].sooner, .espn)

        let unfair = t.rows[5]
        XCTAssertFalse(unfair.fair, "shown, but left out of the summary")
        XCTAssertEqual(texts(unfair), ["95 s", "20 s"])
        XCTAssertEqual(unfair.sooner, .espn)

        // No time of the play from ESPN on this one: relative cells, Tank01 sooner.
        XCTAssertEqual(texts(t.rows[7]), ["first", "+9 s"])
        XCTAssertEqual(t.rows[7].sooner, .tank01)

        // A tie (1.2 s apart): both delays, nothing lit.
        XCTAssertEqual(t.rows[8].lead, .tie)
        XCTAssertEqual(texts(t.rows[8]), ["41 s", "39 s"])
        XCTAssertNil(t.rows[8].sooner)
    }

    func testWithoutTheTimeOfThePlayTheCellsSayWhoWasFirst() throws {
        let t = try timing("admin_state_timing_no_wallclock")
        XCTAssertEqual(t.rows.count, 5)
        XCTAssertEqual(t.rows.map(texts), [
            ["-", "-"],          // neither feed's delay is known and there is no gap yet
            ["+36 s", "first"],  // ESPN 36 s sooner
            ["first", "+12 s"],  // Tank01 12 s sooner
            ["same", "same"],    // 0.4 s apart
            ["+3 s", "first"],   // 2.6 s apart: inside the tie band, so nothing is lit
        ])
        XCTAssertEqual(t.rows.map(\.sooner), [nil, .espn, .tank01, nil, nil])
        XCTAssertNil(t.summary.avgTank01)
        XCTAssertNil(t.summary.avgEspn)
        // Each feed was first on one play: about the same, whatever the average says (the website's rule).
        XCTAssertEqual(t.summary.verdict, "Both feeds were about the same on 4 plays.")
    }

    func testOddValuesNeverBreakTheUpdate() throws {
        let t = try timing("admin_state_timing_odd")
        // An unknown state with no words: the comparison is running, it just isn't comparing yet.
        XCTAssertEqual(t.espn.stateText, "warming_up")
        XCTAssertEqual(t.espn.phase, .looking)
        XCTAssertEqual(t.espn.text, "Looking for this game on ESPN...")

        // The row with neither a key nor a label and the plain string are left out; the rest are kept.
        XCTAssertEqual(t.rows.map(\.label), ["Q1 14:20 · 1st & 10", "1|13:45|2|4", "Q1 12:10 · 3rd & 2",
                                             "Q1 11:00 · 1st & 10"])

        // Int and String numbers, and 1 for true.
        XCTAssertEqual(t.rows[0].tank01, 61)
        XCTAssertEqual(t.rows[0].espn, 19)
        XCTAssertEqual(t.rows[0].gap, 42)
        XCTAssertTrue(t.rows[0].fair)
        XCTAssertEqual(texts(t.rows[0]), ["61 s", "19 s"])
        XCTAssertEqual(t.rows[0].sooner, .espn)

        // No label: the key names it. An unknown lead is worked out from the gap. "yes" isn't a flag.
        XCTAssertEqual(t.rows[1].gap, -7.5)
        XCTAssertEqual(t.rows[1].lead, .tank01)
        XCTAssertFalse(t.rows[1].fair)
        XCTAssertEqual(texts(t.rows[1]), ["first", "+8 s"])

        // A number for the key; a flag, a list and an object where seconds belong.
        XCTAssertEqual(t.rows[2].key, "7")
        XCTAssertNil(t.rows[2].tank01)
        XCTAssertNil(t.rows[2].espn)
        XCTAssertNil(t.rows[2].gap)
        XCTAssertNil(t.rows[2].lead)
        XCTAssertEqual(texts(t.rows[2]), ["-", "-"])

        // A negative delay (a wrong time of the play) and an absurd one stay on screen without trapping.
        XCTAssertEqual(texts(t.rows[3]), ["0 s", "604800 s"])

        // 3.0 and 2.0 as counts, a missing and a negative count, the average gap as text.
        XCTAssertEqual(t.summary, FeedTimingSummary(plays: 3, espnFirst: 2, tank01First: 0, ties: 0, avgGap: 12.5,
                                                    avgTank01: nil, avgEspn: nil))
        XCTAssertEqual(t.summary.verdict, "ESPN was first on 2 of 3 plays, about 13 s sooner on average.")
    }

    func testTimingOfTheWrongShapeIsLeftOut() throws {
        let wrongShapes: [Any] = ["soon", [1, 2], 42, true]
        for value in wrongShapes {
            let state = try withTiming(value)
            XCTAssertNil(state.feed?.timing, "\(value)")
            XCTAssertEqual(state.feed?.state, "idle", "the rest of the feed still arrives")
            XCTAssertEqual(state.game?.homeName, "Los Angeles")
        }

        let empty = try XCTUnwrap(try withTiming([String: Any]()).feed?.timing)
        XCTAssertEqual(empty.espn.phase, .looking)
        XCTAssertTrue(empty.rows.isEmpty)
        XCTAssertEqual(empty.summary, FeedTimingSummary())
        XCTAssertNil(empty.summary.verdict)

        let odd = try XCTUnwrap(try withTiming(["espn": "on", "rows": "none", "summary": [1]] as [String: Any]).feed?.timing)
        XCTAssertEqual(odd.espn.text, "Looking for this game on ESPN...")
        XCTAssertTrue(odd.rows.isEmpty)
        XCTAssertNil(odd.summary.verdict)

        let rowsNull = try XCTUnwrap(try withTiming(["espn": ["state": "error"], "rows": NSNull(),
                                                     "summary": NSNull()] as [String: Any]).feed?.timing)
        XCTAssertEqual(rowsNull.espn.phase, .error)
        XCTAssertTrue(rowsNull.rows.isEmpty)
        XCTAssertEqual(rowsNull.summary, FeedTimingSummary())
    }

    // MARK: - The status line

    func testStatusLineIsTheServersSentenceOrTheSameWords() {
        XCTAssertEqual(FeedTimingStatus(stateText: "looking", message: nil).text, "Looking for this game on ESPN...")
        XCTAssertEqual(FeedTimingStatus(stateText: "on", message: nil).text,
                       "Comparing with ESPN. It is free and uses none of your Tank01 requests.")
        XCTAssertEqual(FeedTimingStatus(stateText: "error", message: "  ").text,
                       "ESPN isn't answering right now. Tank01 still runs the game.")
        XCTAssertEqual(FeedTimingStatus(stateText: "off", message: "").text, "The ESPN comparison is off.")
        XCTAssertEqual(FeedTimingStatus(stateText: "error", message: "ESPN is slow tonight.").text, "ESPN is slow tonight.")
    }

    // MARK: - The verdict line

    func testVerdictESPNFirst() {
        let summary = FeedTimingSummary(plays: 9, espnFirst: 7, tank01First: 1, ties: 1, avgGap: 31.4)
        XCTAssertEqual(summary.verdict, "ESPN was first on 7 of 9 plays, about 31 s sooner on average.")
        XCTAssertEqual(FeedTimingSummary(plays: 1, espnFirst: 1, avgGap: 30).verdict,
                       "ESPN was first on 1 of 1 play, about 30 s sooner on average.")
    }

    func testVerdictTank01First() {
        let summary = FeedTimingSummary(plays: 5, espnFirst: 1, tank01First: 4, ties: 0, avgGap: -12.2)
        XCTAssertEqual(summary.verdict, "Tank01 was first on 4 of 5 plays, about 12 s sooner on average.")
    }

    func testVerdictAboutTheSame() {
        XCTAssertEqual(FeedTimingSummary(plays: 9, espnFirst: 4, tank01First: 3, ties: 2, avgGap: 1.5).verdict,
                       "ESPN was first on 4 of 9 plays, about 2 s sooner on average.")
        XCTAssertEqual(FeedTimingSummary(plays: 1, ties: 1, avgGap: 0.5).verdict,
                       "Both feeds were about the same on 1 play.")
        // The tie band is 3 s either way, as for a single play.
        XCTAssertEqual(FeedTimingSummary(plays: 4, espnFirst: 2, ties: 2, avgGap: 3.0).verdict,
                       "ESPN was first on 2 of 4 plays, about 3 s sooner on average.")
        XCTAssertEqual(FeedTimingSummary(plays: 4, tank01First: 2, ties: 2, avgGap: -3.0).verdict,
                       "Tank01 was first on 2 of 4 plays, about 3 s sooner on average.")
        // The average is left out when it points the other way.
        XCTAssertEqual(FeedTimingSummary(plays: 3, espnFirst: 2, tank01First: 1, avgGap: -4).verdict,
                       "ESPN was first on 2 of 3 plays.")
        // Ahead on average but never first (odd numbers): no claim either way.
        XCTAssertEqual(FeedTimingSummary(plays: 2, ties: 2, avgGap: 5).verdict,
                       "Both feeds were about the same on 2 plays.")
    }

    func testVerdictWithoutAnAverageCountsThePlays() {
        XCTAssertEqual(FeedTimingSummary(plays: 3, espnFirst: 2, tank01First: 1).verdict, "ESPN was first on 2 of 3 plays.")
        XCTAssertEqual(FeedTimingSummary(plays: 3, espnFirst: 1, tank01First: 1, ties: 1).verdict,
                       "Both feeds were about the same on 3 plays.")
    }

    func testNoVerdictBeforeAnyPlayIsCompared() {
        XCTAssertNil(FeedTimingSummary().verdict)
        XCTAssertNil(FeedTimingSummary(plays: 0, espnFirst: 2, avgGap: 30).verdict)
    }

    // MARK: - The cells

    func testCellShowsTheDelayWhenKnown() {
        let r = row(tank01: 58, espn: 22, gap: 36, lead: "espn")
        XCTAssertEqual(r.cell(.tank01), .delay(58))
        XCTAssertEqual(texts(r), ["58 s", "22 s"])
        // Whole seconds, to the nearest.
        XCTAssertEqual(texts(row(tank01: 57.5, espn: 0.4, gap: 57.1, lead: "espn")), ["58 s", "0 s"])
    }

    func testCellWithoutTheTimeOfThePlaySaysFirstAndHowFarBehind() {
        let espnSooner = row(gap: 36, lead: "espn")
        XCTAssertEqual(espnSooner.cell(.espn), .first)
        XCTAssertEqual(espnSooner.cell(.tank01), .behind(36))
        XCTAssertEqual(texts(espnSooner), ["+36 s", "first"])

        XCTAssertEqual(texts(row(gap: -20, lead: "tank01")), ["first", "+20 s"])
        XCTAssertEqual(texts(row(gap: 0.3, lead: "tie")), ["same", "same"])
        // The server named the sooner feed but sent no gap.
        XCTAssertEqual(texts(row(lead: "espn")), ["later", "first"])
        XCTAssertEqual(texts(row(lead: "tie")), ["same", "same"])
    }

    func testCellIsADashUntilTheFeedShowsThePlay() {
        XCTAssertEqual(texts(row(tank01: 58)), ["58 s", "-"])
        XCTAssertEqual(texts(row(espn: 9)), ["-", "9 s"])
        XCTAssertEqual(texts(row()), ["-", "-"])
        XCTAssertEqual(row().cell(.espn), .notShown)
    }

    func testTheSoonerFeedIsHighlighted() {
        XCTAssertEqual(row(tank01: 58, espn: 22, gap: 36, lead: "espn").sooner, .espn)
        XCTAssertEqual(row(gap: -20, lead: "tank01").sooner, .tank01)
        XCTAssertNil(row(tank01: 41, espn: 39, gap: 2, lead: "tie").sooner, "a tie lights neither")
        XCTAssertNil(row(tank01: 58).sooner, "one feed hasn't shown it yet")
        // No lead sent (or an unknown one): worked out from the gap, with the same 3 s tie band.
        XCTAssertEqual(row(gap: 3.5).sooner, .espn)
        XCTAssertEqual(row(gap: -3.5, lead: "sideways").sooner, .tank01)
        XCTAssertNil(row(gap: 3).sooner)
    }

    func testVoiceOverReadsTheWholeRow() {
        XCTAssertEqual(row(tank01: 58, espn: 22, gap: 36, lead: "espn").spoken,
                       "Q2 4:20, 1st & 10. Tank01: 58 seconds. ESPN: 22 seconds. ESPN was sooner.")
        XCTAssertEqual(row(gap: -1, lead: "tie", fair: false).spoken,
                       "Q2 4:20, 1st & 10. Tank01: first. ESPN: 1 second later. "
                       + "Not counted: a feed wasn't being checked when this play happened.")
        XCTAssertEqual(row(tank01: 58).spoken, "Q2 4:20, 1st & 10. Tank01: 58 seconds. ESPN: not shown yet.")
    }
}
