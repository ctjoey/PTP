import Foundation

// Wire models for the host console (`/ws/admin`, `/api/admin/*`). Same rules as Models.swift: field names follow
// the server's snake_case JSON through `JSON.decoder`, and PickThePlayTests/Fixtures holds real server messages.
//
// The host console must never go blank in the middle of a game because one small field changed type, so only the
// game and the play are decoded strictly; everything else is decoded leniently (a part that can't be read is left
// out, and the rest of the update still arrives).

// MARK: - Lenient decoding

extension KeyedDecodingContainer {
    /// A flag that the server may send as true/false or as SQLite's 0/1.
    func lenientBool(_ key: Key) -> Bool? {
        if let value = try? decodeIfPresent(Bool.self, forKey: key) { return value }
        if let number = try? decodeIfPresent(Int.self, forKey: key) { return number != 0 }
        return nil
    }

    /// Text that the server may send as a string or as a number ("3" or 3).
    func lenientString(_ key: Key) -> String? {
        if let value = try? decodeIfPresent(String.self, forKey: key) { return value }
        if let number = try? decodeIfPresent(Int.self, forKey: key) { return String(number) }
        if let number = try? decodeIfPresent(Double.self, forKey: key) { return String(number) }
        return nil
    }

    /// A whole number that the server may send as 7 or 7.0.
    func lenientInt(_ key: Key) -> Int? {
        if let value = try? decodeIfPresent(Int.self, forKey: key) { return value }
        if let number = try? decodeIfPresent(Double.self, forKey: key) { return Int(number) }
        return nil
    }
}

// MARK: - The live-data (Tank01) feed

struct FeedRequests: Equatable {
    var game = 0
    var today = 0
    var gameCap = 0
    var dayCap = 0
    var planRemaining: Int?
    var planLimit: Int?

    /// 0...100, how much of this game's request allowance is used.
    var percentUsed: Int {
        guard gameCap > 0 else { return 0 }
        return min(100, Int((Double(game) * 100 / Double(gameCap)).rounded()))
    }

    /// "Requests this game 214 / 900 · plan has 786 left"
    var summary: String {
        var text = "Requests this game \(game) / \(gameCap)"
        if let planRemaining { text += " · plan has \(planRemaining) left" }
        if percentUsed >= 80 { text += " (nearly at the limit)" }
        else if let planRemaining, planRemaining <= 100 { text += " (running low)" }
        return text
    }

    /// Amber meter: close to this game's limit, or the monthly plan is nearly used up.
    var isLow: Bool { percentUsed >= 80 || (planRemaining ?? Int.max) <= 100 }
}

extension FeedRequests: Decodable {
    private enum CodingKeys: String, CodingKey { case game, today, gameCap, dayCap, planRemaining, planLimit }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        game = c.lenientInt(.game) ?? 0
        today = c.lenientInt(.today) ?? 0
        gameCap = c.lenientInt(.gameCap) ?? 0
        dayCap = c.lenientInt(.dayCap) ?? 0
        planRemaining = c.lenientInt(.planRemaining)
        planLimit = c.lenientInt(.planLimit)
    }
}

struct FeedLag: Decodable, Equatable {
    var median: Double?
    var last: Double?
    var samples: Int?
}

/// The locked play the server is waiting to read from the feed.
struct FeedWaiting: Decodable, Equatable {
    var playId: Int
    var checks: Int?
    var since: Double?
    var nextCheckAt: Double?
}

/// What the feed says happened on the locked play, before it is scored.
struct FeedSuggestion: Equatable, Identifiable {
    /// `ready` (clean; counting down when `autoAt` is set), `review`, `void` or `held`.
    enum Status: String { case ready, review, void, held }

    var playId: Int
    var statusText: String
    var text: String
    var clock: String?
    var downAndDistance: String?
    var playTypeText: String?
    var directionText: String?
    var yards: Int?
    var yardageText: String?
    var flags: [String]
    var warning: String?
    /// Server epoch seconds when it will score itself; nil when it won't (held, paused, auto-score off, review).
    var autoAt: Double?
    var kind: String?

    var id: Int { playId }
    var status: Status { Status(rawValue: statusText) ?? .review }
    var playType: PlayType? { playTypeText.flatMap { PlayType(rawValue: $0) } }
    var direction: Direction? { directionText.flatMap { $0 == Direction.legacyMiddle ? .middle : Direction(rawValue: $0) } }
    /// The distance bucket the feed gave, or worked out from the yards; `.loss` for negative yards.
    var yardage: YardageOutcome? {
        if let yardageText, let value = YardageOutcome(rawValue: yardageText) { return value }
        return yards.map(YardageOutcome.init(yards:))
    }

