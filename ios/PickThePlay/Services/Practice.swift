import SwiftUI

/// The same rules the server uses (models.score_prediction): +10 for each correct part (play type,
/// direction, distance) and a +10 bonus when all three are right, so a perfect call = 40. Possible totals
/// are 0, 10, 20 and 40 (30 can't happen). A loss of yards never matches a distance pick (so a loss also
/// means no bonus), and a pick without a distance (made before the distance pick existed) scores none.
enum ScoreRules {
    struct Result: Equatable {
        var points: Int
        var typeCorrect: Bool
        var directionCorrect: Bool
        var yardageCorrect: Bool
        /// All three right ("Perfect call"; `exact` on the wire), which also earns the bonus.
        var exact: Bool { typeCorrect && directionCorrect && yardageCorrect }
        var correctParts: Int { [typeCorrect, directionCorrect, yardageCorrect].filter { $0 }.count }
    }

    static func score(type: PlayType, direction: Direction, yardage: Yardage?,
                      actualType: PlayType, actualDirection: Direction, actualYardage: YardageOutcome?,
                      scoring: Scoring = .standard) -> Result {
        let typeOK = type == actualType
        let dirOK = direction == actualDirection
        let yardsOK = actualYardage?.matches(yardage) ?? false
        var points = (typeOK ? scoring.type : 0) + (dirOK ? scoring.direction : 0) + (yardsOK ? scoring.yardage : 0)
        if typeOK && dirOK && yardsOK { points += scoring.bonus }
        return Result(points: points, typeCorrect: typeOK, directionCorrect: dirOK, yardageCorrect: yardsOK)
    }

    static func score(type: PlayType, direction: Direction, yardage: Yardage?, actual: PlayOutcome,
                      scoring: Scoring = .standard) -> Result {
        score(type: type, direction: direction, yardage: yardage, actualType: actual.playType,
              actualDirection: actual.direction, actualYardage: actual.yardage, scoring: scoring)
    }

    /// Result headline by how many parts were right: 3 "Perfect call!", 2 "Two of three", 1 "One of three",
    /// 0 "No points this time".
    static func label(correctParts: Int) -> String {
        switch correctParts {
        case 3...: return "Perfect call!"
        case 2: return "Two of three"
        case 1: return "One of three"
        default: return "No points this time"
        }
    }

    /// Fallback headline by points, for a pick without per-part flags: 40 "Perfect call!", 20 "Two of
    /// three", 10 "One of three", 0 "No points this time". A 30 from before the bonus was a perfect call too.
    static func label(points: Int?, scoring: Scoring = .standard) -> String {
        guard let points else { return "You didn't pick this play" }
        if points >= scoring.allParts { return label(correctParts: 3) }
        if points <= 0 { return label(correctParts: 0) }
        return label(correctParts: points > scoring.maxPart ? 2 : 1)
    }

    static func label(for result: Result?) -> String {
        guard let result else { return label(points: nil) }
        return label(correctParts: result.correctParts)
    }

    /// The headline for a scored pick: by the right/wrong flags when all three are known, else by points.
    static func label(pick: Prediction?, scoring: Scoring = .standard) -> String {
        guard let pick else { return label(points: nil) }
        if let parts = correctParts(pick) { return label(correctParts: parts) }
        return label(points: pick.pointsEarned ?? 0, scoring: scoring)
    }

    /// All three right (gold styling, confetti): by the flags when known, else by points.
    static func isPerfect(_ pick: Prediction?, scoring: Scoring = .standard) -> Bool {
        guard let pick else { return false }
        if let parts = correctParts(pick) { return parts == 3 }
        return (pick.pointsEarned ?? 0) >= scoring.allParts
    }

    private static func correctParts(_ pick: Prediction) -> Int? {
        guard let t = pick.typeCorrect, let d = pick.directionCorrect, let y = pick.yardageCorrect else { return nil }
        return [t, d, y].filter { $0 }.count
    }

    /// One line for onboarding, settings and the pick screen.
    static func summary(_ s: Scoring = .standard) -> String {
        "+\(s.type) play type, +\(s.direction) direction, +\(s.yardage) distance, +\(s.bonus) bonus for all three = \(s.exact)."
    }

    /// The server's per-part flags, filled in from the outcome where it left one out.
    static func graded(_ pick: Prediction, outcome: PlayOutcome?) -> Prediction {
        guard let outcome else { return pick }
        var p = pick
        let r = score(type: pick.playType, direction: pick.direction, yardage: pick.yardage, actual: outcome)
        if p.typeCorrect == nil { p.typeCorrect = r.typeCorrect }
        if p.directionCorrect == nil { p.directionCorrect = r.directionCorrect }
        // Without a recorded distance (old server) there's nothing to grade the distance against.
        if p.yardageCorrect == nil, outcome.yardage != nil || pick.yardage == nil { p.yardageCorrect = r.yardageCorrect }
        return p
    }
}

