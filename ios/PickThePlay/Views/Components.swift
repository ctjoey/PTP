import SwiftUI

// MARK: - Scorebug

struct ScorebugView: View {
    var game: Game
    var play: Play?

    var body: some View {
        // Read once: both numbers or neither (the score is a nicety and may be missing).
        let score = game.liveScore
        HStack(spacing: 0) {
            team(name: game.awayName, primary: game.awayPrimary, secondary: game.awaySecondary, tag: "AWAY", leading: true,
                 score: score?.away)
            VStack(spacing: 6) {
                StatusPill(status: game.status)
                Text("@").font(.system(size: 13, weight: .black)).foregroundStyle(Theme.dim)
                Text(play.map(\.label) ?? "Pre-game")
                    .font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.muted)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            .padding(.horizontal, 8)
            .frame(width: 104)
            team(name: game.homeName, primary: game.homePrimary, secondary: game.homeSecondary, tag: "HOME", leading: false,
                 score: score?.home)
        }
        .frame(height: 96)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.border, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(spoken(score))
    }

    /// "Chicago at Detroit, live" or, with a score, "Chicago 17, Detroit 24, live".
    private func spoken(_ score: (home: Int, away: Int)?) -> String {
        let status = game.status.rawValue.lowercased()
        guard let score else { return "\(game.awayName) at \(game.homeName), \(status)" }
        return "\(game.awayName) \(score.away), \(game.homeName) \(score.home), \(status)"
    }

    private func team(name: String, primary: String, secondary: String, tag: String, leading: Bool, score: Int?) -> some View {
        let ink = Theme.ink(on: primary)
        return VStack(alignment: leading ? .leading : .trailing, spacing: 3) {
            // The score sits between the abbreviation and the "@": CHI 17 @ 24 DET.
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if let score, !leading { scoreText(score) }
                Text(Football.abbreviation(name)).font(.system(size: 28, weight: .black)).italic()
                if let score, leading { scoreText(score) }
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            Text(name).font(.system(size: 12, weight: .bold)).opacity(0.85).lineLimit(1)
            Text(tag).font(.system(size: 9, weight: .heavy)).tracking(1.8).opacity(0.7)
        }
        .foregroundStyle(ink)
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: leading ? .leading : .trailing)
        .background(
            LinearGradient(colors: [Color(hex: primary), Color(hex: primary).opacity(0.6)],
                           startPoint: leading ? .topLeading : .topTrailing, endPoint: leading ? .bottomTrailing : .bottomLeading)
                .background(Color.black)
        )
        .overlay(alignment: .bottom) { Rectangle().fill(Color(hex: secondary)).frame(height: 4) }
    }

    /// Quiet: just the number, no spinner or label; it changes in place when the next check brings a new one.
    private func scoreText(_ score: Int) -> some View {
        Text("\(score)")
            .font(.system(size: 24, weight: .heavy).monospacedDigit())
            .opacity(0.9)
            .contentTransition(.numericText())
            .animation(.snappy, value: score)
    }
}

struct StatusPill: View {
    var status: GameStatus
    @State private var pulse = false

    var body: some View {
        let color: Color = status == .live ? Theme.danger : (status == .final ? Theme.text : Theme.blue)
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 7, height: 7)
                .opacity(status == .live && pulse ? 0.3 : 1)
            Text(status.rawValue).font(.system(size: 11, weight: .black)).tracking(1.3)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 9).padding(.vertical, 4)
        .background(color.opacity(status == .live ? 0.18 : 0.12), in: Capsule())
        .onAppear {
            withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) { pulse = true }
        }
    }
}

// MARK: - Picking

struct PickButton: View {
    var title: String
    var caption: String? = nil
    /// An SF Symbol shown under the title instead of a caption (the direction arrows).
    var symbol: String? = nil
    var selected: Bool
    var enabled: Bool
    var height: CGFloat = 64
    /// What VoiceOver says instead of the shouted title, e.g. "Short, 0 to 5 yards".
    var spoken: String? = nil
    var action: () -> Void