    /// The feed says it was not a play at all (a penalty wiped it out), or the host held that verdict.
    var isVoid: Bool {
        status == .void || (status == .held && playType == nil && direction == nil && text.lowercased().contains("no play"))
    }

    /// The parts the feed could not fill in and the host must choose.
    var missingParts: [Part] {
        if isVoid { return [] }
        var missing: [Part] = []
        if playType == nil { missing.append(.playType) }
        if direction == nil { missing.append(.direction) }
        if yardage == nil { missing.append(.yardage) }
        return missing
    }

    enum Part: String, CaseIterable {
        case playType, direction, yardage
        var label: String {
            switch self {
            case .playType: return "play type"
            case .direction: return "direction"
            case .yardage: return "distance"
            }
        }
    }
}

extension FeedSuggestion: Decodable {
    private enum CodingKeys: String, CodingKey {
        case playId, status, text, clock, downAndDistance, playType, direction, yards, yardage, flags, warning, autoAt, kind
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        playId = try c.decode(Int.self, forKey: .playId)
        statusText = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "review"
        text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? ""
        clock = try? c.decodeIfPresent(String.self, forKey: .clock)
        downAndDistance = try? c.decodeIfPresent(String.self, forKey: .downAndDistance)
        playTypeText = try? c.decodeIfPresent(String.self, forKey: .playType)
        directionText = try? c.decodeIfPresent(String.self, forKey: .direction)
        yards = c.lenientInt(.yards)
        yardageText = try? c.decodeIfPresent(String.self, forKey: .yardage)
        flags = (try? c.decodeIfPresent([String].self, forKey: .flags)) ?? []
        warning = try? c.decodeIfPresent(String.self, forKey: .warning)
        autoAt = try? c.decodeIfPresent(Double.self, forKey: .autoAt)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
    }
}

/// The feed's reading of an already-scored play, when it differs from how the host scored it.
struct FeedDisagreement: Equatable {
    var playId: Int
    /// "PASS - RIGHT - MEDIUM"
    var feed: String
    var scored: String
    var text: String?
    var feedPlayType: String?
    var feedDirection: String?
    var feedYardage: String?
    var feedYards: Int?
}

extension FeedDisagreement: Decodable {
    private enum CodingKeys: String, CodingKey { case playId, feed, scored, text, feedResult }
    private enum ResultKeys: String, CodingKey { case playType, direction, yards, yardage }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        playId = try c.decode(Int.self, forKey: .playId)
        feed = (try? c.decodeIfPresent(String.self, forKey: .feed)) ?? ""
        scored = (try? c.decodeIfPresent(String.self, forKey: .scored)) ?? ""
        text = try? c.decodeIfPresent(String.self, forKey: .text)
        if let r = try? c.nestedContainer(keyedBy: ResultKeys.self, forKey: .feedResult) {
            feedPlayType = try? r.decodeIfPresent(String.self, forKey: .playType)
            feedDirection = try? r.decodeIfPresent(String.self, forKey: .direction)
            feedYardage = try? r.decodeIfPresent(String.self, forKey: .yardage)
            feedYards = r.lenientInt(.yards)
        }
    }
}

/// The down and distance the feed expects for the next play.
struct NextDown: Equatable {
    var down: Int
    var distance: String?
}

extension NextDown: Decodable {
    private enum CodingKeys: String, CodingKey { case down, distance }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        down = try c.decode(Int.self, forKey: .down)
        distance = c.lenientString(.distance)
    }
}

/// The last play the feed scored (or voided), for the confirmation line.
struct FeedLastScored: Decodable, Equatable {
    var playId: Int
    var by: String?
    var auto: Bool?
    var summary: String?
    var at: Double?
    var voided: Bool?
}

/// `admin_state.feed`: everything about the live-data connection.
struct FeedState: Equatable {
    /// off · idle · waiting · paused · capped · error · not_started · done
    var state: String
    var available: Bool
    var linked: Bool
    /// "tank01" · "demo" · nil
    var source: String?
    var gameId: String?
    var message: String?
    var paused: Bool
    var autoScore: Bool
    var autoOpen: Bool
    var requests: FeedRequests?
    var lag: FeedLag?
    var waiting: FeedWaiting?
    var suggestion: FeedSuggestion?
    var disagreement: FeedDisagreement?
    var nextDown: NextDown?
    var autoOpenAt: Double?
    var lastScored: FeedLastScored?

