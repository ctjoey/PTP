import SwiftUI

// Live data on the host console: the card on the Run tab (status, the feed's suggestion, the "feed disagrees"
// banner, the "scored by the feed" line) and the "Pick today's game" sheet. It mirrors the website's console
// (static/js/admin-feed.js). Screens here never talk to the network: every action is a HostState method.

// MARK: - Colours and small helpers

private enum FeedColor {
    static func tone(_ tone: FeedState.Tone) -> Color {
        switch tone {
        case .good: return Theme.accent
        case .wait: return Theme.warn
        case .bad: return Theme.danger
        case .off: return Theme.muted
        }
    }

    static func suggestion(_ status: FeedSuggestion.Status) -> Color {
        switch status {
        case .ready: return Theme.accent
        case .void: return Theme.blue
        case .review: return Theme.warn
        case .held: return Theme.muted
        }
    }
}

/// Whole seconds for the countdown lines, kept in a sane range so odd server numbers can't trap.
private func wholeSeconds(_ value: Double, up: Bool = true) -> Int {
    Int(min(max(value, 0), 604_800).rounded(up ? .up : .toNearestOrAwayFromZero))
}

/// "Nothing found." / "Nothing found" -> a sentence that ends with one full stop.
private func sentence(_ text: String) -> String {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let last = trimmed.last else { return trimmed }
    return ".!?".contains(last) ? trimmed : trimmed + "."
}

private func direction(from text: String?) -> Direction? {
    guard let text else { return nil }
    return text == Direction.legacyMiddle ? .middle : Direction(rawValue: text)
}

/// A rounded outline drawn over a card (the suggestion and the disagreement stand out from the calm cards).
private struct CardOutline: View {
    var color: Color

    var body: some View {
        RoundedRectangle(cornerRadius: 18, style: .continuous)
            .stroke(color, lineWidth: 2)
            .allowsHitTesting(false)
    }
}

/// The state of the live-data connection as a small capsule: READY, WAITING, PAUSED ...
private struct FeedStateChip: View {
    var text: String
    var tone: FeedState.Tone

    var body: some View {
        let color = FeedColor.tone(tone)
        Text(text)
            .font(.system(size: 12, weight: .heavy)).tracking(0.7)
            .foregroundStyle(color)
            .lineLimit(1)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(color.opacity(0.18), in: Capsule())
    }
}

/// One part of the result: RUN, LEFT, MEDIUM 7 yds. Blue ones were chosen by the host, not read from the feed.
private struct FeedResultChip: View {
    enum Style { case feed, loss, mine, noPlay }

    var text: String
    var detail: String?
    var style: Style

    var body: some View {
        let color: Color = {
            switch style {
            case .feed: return Theme.accent
            case .loss: return Theme.danger
            case .mine, .noPlay: return Theme.blue
            }
        }()
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(text).font(.system(size: 15, weight: .heavy)).tracking(0.6)
            if let detail {
                Text(detail).font(.system(size: 12, weight: .heavy)).opacity(0.85)
            }
            if style == .mine {
                Image(systemName: "pencil").font(.system(size: 11, weight: .heavy)).accessibilityHidden(true)
            }
        }
        .foregroundStyle(style == .loss ? Theme.danger : Theme.text)
        .lineLimit(1)
        .minimumScaleFactor(0.7)
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(color.opacity(0.16), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(color, lineWidth: 2))
    }
}

/// A thin progress bar (requests used, seconds left).
private struct FeedBar: View {
    var fraction: Double
    var color: Color

    var body: some View {
        let shown = min(1, max(0, fraction))
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.surface3)
                Capsule().fill(color).frame(width: geo.size.width * shown)
            }
        }
        .frame(height: 8)
        .accessibilityHidden(true)
    }
}

/// A quiet text button (Skip, Fix result, Turn off ...): at least 44 pt tall, no frame.
private struct QuietButton: View {
    var title: String
    var color: Color = Theme.muted
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(color)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - The card on the Run tab

/// Everything about live data under the play controller. Shows nothing without a game or without live-data support.
struct HostLiveDataCard: View {
    @EnvironmentObject var host: HostState

