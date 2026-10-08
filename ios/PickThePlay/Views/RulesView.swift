import SwiftUI

/// Rules of the Game: the same wording as the web's /rules page. Static, so it works with no server and
/// before signing in (Onboarding shows it as a sheet; signed-in players have the Rules tab).
struct RulesView: View {
    /// True when shown as a sheet (from onboarding): adds a Done button.
    var showsDone = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        howItWorks
                        points.id("points")
                        directions.id("directions")
                        officialCalls
                    }
                    .padding(16)
                }
                // Store screenshots only: jump to the Points or the Left/Middle/Right section.
                .task {
                    guard let anchor = ScreenshotMode.rulesAnchor else { return }
                    try? await Task.sleep(nanoseconds: 600_000_000)
                    proxy.scrollTo(anchor, anchor: .top)
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Rules of the Game")
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if showsDone {
                        Button("Done") { dismiss() }
                    }
                }
            }
        }
    }

    // MARK: - Sections

    private var howItWorks: some View {
        RulesCard(title: "How it works") {
            RulesBullet("Pick the Play follows a live pro football game, one play at a time.")
            RulesBullet("Before each snap the host opens the play. You have a short window (usually 15 seconds; the countdown is on screen) to make three calls: **Run or Pass**, **Left, Middle or Right**, and **Short, Medium or Long**.")
            RulesBullet("You can change your pick until it locks at the snap or when the countdown ends.")
            RulesBullet("After the play, the official result is read from the live play-by-play data and scored automatically. The host checks it and can correct it, and points land within seconds.")
            RulesBullet("Only scrimmage plays are called. Kickoffs, punts, field goals, extra points and kneel-downs are skipped. A play wiped out by a penalty (no play) is voided and nobody scores.")
            RulesBullet("Play for the live leaderboard, or head-to-head with friends in a private lounge.")
        }
    }

    private var points: some View {
        RulesCard(title: "Points") {
            PointsTable()
            BonusMath()
                .padding(.top, 4)
            RulesParagraph("Example: you call Run · Left · Short and the play is Run · Left · Medium: 20 points. Call Run · Left · Medium: 40.")
            RulesParagraph("Distance is the total yards gained on the play: **Short** 0–5 yards, **Medium** 6–10, **Long** 11 or more. An incomplete pass or no gain is 0 yards, so it counts as Short. A loss of yards scores no distance points, and so no bonus.")
        }
    }

    private var directions: some View {
        RulesCard(title: "Left, Middle and Right") {
            RulesParagraph("Direction is always from the offense's point of view: as the quarterback looks downfield. It doesn't change with the TV camera angle.")

            VStack(alignment: .leading, spacing: 12) {
                RulesParagraph("*Running plays* are called by where the run is directed (the official run location and run gap):")
                RunGapDiagram()
                RulesBullet("**Middle:** up the middle. Any run between the left guard and the right guard, including straight through the A-gaps on either side of the center.")
                RulesBullet("**Left:** any run directed outside the center to the left: at the left guard (inside run), left tackle (off-tackle) or left end (outside sweep or bounce toward the left sideline).")
                RulesBullet("**Right:** the same to the right: right guard, right tackle or right end.")
            }
            .padding(.top, 4)

            VStack(alignment: .leading, spacing: 12) {
                RulesParagraph("*Passing plays* are called by where the ball is thrown as it crosses the line of scrimmage, using the hash marks:")
                PassZoneDiagram()
                RulesBullet("**Left:** thrown outside the left hash mark, out to the left sideline.")
                RulesBullet("**Middle:** thrown between the hash marks (18 feet 6 inches apart).")
                RulesBullet("**Right:** thrown outside the right hash mark, out to the right sideline.")
            }
            .padding(.top, 4)

            RulesParagraph("A sack counts as a pass play with a loss. A quarterback scramble is charted as a run. When no direction is charted, the host makes the call.")
                .padding(.top, 4)
        }
    }

    private var officialCalls: some View {
        RulesCard(title: "Official calls") {
            RulesBullet("All official play calls are derived from the official NFL statistics: play type, run location and gap, pass location and yards gained.")
            RulesBullet("All final calls are at the host's discretion.")
            RulesBullet("Pick the Play is an independent fan game. It is not affiliated with, endorsed by or sponsored by the NFL or any club.")
        }
    }
}