    var body: some View {
        let detail = selected ? Theme.accentInk.opacity(0.7) : Theme.muted
        Button(action: action) {
            VStack(spacing: 2) {
                Text(title).font(.system(size: height > 56 ? 19 : 16, weight: .black)).tracking(0.6)
                    .lineLimit(1).minimumScaleFactor(0.7)
                if let symbol {
                    Image(systemName: symbol).font(.system(size: 11, weight: .black)).foregroundStyle(detail)
                } else if let caption {
                    Text(caption).font(.system(size: 11, weight: .bold)).foregroundStyle(detail)
                        .lineLimit(1).minimumScaleFactor(0.8)
                }
            }
            .padding(.horizontal, 4)
            .frame(maxWidth: .infinity, minHeight: height)
            .foregroundStyle(selected ? Theme.accentInk : Theme.text)
            .background(selected ? Theme.accent : Theme.surface2, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(selected ? Theme.accent : Theme.border, lineWidth: 2))
            .shadow(color: selected ? Theme.accent.opacity(0.45) : .clear, radius: 10, y: 4)
        }
        .buttonStyle(PressStyle())
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.45)
        .accessibilityLabel(spoken ?? title)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct PressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.easeOut(duration: 0.08), value: configuration.isPressed)
    }
}

/// The three-part call shared by the live game and practice: Run/Pass, Left/Middle/Right as the QB
/// looks downfield, and Short/Medium/Long by total yards gained. Each group header shows its points.
struct PickPanel: View {
    var type: PlayType?
    var direction: Direction?
    var yardage: Yardage?
    var enabled: Bool
    var scoring: Scoring = .standard
    var onType: (PlayType) -> Void
    var onDirection: (Direction) -> Void
    var onYardage: (Yardage) -> Void

    @Environment(\.compactLayout) private var compact

    var body: some View {
        VStack(alignment: .leading, spacing: compact ? 7 : 10) {
            GroupHeader(title: "Run or pass?", points: scoring.type)
            HStack(spacing: 10) {
                ForEach(PlayType.allCases) { option in
                    PickButton(title: option.rawValue, caption: option.caption, selected: type == option, enabled: enabled,
                               height: compact ? 50 : 62, spoken: option.title) {
                        onType(option)
                    }
                }
            }
            GroupHeader(title: "Which way?", hint: "As the QB looks downfield", points: scoring.direction)
                .padding(.top, compact ? 2 : 4)
            HStack(spacing: 10) {
                ForEach(Direction.allCases) { option in
                    PickButton(title: option.rawValue, symbol: option.symbol, selected: direction == option, enabled: enabled,
                               height: compact ? 46 : 54, spoken: "\(option.title), as the QB looks downfield") {
                        onDirection(option)
                    }
                }
            }
            GroupHeader(title: "How far?", hint: "No points for a loss", points: scoring.yardage)
                .padding(.top, compact ? 2 : 4)
            HStack(spacing: 10) {
                ForEach(Yardage.allCases) { option in
                    PickButton(title: option.rawValue, caption: option.range, selected: yardage == option, enabled: enabled,
                               height: compact ? 46 : 54, spoken: "\(option.title), \(option.spokenRange)") {
                        onYardage(option)
                    }
                }
            }
        }
        .sensoryFeedback(.selection, trigger: "\(type?.rawValue ?? "")|\(direction?.rawValue ?? "")|\(yardage?.rawValue ?? "")")
    }
}

/// "WHICH WAY?  As the QB looks downfield ........ +10"
struct GroupHeader: View {
    var title: String
    var hint: String? = nil
    var points: Int

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).kicker()
            if let hint {
                Text(hint).font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.muted)
                    .lineLimit(1).minimumScaleFactor(0.75)
            }
            Spacer(minLength: 4)
            Text("+\(points)").font(.system(size: 11, weight: .heavy).monospacedDigit()).foregroundStyle(Theme.accent)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
        .accessibilityAddTraits(.isHeader)
    }

    /// "Which way? As the QB looks downfield. 10 points" (titles already end in "?").
    private var spoken: String {
        var text = title
        if let hint { text += " \(hint)." }
        return text + " \(points) points"
    }
}

