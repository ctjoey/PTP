import SwiftUI

// The host console's "Log" tab (this game's leaderboard and play log) and the "Fix result" sheet.
// Ports the website's Leaderboard and Play Log cards (admin.js renderBoard / renderHistory) and its Fix result
// editor (admin-feed.js openFix / paintEditor / saveFix). One difference on purpose: the website offers Fix result
// only while live data is on; here every scored play can be fixed, because the server's correct_play works either way.

// MARK: - The Log tab

struct HostLogView: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts

    /// How many leaderboard rows fit comfortably on a phone.
    private static let boardSize = 10

    var body: some View {
        VStack(spacing: 14) {
            leaderboardCard
            playLogCard
        }
    }

    // MARK: Leaderboard

    private var leaderboardCard: some View {
        let rows = host.snapshot?.leaderboard ?? []
        let shown = Array(rows.prefix(Self.boardSize))
        let ranked = max(host.snapshot?.rankedPlayers ?? 0, rows.count)
        return HostCard("Leaderboard · this game", systemImage: "list.number") {
            if shown.isEmpty {
                LogEmptyNote(text: "No scores yet.")
            } else {
                VStack(spacing: 8) {
                    ForEach(shown) { row in
                        LogBoardLine(row: row)
                    }
                }
                HostHint(rankedText(ranked: ranked, shown: shown.count))
            }
        }
    }

    /// "12 players ranked", or "Top 10 of 37 players ranked" when the list is cut short.
    private func rankedText(ranked: Int, shown: Int) -> String {
        let players = HostText.count(ranked, "player")
        return ranked > shown ? "Top \(shown) of \(players) ranked" : "\(players) ranked"
    }

    // MARK: Play log

    private var playLogCard: some View {
        let plays = host.snapshot?.history ?? []
        return HostCard("Play log", systemImage: "list.bullet.rectangle") {
            if plays.isEmpty {
                LogEmptyNote(text: "Plays you run show up here.")
            } else {
                HostHint("Newest first. If a play was scored wrongly, tap Fix result.")
                VStack(spacing: 8) {
                    ForEach(plays) { play in
                        LogPlayLine(play: play, busy: host.isBusy) { openFix(play) }
                    }
                }
            }
        }
    }

    private func openFix(_ play: HistoryPlay) {
        guard let draft = FixDraft(row: play) else {
            host.show("This play has no result to start from, so it can't be fixed here.", isError: true)
            return
        }
        drafts.fix = draft
    }
}

// MARK: - Pieces of the Log tab

/// "No scores yet." and friends.
private struct LogEmptyNote: View {
    var text: String