// MARK: - Building blocks

private struct RulesCard<Content: View>: View {
    var title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title3.weight(.black))
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .card()
    }
}

/// A paragraph; literals may use **bold** and *italic* (SwiftUI renders inline Markdown in a LocalizedStringKey).
private struct RulesParagraph: View {
    var text: LocalizedStringKey
    init(_ text: LocalizedStringKey) { self.text = text }

    var body: some View {
        Text(text).font(.callout).foregroundStyle(Theme.text)
    }
}

private struct RulesBullet: View {
    var text: LocalizedStringKey
    init(_ text: LocalizedStringKey) { self.text = text }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("•").foregroundStyle(Theme.accent).accessibilityHidden(true)
            Text(text).foregroundStyle(Theme.text)
        }
        .font(.callout)
    }
}

/// The points table: three parts, the bonus, the perfect call. Shared by Rules and Onboarding.
struct PointsTable: View {
    var scoring: Scoring = .standard

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row("Play type right", detail: "Run / Pass", points: "+\(scoring.type)")
            Divider().overlay(Theme.border)
            row("Direction right", detail: "Left / Middle / Right", points: "+\(scoring.direction)")
            Divider().overlay(Theme.border)
            row("Distance right", detail: "Short / Medium / Long", points: "+\(scoring.yardage)")
            Divider().overlay(Theme.border)
            row("Bonus: all three right", detail: nil, points: "+\(scoring.bonus)", gold: true)
            Divider().overlay(Theme.border)
            HStack(alignment: .firstTextBaseline) {
                Text("Perfect call").font(.headline.weight(.black)).foregroundStyle(Theme.text)
                Spacer(minLength: 8)
                Text("\(scoring.exact)").font(.title3.weight(.black).monospacedDigit()).foregroundStyle(Theme.gold)
            }
            .padding(.vertical, 8)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Points. Play type right, \(scoring.type). Direction right, \(scoring.direction). Distance right, \(scoring.yardage). Bonus for all three right, \(scoring.bonus). Perfect call, \(scoring.exact) points.")
    }

    private func row(_ title: String, detail: String?, points: String, gold: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline) {
            (Text(title).foregroundColor(Theme.text) + Text(verbatim: detail.map { " (\($0))" } ?? "").foregroundColor(Theme.muted))
                .font(.subheadline)
            Spacer(minLength: 8)
            Text(points).font(.subheadline.weight(.black).monospacedDigit()).foregroundStyle(gold ? Theme.gold : Theme.accent)
        }
        .padding(.vertical, 8)
    }
}

/// The bonus math as blocks: [+10 Type] [+10 Direction] [+10 Distance] [+10 Bonus] = 40.
struct BonusMath: View {
    var scoring: Scoring = .standard

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                block("Type", scoring.type, color: Theme.accent)
                block("Direction", scoring.direction, color: Theme.accent)
                block("Distance", scoring.yardage, color: Theme.accent)
                block("Bonus", scoring.bonus, color: Theme.gold)
            }
            HStack(alignment: .firstTextBaseline) {
                Text("All three right, plus the bonus").font(.footnote).foregroundStyle(Theme.muted)
                Spacer(minLength: 8)
                Text("= \(scoring.exact)").font(.title2.weight(.black).monospacedDigit()).foregroundStyle(Theme.gold)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("A perfect call: \(scoring.type) for the play type, plus \(scoring.direction) for the direction, plus \(scoring.yardage) for the distance, plus a \(scoring.bonus) point bonus, equals \(scoring.exact) points.")
    }

    private func block(_ title: String, _ points: Int, color: Color) -> some View {
        VStack(spacing: 2) {
            Text("+\(points)").font(.system(size: 18, weight: .black).monospacedDigit()).foregroundStyle(color)
            Text(title).font(.system(size: 10, weight: .heavy)).tracking(0.6).textCase(.uppercase)
                .foregroundStyle(Theme.muted).lineLimit(1).minimumScaleFactor(0.6)
        }
        .padding(.horizontal, 2)
        .frame(maxWidth: .infinity, minHeight: 54)
        .background(color.opacity(0.14), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(color.opacity(0.55), lineWidth: 1))
    }
}

// MARK: - Diagrams (drawn from behind the quarterback: QB at the bottom, looking up the field)

