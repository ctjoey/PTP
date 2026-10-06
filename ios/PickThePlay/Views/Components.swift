import SwiftUI

// MARK: - Scorebug

struct ScorebugView: View {
    var game: Game
    var play: Play?

    var body: some View {
        HStack(spacing: 0) {
            team(name: game.awayName, primary: game.awayPrimary, secondary: game.awaySecondary, tag: "AWAY", leading: true)
            VStack(spacing: 6) {
                StatusPill(status: game.status)
                Text("@").font(.system(size: 13, weight: .black)).foregroundStyle(Theme.dim)
                Text(play.map(\.label) ?? "Pre-game")
                    .font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.muted)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
            .padding(.horizontal, 8)
            .frame(width: 104)
            team(name: game.homeName, primary: game.homePrimary, secondary: game.homeSecondary, tag: "HOME", leading: false)
        }
        .frame(height: 96)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.border, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(game.awayName) at \(game.homeName), \(game.status.rawValue.lowercased())")
    }

    private func team(name: String, primary: String, secondary: String, tag: String, leading: Bool) -> some View {
        let ink = Theme.ink(on: primary)
        return VStack(alignment: leading ? .leading : .trailing, spacing: 3) {
            Text(Football.abbreviation(name)).font(.system(size: 28, weight: .black)).italic()
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
    var caption: String?
    var selected: Bool
    var enabled: Bool
    var height: CGFloat = 64
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Text(title).font(.system(size: height > 60 ? 19 : 16, weight: .black)).tracking(0.6)
                if let caption {
                    Text(caption).font(.system(size: 11, weight: .bold))
                        .foregroundStyle(selected ? Theme.accentInk.opacity(0.7) : Theme.muted)
                }
            }
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

/// Run/Pass + Left/Center/Right grid shared by the live game and practice.
struct PickPanel: View {
    var type: PlayType?
    var direction: Direction?
    var enabled: Bool
    var onType: (PlayType) -> Void
    var onDirection: (Direction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Run or pass?").kicker()
            HStack(spacing: 10) {
                ForEach(PlayType.allCases) { option in
                    PickButton(title: option.rawValue, caption: option.caption, selected: type == option, enabled: enabled) {
                        onType(option)
                    }
                }
            }
            Text("Which direction?").kicker().padding(.top, 4)
            HStack(spacing: 10) {
                ForEach(Direction.allCases) { option in
                    PickButton(title: option.rawValue, caption: nil, selected: direction == option, enabled: enabled,
                               height: 56) {
                        onDirection(option)
                    }
                    .overlay(alignment: .bottom) {
                        Image(systemName: option.symbol).font(.system(size: 10, weight: .black))
                            .foregroundStyle(direction == option ? Theme.accentInk.opacity(0.7) : Theme.muted)
                            .padding(.bottom, 7)
                            .allowsHitTesting(false)
                    }
                }
            }
        }
        .sensoryFeedback(.selection, trigger: (type?.rawValue ?? "") + (direction?.rawValue ?? ""))
    }
}

struct CountdownRing: View {
    var remaining: Double
    var total: Double

    var body: some View {
        let fraction = total > 0 ? max(0, min(1, remaining / total)) : 0
        let color: Color = remaining <= 3 ? Theme.danger : (remaining <= 7 ? Theme.warn : Theme.accent)
        ZStack {
            Circle().stroke(Theme.surface3, lineWidth: 8)
            Circle().trim(from: 0, to: fraction)
                .stroke(color, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text("\(Int(remaining.rounded(.up)))")
                .font(.system(size: 30, weight: .black).monospacedDigit())
                .foregroundStyle(remaining <= 3 ? Theme.danger : Theme.text)
                .contentTransition(.numericText(countsDown: true))
        }
        .frame(width: 84, height: 84)
        .accessibilityLabel("\(Int(remaining.rounded(.up))) seconds left")
    }
}

// MARK: - Chips, bars, stats

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

/// "Your pick: PASS ✓ LEFT ✗"
struct PickChips: View {
    var pick: Prediction?
    var graded: Bool

    var body: some View {
        HStack(spacing: 8) {
            if let pick {
                Chip(text: "Your pick")
                chip(pick.playType.rawValue, pick.typeCorrect)
                chip(pick.direction.rawValue, pick.directionCorrect)
            } else {
                Chip(text: "No pick this play")
            }
        }
    }

    private func chip(_ text: String, _ correct: Bool?) -> Chip {
        guard graded, let correct else { return Chip(text: text) }
        return Chip(text: "\(text) \(correct ? "✓" : "✗")", style: correct ? .good : .bad)
    }
}

struct CrowdBars: View {
    var crowd: Crowd

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("How \(crowd.total) \(crowd.total == 1 ? "player" : "players") called it").kicker()
            ForEach(PlayType.allCases) { bar($0.rawValue, crowd.count($0)) }
            ForEach(Direction.allCases) { bar($0.rawValue, crowd.count($0)) }
        }
    }

    private func bar(_ label: String, _ count: Int) -> some View {
        let share = crowd.total > 0 ? Double(count) / Double(crowd.total) : 0
        return HStack(spacing: 8) {
            Text(label).font(.system(size: 13, weight: .bold)).frame(width: 64, alignment: .leading)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.surface3)
                    Capsule().fill(Theme.blue).frame(width: geo.size.width * share)
                }
            }
            .frame(height: 10)
            Text("\(Int((share * 100).rounded()))%").font(.system(size: 13, weight: .bold).monospacedDigit())
                .foregroundStyle(Theme.muted).frame(width: 44, alignment: .trailing)
        }
        .foregroundStyle(Theme.text)
    }
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
    var playType: PlayType
    var direction: Direction
    var points: Int?
    var label: String
    var exact: Bool
    /// Changing this replays the reveal animation.
    var animationKey: Int

    @State private var shown = false

    var body: some View {
        VStack(spacing: 14) {
            HStack(spacing: 10) {
                tile("PLAY TYPE", playType.rawValue, delay: 0)
                tile("DIRECTION", direction.rawValue, delay: 0.25)
            }
            Text(points.map { "+\($0)" } ?? "—")
                .font(.system(size: 60, weight: .black).monospacedDigit())
                .foregroundStyle(exact ? Theme.gold : ((points ?? 0) > 0 ? Theme.accent : Theme.dim))
                .shadow(color: exact ? Theme.gold.opacity(0.5) : .clear, radius: 18)
                .scaleEffect(shown ? 1 : 0.3)
                .opacity(shown ? 1 : 0)
                .animation(.spring(response: 0.45, dampingFraction: 0.55).delay(0.55), value: shown)
            Text(label).font(.system(size: 14, weight: .black)).tracking(1.4).textCase(.uppercase)
                .foregroundStyle(Theme.text)
                .opacity(shown ? 1 : 0)
                .animation(.easeOut(duration: 0.3).delay(0.8), value: shown)
        }
        .onAppear { shown = true }
        .onChange(of: animationKey) { _, _ in
            shown = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { shown = true }
        }
        .sensoryFeedback(.success, trigger: shown && (points ?? 0) > 0)
    }

    private func tile(_ title: String, _ value: String, delay: Double) -> some View {
        VStack(spacing: 2) {
            Text(title).font(.system(size: 11, weight: .heavy)).tracking(1.6).foregroundStyle(Theme.muted)
            Text(value).font(.system(size: 28, weight: .black)).foregroundStyle(Theme.text)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
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