    var body: some View {
        if let feed = host.feed, host.game != nil {
            let scored = scoredInfo(feed)
            VStack(spacing: 14) {
                // What needs the host's eye comes first.
                if let suggestion = feed.suggestion {
                    FeedSuggestionSection(suggestion: suggestion, paused: feed.paused)
                        .id("suggestion-\(suggestion.playId)")
                }
                if let disagreement = feed.disagreement {
                    FeedDisagreementSection(disagreement: disagreement)
                        .id("disagreement-\(disagreement.playId)")
                }
                if let scored {
                    FeedScoredSection(text: scored.text, fixRow: scored.fixRow)
                }
                FeedStatusSection(feed: feed)
                if !feed.linked {
                    FeedConnectSection(feed: feed)
                }
            }
            .sensoryFeedback(.warning, trigger: alertKey(feed)) { _, new in !new.isEmpty }
        }
    }

    /// Changes when a suggestion needs the host's check or the feed disagrees with a scored play; "" otherwise.
    private func alertKey(_ feed: FeedState) -> String {
        if let d = feed.disagreement { return "disagree|\(d.playId)|\(d.feed)|\(d.scored)" }
        if let sg = feed.suggestion, sg.status == .review || sg.warning != nil {
            return "check|\(sg.playId)|\(sg.statusText)|\(sg.warning ?? "")"
        }
        return ""
    }

    private struct ScoredInfo {
        var text: String
        /// The play log row to fix; nil when voided or not fixable.
        var fixRow: HistoryPlay?
    }

    /// "Scored by the feed: ..." for the play that just finished, when the feed (not the host) scored it.
    private func scoredInfo(_ feed: FeedState) -> ScoredInfo? {
        guard let play = host.play, play.state == .resolved else { return nil }
        let row = host.snapshot?.history.first(where: { $0.id == play.id })
        if row?.resolvedBy == "host-fix" { return nil }  // corrected since: the Play log marks it
        guard let last = feed.lastScored, last.playId == play.id else { return nil }
        if last.voided == true || play.voided {
            return ScoredInfo(text: "Voided by the feed: no play.", fixRow: nil)
        }
        let lead = last.auto == false ? "Scored from the feed" : "Scored by the feed"
        let summary = last.summary?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let text = summary.isEmpty ? lead + "." : lead + ": " + summary
        let fixable = row.flatMap { FixDraft(row: $0) != nil ? $0 : nil }
        return ScoredInfo(text: text, fixRow: fixable)
    }
}

// MARK: - Status, buttons and settings

private struct FeedStatusSection: View {
    @EnvironmentObject var host: HostState
    var feed: FeedState

    @State private var working: String?
    @State private var showSettings = false
    @State private var confirmTurnOff = false

    var body: some View {
        HostCard("Live data", systemImage: "dot.radiowaves.left.and.right") {
            statusHeader
            if feed.linked {
                controls
                if let requests = feed.requests, requests.gameCap > 0 {
                    usage(requests)
                    if feed.state == "capped" && requests.game >= requests.gameCap { allowMore }
                }
            }
            settings
        }
        .confirmationDialog("Turn off live data?", isPresented: $confirmTurnOff, titleVisibility: .visible, actions: {
            Button("Turn off live data", role: .destructive) {
                run("unlink") {
                    let done = await host.feedLink(nil)
                    if done { host.show("Live data is off for this game.") }
                    return done
                }
            }
            Button("Keep it on", role: .cancel) {}
        }, message: {
            Text("The feed will stop suggesting and scoring plays. You can follow a game again later.")
        })
    }

    private func run(_ key: String, _ work: @escaping () async -> Bool) {
        guard working == nil else { return }
        working = key
        Task {
            _ = await work()
            working = nil
        }
    }

    // MARK: Header (ticks, because "quiet" and the countdowns change with time alone)