private extension Direction {
    /// The same colour for a direction in both diagrams.
    var zoneColor: Color {
        switch self {
        case .left: return Theme.blue
        case .middle: return Theme.accent
        case .right: return Theme.warn
        }
    }

    var zoneSymbol: String {
        switch self {
        case .left: return "arrow.up.left"
        case .middle: return "arrow.up"
        case .right: return "arrow.up.right"
        }
    }
}

/// Runs: the offensive line, LE LT LG C RG RT RE. Middle is between the guards (the A-gaps either side of
/// the center); Left and Right are the guard, tackle and end on each side.
private struct RunGapDiagram: View {
    private static let linemen = ["LE", "LT", "LG", "C", "RG", "RT", "RE"]
    private static let zoneHeight: CGFloat = 60
    /// The line of scrimmage (and the linemen on it) and the quarterback behind it.
    private static let lineY: CGFloat = 84
    private static let quarterbackY: CGFloat = 128

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { geo in
                let unit = geo.size.width / 7
                let size = min(unit * 0.8, 36)
                ZStack {
                    // Each lineman sits in a unit-wide slot (LG centred at 2.5 units, C at 3.5, RG at 4.5).
                    // Middle runs from the inside of the left guard to the inside of the right guard (the
                    // A-gaps); the guards, tackles and ends are Left and Right.
                    zone(.left, detail: "Guard · Tackle · End", x: 0, width: unit * 2.8)
                    zone(.middle, detail: "A-gaps", x: unit * 2.8, width: unit * 1.4)
                    zone(.right, detail: "Guard · Tackle · End", x: unit * 4.2, width: unit * 2.8)
                    // Where each call runs: the two A-gaps either side of the center, and outside on each side.
                    ForEach([3, 4], id: \.self) { gap in
                        arrow(.middle).position(x: unit * CGFloat(gap), y: 46)
                    }
                    arrow(.left).position(x: unit * 1.4, y: 46)
                    arrow(.right).position(x: unit * 5.6, y: 46)
                    // The line of scrimmage and the linemen on it.
                    Rectangle().fill(Theme.border).frame(width: geo.size.width, height: 1)
                        .position(x: geo.size.width / 2, y: Self.lineY)
                    ForEach(0..<Self.linemen.count, id: \.self) { index in
                        player(Self.linemen[index], size: size, highlight: index == 3)
                            .position(x: unit * (CGFloat(index) + 0.5), y: Self.lineY)
                    }
                    player("QB", size: size, highlight: true)
                        .position(x: unit * 3.5, y: Self.quarterbackY)
                }
            }
            .frame(height: 148)
            Text("Seen from behind the quarterback, looking downfield")
                .font(.caption).foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Diagram of the offensive line seen from behind the quarterback: left end, left tackle, left guard, center, right guard, right tackle, right end. Middle is between the guards, through the A-gaps on either side of the center. Left is the left guard, tackle and end. Right is the right guard, tackle and end.")
    }

    private func zone(_ direction: Direction, detail: String, x: CGFloat, width: CGFloat) -> some View {
        let color = direction.zoneColor
        return RoundedRectangle(cornerRadius: 8, style: .continuous)
            .fill(color.opacity(0.16))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(color.opacity(0.6), lineWidth: 1))
            .overlay(alignment: .top) {
                VStack(spacing: 2) {
                    Text(direction.rawValue).font(.system(size: 11, weight: .black)).tracking(0.8)
                    Text(detail).font(.system(size: 9, weight: .semibold)).opacity(0.85)
                }
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .foregroundStyle(color)
                .padding(.top, 6)
                .padding(.horizontal, 2)
            }
            .frame(width: max(0, width - 4), height: Self.zoneHeight)
            .position(x: x + width / 2, y: Self.zoneHeight / 2)
    }

    private func arrow(_ direction: Direction) -> some View {
        Image(systemName: direction.zoneSymbol)
            .font(.system(size: 13, weight: .black))
            .foregroundStyle(direction.zoneColor)
    }

    private func player(_ name: String, size: CGFloat, highlight: Bool) -> some View {
        Circle()
            .fill(highlight ? Theme.surface3 : Theme.surface2)
            .overlay(Circle().stroke(highlight ? Theme.text.opacity(0.7) : Theme.border, lineWidth: 1.5))
            .overlay {
                Text(name).font(.system(size: 11, weight: .black)).foregroundStyle(Theme.text)
                    .lineLimit(1).minimumScaleFactor(0.6)
            }
            .frame(width: size, height: size)
    }
}

