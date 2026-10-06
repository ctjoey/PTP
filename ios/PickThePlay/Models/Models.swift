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
    var title: String { self == .run ? "Run" : "Pass" }
    var caption: String { self == .run ? "On the ground" : "Through the air" }
}

/// Left / center / right as the quarterback sees it, looking downfield (the offense's point of view).
enum Direction: String, Codable, CaseIterable, Identifiable {
    case left = "LEFT"
    case center = "CENTER"
    case right = "RIGHT"
    var id: String { rawValue }
    var title: String {
        switch self {
        case .left: return "Left"
        case .center: return "Center"
        case .right: return "Right"
        }
    }
    var symbol: String {
        switch self {
        case .left: return "arrow.left"
        case .center: return "arrow.up"
        case .right: return "arrow.right"
        }
    }
}

/// The player's distance pick: total yards gained on the play. Called `yardage` in code and JSON so it
/// never clashes with down-and-distance ("3rd & 7"); players see it as "Distance" / "How far?".
enum Yardage: String, Codable, CaseIterable, Identifiable {
    case short = "SHORT"
    case medium = "MEDIUM"
    case long = "LONG"
    var id: String { rawValue }
    var title: String {
        switch self {
        case .short: return "Short"
        case .medium: return "Medium"
        case .long: return "Long"
        }
    }
    /// "0–5 yds"
    var range: String {
        switch self {
        case .short: return "0–5 yds"
        case .medium: return "6–10 yds"
        case .long: return "11+ yds"
        }
    }
    /// VoiceOver: "0 to 5 yards"
    var spokenRange: String {
        switch self {
        case .short: return "0 to 5 yards"
        case .medium: return "6 to 10 yards"
        case .long: return "11 or more yards"
        }
    }
}

/// What the play actually gained, as the admin (or a data feed) resolved it. A loss never matches a pick.
enum YardageOutcome: String, Codable, CaseIterable {
    case short = "SHORT"
    case medium = "MEDIUM"
    case loss = "LOSS"
    case long = "LONG"

    /// Same buckets as the server: < 0 loss, 0–5 short (an incomplete pass is 0), 6–10 medium, 11+ long.
    init(yards: Int) {
        switch yards {
        case ..<0: self = .loss
        case 0...5: self = .short
        case 6...10: self = .medium
        default: self = .long
        }
    }

    var title: String { pick?.title ?? "Loss" }

    /// The pick this outcome rewards; nil for a loss.
    var pick: Yardage? {
        switch self {
        case .short: return .short
        case .medium: return .medium
        case .long: return .long
        case .loss: return nil
        }
    }

    func matches(_ pick: Yardage?) -> Bool { pick != nil && pick == self.pick }
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
    /// Null until resolved (and on plays resolved before the distance pick existed).
    var correctYardage: YardageOutcome?
    /// Exact yards gained when the admin or a data feed entered them.
    var yardsGained: Int?

    /// "Play 3 · 3rd & 7"
    var label: String {
        var parts = ["Play \(playNumber)"]
        if let dd = Football.downAndDistance(down: down, distance: distance) { parts.append(dd) }
        return parts.joined(separator: " · ")
    }

    /// What happened, once the play is resolved (nil while open/locked or when voided).
    var outcome: PlayOutcome? {
        guard !voided, let correctPlayType, let correctDirection else { return nil }
        return PlayOutcome(playType: correctPlayType, direction: correctDirection,
                           yardage: correctYardage ?? yardsGained.map(YardageOutcome.init(yards:)), yards: yardsGained)
    }
}

/// The result of a play: type, direction (QB's view) and how far it went.
struct PlayOutcome: Equatable {
    var playType: PlayType
    var direction: Direction
    /// Nil only for a play resolved by a server that predates the distance pick.
    var yardage: YardageOutcome?
    var yards: Int?

    init(playType: PlayType, direction: Direction, yardage: YardageOutcome?, yards: Int? = nil) {
        self.playType = playType
        self.direction = direction
        self.yardage = yardage
        self.yards = yards
    }

    /// A simulated play: the bucket follows from the yards.
    init(playType: PlayType, direction: Direction, yards: Int) {
        self.init(playType: playType, direction: direction, yardage: YardageOutcome(yards: yards), yards: yards)
    }

    /// "Run · Left · Medium (7 yds)", "Pass · Right · Loss (-4 yds)"
    var summary: String {
        var text = "\(playType.title) · \(direction.title)"
        if let yardage { text += " · \(yardage.title)" }
        if let yards { text += " (\(Football.yards(yards)))" }
        return text
    }