    private var statusHeader: some View {
        TimelineView(.periodic(from: .now, by: 0.5)) { _ in
            let now = host.now()
            let tone = feed.tone(at: now)
            let label = feed.label(at: now)
            let message = (feed.message?.isEmpty == false ? feed.message : nil) ?? feed.defaultMessage
            HStack(alignment: .top, spacing: 12) {
                ToneDot(tone: tone).padding(.top, 5)
                VStack(alignment: .leading, spacing: 6) {
                    VStack(alignment: .leading, spacing: 6) {
                        FeedStateChip(text: label, tone: tone)
                        Text(message)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(Theme.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    if let line = subLine(now: now) {
                        Text(line)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Theme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func subLine(now: Double) -> String? {
        if let at = feed.autoOpenAt, at > now {
            return "Next play opens automatically in \(wholeSeconds(at - now)) s. Pause cancels it."
        }
        guard feed.state == "waiting", let waiting = feed.waiting else { return nil }
        var bits: [String] = []
        if let since = waiting.since { bits.append("waiting \(wholeSeconds(now - since, up: false)) s") }
        if let checks = waiting.checks, checks > 0 { bits.append("\(HostText.count(checks, "check")) so far") }
        if let next = waiting.nextCheckAt { bits.append("next check in \(wholeSeconds(next - now)) s") }
        guard !bits.isEmpty else { return nil }
        let text = bits.joined(separator: " · ")
        return text.prefix(1).uppercased() + String(text.dropFirst())
    }

    // MARK: Pause and check

    private var controls: some View {
        let paused = feed.paused
        return HStack(spacing: 10) {
            HostButton(paused ? "Resume" : "Pause", systemImage: paused ? "play.fill" : "pause.fill",
                       kind: paused ? .primary : .secondary, busy: working == "pause") {
                run("pause") {
                    if host.feed?.paused ?? paused { return await host.feedResume() }
                    return await host.feedPause()
                }
            }
            .disabled(host.isBusy || working != nil || !feed.linked || feed.state == "off" || feed.state == "done")
            HostButton("Check now", systemImage: "arrow.clockwise", busy: working == "check") {
                run("check") { await host.feedCheckNow() }
            }
            .disabled(host.isBusy || working != nil || !feed.linked || ["off", "done", "capped"].contains(feed.state))
        }
    }

    private func usage(_ requests: FeedRequests) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(requests.summary)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(requests.isLow ? Theme.warn : Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
            FeedBar(fraction: Double(requests.percentUsed) / 100, color: requests.isLow ? Theme.warn : Theme.accent)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Requests used this game")
        .accessibilityValue(requests.summary)
    }

    private var allowMore: some View {
        VStack(alignment: .leading, spacing: 8) {
            HostHint("This game has used all its requests, so the feed has stopped. You can allow 100 more.")
            HostButton("Allow 100 more", systemImage: "plus", kind: .warn, busy: working == "more") {
                run("more") { await host.feedAllowMore(100) }
            }
            .disabled(host.isBusy || working != nil)
        }
    }

    // MARK: Settings (folded away: they rarely change)

    private var settings: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                showSettings.toggle()
            } label: {
                HStack(spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Settings and details")
                            .font(.system(size: 15, weight: .bold))
                            .foregroundStyle(Theme.text)
                        Text("Auto-score: \(feed.autoScore ? "on" : "off") · Open next play: \(feed.autoOpen ? "on" : "off")")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Theme.muted)
                            .multilineTextAlignment(.leading)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: showSettings ? "chevron.up" : "chevron.down")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(Theme.muted)
                        .accessibilityHidden(true)
                }
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityValue(showSettings ? "expanded" : "collapsed")
            .accessibilityHint("Shows the live data switches")

            if showSettings {
                FeedSwitchRow(
                    title: "Auto-score clean plays",
                    hint: "The feed's result is scored for you after a few seconds. A sack or a QB scramble counts as no play. Other odd plays (turnovers, penalties) always wait for you.",
                    serverValue: feed.autoScore
                ) { on in await host.feedSetAutoScore(on) }
                FeedSwitchRow(
                    title: "Open next play automatically",
                    hint: "Off by default. Opens the next play for you after a clean score on 1st to 3rd down. Only turn it on once you have seen the typical delay below on a few plays: if the feed runs slow, the next play could open too late.",
                    serverValue: feed.autoOpen
                ) { on in await host.feedSetAutoOpen(on) }
                Text(delayText)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                if feed.linked {
                    QuietButton(title: "Turn off live data for this game", color: Theme.danger) {
                        confirmTurnOff = true
                    }
                    .disabled(host.isBusy || working != nil)
                }
            }
        }
    }

    private var delayText: String {
        guard let median = feed.lag?.median else { return "Typical delay: not measured yet" }
        var text = "Typical delay: \(wholeSeconds(median.rounded())) s"
        if let samples = feed.lag?.samples, samples > 0 { text += " (from \(HostText.count(samples, "play")))" }
        return text
    }
}

/// A switch that shows the server's value, but keeps the host's new choice on screen while the server catches up.
private struct FeedSwitchRow: View {
    @EnvironmentObject var host: HostState
    var title: String
    var hint: String
    var serverValue: Bool
    var send: (Bool) async -> Bool

    @State private var chosen: Bool?
    @State private var attempt = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: Binding(get: { chosen ?? serverValue }, set: { choose($0) })) {
                Text(title).font(.system(size: 16, weight: .bold)).foregroundStyle(Theme.text)
            }
            .tint(Theme.accent)
            .disabled(host.isBusy)
            .accessibilityHint(hint)
            HostHint(hint).accessibilityHidden(true)
        }
        .onChange(of: serverValue) { _, new in
            if chosen == new { chosen = nil }
        }
    }

