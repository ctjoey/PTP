import XCTest
@testable import PickThePlay

final class GameLogicTests: XCTestCase {
    func testScoringMatchesServerRules() {
        for type in PlayType.allCases {
            for direction in Direction.allCases {
                for actualType in PlayType.allCases {
                    for actualDirection in Direction.allCases {
                        let r = ScoreRules.score(type: type, direction: direction, actualType: actualType,
                                                 actualDirection: actualDirection)
                        let typeOK = type == actualType
                        let dirOK = direction == actualDirection
                        XCTAssertEqual(r.points, typeOK && dirOK ? 30 : (typeOK || dirOK ? 10 : 0))
                        XCTAssertEqual(r.exact, typeOK && dirOK)
                    }
                }
            }
        }
    }

    @MainActor
    func testPracticeRoundScoresPicks() {
        let game = PracticeGame()
        game.outcome = { (.pass, .left) }
        game.nextPlay()
        game.pickType = .pass
        game.pickDirection = .left
        game.resolve()
        XCTAssertEqual(game.score, 30)
        XCTAssertEqual(game.exactHits, 1)
        guard case .result(.pass, .left, let outcome) = game.phase else { return XCTFail("expected a result") }
        XCTAssertEqual(outcome?.points, 30)

        game.nextPlay()
        game.pickType = .run
        game.pickDirection = .left
        game.resolve()
        XCTAssertEqual(game.score, 40)
        XCTAssertEqual(game.played, 2)

        game.nextPlay()  // no pick: no points, doesn't count as played
        game.resolve()
        XCTAssertEqual(game.played, 2)
        game.stop()
    }

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
        XCTAssertEqual(Football.downAndDistance(down: 4, distance: "Goal"), "4th & Goal")
        XCTAssertNil(Football.downAndDistance(down: nil, distance: "10"))
    }
}