    /// True when the server has been waiting a long time for a locked play (injury or replay review?).
    func isQuiet(at now: Double) -> Bool {
        guard state == "waiting" else { return false }
        if let message, message.lowercased().contains("quiet") || message.lowercased().contains("long delay") { return true }
        if let since = waiting?.since { return now - since > 180 }
        return false
    }

    /// The plain-English state shown on the status chip.
    func label(at now: Double) -> String {
        if isQuiet(at: now) { return "QUIET" }
        switch state {
        case "off": return "OFF"
        case "idle": return "READY"
        case "waiting": return "WAITING"
        case "paused": return "PAUSED"
        case "capped": return "LIMIT REACHED"
        case "error": return "PROBLEM"
        case "not_started": return "NOT STARTED"
        case "done": return "DONE"
        default: return state.uppercased()
        }
    }

    /// good = running normally, wait = needs patience, bad = needs the host, off = nothing to show.
    enum Tone { case good, wait, bad, off }

    func tone(at now: Double) -> Tone {
        if isQuiet(at: now) { return .wait }
        switch state {
        case "idle", "waiting": return .good
        case "paused", "not_started": return .wait
        case "capped", "error": return .bad
        default: return .off
        }
    }

    /// Words for the status line when the server sent none.
    var defaultMessage: String {
        switch state {
        case "off": return "Live data is off. You score every play by hand."
        case "idle": return "Ready. Checking starts when a play locks."
        case "waiting": return "Waiting for the feed to show this play."
        case "paused": return "Paused. Nothing is being checked."
        case "capped": return "Request limit reached. You score by hand."
        case "error": return "Can't reach the feed. You can score by hand."
        case "not_started": return "The game hasn't started yet."
        case "done": return "The game is finished."
        default: return ""
        }
    }

    var isRecorded: Bool { source == "demo" }
}

extension FeedState: Decodable {
    private enum CodingKeys: String, CodingKey {
        case state, available, linked, source, gameId, message, paused, autoScore, autoOpen, requests, lag, waiting
        case suggestion, disagreement, nextDown, autoOpenAt, lastScored
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = (try? c.decodeIfPresent(String.self, forKey: .state)) ?? "off"
        available = c.lenientBool(.available) ?? false
        linked = c.lenientBool(.linked) ?? false
        source = try? c.decodeIfPresent(String.self, forKey: .source)
        gameId = try? c.decodeIfPresent(String.self, forKey: .gameId)
        message = try? c.decodeIfPresent(String.self, forKey: .message)
        paused = c.lenientBool(.paused) ?? false
        autoScore = c.lenientBool(.autoScore) ?? true
        autoOpen = c.lenientBool(.autoOpen) ?? false
        requests = try? c.decodeIfPresent(FeedRequests.self, forKey: .requests)
        lag = try? c.decodeIfPresent(FeedLag.self, forKey: .lag)
        waiting = try? c.decodeIfPresent(FeedWaiting.self, forKey: .waiting)
        suggestion = try? c.decodeIfPresent(FeedSuggestion.self, forKey: .suggestion)
        disagreement = try? c.decodeIfPresent(FeedDisagreement.self, forKey: .disagreement)
        nextDown = try? c.decodeIfPresent(NextDown.self, forKey: .nextDown)
        autoOpenAt = try? c.decodeIfPresent(Double.self, forKey: .autoOpenAt)
        lastScored = try? c.decodeIfPresent(FeedLastScored.self, forKey: .lastScored)
    }
}

// MARK: - The play log

/// One row of `admin_state.history` (newest first). The database stores `voided` as 0/1.
struct HistoryPlay: Equatable, Identifiable {
    var id: Int
    var playNumber: Int
    var down: Int?
    var distance: String?
    var state: PlayState
    var voided: Bool
    var correctPlayType: PlayType?
    var correctDirection: Direction?
    var correctYardage: YardageOutcome?
    var yardsGained: Int?
    /// host · feed · void · host-fix; nil on rows from before live data.
    var resolvedBy: String?
    var feedText: String?
    var picks: Int
    var exactHits: Int

    /// "Play 3 · 3rd & 7"
    var label: String {
        var parts = ["Play \(playNumber)"]
        if let dd = Football.downAndDistance(down: down, distance: distance) { parts.append(dd) }
        return parts.joined(separator: " · ")
    }

    var downAndDistance: String? { Football.downAndDistance(down: down, distance: distance) }