    private func choose(_ on: Bool) {
        chosen = on
        attempt += 1
        let mine = attempt
        Task {
            let accepted = await send(on)
            if accepted {
                // The server's new state can arrive just after its answer; keep the choice showing until it does.
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
            if attempt == mine { chosen = nil }
        }
    }
}

// MARK: - Not connected yet

private struct FeedConnectSection: View {
    @EnvironmentObject var host: HostState
    var feed: FeedState

    @State private var showPicker = false
    @State private var working = false

    var body: some View {
        HostCard("Follow a real game", systemImage: "football") {
            Text(feed.available
                 ? "Follow a real game and the feed suggests each result for you."
                 : "Live data isn't set up on this server (no Tank01 key), so you score by hand. You can still practice with the recorded game.")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            if feed.available {
                HostButton("Connect live data", systemImage: "antenna.radiowaves.left.and.right", kind: .primary, busy: working) {
                    showPicker = true
                }
                .disabled(host.isBusy || working)
            } else {
                HostButton("Follow the recorded practice game", systemImage: "play.rectangle", kind: .primary, busy: working) {
                    link(id: FeedGame.demoID, recorded: true)
                }
                .disabled(host.isBusy || working)
            }
        }
        .sheet(isPresented: $showPicker) {
            GamePickerSheet { game in link(id: game.feedGameId, recorded: game.isRecorded) }
                .environmentObject(host)
        }
    }

    private func link(id: String, recorded: Bool) {
        guard !working else { return }
        working = true
        Task {
            if await host.feedLink(id) {
                host.show(recorded ? "Following the recorded practice game." : "Live data linked to this game.")
            }
            working = false
        }
    }
}

// MARK: - The feed's suggestion

private struct FeedSuggestionSection: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    var suggestion: FeedSuggestion
    var paused: Bool

    @State private var working: String?
    @State private var confirmVoid = false

    private var sg: FeedSuggestion { suggestion }
    private var color: Color { FeedColor.suggestion(sg.status) }
    private var merged: ResultDraft { drafts.choices.merged(with: sg) }
    private var missing: [FeedSuggestion.Part] { drafts.choices.missing(sg) }
    private var canScore: Bool { drafts.choices.canScore(sg) }
    private var showsSkip: Bool { sg.warning != nil || sg.status == .review || sg.status == .held }

    var body: some View {
        HostCard {
            header
            if !sg.text.isEmpty {
                Text(sg.text)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            resultChips
            ForEach(missing, id: \.self) { part in ask(part) }
            flags
            warning
            if sg.countsDown {
                FeedCountdown(suggestion: sg)
                HostHint(sg.isVoid ? "It voids by itself when the bar runs out. Tap Hold to stop it."
                                   : "It scores by itself when the bar runs out. Tap Hold to stop it.")
            }
            if let note = sg.note(missing: missing, paused: paused) { HostHint(note) }
            buttons
        }
        .overlay(CardOutline(color: color))
        .confirmationDialog("Void this play?", isPresented: $confirmVoid, titleVisibility: .visible, actions: {
            Button("Void play", role: .destructive) { voidNow() }
            Button("Keep it", role: .cancel) {}
        }, message: {
            Text("The feed says this wasn't a real play. Nobody scores for it, and you can't undo this.")
        })
    }

    private func run(_ key: String, _ work: @escaping () async -> Bool) {
        guard working == nil else { return }
        working = key
        Task {
            _ = await work()
            working = nil
        }
    }

    /// Change what the host has chosen for this suggestion (keeping the choices tied to this play).
    private func edit(_ change: (inout SuggestionChoices) -> Void) {
        var next = drafts.choices
        next.sync(to: sg)
        change(&next)
        drafts.choices = next
    }

    // MARK: Header and result

    private var clockLine: String? {
        let parts = [sg.clock, sg.downAndDistance].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var badge: some View {
        Text(sg.badge)
            .font(.system(size: 12, weight: .black)).tracking(1.4)
            .foregroundStyle(color)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
    }

    @ViewBuilder private var clock: some View {
        if let line = clockLine {
            Text(line)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.muted)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
    }

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                badge
                Spacer(minLength: 8)
                clock
            }
            VStack(alignment: .leading, spacing: 4) {
                badge
                clock
            }
        }
    }