/// Passes: the field from behind the quarterback; the two hash marks split it into Left | Middle | Right
/// where the ball crosses the line of scrimmage. Not to scale: the middle is drawn wider so it reads.
private struct PassZoneDiagram: View {
    private static let fieldHeight: CGFloat = 176
    private static let scrimmage: CGFloat = 126
    /// Where the hash marks sit, as a fraction of the width.
    private static let leftHash: CGFloat = 0.375
    private static let rightHash: CGFloat = 0.625

    var body: some View {
        VStack(spacing: 6) {
            GeometryReader { geo in
                let width = geo.size.width
                let leftHash = width * Self.leftHash
                let rightHash = width * Self.rightHash
                ZStack {
                    Theme.field
                    // Zones beyond the line of scrimmage.
                    zone(.left, x: 0, width: leftHash)
                    zone(.middle, x: leftHash, width: rightHash - leftHash)
                    zone(.right, x: rightHash, width: width - rightHash)
                    // Yard lines every 5 yards.
                    ForEach([1, 2, 3, 4], id: \.self) { line in
                        Rectangle().fill(Theme.text.opacity(0.14)).frame(width: width, height: 1)
                            .position(x: width / 2, y: Self.scrimmage - CGFloat(line) * 30)
                    }
                    // Hash marks, one a yard.
                    ForEach([Self.leftHash, Self.rightHash], id: \.self) { fraction in
                        ForEach(0..<29, id: \.self) { yard in
                            Rectangle().fill(Theme.text.opacity(0.55)).frame(width: 8, height: 1.5)
                                .position(x: width * fraction, y: 3 + CGFloat(yard) * 6)
                        }
                    }
                    // Line of scrimmage and the quarterback behind it.
                    Rectangle().fill(Theme.blue).frame(width: width, height: 2).position(x: width / 2, y: Self.scrimmage)
                    Text("LINE OF SCRIMMAGE").font(.system(size: 8, weight: .heavy)).tracking(0.4)
                        .foregroundStyle(Theme.blue)
                        .position(x: 54, y: Self.scrimmage + 10)
                    Circle().fill(Theme.surface3)
                        .overlay(Circle().stroke(Theme.text.opacity(0.7), lineWidth: 1.5))
                        .overlay { Text("QB").font(.system(size: 11, weight: .black)).foregroundStyle(Theme.text) }
                        .frame(width: 32, height: 32)
                        .position(x: width / 2, y: Self.scrimmage + 28)
                }
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.border, lineWidth: 1))
            }
            .frame(height: Self.fieldHeight)
            GeometryReader { geo in
                ZStack {
                    Text("Left sideline").position(x: 36, y: 8)
                    Text("Left hash").position(x: geo.size.width * Self.leftHash, y: 8)
                    Text("Right hash").position(x: geo.size.width * Self.rightHash, y: 8)
                    Text("Right sideline").position(x: geo.size.width - 38, y: 8)
                }
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Theme.muted)
            }
            .frame(height: 16)
            Text("Seen from behind the quarterback (not to scale)")
                .font(.caption).foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Diagram of the field seen from behind the quarterback. The two hash marks split it into three zones where the ball crosses the line of scrimmage: Left, outside the left hash mark; Middle, between the hash marks; Right, outside the right hash mark.")
    }

    private func zone(_ direction: Direction, x: CGFloat, width: CGFloat) -> some View {
        let color = direction.zoneColor
        return Rectangle()
            .fill(color.opacity(0.13))
            .overlay(alignment: .top) {
                VStack(spacing: 4) {
                    Text(direction.rawValue).font(.system(size: 12, weight: .black)).tracking(0.8)
                        .lineLimit(1).minimumScaleFactor(0.6)
                    Image(systemName: direction.zoneSymbol).font(.system(size: 14, weight: .black))
                }
                .foregroundStyle(color)
                .padding(.top, 34)
            }
            .frame(width: width, height: Self.scrimmage)
            .position(x: x + width / 2, y: Self.scrimmage / 2)
    }
}