    var body: some View {
        Text(text)
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(Theme.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// One leaderboard line: rank, name, "5 picks · 2 perfect", score.
private struct LogBoardLine: View {
    var row: BoardRow

    var body: some View {
        let rank = String(row.rank)
        let score = String(row.score)
        HStack(spacing: 10) {
            Text(rank)
                .font(.system(size: 17, weight: .black).monospacedDigit())
                .foregroundStyle(row.rank == 1 ? Theme.gold : Theme.muted)
                .frame(minWidth: 26, alignment: .leading)
                .fixedSize()
            VStack(alignment: .leading, spacing: 2) {
                Text(row.username)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(Theme.text)
                    .lineLimit(2)
                Text(detail)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.muted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(score)
                .font(.system(size: 20, weight: .black).monospacedDigit())
                .foregroundStyle(Theme.text)
                .fixedSize()
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Rank \(rank), \(row.username). \(detail). \(score) points.")
    }

    /// "5 picks · 2 perfect"
    private var detail: String { "\(HostText.count(row.picks, "pick")) · \(row.exactHits) perfect" }
}

/// One play in the log, with its Fix result button.
private struct LogPlayLine: View {
    var play: HistoryPlay
    /// An action is waiting for the server: hold off on starting a fix.
    var busy: Bool
    var onFix: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Text("#" + String(play.playNumber))
                    .font(.system(size: 15, weight: .black).monospacedDigit())
                    .foregroundStyle(Theme.muted)
                    .frame(minWidth: 36, alignment: .leading)
                    .fixedSize()
                VStack(alignment: .leading, spacing: 4) {
                    Text(play.resultText)
                        .font(.system(size: 16, weight: .heavy))
                        .foregroundStyle(play.voided ? Theme.muted : Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if play.downAndDistance != nil || !tags.isEmpty {
                        HStack(spacing: 6) {
                            if let dd = play.downAndDistance {
                                Text(dd)
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundStyle(Theme.muted)
                            }
                            ForEach(tags, id: \.text) { tag in
                                LogTagChip(text: tag.text, color: tag.color)
                            }
                        }
                    }
                    Text(picksText)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.muted)
                    if let feedText {
                        Text(feedText)
                            .font(.system(size: 12, weight: .regular))
                            .foregroundStyle(Theme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            if play.isFixable {
                Button(action: onFix) {
                    Label("Fix result", systemImage: "pencil")
                        .font(.system(size: 15, weight: .bold))
                        .frame(minHeight: 44)
                }
                .buttonStyle(.bordered)
                .tint(Theme.text)
                .disabled(busy)
                .accessibilityLabel("Fix result for play \(play.playNumber)")
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    // MARK: What the row says

    private struct Tag {
        var text: String
        var color: Color
        var spoken: String
    }

    /// The markers beside the result, as on the website: auto, corrected, and no play.
    private var tags: [Tag] {
        var out: [Tag] = []
        if play.voided {
            out.append(Tag(text: "NO PLAY", color: Theme.danger, spoken: "No play"))
        } else if play.resolvedBy == "host-fix" {
            out.append(Tag(text: "CORRECTED", color: Theme.warn, spoken: "Corrected after scoring"))
        } else if play.resolvedBy == "feed" {
            out.append(Tag(text: "AUTO", color: Theme.muted, spoken: "Scored automatically from the live feed"))
        }
        return out
    }

    /// "5 picks · 2 perfect". The perfect count only means something once the play is scored.
    private var picksText: String {
        let picks = HostText.count(play.picks, "pick")
        guard play.state == .resolved, !play.voided else { return picks }
        return "\(picks) · \(play.exactHits) perfect"
    }

    /// What the live feed said about the play, when it said anything.
    private var feedText: String? {
        guard let text = play.feedText?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }

    private var spoken: String {
        var parts = ["Play \(play.playNumber)"]
        if play.voided {
            parts.append("Voided, no play")
        } else if let outcome = play.outcome {
            parts.append(outcome.spokenSummary)
        } else {
            parts.append(play.state.rawValue.lowercased())
        }
        if let dd = play.downAndDistance { parts.append(dd) }
        parts.append(contentsOf: tags.map(\.spoken))
        parts.append(picksText)
        if let feedText { parts.append("Live feed said: " + feedText) }
        return parts.joined(separator: ". ")
    }
}

/// The small marker beside a result ("AUTO", "CORRECTED", "NO PLAY").
private struct LogTagChip: View {
    var text: String
    var color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .heavy))
            .tracking(0.8)
            .foregroundStyle(color)
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule())
    }
}

// MARK: - Fix result

/// Opened by the console with `.sheet(item: $drafts.fix)`: change how a scored play was scored, and every
/// player's points for it are worked out again. Everything the host picks lives in `drafts.fix`, so nothing is
/// lost if the sheet is rebuilt while updates stream in.
struct FixResultSheet: View {
    @EnvironmentObject var host: HostState
    @EnvironmentObject var drafts: HostDrafts

    /// Save fix is waiting for the server.
    @State private var saving = false
    @FocusState private var yardsFocused: Bool

    /// Same order as the website's editor (the model's own order puts Loss before Long).
    private static let distanceChoices: [YardageOutcome] = [.short, .medium, .long, .loss]
    /// The longest thing worth typing in the yards box ("-99" is three characters; the rest is room for typos).
    private static let longestYardsText = 6

    var body: some View {
        NavigationStack {
            ScrollView {
                if let fix = drafts.fix {
                    editor(fix)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Theme.bg.ignoresSafeArea())
            .overlay(alignment: .top) {
                // The console's own banner is hidden behind this sheet, so errors (and "not connected") show here too.
                HostNoticeBanner()
                    .animation(.easeInOut(duration: 0.25), value: host.notice)
            }
            .safeAreaInset(edge: .bottom) { saveBar }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { cancel() }
                        .disabled(saving)
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    private var title: String {
        guard let fix = drafts.fix else { return "Fix result" }
        return "Fix result for play #" + String(fix.playNumber)
    }

    // MARK: The editor

    private func editor(_ fix: FixDraft) -> some View {
        VStack(spacing: 14) {
            HostCard("Scored now", systemImage: "checkmark.circle") {
                Text(fix.original.summary)
                    .font(.system(size: 20, weight: .black))
                    .foregroundStyle(Theme.text)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                if let row = row(for: fix) {
                    HostHint(picksLine(row))
                    if let said = feedSaid(row) { HostHint("Live feed said: " + said) }
                }
            }
            HostCard("What really happened", systemImage: "pencil") {
                VStack(alignment: .leading, spacing: 8) {
                    groupLabel("Run or pass?")
                    HStack(spacing: 10) {
                        ForEach(PlayType.allCases) { option in
                            PickButton(title: option.rawValue, caption: option.caption,
                                       selected: fix.result.playType == option, enabled: !saving,
                                       height: 54, spoken: option.title) {
                                edit { $0.choose(option) }
                            }
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    groupLabel("Which way?", hint: "As the QB looks downfield")
                    HStack(spacing: 10) {
                        ForEach(Direction.allCases) { option in
                            PickButton(title: option.rawValue, symbol: option.symbol,
                                       selected: fix.result.direction == option, enabled: !saving,
                                       height: 54, spoken: "\(option.title), as the QB looks downfield") {
                                edit { $0.choose(option) }
                            }
                        }
                    }
                }
                VStack(alignment: .leading, spacing: 8) {
                    groupLabel("How far?")
                    HStack(spacing: 6) {
                        ForEach(Self.distanceChoices, id: \.self) { option in
                            PickButton(title: option.rawValue, caption: caption(for: option),
                                       selected: fix.result.yardage == option, enabled: !saving,
                                       height: 54, spoken: spoken(for: option)) {
                                edit { $0.choose(option) }
                            }
                        }
                    }
                }
                yardsSection(fix)
                HostHint("Everyone's points for this play are re-scored.")
            }
        }
        .padding(16)
    }

    private func groupLabel(_ title: String, hint: String? = nil) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).kicker()
            if let hint {
                Text(hint)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private func caption(for option: YardageOutcome) -> String {
        option.pick?.range ?? "Lost yards"
    }

    private func spoken(for option: YardageOutcome) -> String {
        guard let pick = option.pick else { return "Loss, the offense lost yards" }
        return "\(pick.title), \(pick.spokenRange)"
    }

    // MARK: Exact yards

    private func yardsSection(_ fix: FixDraft) -> some View {
        let typingMinus = fix.result.yardsText.trimmingCharacters(in: .whitespaces) == "-"
        return VStack(alignment: .leading, spacing: 8) {
            groupLabel("Exact yards", hint: "Optional")
            TextField("e.g. 7 or -4", text: yardsBinding)
                .keyboardType(.numbersAndPunctuation)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .onSubmit { yardsFocused = false }
                .focused($yardsFocused)
                .font(.system(size: 20, weight: .heavy))
                .foregroundStyle(Theme.text)
                .padding(.horizontal, 14)
                .frame(minHeight: 50)
                .background(Theme.surface2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(yardsFocused ? Theme.accent : Theme.border, lineWidth: 1))
                .disabled(saving)
                .accessibilityLabel("Yards gained, optional. Use a minus sign for a loss.")
            if typingMinus {
                HostHint("Now type the number, like -4.")
            } else if fix.result.yardsAreInvalid {
                HostHint("Yards must be a whole number from -99 to 99.", color: Theme.danger)
            } else {
                HostHint("If you type the yards, the distance above is picked for you.")
            }
        }
    }

    /// The yards box edits the draft directly: typing a number picks its distance (`ResultDraft.typeYards`).
    private var yardsBinding: Binding<String> {
        Binding(
            get: { drafts.fix?.result.yardsText ?? "" },
            set: { newValue in
                let text = String(newValue.prefix(Self.longestYardsText))
                edit { $0.typeYards(text) }
            })
    }

    // MARK: Saving

    /// Changes the draft in place and writes it back, so the sheet always shows what `drafts.fix` holds.
    private func edit(_ change: (inout ResultDraft) -> Void) {
        guard var fix = drafts.fix else { return }
        change(&fix.result)
        drafts.fix = fix
    }

    private func cancel() {
        yardsFocused = false
        drafts.fix = nil
    }

    private func save(_ fix: FixDraft) {
        guard canSave(fix), !host.isBusy, !saving else { return }
        yardsFocused = false
        saving = true
        let host = self.host
        let drafts = self.drafts
        Task {
            let worked = await host.correctPlay(playID: fix.playID, result: fix.result)
            saving = false
            guard worked else { return }  // the error is already showing; the sheet stays open to try again
            host.show("Corrected: " + fix.result.summary)
            drafts.fix = nil
        }
    }

    /// Typed yards that sit in a different distance than the one picked (the server would refuse them).
    private func yardsDisagree(_ fix: FixDraft) -> Bool {
        guard let yards = fix.result.yards, let yardage = fix.result.yardage else { return false }
        return YardageOutcome(yards: yards) != yardage
    }

    private func canSave(_ fix: FixDraft) -> Bool { fix.canSave && !yardsDisagree(fix) }

    // MARK: Save bar

    @ViewBuilder
    private var saveBar: some View {
        if let fix = drafts.fix {
            let line = status(fix)
            VStack(spacing: 8) {
                HostHint(line.text, color: line.color)
                HostButton("Save fix", systemImage: "checkmark.circle.fill", kind: .primary, busy: saving) {
                    save(fix)
                }
                .disabled(!canSave(fix) || host.isBusy || saving)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(Theme.bg.ignoresSafeArea(edges: .bottom))
            .overlay(alignment: .top) { Rectangle().fill(Theme.border).frame(height: 1) }
        }
    }

    /// The line above Save fix: why it is greyed out, or what will be saved.
    private func status(_ fix: FixDraft) -> (text: String, color: Color) {
        if fix.result.yardsAreInvalid {
            let typingMinus = fix.result.yardsText.trimmingCharacters(in: .whitespaces) == "-"
            return typingMinus ? ("Type the rest of the number, or clear the yards.", Theme.muted)
                : ("Fix the yards above, then save.", Theme.danger)
        }
        if !fix.result.isComplete { return ("Choose run or pass, a direction and a distance.", Theme.muted) }
        if yardsDisagree(fix) { return ("The yards you typed don't fit that distance. Pick the distance again.", Theme.warn) }
        if !fix.isChanged { return ("This is how it is scored now. Change something to save.", Theme.muted) }
        return ("New result: " + fix.result.summary, Theme.text)
    }

    // MARK: Context from the log

    private func row(for fix: FixDraft) -> HistoryPlay? {
        host.snapshot?.history.first { $0.id == fix.playID }
    }

    /// "5 picks · 2 perfect"
    private func picksLine(_ row: HistoryPlay) -> String {
        "\(HostText.count(row.picks, "pick")) on this play · \(row.exactHits) perfect now"
    }

    private func feedSaid(_ row: HistoryPlay) -> String? {
        guard let text = row.feedText?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        return text
    }
}