    /// The scored result, nil while the play is open or locked, or when it was voided.
    var outcome: PlayOutcome? {
        guard state == .resolved, !voided, let correctPlayType, let correctDirection else { return nil }
        return PlayOutcome(playType: correctPlayType, direction: correctDirection,
                           yardage: correctYardage ?? yardsGained.map(YardageOutcome.init(yards:)), yards: yardsGained)
    }

    /// What the log row says: the result, "VOID", or the play's state.
    var resultText: String {
        if voided { return "VOID" }
        if let outcome { return outcome.summary }
        return state.rawValue
    }

    /// A scored play that can be corrected.
    var isFixable: Bool { state == .resolved && !voided }
}

extension HistoryPlay: Decodable {
    private enum CodingKeys: String, CodingKey {
        case id, playNumber, down, distance, state, voided, correctPlayType, correctDirection, correctYardage
        case yardsGained, resolvedBy, feedText, picks, exactHits
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        playNumber = try c.decode(Int.self, forKey: .playNumber)
        down = c.lenientInt(.down)
        distance = c.lenientString(.distance)
        state = try c.decode(PlayState.self, forKey: .state)
        voided = c.lenientBool(.voided) ?? false
        correctPlayType = try? c.decodeIfPresent(PlayType.self, forKey: .correctPlayType)
        correctDirection = try? c.decodeIfPresent(Direction.self, forKey: .correctDirection)
        correctYardage = try? c.decodeIfPresent(YardageOutcome.self, forKey: .correctYardage)
        yardsGained = c.lenientInt(.yardsGained)
        resolvedBy = try? c.decodeIfPresent(String.self, forKey: .resolvedBy)
        feedText = try? c.decodeIfPresent(String.self, forKey: .feedText)
        picks = c.lenientInt(.picks) ?? 0
        exactHits = c.lenientInt(.exactHits) ?? 0
    }
}

// MARK: - admin_state

/// The banner the host is showing to the players.
struct HostBanner: Decodable, Equatable {
    var id: Int
    var text: String
}

/// `admin_state`: the whole console in one message, pushed on every change.
struct AdminState: Equatable {
    var event: String
    var serverTime: Double
    var game: Game?
    var play: Play?
    var pickStats: Crowd?
    var playersOnline: Int
    var spectatorsOnline: Int
    var adminsOnline: Int
    var leaderboard: [BoardRow]
    var rankedPlayers: Int
    var history: [HistoryPlay]
    var windowSeconds: Double
    var feed: FeedState?
    var announcement: HostBanner?
    var registeredPlayers: Int?

    /// A play is open or locked (waiting for a result).
    var hasActivePlay: Bool { play.map { $0.state != .resolved } ?? false }
}

extension AdminState: Decodable {
    private enum CodingKeys: String, CodingKey {
        case event, serverTime, game, play, pickStats, playersOnline, spectatorsOnline, adminsOnline, leaderboard
        case rankedPlayers, history, windowSeconds, feed, announcement, registeredPlayers
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        event = (try? c.decodeIfPresent(String.self, forKey: .event)) ?? "sync"
        serverTime = (try? c.decodeIfPresent(Double.self, forKey: .serverTime)) ?? Date().timeIntervalSince1970
        // Strict: a game or play that can't be read must not look like "no game yet" (the host might start a second one).
        game = try c.decodeIfPresent(Game.self, forKey: .game)
        play = try c.decodeIfPresent(Play.self, forKey: .play)
        pickStats = try? c.decodeIfPresent(Crowd.self, forKey: .pickStats)
        playersOnline = c.lenientInt(.playersOnline) ?? 0
        spectatorsOnline = c.lenientInt(.spectatorsOnline) ?? 0
        adminsOnline = c.lenientInt(.adminsOnline) ?? 0
        leaderboard = (try? c.decodeIfPresent([BoardRow].self, forKey: .leaderboard)) ?? []
        rankedPlayers = c.lenientInt(.rankedPlayers) ?? leaderboard.count
        // Row by row, so one odd row doesn't empty the log.
        history = (try? c.decodeIfPresent([LossyRow<HistoryPlay>].self, forKey: .history))?.compactMap(\.value) ?? []
        windowSeconds = (try? c.decodeIfPresent(Double.self, forKey: .windowSeconds)) ?? 15
        feed = try? c.decodeIfPresent(FeedState.self, forKey: .feed)
        announcement = try? c.decodeIfPresent(HostBanner.self, forKey: .announcement)
        registeredPlayers = c.lenientInt(.registeredPlayers)
    }
}

/// Decodes one array element without failing the whole array.
struct LossyRow<T: Decodable>: Decodable {
    var value: T?
    init(from decoder: Decoder) throws { value = try? T(from: decoder) }
}