/// Offline practice: simulated plays with the live game's timer and scoring, so anyone can try the
/// app when no live game is running (and so App Review always has something to play).
@MainActor
final class PracticeGame: ObservableObject {
    enum Phase: Equatable {
        case open(deadline: Date)
        case locked
        /// `scored` is nil when the player didn't make all three picks in time.
        case result(PlayOutcome, scored: ScoreRules.Result?)
    }

    static let window: TimeInterval = 15

    @Published private(set) var playNumber = 0
    @Published private(set) var down = 1
    @Published private(set) var distance = "10"
    @Published private(set) var phase: Phase = .locked
    @Published var pickType: PlayType?
    @Published var pickDirection: Direction?
    @Published var pickYardage: Yardage?
    @Published private(set) var score = 0
    /// Plays with all three parts right.
    @Published private(set) var exactHits = 0
    @Published private(set) var played = 0

    private var timer: Task<Void, Never>?
    private var lastYards: Int?
    /// Injected in tests so outcomes are deterministic.
    var outcome: () -> PlayOutcome = PracticeGame.randomOutcome

    var hasFullPick: Bool { pickType != nil && pickDirection != nil && pickYardage != nil }

    var label: String {
        var parts = ["Practice play \(playNumber)"]
        if let dd = Football.downAndDistance(down: down, distance: distance) { parts.append(dd) }
        return parts.joined(separator: " · ")
    }

    func nextPlay(now: Date = Date()) {
        timer?.cancel()
        playNumber += 1
        if playNumber > 1 { advanceDowns() }
        pickType = nil
        pickDirection = nil
        pickYardage = nil
        let deadline = now.addingTimeInterval(Self.window)
        phase = .open(deadline: deadline)
        timer = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.window * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.snap()
        }
    }

    /// Lock picks and run the play (called by the timer, or by the player to skip the wait).
    func snap() {
        guard case .open = phase else { return }
        timer?.cancel()
        phase = .locked
        timer = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_400_000_000)
            guard !Task.isCancelled else { return }
            self?.resolve()
        }
    }

    func resolve() {
        timer?.cancel()
        let actual = outcome()
        var result: ScoreRules.Result?
        // Same as the live game: the pick only counts once all three parts are in.
        if let pickType, let pickDirection, let pickYardage {
            let r = ScoreRules.score(type: pickType, direction: pickDirection, yardage: pickYardage, actual: actual)
            score += r.points
            if r.exact { exactHits += 1 }
            played += 1
            result = r
        }
        lastYards = actual.yards
        phase = .result(actual, scored: result)
    }

    /// The player's pick for the result view, graded like a live prediction.
    var gradedPick: Prediction? {
        guard case .result(_, let scored?) = phase, let pickType, let pickDirection else { return nil }
        return Prediction(userId: nil, playId: playNumber, playType: pickType, direction: pickDirection, yardage: pickYardage,
                          pointsEarned: scored.points, typeCorrect: scored.typeCorrect,
                          directionCorrect: scored.directionCorrect, yardageCorrect: scored.yardageCorrect)
    }

    func stop() {
        timer?.cancel()
        timer = nil
    }

    func reset() {
        stop()
        playNumber = 0
        down = 1
        distance = "10"
        score = 0
        exactHits = 0
        played = 0
        lastYards = nil
        phase = .locked
    }

    /// Down and distance follow the last simulated gain: move the chains, or it's the next down.
    private func advanceDowns() {
        let toGo = Int(distance) ?? 10
        guard let gained = lastYards else {
            down = 1
            distance = "10"
            return
        }
        if gained >= toGo || down >= 4 {  // first down, or a new series after 4th down
            down = 1
            distance = "10"
        } else {
            down += 1
            distance = "\(min(99, toGo - gained))"
        }
    }

    /// Rough NFL-ish tendencies: slightly more passes, runs favour the middle less than you'd think.
    nonisolated static func randomOutcome() -> PlayOutcome {
        let type: PlayType = Double.random(in: 0..<1) < 0.56 ? .pass : .run
        let roll = Double.random(in: 0..<1)
        let direction: Direction = roll < 0.36 ? .left : (roll < 0.64 ? .middle : .right)
        return PlayOutcome(playType: type, direction: direction, yards: randomYards(for: type))
    }

    /// Yards gained, by a uniform `roll` in 0..<1 (so tests can pin the bucket):
    /// runs: 10% loss, 58% 0–5, 22% 6–10, 10% 11+.
    /// passes: 37% incomplete (0 yds = short), 18% 1–5, 21% 6–10, 24% 11+ (no sacks: a sack is no play).
    nonisolated static func randomYards(for type: PlayType, roll: Double = Double.random(in: 0..<1)) -> Int {
        switch type {
        case .run:
            if roll < 0.10 { return -Int.random(in: 1...4) }
            if roll < 0.68 { return Int.random(in: 0...5) }
            if roll < 0.90 { return Int.random(in: 6...10) }
            return Int.random(in: 11...45)
        case .pass:
            if roll < 0.37 { return 0 }
            if roll < 0.55 { return Int.random(in: 1...5) }
            if roll < 0.76 { return Int.random(in: 6...10) }
            return Int.random(in: 11...60)
        }
    }
}
