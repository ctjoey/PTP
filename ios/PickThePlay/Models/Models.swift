import Foundation

// Wire models for the Pick the Play server. Field names follow the server's snake_case JSON and are
// mapped by `JSON.decoder` (convertFromSnakeCase). The test fixtures in PickThePlayTests/Fixtures
// are real server messages, so a mismatch here fails the unit tests rather than a live game.

enum GameStatus: String, Codable {
    case scheduled = "SCHEDULED"
    case live = "LIVE"
    case final = "FINAL"
}

enum PlayState: String, Codable {
    case open = "OPEN"
    case locked = "LOCKED"
    case resolved = "RESOLVED"
}

enum PlayType: String, Codable, CaseIterable, Identifiable {
    case run = "RUN"
    case pass = "PASS"
    var id: String { rawValue }
    var caption: String { self == .run ? "On the ground" : "Through the air" }
}

enum Direction: String, Codable, CaseIterable, Identifiable {
    case left = "LEFT"
    case center = "CENTER"
    case right = "RIGHT"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .left: return "arrow.left"
        case .center: return "arrow.up"
        case .right: return "arrow.right"
        }
    }
}

struct Game: Codable, Equatable {
    var id: Int
    var homeName: String
    var homePrimary: String
    var homeSecondary: String
    var awayName: String
    var awayPrimary: String
    var awaySecondary: String
    var status: GameStatus
}

struct Play: Codable, Equatable {
    var id: Int
    var gameId: Int
    var playNumber: Int
    var down: Int?
    var distance: String?
    var state: PlayState
    var voided: Bool
    var openedAt: Double
    var locksAt: Double
    var correctPlayType: PlayType?
    var correctDirection: Direction?

    /// "Play 3 · 3rd & 7"
    var label: String {
        var parts = ["Play \(playNumber)"]
        if let dd = Football.downAndDistance(down: down, distance: distance) { parts.append(dd) }
        return parts.joined(separator: " · ")
    }
}

struct Prediction: Codable, Equatable {
    var userId: Int?
    var playId: Int
    var playType: PlayType
    var direction: Direction
    var pointsEarned: Int?
    var typeCorrect: Bool?
    var directionCorrect: Bool?
}

struct Me: Codable, Equatable {
    var id: Int
    var username: String
    var gameScore: Int
    var rank: Int?
    var exactHits: Int
    var totalScore: Int
}

struct BoardRow: Codable, Equatable, Identifiable {
    var userId: Int
    var username: String
    var score: Int
    var rank: Int
    var exactHits: Int
    var picks: Int
    var isHost: Bool?
    var totalScore: Int?
    var id: Int { userId }
}

struct Crowd: Codable, Equatable {
    var total: Int
    var run: Int
    var pass: Int
    var left: Int
    var center: Int
    var right: Int
    var exact: Int
    var scored: Int

    enum CodingKeys: String, CodingKey {
        case total, exact, scored
        case run = "RUN", pass = "PASS", left = "LEFT", center = "CENTER", right = "RIGHT"
    }

    func count(_ type: PlayType) -> Int { type == .run ? run : pass }
    func count(_ direction: Direction) -> Int {
        switch direction {
        case .left: return left
        case .center: return center
        case .right: return right
        }
    }
}

struct Scoring: Codable, Equatable {
    var type: Int
    var direction: Int
    var exact: Int
    static let standard = Scoring(type: 10, direction: 10, exact: 30)
}

/// A lounge as returned by the REST API (no leaderboard) or inside a state snapshot (with one).
struct Lounge: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var hostUserId: Int
    var hostUsername: String?
    var memberCount: Int
    var leaderboard: [BoardRow]?
}

/// The personalised snapshot the server pushes on every change.
struct StateSnapshot: Codable, Equatable {
    var event: String
    var serverTime: Double
    var game: Game?
    var play: Play?
    var myPrediction: Prediction?
    var me: Me?
    var leaderboard: [BoardRow]
    var rankedPlayers: Int
    var crowd: Crowd?
    var lounge: Lounge?
    var scoring: Scoring
}

// MARK: - REST payloads

struct UserAccount: Codable, Equatable {
    var id: Int
    var username: String
    var totalScore: Int
    var token: String?
}

struct MeResponse: Codable {
    var user: UserAccount
    var lounges: [Lounge]
}

// MARK: - Socket messages

/// Everything the server can send on `/ws`.
enum ServerMessage {
    case state(StateSnapshot)
    case predictionSaved(Prediction)
    case error(code: String?, message: String)
    case pong(serverTime: Double)
    case other

    private struct Envelope: Decodable {
        var type: String
        var code: String?
        var message: String?
        var serverTime: Double?
    }

    private struct Saved: Decodable { var prediction: Prediction }

    static func decode(_ data: Data) throws -> ServerMessage {
        let envelope = try JSON.decoder.decode(Envelope.self, from: data)
        switch envelope.type {
        case "state": return .state(try JSON.decoder.decode(StateSnapshot.self, from: data))
        case "prediction_saved": return .predictionSaved(try JSON.decoder.decode(Saved.self, from: data).prediction)
        case "error": return .error(code: envelope.code, message: envelope.message ?? "Something went wrong.")
        case "pong": return .pong(serverTime: envelope.serverTime ?? 0)
        default: return .other
        }
    }
}

struct HelloMessage: Encodable {
    let type = "hello"
    var token: String?
    var lounge: String?
}

struct PredictMessage: Encodable {
    let type = "predict"
    var playId: Int
    var playType: PlayType
    var direction: Direction
}

struct PingMessage: Encodable {
    let type = "ping"
}

enum JSON {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()
}

enum Football {
    static func ordinal(_ n: Int) -> String {
        switch n {
        case 1: return "1st"
        case 2: return "2nd"
        case 3: return "3rd"
        default: return "\(n)th"
        }
    }

    static func downAndDistance(down: Int?, distance: String?) -> String? {
        guard let down else { return nil }
        if let distance, !distance.isEmpty { return "\(ordinal(down)) & \(distance)" }
        return ordinal(down)
    }

    /// "Green Bay" -> "GB", "Chicago" -> "CHI"
    static func abbreviation(_ name: String) -> String {
        let words = name.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard let first = words.first else { return "—" }
        if words.count == 1 { return String(first.prefix(3)).uppercased() }
        return String(words.compactMap(\.first).prefix(3)).uppercased()
    }
}