struct CountdownRing: View {
    var remaining: Double
    var total: Double
    var size: CGFloat = 84

    var body: some View {
        let fraction = total > 0 ? max(0, min(1, remaining / total)) : 0
        let color: Color = remaining <= 3 ? Theme.danger : (remaining <= 7 ? Theme.warn : Theme.accent)
        let line: CGFloat = size < 80 ? 7 : 8
        ZStack {
            Circle().stroke(Theme.surface3, lineWidth: line)
            Circle().trim(from: 0, to: fraction)
                .stroke(color, style: StrokeStyle(lineWidth: line, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text("\(Int(remaining.rounded(.up)))")
                .font(.system(size: size * 0.36, weight: .black).monospacedDigit())
                .foregroundStyle(remaining <= 3 ? Theme.danger : Theme.text)
                .contentTransition(.numericText(countsDown: true))
        }
        .frame(width: size, height: size)
        .accessibilityLabel("\(Int(remaining.rounded(.up))) seconds left")
    }
}

// MARK: - Chips, bars, stats

/// "Pick all 3 correctly: +10 bonus = 40" under the pick panel (live and practice). A server from before the
/// bonus (bonus 0) gets the plain "Pick all 3 correctly = 30".
struct BonusChip: View {
    var scoring: Scoring = .standard

    var body: some View {
        Chip(text: scoring.bonusLine, style: .gold)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .frame(maxWidth: .infinity)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(scoring.bonusSpoken)
    }
}

struct Chip: View {
    enum Style { case plain, good, bad, gold }
    var text: String
    var style: Style = .plain

    var body: some View {
        let color: Color = {
            switch style {
            case .plain: return Theme.muted
            case .good: return Theme.accent
            case .bad: return Theme.danger
            case .gold: return Theme.gold
            }
        }()
        Text(text)
            .font(.system(size: 12, weight: .heavy)).tracking(0.7)
            .foregroundStyle(color)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(style == .plain ? Theme.surface3 : color.opacity(0.18), in: Capsule())
    }
}

/// "YOUR PICK" over PASS ✓  LEFT ✗  SHORT ✓ (marks only once the play is graded).
struct PickChips: View {
    var pick: Prediction?
    var graded: Bool

    var body: some View {
        VStack(spacing: 6) {
            if let pick {
                Text("Your pick").kicker()
                HStack(spacing: 6) {
                    chip(pick.playType.rawValue, pick.typeCorrect)
                    chip(pick.direction.rawValue, pick.directionCorrect)
                    chip(pick.yardage?.rawValue ?? "NO DISTANCE", pick.yardageCorrect)
                }
            } else {
                Chip(text: "No pick this play")
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }

    private func chip(_ text: String, _ correct: Bool?) -> Chip {
        guard graded, let correct else { return Chip(text: text) }
        return Chip(text: "\(text) \(correct ? "✓" : "✗")", style: correct ? .good : .bad)
    }

    private var spoken: String {
        guard let pick else { return "No pick this play" }
        func part(_ name: String, _ correct: Bool?) -> String {
            guard graded, let correct else { return name }
            return "\(name), \(correct ? "correct" : "missed")"
        }
        let parts = [part(pick.playType.title, pick.typeCorrect), part(pick.direction.title, pick.directionCorrect),
                     part(pick.yardage?.title ?? "No distance", pick.yardageCorrect)]
        return "Your pick: " + parts.joined(separator: ". ")
    }
}

/// How the crowd split on each part, one stacked bar per part (shown once picks are locked).
struct CrowdBars: View {
    var crowd: Crowd

    private struct Share: Identifiable {
        var id: String
        var count: Int
        var shade: Double
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("How \(crowd.total) \(crowd.total == 1 ? "player" : "players") called it").kicker()
            split(PlayType.allCases.map { ($0.rawValue, crowd.count($0)) })
            split(Direction.allCases.map { ($0.rawValue, crowd.count($0)) })
            if crowd.hasYardage {
                split(Yardage.allCases.map { ($0.rawValue, crowd.count($0) ?? 0) })
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func split(_ counts: [(String, Int)]) -> some View {
        let shades: [Double] = [1, 0.6, 0.32]
        let shares = counts.enumerated().map { index, entry in
            Share(id: entry.0, count: entry.1, shade: shades[min(index, shades.count - 1)])
        }
        return VStack(alignment: .leading, spacing: 5) {
            GeometryReader { geo in
                HStack(spacing: 0) {
                    ForEach(shares) { share in
                        Rectangle().fill(Theme.blue.opacity(share.shade))
                            .frame(width: geo.size.width * fraction(share.count))
                    }
                }
            }
            .frame(height: 10)
            .background(Theme.surface3)
            .clipShape(Capsule())
            HStack(spacing: 12) {
                ForEach(shares) { share in
                    HStack(spacing: 4) {
                        Circle().fill(Theme.blue.opacity(share.shade)).frame(width: 7, height: 7)
                        Text("\(share.id) \(percent(share.count))%")
                            .font(.system(size: 12, weight: .bold).monospacedDigit())
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                    }
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(shares.map { "\($0.id.capitalized) \(percent($0.count)) percent" }.joined(separator: ", "))
    }

    private func fraction(_ count: Int) -> Double {
        crowd.total > 0 ? min(1, Double(count) / Double(crowd.total)) : 0
    }

    private func percent(_ count: Int) -> Int { Int((fraction(count) * 100).rounded()) }
}

struct StatTile: View {
    var label: String
    var value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.system(size: 10, weight: .heavy)).tracking(1.4).foregroundStyle(Theme.muted).textCase(.uppercase)
            Text(value).font(.system(size: 22, weight: .black).monospacedDigit()).foregroundStyle(Theme.text)
                .contentTransition(.numericText())
                .animation(.snappy, value: value)
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.border, lineWidth: 1))
    }
}

struct ConnectionDot: View {
    var status: LiveConnection.Status

    var body: some View {
        let color: Color = status == .online ? Theme.accent : (status == .connecting ? Theme.warn : Theme.dim)
        Circle().fill(color).frame(width: 10, height: 10)
            .overlay(Circle().stroke(color.opacity(0.3), lineWidth: 6))
            .accessibilityLabel(status == .online ? "Live" : (status == .connecting ? "Connecting" : "Offline"))
    }
}

// MARK: - Result reveal

struct ResultReveal: View {
    var outcome: PlayOutcome
    var points: Int?
    var label: String
    var exact: Bool
    /// Changing this replays the reveal animation.
    var animationKey: Int

    @State private var shown = false
    @Environment(\.compactLayout) private var compact

    var body: some View {
        VStack(spacing: compact ? 10 : 14) {
            HStack(spacing: 8) {
                tile("Play type", outcome.playType.rawValue, detail: outcome.playType.caption, delay: 0)
                tile("Direction", outcome.direction.rawValue, detail: "QB's view", delay: 0.2)
                tile("Distance", outcome.yardage?.rawValue ?? "—", detail: distanceDetail, delay: 0.4,
                     loss: outcome.yardage == .loss)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("What happened: \(outcome.spokenSummary)")
            Text(points.map { "+\($0)" } ?? "—")
                .font(.system(size: compact ? 50 : 60, weight: .black).monospacedDigit())
                .foregroundStyle(exact ? Theme.gold : ((points ?? 0) > 0 ? Theme.accent : Theme.dim))
                .shadow(color: exact ? Theme.gold.opacity(0.5) : .clear, radius: 18)
                .scaleEffect(shown ? 1 : 0.3)
                .opacity(shown ? 1 : 0)
                .animation(.spring(response: 0.45, dampingFraction: 0.55).delay(0.75), value: shown)
                .accessibilityLabel(points.map { "\($0) points" } ?? "No points")
            Text(label).font(.system(size: 14, weight: .black)).tracking(1.4).textCase(.uppercase)
                .foregroundStyle(Theme.text)
                .opacity(shown ? 1 : 0)
                .animation(.easeOut(duration: 0.3).delay(1.0), value: shown)
            // The same words as under "Perfect call" in the points table.
            if exact {
                Text(ScoreRules.perfectNote).font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Theme.gold)
                    .opacity(shown ? 1 : 0)
                    .animation(.easeOut(duration: 0.3).delay(1.15), value: shown)
            }
        }
        .onAppear { shown = true }
        .onChange(of: animationKey) { _, _ in
            shown = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { shown = true }
        }
        .sensoryFeedback(.success, trigger: shown && (points ?? 0) > 0)
    }

    /// "7 yds" when the yards are known, else the bucket's range.
    private var distanceDetail: String {
        if let yards = outcome.yards { return Football.yards(yards) }
        guard let yardage = outcome.yardage else { return " " }
        return yardage.pick?.range ?? "Lost yards"
    }

    private func tile(_ title: String, _ value: String, detail: String, delay: Double, loss: Bool = false) -> some View {
        VStack(spacing: 3) {
            Text(title).font(.system(size: 10, weight: .heavy)).tracking(1.2).textCase(.uppercase)
                .foregroundStyle(Theme.muted).lineLimit(1).minimumScaleFactor(0.8)
            Text(value).font(.system(size: compact ? 20 : 23, weight: .black))
                .foregroundStyle(loss ? Theme.danger : Theme.text)
                .lineLimit(1).minimumScaleFactor(0.6)
            Text(detail).font(.system(size: 11, weight: .bold)).foregroundStyle(loss ? Theme.danger.opacity(0.85) : Theme.muted)
                .lineLimit(1).minimumScaleFactor(0.8)
        }
        .padding(.horizontal, 6)
        .frame(maxWidth: .infinity)
        .padding(.vertical, compact ? 10 : 14)
        .background(LinearGradient(colors: [Theme.surface3, Theme.surface2], startPoint: .top, endPoint: .bottom),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.border, lineWidth: 1))
        .rotation3DEffect(.degrees(shown ? 0 : 90), axis: (x: 1, y: 0, z: 0))
        .opacity(shown ? 1 : 0)
        .animation(.spring(response: 0.5, dampingFraction: 0.7).delay(delay), value: shown)
    }
}

/// A short burst of team-coloured confetti for exact matches.
struct ConfettiView: View {
    var colors: [Color]
    @State private var fall = false

    private struct Piece: Identifiable {
        let id: Int
        let x: CGFloat
        let drift: CGFloat
        let spin: Double
        let delay: Double
    }

    // @State so the random layout is chosen once, not on every parent re-render.
    @State private var pieces: [Piece] = (0..<44).map { i in
        Piece(id: i, x: CGFloat.random(in: 0...1), drift: CGFloat.random(in: -90...90),
              spin: Double.random(in: 180...720), delay: Double.random(in: 0.4...0.9))
    }

    var body: some View {
        GeometryReader { geo in
            ForEach(pieces) { piece in
                RoundedRectangle(cornerRadius: 2)
                    .fill(colors.isEmpty ? Theme.gold : colors[piece.id % colors.count])
                    .frame(width: 8, height: 14)
                    .rotationEffect(.degrees(fall ? piece.spin : 0))
                    .position(x: geo.size.width * piece.x + (fall ? piece.drift : 0), y: fall ? geo.size.height + 20 : -20)
                    .opacity(fall ? 0 : 1)
                    .animation(.easeIn(duration: 2.2).delay(piece.delay), value: fall)
            }
        }
        .allowsHitTesting(false)
        .onAppear { fall = true }
    }
}
