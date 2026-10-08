import Foundation

// The host console's decisions, kept out of the views so they can be tested without a phone. Everything here
// mirrors what the website's console does (static/js/admin.js and admin-feed.js).

/// A team as the Create Game request needs it.
struct TeamChoice: Equatable {
    var name: String
    var primary: String
    var secondary: String

    init(name: String, primary: String, secondary: String) {
        self.name = name
        self.primary = primary.lowercased()
        self.secondary = secondary.lowercased()
    }

    init(_ team: FeedTeam) {
        self.init(name: team.name, primary: team.primary, secondary: team.secondary)
    }
}

/// Where the game stands, which decides what the Run screen offers.
enum RunStage: Equatable {
    /// No game yet: set one up.
    case noGame
    /// The game is FINAL: start another.
    case gameOver
    /// Between plays: open the next one.
    case ready
    /// Players are picking (the countdown is running).
    case open
    /// Picks are closed; waiting for the result.
    case locked
}

extension AdminState {
    var stage: RunStage {
        guard let game else { return .noGame }
        if game.status == .final { return .gameOver }
        switch play?.state {
        case .some(.open): return .open
        case .some(.locked): return .locked
        default: return .ready
        }
    }

    /// The game can be marked FINAL (not while a play is open or locked).
    var canEndGame: Bool {
        guard let game else { return false }
        return game.status != .final && !hasActivePlay
    }

    /// The scored play whose result can be fixed from the "scored by the feed" line.
    var latestHistory: HistoryPlay? { history.first }
}

// MARK: - The result of a play

/// The host's own entry of what happened: type, direction, distance, and optionally the exact yards.
/// Picking a distance bucket clears typed yards that disagree with it; typing yards picks the bucket.
struct ResultDraft: Equatable {
    static let yardsRange = -99...99

    var playType: PlayType?
    var direction: Direction?
    var yardage: YardageOutcome?
    var yardsText = ""

    init(playType: PlayType? = nil, direction: Direction? = nil, yardage: YardageOutcome? = nil, yards: Int? = nil) {
        self.playType = playType
        self.direction = direction
        self.yardage = yardage
        self.yardsText = yards.map(String.init) ?? ""
    }

    /// The typed yards, nil when empty or not a whole number between -99 and 99.
    var yards: Int? {
        let text = yardsText.trimmingCharacters(in: .whitespaces)
        guard let n = Int(text), Self.yardsRange.contains(n) else { return nil }
        return n
    }

    /// Something is typed but it isn't a usable number.
    var yardsAreInvalid: Bool {
        !yardsText.trimmingCharacters(in: .whitespaces).isEmpty && yards == nil
    }

    var isEmpty: Bool { playType == nil && direction == nil && yardage == nil && yardsText.isEmpty }
    var isComplete: Bool { playType != nil && direction != nil && yardage != nil && !yardsAreInvalid }

    mutating func choose(_ type: PlayType) { playType = type }
    mutating func choose(_ direction: Direction) { self.direction = direction }

    /// A distance bucket; typed yards that don't fit it are cleared.
    mutating func choose(_ bucket: YardageOutcome) {
        yardage = bucket
        if let yards, YardageOutcome(yards: yards) != bucket { yardsText = "" }
    }

    /// Typing yards picks the matching bucket.
    mutating func typeYards(_ text: String) {
        yardsText = text
        if let yards { yardage = YardageOutcome(yards: yards) }
    }

    mutating func clear() { self = ResultDraft() }

    /// "Pass · Left · Medium (7 yds)", or what has been chosen so far.
    var summary: String {
        var parts: [String] = []
        if let playType { parts.append(playType.title) }
        if let direction { parts.append(direction.title) }
        if let yardage {
            if let yards { parts.append("\(yardage.title) (\(Football.yards(yards)))") } else { parts.append(yardage.title) }
        }
        return parts.joined(separator: " · ")
    }