// MARK: - Replies

/// `admin_ack`: the server's answer to one admin action.
struct AdminAck: Equatable {
    var requestId: Int?
    var action: String?
    var ok: Bool
    var error: String?
    /// For `announce`: how many screens the message went to.
    var sentTo: Int?
}

extension AdminAck: Decodable {
    private enum CodingKeys: String, CodingKey { case requestId, action, ok, error, result }
    private enum ResultKeys: String, CodingKey { case sentTo }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = c.lenientInt(.requestId)
        action = try? c.decodeIfPresent(String.self, forKey: .action)
        ok = c.lenientBool(.ok) ?? false
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        if let r = try? c.nestedContainer(keyedBy: ResultKeys.self, forKey: .result) { sentTo = r.lenientInt(.sentTo) }
    }
}

/// Everything the server can send on `/ws/admin`.
enum AdminServerMessage {
    case state(AdminState)
    case ack(AdminAck)
    /// The key was refused; the server closes the socket right after.
    case authError(String)
    case pong
    case other

    private struct Envelope: Decodable {
        var type: String
        var message: String?
    }

    static func decode(_ data: Data) throws -> AdminServerMessage {
        let envelope = try JSON.decoder.decode(Envelope.self, from: data)
        switch envelope.type {
        case "admin_state": return .state(try JSON.decoder.decode(AdminState.self, from: data))
        case "admin_ack": return .ack(try JSON.decoder.decode(AdminAck.self, from: data))
        case "auth_error": return .authError(envelope.message ?? "Invalid admin key.")
        case "pong": return .pong
        default: return .other
        }
    }
}

// MARK: - REST: the day's games and the players list

struct FeedTeam: Decodable, Equatable {
    var abbr: String?
    var name: String
    var primary: String
    var secondary: String
}

/// One game in "Pick today's game" (or the recorded practice game, whose id is "demo").
struct FeedGame: Decodable, Equatable, Identifiable {
    static let demoID = "demo"

    var feedGameId: String
    var away: FeedTeam
    var home: FeedTeam
    var time: String?
    var status: String?

    var id: String { feedGameId }
    var isRecorded: Bool { feedGameId == FeedGame.demoID }

    /// "TB at DAL - 8:15p (Scheduled)"
    var label: String {
        let a = away.abbr ?? away.name
        let h = home.abbr ?? home.name
        var text = "\(a) at \(h)"
        if let time, !time.isEmpty { text += " - \(time)" }
        if let status, !status.isEmpty, status != "Scheduled" { text += " (\(status))" }
        return text
    }
}

struct FeedGames: Decodable, Equatable {
    var date: String?
    var games: [FeedGame]
    var demo: FeedGame?
    var available: Bool?
    var error: String?
    var cached: Bool?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try? c.decodeIfPresent(String.self, forKey: .date)
        games = (try? c.decodeIfPresent([LossyRow<FeedGame>].self, forKey: .games))?.compactMap(\.value) ?? []
        demo = try? c.decodeIfPresent(FeedGame.self, forKey: .demo)
        available = c.lenientBool(.available)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        cached = c.lenientBool(.cached)
    }

    private enum CodingKeys: String, CodingKey { case date, games, demo, available, error, cached }
}

struct HostPlayer: Decodable, Equatable, Identifiable {
    var id: Int
    var username: String
    var picks: Int
    var gameScore: Int
    var totalScore: Int
    var online: Bool

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        username = try c.decode(String.self, forKey: .username)
        picks = c.lenientInt(.picks) ?? 0
        gameScore = c.lenientInt(.gameScore) ?? 0
        totalScore = c.lenientInt(.totalScore) ?? 0
        online = c.lenientBool(.online) ?? false
    }

    private enum CodingKeys: String, CodingKey { case id, username, picks, gameScore, totalScore, online }

    /// "3 picks · 60 game pts · 120 season"
    var summary: String {
        "\(picks) \(picks == 1 ? "pick" : "picks") · \(gameScore) game pts · \(totalScore) season"
    }
}

struct HostPlayers: Decodable, Equatable {
    var count: Int
    var players: [HostPlayer]
    var blockedNames: [String]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        players = (try? c.decodeIfPresent([LossyRow<HostPlayer>].self, forKey: .players))?.compactMap(\.value) ?? []
        count = c.lenientInt(.count) ?? players.count
        blockedNames = (try? c.decodeIfPresent([String].self, forKey: .blockedNames)) ?? []
    }

    private enum CodingKeys: String, CodingKey { case count, players, blockedNames }
}
