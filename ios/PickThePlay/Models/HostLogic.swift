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

    /// Live data is linked to this game and a play is locked: the feed it belongs to, nil otherwise.
    private var feedOfLockedPlay: FeedState? {
        guard play?.state == .locked, let feed, feed.linked else { return nil }
        return feed
    }

    /// The locked play is one that live data is following: it has a suggestion for it, or it is waiting to check again.
    var feedIsWatchingLockedPlay: Bool {
        guard let feed = feedOfLockedPlay else { return false }
        return feed.suggestion != nil || feed.waiting?.nextCheckAt != nil
    }

    /// The locked play is the host's to score: live data is linked but idle, with nothing waiting and nothing
    /// suggested (it joined late and can't tell which play this one was). The website's console uses the same test.
    var lockedPlayIsLeftToHost: Bool {
        guard let feed = feedOfLockedPlay else { return false }
        return feed.state == "idle" && feed.waiting == nil && feed.suggestion == nil
    }
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

/// What the host filled in for the parts of a suggestion the feed couldn't read (an interception's direction, say).
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
            if isVoid { return HostText.reviewVoidNote }
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

// MARK: - The timer for the next play

/// The Timer (s) box next to Down and To go: how long players have to pick. Same limits as the server (and the
/// website): 5 to 60 seconds, 15 unless the host changes it.
struct TimerDraft: Equatable {
    static let range = 5...60
    static let standard = 15
    /// The longest thing worth typing ("60" is two characters; the rest is room for typos).
    static let longestText = 3

    private(set) var text = String(TimerDraft.standard)

    /// The box as typed: digits only, a few characters.
    mutating func type(_ value: String) {
        text = String(value.filter(\.isASCIIDigit).prefix(Self.longestText))
    }

    /// The typed number, nil when the box is empty.
    var typed: Int? {
        let digits = text.trimmingCharacters(in: .whitespaces)
        return digits.isEmpty ? nil : (Int(digits) ?? Self.range.upperBound)
    }

    /// What is sent: the typed number held inside 5...60, or 15 when the box is empty.
    var seconds: Int { typed.map(Self.clamp) ?? Self.standard }

    /// The box holds a number the server accepts as it is.
    var isValid: Bool { typed.map(Self.range.contains) ?? false }

    static func clamp(_ value: Int) -> Int { min(range.upperBound, max(range.lowerBound, value)) }

    /// Shows what will be sent, so the box never says one thing and the server gets another.
    mutating func normalize() { text = String(seconds) }

    /// A plain note when the box isn't a usable number as typed; nil when it is fine.
    var warning: String? {
        if isValid { return nil }
        return typed == nil ? "Type the seconds players get to pick, from 5 to 60. \(seconds) will be used."
                            : "The timer is 5 to 60 seconds. \(seconds) will be used."
    }
}

private extension Character {
    var isASCIIDigit: Bool { isASCII && isNumber }
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

    /// The distance buttons in the website's order (the model's own order puts Loss before Long).
    static let distanceChoices: [YardageOutcome] = [.short, .medium, .long, .loss]

    // MARK: Words shared with the website's console

    /// The live-data chip when a locked play is the host's to score.
    static let scoreByHand = "SCORE BY HAND"
    static let voidPlay = "Void play"
    static let voidHint = "Use it for a penalty, a sack, a QB scramble, no play, or if you missed it. Nobody scores."
    /// Under the chip when live data joined late (the server sends the same words).
    static let leftToHostNotice = "Live data joined late, so it can't tell which play this one was. Score it by hand (or void it), then open the next play and live data takes over."
    static let reviewVoidNote = "The feed says no play, but check it against the TV first. Tap Void play if it's right, or score it yourself with Change."
    static let lockedLeftToHost = "Live data can't score this play. Tap what happened (Run or Pass, direction, distance), then press Score Play. No play, or you missed it? Press Void play."
    static let lockedWatched = "Picks are closed. Live data scores it when the result shows up. You can also tap what happened and press Score Play, or press Void play."
    /// A locked play nothing is watching (no live data, or it is paused or out of requests): no promise about the feed.
    static let lockedByHand = "Picks are closed. Tap what happened (Run or Pass, direction, distance), then press Score Play. No play, or you missed it? Press Void play."
    /// The host console works only with the game server the app was built for (the admin key is never sent elsewhere).
    static let wrongServer = "The host console only works with the built-in game server. Change the server back in Settings."

    /// What the Picks-are-locked card says, by what live data is doing with the play.
    static func lockedGuidance(_ state: AdminState) -> String {
        if state.lockedPlayIsLeftToHost { return lockedLeftToHost }
        return state.feedIsWatchingLockedPlay ? lockedWatched : lockedByHand
    }

    /// The line under the live-data chip. When the play is left to the host it must say so, whatever the server's
    /// generic "Connected" words are.
    static func statusMessage(_ feed: FeedState, leftToHost: Bool) -> String {
        let sent = feed.message?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if leftToHost { return sent.lowercased().contains("by hand") ? sent : leftToHostNotice }
        return sent.isEmpty ? feed.defaultMessage : sent
    }

    /// Whole seconds for the countdown lines, kept in a sane range so odd server numbers can't trap.
    static func wholeSeconds(_ value: Double, up: Bool = true) -> Int {
        guard !value.isNaN else { return 0 }
        return Int(min(max(value, 0), 604_800).rounded(up ? .up : .toNearestOrAwayFromZero))
    }

    /// The line under "NOT STARTED": when the feed looks again, and what the host can do meanwhile.
    static func notStartedLine(nextCheckAt: Double?, now: Double) -> String {
        let ask = "Is the game on? Tap Check now."
        guard let nextCheckAt else { return ask }
        return "Next check in \(wholeSeconds(nextCheckAt - now)) s. " + ask
    }
}

extension FixDraft: Identifiable {
    var id: Int { playID }
}

extension FeedGame {
    /// The recorded practice game (no data-provider requests): Carolina at Washington. The server sends the same one.
    static let practice = FeedGame(
        feedGameId: FeedGame.demoID,
        away: FeedTeam(abbr: "CAR", name: "Carolina", primary: "#0085CA", secondary: "#101820"),
        home: FeedTeam(abbr: "WSH", name: "Washington", primary: "#5A1414", secondary: "#FFB612"),
        time: "Anytime", status: "Practice game (no requests used)")
}