    private func isMine(_ part: FeedSuggestion.Part) -> Bool {
        switch part {
        case .playType: return sg.playType == nil
        case .direction: return sg.direction == nil
        case .yardage: return sg.yardage == nil
        }
    }

    private var anyMine: Bool {
        guard !sg.isVoid else { return false }
        let result = merged
        return (result.playType != nil && isMine(.playType)) || (result.direction != nil && isMine(.direction))
            || (result.yardage != nil && isMine(.yardage))
    }

    @ViewBuilder private var resultChips: some View {
        if sg.isVoid {
            FeedResultChip(text: "NO PLAY", detail: nil, style: .noPlay)
        } else if merged.playType != nil || merged.direction != nil || merged.yardage != nil {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { chipList }
                VStack(alignment: .leading, spacing: 8) { chipList }
            }
            if anyMine { HostHint("Blue means you chose it. Tap it to choose again.") }
        }
    }

    @ViewBuilder private var chipList: some View {
        let result = merged
        if let type = result.playType {
            chip(.playType, text: type.rawValue, spoken: type.title)
        }
        if let way = result.direction {
            chip(.direction, text: way.rawValue, spoken: way.title)
        }
        if let far = result.yardage {
            chip(.yardage, text: far.rawValue, detail: result.yards.map { Football.yards($0) },
                 spoken: far.title + (result.yards.map { ", " + Football.yards($0) } ?? ""), loss: far == .loss)
        }
    }

    @ViewBuilder private func chip(_ part: FeedSuggestion.Part, text: String, detail: String? = nil,
                                   spoken: String, loss: Bool = false) -> some View {
        if isMine(part) {
            Button {
                forget(part)
            } label: {
                FeedResultChip(text: text, detail: detail, style: .mine).frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(part.label.capitalized): \(spoken), chosen by you")
            .accessibilityHint("Double tap to choose again")
        } else {
            FeedResultChip(text: text, detail: detail, style: loss ? .loss : .feed)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(part.label.capitalized): \(spoken)")
        }
    }

    private func forget(_ part: FeedSuggestion.Part) {
        edit { choices in
            switch part {
            case .playType: choices.playType = nil
            case .direction: choices.direction = nil
            case .yardage: choices.yardage = nil
            }
        }
    }

    // MARK: Parts the feed couldn't read

    @ViewBuilder private func ask(_ part: FeedSuggestion.Part) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Pick the \(part.label)")
                .font(.system(size: 12, weight: .heavy)).tracking(1.0)
                .textCase(.uppercase)
                .foregroundStyle(Theme.warn)
                .accessibilityAddTraits(.isHeader)
            switch part {
            case .playType:
                HStack(spacing: 8) {
                    ForEach(PlayType.allCases) { option in
                        PickButton(title: option.rawValue, caption: option.caption, selected: false, enabled: true,
                                   height: 50, spoken: option.title) {
                            edit { $0.playType = option }
                        }
                    }
                }
            case .direction:
                HStack(spacing: 8) {
                    ForEach(Direction.allCases) { option in
                        PickButton(title: option.rawValue, symbol: option.symbol, selected: false, enabled: true,
                                   height: 50, spoken: "\(option.title), as the QB looks downfield") {
                            edit { $0.direction = option }
                        }
                    }
                }
            case .yardage:
                VStack(spacing: 8) {
                    HStack(spacing: 8) { yardageButton(.short); yardageButton(.medium) }
                    HStack(spacing: 8) { yardageButton(.long); yardageButton(.loss) }
                }
            }
        }
        .padding(10)
        .background(Theme.warn.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.warn, lineWidth: 1.5))
        .accessibilityElement(children: .contain)
    }

    private func yardageButton(_ option: YardageOutcome) -> some View {
        PickButton(title: option.rawValue, caption: option.pick?.range ?? "Lost yards", selected: false, enabled: true,
                   height: 50, spoken: option.pick.map { "\($0.title), \($0.spokenRange)" } ?? "Loss, lost yards") {
            edit { $0.yardage = option }
        }
    }

    // MARK: Flags and warning

    @ViewBuilder private var flags: some View {
        if !sg.flags.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(sg.flags.enumerated()), id: \.offset) { entry in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").accessibilityHidden(true)
                        Text(entry.element).fixedSize(horizontal: false, vertical: true)
                    }
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.text)
                }
            }
        }
    }

    @ViewBuilder private var warning: some View {
        if let text = sg.warning, !text.isEmpty {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 14, weight: .bold))
                    .accessibilityHidden(true)
                Text(text)
                    .font(.system(size: 14, weight: .bold))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(Theme.warn)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.warn.opacity(0.14), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.warn, lineWidth: 1))
        }
    }

    // MARK: Buttons

    private var buttons: some View {
        VStack(spacing: 10) {
            if sg.isVoid {
                HostButton("Void play", systemImage: "nosign", kind: .danger, busy: working == "score") {
                    confirmVoid = true
                }
                .disabled(host.isBusy || working != nil)
            } else {
                HostButton("Score now", systemImage: "checkmark", kind: .primary, busy: working == "score") {
                    run("score") { await host.feedAccept(sg, choices: drafts.choices) }
                }
                .disabled(host.isBusy || working != nil || !canScore)
            }
            HStack(spacing: 10) {
                if sg.countsDown {
                    HostButton("Hold", systemImage: "hand.raised.fill", busy: working == "hold") {
                        run("hold") { await host.feedHold() }
                    }
                    .disabled(host.isBusy || working != nil)
                }
                HostButton("Change", systemImage: "pencil", busy: working == "change") { change() }
                    .disabled(host.isBusy || working != nil)
            }
            if showsSkip {
                QuietButton(title: "Skip this feed play") {
                    run("skip") { await host.feedSkip(playID: sg.playId) }
                }
                .disabled(host.isBusy || working != nil)
                HostHint("Use this when the feed is showing a different play than the one you ran. You then score by hand.")
            }
        }
    }

    /// The host confirmed "Void play". If the countdown already did it, or the feed moved on, there is nothing to do.
    private func voidNow() {
        guard host.feed?.suggestion?.playId == sg.playId else { return }
        run("score") { await host.feedAccept(sg, choices: drafts.choices) }
    }

    /// Stop the countdown, then put the suggestion into the Run tab's result boxes so the host can adjust it.
    private func change() {
        let current = sg
        run("change") {
            if current.countsDown {
                // If the hold didn't work the countdown may still score it: leave the error showing and stop here.
                guard await host.feedHold() else { return false }
            }
            drafts.load(current)
            host.show(current.isVoid ? "Choose the result above, then tap Score Play."
                                     : "Adjust the result above, then tap Score Play.")
            return true
        }
    }
}