    /// The fields of `resolve_play` / `correct_play`. Only call when `isComplete`.
    var fields: [String: Any] {
        var out: [String: Any] = [:]
        if let playType { out["play_type"] = playType.rawValue }
        if let direction { out["direction"] = direction.rawValue }
        if let yardage { out["yardage"] = yardage.rawValue }
        if let yards { out["yards"] = yards }
        return out
    }
}

// MARK: - The feed's suggestion

/// What the host filled in for the parts of a suggestion the feed couldn't read (a sack's direction, say).
struct SuggestionChoices: Equatable {
    private(set) var playId: Int?
    var playType: PlayType?
    var direction: Direction?
    var yardage: YardageOutcome?

    /// Forget the choices when the suggestion is for a different play.
    mutating func sync(to suggestion: FeedSuggestion?) {
        guard suggestion?.playId != playId else { return }
        self = SuggestionChoices()
        playId = suggestion?.playId
    }

    /// The suggestion with the host's choices filling the gaps.
    func merged(with sg: FeedSuggestion) -> ResultDraft {
        let mine = sg.playId == playId
        return ResultDraft(
            playType: sg.playType ?? (mine ? playType : nil),
            direction: sg.direction ?? (mine ? direction : nil),
            yardage: sg.yardage ?? (mine ? yardage : nil),
            yards: sg.yards)
    }

    /// Parts still missing after the host's choices.
    func missing(_ sg: FeedSuggestion) -> [FeedSuggestion.Part] {
        if sg.isVoid { return [] }
        let result = merged(with: sg)
        var out: [FeedSuggestion.Part] = []
        if result.playType == nil { out.append(.playType) }
        if result.direction == nil { out.append(.direction) }
        if result.yardage == nil { out.append(.yardage) }
        return out
    }

    /// "Score now" (or "Void play") is possible.
    func canScore(_ sg: FeedSuggestion) -> Bool { sg.isVoid || missing(sg).isEmpty }

    /// The fields of `feed_accept`: the play, `void`, or the host's choices for the parts the feed lacked.
    func acceptFields(_ sg: FeedSuggestion) -> [String: Any] {
        var out: [String: Any] = ["play_id": sg.playId]
        if sg.isVoid {
            out["void"] = true
            return out
        }
        guard sg.playId == playId else { return out }
        if sg.playType == nil, let playType { out["play_type"] = playType.rawValue }
        if sg.direction == nil, let direction { out["direction"] = direction.rawValue }
        if sg.yardage == nil, let yardage { out["yardage"] = yardage.rawValue }
        return out
    }
}

extension FeedSuggestion {
    /// The suggestion's badge: what the feed thinks of the play.
    var badge: String {
        switch status {
        case .ready: return "FEED SUGGESTS"
        case .void: return "FEED SAYS NO PLAY"
        case .review: return "NEEDS YOUR CHECK"
        case .held: return "ON HOLD"
        }
    }

    /// The server will score (or void) this by itself when the countdown ends.
    var countsDown: Bool { (status == .ready || status == .void) && autoAt != nil }

    /// Seconds until it scores itself, nil when it won't.
    func secondsUntilAuto(at now: Double) -> Double? { countsDown ? autoAt.map { max(0, $0 - now) } : nil }

    /// The line under the suggestion that says what happens next.
    func note(missing: [Part], paused: Bool) -> String? {
        switch status {
        case .review:
            if missing.isEmpty { return "An unusual play, so it won't score by itself. Check it, then tap Score." }
            let names = missing.map(\.label)
            let list = names.count == 1 ? names[0] : names.dropLast().joined(separator: ", ") + " and " + names[names.count - 1]
            return "Pick the \(list) above, then Score."
        case .held:
            return isVoid ? "On hold. Tap Void play to confirm it, or score it yourself with Change."
                : "On hold. Nothing is scored until you tap Score now."
        case .ready, .void:
            if countsDown { return nil }
            return paused ? "Live data is paused, so this won't score by itself. Tap Score now, or Resume."
                : "Auto-score is off. Tap Score now when you're happy with it."
        }
    }
}