    /// For VoiceOver: "Run, Left, Medium, 7 yards"
    var spokenSummary: String {
        var parts = [playType.title, direction.title]
        if let yardage { parts.append(yardage.title) }
        if let yards { parts.append(abs(yards) == 1 ? "\(yards) yard" : "\(yards) yards") }
        return parts.joined(separator: ", ")
    }
}

struct Prediction: Codable, Equatable {
    var userId: Int?
    var playId: Int
    var playType: PlayType
    var direction: Direction
    /// Null on picks made before the distance pick existed (they score no distance points).
    var yardage: Yardage?
    var pointsEarned: Int?
    var typeCorrect: Bool?
    var directionCorrect: Bool?
    var yardageCorrect: Bool?
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

/// How everyone picked a locked or resolved play. `exact` = all three right, `scored` = any points.
struct Crowd: Codable, Equatable {
    var total: Int
    var run: Int
    var pass: Int
    var left: Int
    var center: Int
    var right: Int
    /// Distance split; absent from servers that predate the distance pick.
    var short: Int?
    var medium: Int?
    var long: Int?
    var exact: Int
    var scored: Int

    enum CodingKeys: String, CodingKey {
        case total, exact, scored
        case run = "RUN", pass = "PASS", left = "LEFT", center = "CENTER", right = "RIGHT"
        case short = "SHORT", medium = "MEDIUM", long = "LONG"
    }

    func count(_ type: PlayType) -> Int { type == .run ? run : pass }
    func count(_ direction: Direction) -> Int {
        switch direction {
        case .left: return left
        case .center: return center
        case .right: return right
        }
    }
    func count(_ yardage: Yardage) -> Int? {
        switch yardage {
        case .short: return short
        case .medium: return medium
        case .long: return long
        }
    }
    var hasYardage: Bool { short != nil || medium != nil || long != nil }
}

/// Points per correct part. `exact` is all three right (the name stays for wire compatibility).
struct Scoring: Codable, Equatable {
    var type: Int
    var direction: Int
    var yardage: Int
    var exact: Int
    static let standard = Scoring(type: 10, direction: 10, yardage: 10, exact: 30)

    init(type: Int, direction: Int, yardage: Int, exact: Int) {
        self.type = type
        self.direction = direction
        self.yardage = yardage
        self.exact = exact
    }

    private enum CodingKeys: String, CodingKey { case type, direction, yardage, exact }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decode(Int.self, forKey: .type)
        direction = try c.decode(Int.self, forKey: .direction)
        // Older servers didn't send it; the distance pick is worth the same as the others.
        yardage = try c.decodeIfPresent(Int.self, forKey: .yardage) ?? Scoring.standard.yardage
        exact = try c.decode(Int.self, forKey: .exact)
    }

    /// The most a single correct part is worth (for "one of three" vs "two of three").
    var maxPart: Int { max(type, direction, yardage) }
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

/// `{"type":"predict","play_id":7,"play_type":"PASS","direction":"LEFT","yardage":"SHORT"}`. Sent only once
/// all three parts are chosen; sending again while the play is OPEN replaces the pick.
struct PredictMessage: Encodable {
    let type = "predict"
    var playId: Int
    var playType: PlayType
    var direction: Direction
    var yardage: Yardage

    /// The REST body for `POST /api/predictions`.
    var body: [String: Any] {
        ["play_id": playId, "play_type": playType.rawValue, "direction": direction.rawValue, "yardage": yardage.rawValue]
    }
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

    /// "7 yds", "1 yd", "-4 yds"
    static func yards(_ n: Int) -> String { abs(n) == 1 ? "\(n) yd" : "\(n) yds" }

    /// What the live or practice screen says while picks are incomplete.
    static func pickPrompt(type: PlayType?, direction: Direction?, yardage: Yardage?) -> String {
        var missing: [String] = []
        if type == nil { missing.append("Run or Pass") }
        if direction == nil { missing.append("a direction") }
        if yardage == nil { missing.append("a distance") }
        switch missing.count {
        case 0: return "Not sent yet. Tap any choice to send it again."
        case 1: return "Now pick \(missing[0])"
        case 2: return "Now pick \(missing[0]) and \(missing[1])"
        default: return "Make your call: type, direction and distance"
        }
    }

    /// "Green Bay" -> "GB", "Chicago" -> "CHI"
    static func abbreviation(_ name: String) -> String {
        let words = name.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard let first = words.first else { return "—" }
        if words.count == 1 { return String(first.prefix(3)).uppercased() }
        return String(words.compactMap(\.first).prefix(3)).uppercased()
    }
}