/// "Scoring in 5 s" with a bar that runs down. The bar's total is the most time left that it has seen for this timer.
private struct FeedCountdown: View {
    @EnvironmentObject var host: HostState
    var suggestion: FeedSuggestion

    @State private var total: Double = 1

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.5)) { _ in
            let remaining = suggestion.secondsUntilAuto(at: host.now()) ?? 0
            let verb = suggestion.status == .void ? "Voiding" : "Scoring"
            VStack(alignment: .leading, spacing: 6) {
                Text(remaining > 0.05 ? "\(verb) in \(wholeSeconds(remaining)) s" : "\(verb) now…")
                    .font(.system(size: 17, weight: .black).monospacedDigit())
                    .foregroundStyle(Theme.text)
                FeedBar(fraction: remaining / max(total, 1, remaining), color: FeedColor.suggestion(suggestion.status))
            }
            .accessibilityElement(children: .combine)
        }
        .task(id: suggestion.autoAt) {
            total = max(1, suggestion.secondsUntilAuto(at: host.now()) ?? 1)
        }
    }
}

// MARK: - The feed disagrees with a scored play

private struct FeedDisagreementSection: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts
    var disagreement: FeedDisagreement

    @State private var working: String?

    private var row: HistoryPlay? { host.snapshot?.history.first(where: { $0.id == disagreement.playId }) }

    var body: some View {
        let d = disagreement
        HostCard {
            Text(row.map { "The feed disagrees with play #\($0.playNumber)" } ?? "The feed disagrees with a scored play")
                .font(.system(size: 16, weight: .black))
                .foregroundStyle(Theme.danger)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text("Feed says \(d.feed), but this play was scored \(d.scored).")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            if let quote = d.text, !quote.isEmpty {
                Text("\"\(quote)\"")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                HostButton("Fix result", systemImage: "wrench.and.screwdriver", kind: .warn) { openFix() }
                    .disabled(host.isBusy || working != nil || row?.isFixable != true)
                HostButton("Dismiss", busy: working == "dismiss") {
                    guard working == nil else { return }
                    working = "dismiss"
                    Task {
                        _ = await host.feedDismiss()
                        working = nil
                    }
                }
                .disabled(host.isBusy || working != nil)
            }
            if row?.isFixable != true {
                HostHint("This play can't be fixed from here, so you can only dismiss this.")
            }
        }
        .overlay(CardOutline(color: Theme.danger))
    }

    /// Open the Fix result editor on the play, starting from what the feed says.
    private func openFix() {
        guard let row else { return }
        let d = disagreement
        let preset = ResultDraft(
            playType: d.feedPlayType.flatMap { PlayType(rawValue: $0) },
            direction: direction(from: d.feedDirection),
            yardage: d.feedYardage.flatMap { YardageOutcome(rawValue: $0) } ?? d.feedYards.map { YardageOutcome(yards: $0) },
            yards: d.feedYards)
        drafts.fix = FixDraft(row: row, preset: preset)
    }
}