// MARK: - Down and distance for the next play

/// The Down and To go boxes. The feed fills them in after each scored play until the host touches them.
struct DownDraft: Equatable {
    var down: Int = 1
    var distance: String = "10"
    private(set) var touched = false
    private(set) var fromFeed = false
    private var appliedKey: String?

    mutating func setDown(_ value: Int) {
        down = min(4, max(1, value))
        touched = true
        fromFeed = false
    }

    mutating func setDistance(_ text: String) {
        distance = text
        touched = true
        fromFeed = false
    }

    /// A new game: back to 1st & 10.
    mutating func reset() { self = DownDraft() }

    /// Called for every update. Fills the boxes from the feed between plays unless the host already edited them.
    mutating func prefill(from next: NextDown?, gameID: Int?, lastPlayID: Int?, hasActivePlay: Bool) {
        if hasActivePlay { touched = false }  // a new play is open: the next edit is about the one after it
        guard let next, let gameID else {
            fromFeed = false
            return
        }
        let key = "\(gameID):\(lastPlayID ?? 0):\(next.down):\(next.distance ?? "")"
        if !hasActivePlay && !touched && appliedKey != key {
            appliedKey = key
            down = min(4, max(1, next.down))
            if let distance = next.distance { self.distance = distance }
            fromFeed = true
        }
    }

    /// "Filled in from the live feed" is worth showing.
    func showsFeedHint(hasActivePlay: Bool) -> Bool { fromFeed && !touched && !hasActivePlay }

    /// The distance to send: nil when the box is empty.
    var distanceToSend: String? {
        let text = distance.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : String(text.prefix(8))
    }
}

// MARK: - Fixing a scored play

/// The "Fix result" editor: starts from how the play was scored; saving needs a complete, different result.
struct FixDraft: Equatable {
    var playID: Int
    var playNumber: Int
    var original: ResultDraft
    var result: ResultDraft

    /// nil when the row isn't a scored play. `preset` (the feed's reading) replaces the parts it knows.
    init?(row: HistoryPlay, preset: ResultDraft? = nil) {
        guard row.isFixable, let outcome = row.outcome else { return nil }
        let base = ResultDraft(playType: outcome.playType, direction: outcome.direction, yardage: outcome.yardage, yards: outcome.yards)
        playID = row.id
        playNumber = row.playNumber
        original = base
        var edited = base
        if let preset {
            if let type = preset.playType { edited.playType = type }
            if let direction = preset.direction { edited.direction = direction }
            if let yardage = preset.yardage { edited.yardage = yardage }
            if preset.yards != nil { edited.yardsText = preset.yardsText }
        }
        result = edited
    }

    /// Something differs from how the play was scored (typed yards only count when they differ).
    var isChanged: Bool {
        result.playType != original.playType || result.direction != original.direction || result.yardage != original.yardage
            || (result.yards != nil && result.yards != original.yards)
    }

    var canSave: Bool { result.isComplete && isChanged }
}

// MARK: - Small helpers

enum HostText {
    /// "TB @ DAL — LIVE" style line for the header.
    static func gameLine(_ game: Game) -> String {
        "\(game.awayName) at \(game.homeName)"
    }

    static func count(_ n: Int, _ word: String) -> String { "\(n) \(word)\(n == 1 ? "" : "s")" }

    /// YYYYMMDD for the schedule request, in the phone's calendar and time zone.
    static func scheduleDate(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d%02d%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }

    /// The four quick messages, as on the website.
    static let quickMessages: [(title: String, text: String)] = [
        ("Delayed", "Kickoff is delayed. Hang tight!"),
        ("Halftime", "Halftime! Back in about 15 minutes."),
        ("Paused", "Picks are paused for a moment. Hang tight!"),
        ("Game over", "That's the game. Thanks for playing!"),
    ]

    static let maxMessage = 200
}

extension FixDraft: Identifiable {
    var id: Int { playID }
}
