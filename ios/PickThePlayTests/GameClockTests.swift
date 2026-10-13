import XCTest
@testable import PickThePlay

/// The game clock bar under the host console's scorebug: `feed.clock` decodes leniently and reads like the website.
final class GameClockTests: XCTestCase {
    private func decode(_ json: String) throws -> FeedClock {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(FeedClock.self, from: Data(json.utf8))
    }

    func testDecodesTheServerShape() throws {
        let c = try decode(#"{"feed": "Q3 3:42", "live": "Q3 2:58", "feed_at": 1800000100.5, "app": "Q3 3:42", "behind": 0}"#)
        XCTAssertEqual(c, FeedClock(feed: "Q3 3:42", live: "Q3 2:58", feedAt: 1800000100.5, app: "Q3 3:42", behind: 0))
        XCTAssertTrue(c.hasAnything)
    }

    func testOddValuesNeverBreakIt() throws {
        let c = try decode(#"{"feed": "", "live": null, "feed_at": "soon", "app": 7, "behind": -2}"#)
        XCTAssertNil(c.feed)
        XCTAssertNil(c.live)
        XCTAssertNil(c.feedAt)
        XCTAssertEqual(c.app, "7")
        XCTAssertEqual(c.behind, 0)
        XCTAssertFalse(try decode("{}").hasAnything)
    }

    func testStatusLineMatchesTheWebsite() {
        XCTAssertEqual(FeedClock(behind: 3).statusLine(lockedPlayNumber: 22),
                       "3 plays ahead of the app: press Check now to catch up")
        XCTAssertEqual(FeedClock(behind: 1).statusLine(lockedPlayNumber: nil), "1 play ahead of the app: press Check now to catch up")
        XCTAssertEqual(FeedClock().statusLine(lockedPlayNumber: 22),
                       "Play 22 is waiting for the feed to publish it. Nothing newer yet, so Check now cannot help.")
        XCTAssertEqual(FeedClock().statusLine(lockedPlayNumber: nil), "In step with the feed")
        XCTAssertTrue(FeedClock(behind: 1).needsAttention(lockedPlayNumber: nil))
        XCTAssertFalse(FeedClock().needsAttention(lockedPlayNumber: nil))
    }

    func testAgeText() {
        let c = FeedClock(feedAt: 1000)
        XCTAssertEqual(c.ageText(now: 1012.4), "as of 12 s ago")
        XCTAssertEqual(c.ageText(now: 1150), "as of 3 min ago (it only checks while a play is locked)")
        XCTAssertNil(FeedClock().ageText(now: 1000))
    }
}
