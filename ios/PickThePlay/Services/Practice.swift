import SwiftUI

/// The same rules the server uses (models.score_prediction): +10 type, +10 direction, +30 for both.
enum ScoreRules {
    struct Result: Equatable {
        var points: Int
        var typeCorrect: Bool
        var directionCorrect: Bool
        var exact: Bool { typeCorrect && directionCorrect }
    }

    static func score(type: PlayType, direction: Direction, actualType: PlayType, actualDirection: Direction,
                      scoring: Scoring = .standard) -> Result {
        let typeOK = type == actualType
        let dirOK = direction == actualDirection
        let points = typeOK && dirOK ? scoring.exact : (typeOK ? scoring.type : (dirOK ? scoring.direction : 0))
        return Result(points: points, typeCorrect: typeOK, directionCorrect: dirOK)
    }

    static func label(for result: Result?, scoring: Scoring = .standard) -> String {
        guard let result else { return "You didn't pick this play" }
        if result.exact { return "Exact match!" }
        if result.typeCorrect { return "Play type correct" }
        if result.directionCorrect { return "Direction correct" }
        return "No points this time"
    }
}

/// Offline practice: simulated plays with the live game's timer and scoring, so anyone can try the
/// app when no live game is running (and so App Review always has something to play).
@MainActor
final class PracticeGame: ObservableObject {
    enum Phase: Equatable {
        case open(deadline: Date)
        case locked
        case result(actualType: PlayType, actualDirection: Direction, outcome: ScoreRules.Result?)
    }

    static let window: TimeInterval = 15

    @Published private(set) var playNumber = 0
    @Published private(set) var down = 1
    @Published private(set) var distance = "10"
    @Published private(set) var phase: Phase = .locked
    @Published var pickType: PlayType?
    @Published var pickDirection: Direction?
    @Published private(set) var score = 0
    @Published private(set) var exactHits = 0
    @Published private(set) var played = 0

    private var timer: Task<Void, Never>?
    /// Injected in tests so outcomes are deterministic.
    var outcome: () -> (PlayType, Direction) = PracticeGame.randomOutcome

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
        let (type, direction) = outcome()
        var result: ScoreRules.Result?
        if let pickType, let pickDirection {
            let r = ScoreRules.score(type: pickType, direction: pickDirection, actualType: type, actualDirection: direction)
            score += r.points
            if r.exact { exactHits += 1 }
            played += 1
            result = r
        }
        phase = .result(actualType: type, actualDirection: direction, outcome: result)
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
        phase = .locked
    }

    private func advanceDowns() {
        if down >= 4 || Int.random(in: 0..<3) == 0 {
            down = 1
            distance = "10"
        } else {
            down += 1
            distance = ["1", "2", "3", "4", "5", "6", "7", "8", "10", "12", "15"].randomElement() ?? "10"
        }
    }

    /// Rough NFL-ish tendencies: slightly more passes, runs favour the middle less than you'd think.
    nonisolated static func randomOutcome() -> (PlayType, Direction) {
        let type: PlayType = Double.random(in: 0..<1) < 0.56 ? .pass : .run
        let roll = Double.random(in: 0..<1)
        let direction: Direction = roll < 0.36 ? .left : (roll < 0.64 ? .center : .right)
        return (type, direction)
    }
}