// MARK: - Scored by the feed

private struct FeedScoredSection: View {
    @EnvironmentObject var drafts: HostDrafts
    var text: String
    var fixRow: HistoryPlay?

    var body: some View {
        HostCard {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(Theme.accent)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 0) {
                    Text(text)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if let fixRow {
                        QuietButton(title: "Fix result", color: Theme.blue) {
                            drafts.fix = FixDraft(row: fixRow)
                        }
                        .accessibilityLabel("Fix result for play \(fixRow.playNumber)")
                    }
                }
            }
        }
    }
}

// MARK: - Pick today's game

/// "Pick today's game": the day's schedule, plus the recorded practice game. Calls `onPick` and dismisses itself.
/// It never picks a game by itself. It reads the day's schedule when it opens (the server remembers it for a few
/// minutes); moving to another day only loads when the host taps Load games, because each load can use a request.
struct GamePickerSheet: View {
    var onPick: (FeedGame) -> Void

    @EnvironmentObject var host: HostState
    @Environment(\.dismiss) private var dismiss

    @State private var date = Date()
    @State private var loaded: FeedGames?
    /// The day (YYYYMMDD) `loaded` is for.
    @State private var loadedKey: String?
    @State private var loading = false
    @State private var failure: String?

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.setLocalizedDateFormatFromTemplate("EEEMMMd")
        return formatter
    }()

    private static let practiceFallback = FeedGame(
        feedGameId: FeedGame.demoID,
        away: FeedTeam(abbr: "CAR", name: "Carolina", primary: "#0085CA", secondary: "#101820"),
        home: FeedTeam(abbr: "WSH", name: "Washington", primary: "#5A1414", secondary: "#FFB612"),
        time: "Anytime", status: "Practice game (no requests used)")

    private var key: String { HostText.scheduleDate(date) }
    private var dayText: String { Self.dayFormatter.string(from: date) }
    private var isToday: Bool { Calendar.current.isDateInToday(date) }
    /// The server can't read the schedule (no Tank01 key): only the practice game is offered.
    private var practiceOnly: Bool { host.feed?.available == false || loaded?.available == false }
    /// The list on screen is for the day shown in the date row.
    private var listIsCurrent: Bool { loaded != nil && loadedKey == key }
    private var practiceGame: FeedGame { loaded?.demo ?? Self.practiceFallback }
    /// Why only the practice game is offered: the server's own words when it sent some.
    private var practiceNote: String {
        if loaded?.available == false, let why = loaded?.error, !why.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return sentence(why)
        }
        return "Live data isn't set up on this server (no Tank01 key). Only the recorded practice game is offered."
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    if practiceOnly {
                        HostCard {
                            HostHint(practiceNote)
                        }
                    } else {
                        dateCard
                        if listIsCurrent, let games = loaded?.games, !games.isEmpty {
                            gamesCard(games)
                        }
                    }
                    practiceCard
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Pick today's game")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .task { await load() }
        .onChange(of: key) { _, _ in failure = nil }
    }

    // MARK: Date and loading

    private var dateCard: some View {
        HostCard("Date", systemImage: "calendar") {
            HStack(spacing: 8) {
                dayButton(symbol: "chevron.left", label: "Previous day", step: -1)
                Text(dayText)
                    .font(.system(size: 22, weight: .black))
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .frame(maxWidth: .infinity)
                    .accessibilityLabel("Date: \(dayText)")
                dayButton(symbol: "chevron.right", label: "Next day", step: 1)
            }
            if !isToday {
                QuietButton(title: "Back to today", color: Theme.blue) { date = Date() }
            }
            HostButton(loadTitle, systemImage: "arrow.clockwise", kind: listIsCurrent ? .secondary : .primary, busy: loading) {
                Task { await load() }
            }
            .disabled(loading)
            status
        }
    }

    private var loadTitle: String {
        if failure != nil && !listIsCurrent { return "Try again" }
        return listIsCurrent ? "Reload" : "Load games"
    }

    private func dayButton(symbol: String, label: String, step: Int) -> some View {
        Button {
            date = Calendar.current.date(byAdding: .day, value: step, to: date) ?? date
        } label: {
            Image(systemName: symbol)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(Theme.text)
                .frame(width: 48, height: 48)
                .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Theme.border, lineWidth: 1))
        }
        .buttonStyle(PressStyle())
        .accessibilityLabel(label)
    }

    /// The line under the buttons: what is happening, what was found, or what went wrong.
    @ViewBuilder private var status: some View {
        if loading {
            HStack(spacing: 10) {
                ProgressView()
                Text("Loading the schedule…")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.muted)
            }
        } else if let failure {
            HostHint(failure, color: Theme.danger)
        } else if listIsCurrent, let loaded {
            HostHint(foundText(loaded))
        } else {
            HostHint("Tap Load games to see the games on \(dayText).")
        }
    }

    private func foundText(_ data: FeedGames) -> String {
        let count = data.games.count
        guard count > 0 else { return "No games found for \(dayText). Try another date, or pick the practice game." }
        let used = data.cached == true ? "from a recent check, no new request" : "1 request used"
        return "\(count) \(count == 1 ? "game" : "games") found for \(dayText) (\(used))."
    }

    private func load() async {
        guard !loading else { return }
        let wanted = date
        let wantedKey = HostText.scheduleDate(wanted)
        loading = true
        failure = nil
        do {
            let data = try await host.loadGames(date: wantedKey)
            loaded = data
            loadedKey = wantedKey
            if let problem = data.error, !problem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                failure = sentence(problem) + " You can still pick the practice game."
            }
        } catch {
            if !Task.isCancelled, !(error is CancellationError) {
                let why: String
                if let api = error as? APIError {
                    why = api.isUnauthorized ? "The admin key was rejected" : api.message
                } else {
                    why = error.localizedDescription
                }
                let reason = why.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "."))
                failure = "Couldn't load the schedule: \(reason). You can still pick the practice game."
            }
        }
        loading = false
    }

    // MARK: Games

    private func gamesCard(_ games: [FeedGame]) -> some View {
        HostCard("Games on \(dayText)", systemImage: "list.bullet") {
            ForEach(games) { game in
                GamePickRow(game: game, names: "\(game.away.name) at \(game.home.name)", detail: detail(game),
                            spoken: game.label, enabled: !host.isBusy) { pick(game) }
            }
        }
    }

    private func detail(_ game: FeedGame) -> String? {
        var bits: [String] = []
        if let time = game.time, !time.isEmpty { bits.append(time) }
        if let status = game.status, !status.isEmpty, status != "Scheduled" { bits.append(status) }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
    }

    private var practiceCard: some View {
        HostCard(practiceOnly ? "Practice" : "Or practice", systemImage: "play.rectangle") {
            let game = practiceGame
            GamePickRow(game: game, names: "Practice with a recorded game (no requests used)", detail: nil,
                        spoken: "Practice with a recorded game, no requests used", enabled: !host.isBusy) { pick(game) }
        }
    }

    private func pick(_ game: FeedGame) {
        onPick(game)
        dismiss()
    }
}

/// One big tappable game: the team abbreviations in large type, the full names and the time under them.
private struct GamePickRow: View {
    var game: FeedGame
    var names: String
    var detail: String?
    var spoken: String
    var enabled: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        team(game.away)
                        Text("at").font(.system(size: 14, weight: .bold)).foregroundStyle(Theme.muted)
                        team(game.home)
                    }
                    Text(names)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if let detail {
                        Text(detail)
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(Theme.text)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(Theme.dim)
                    .accessibilityHidden(true)
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
            .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Theme.border, lineWidth: 1))
        }
        .buttonStyle(PressStyle())
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.5)
        .accessibilityLabel(spoken)
        .accessibilityHint("Double tap to follow this game")
    }

    private func team(_ team: FeedTeam) -> some View {
        let abbreviation = (team.abbr?.isEmpty == false ? team.abbr : nil) ?? Football.abbreviation(team.name)
        return HStack(spacing: 6) {
            Circle().fill(Color(hex: team.primary)).frame(width: 12, height: 12)
                .overlay(Circle().stroke(Color(hex: team.secondary), lineWidth: 2))
                .accessibilityHidden(true)
            Text(abbreviation)
                .font(.system(size: 26, weight: .black))
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
    }
}
