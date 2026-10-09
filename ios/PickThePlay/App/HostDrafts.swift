import SwiftUI

/// The host's half-finished entries on the console (the result being picked, the down and distance, the timer, the
/// parts of a suggestion the feed couldn't read, the game being set up, a fix in progress, the Players search and the
/// Message draft). They live apart from `HostState`'s server state because they are about this screen, not the
/// server; `update(from:)` keeps them in step with every `admin_state`. `HostState` owns one for the whole run of the
/// app, so switching tabs (or closing the console for a moment) never loses what the host was typing.
@MainActor
final class HostDrafts: ObservableObject {
    /// The result being entered by hand for the open or locked play.
    @Published var result = ResultDraft()
    @Published var down = DownDraft()
    /// The Timer (s) box: how long players get to pick. The last value stays until the host changes it.
    @Published var timer = TimerDraft()
    /// The Players tab's "Find a name" box.
    @Published var playerSearch = ""
    /// The Message tab's text, until it is sent.
    @Published var messageDraft = ""
    /// What the host chose for the parts of the feed's suggestion that it couldn't read.
    @Published var choices = SuggestionChoices()
    /// A game picked from the schedule (or the practice game), waiting for Create Game.
    @Published var pickedGame: FeedGame?
    /// The Fix result editor, when open.
    @Published var fix: FixDraft?

    private var lastPlayID: Int?
    private var lastGameID: Int?
    private var holdKey: String?

    /// Called for every `admin_state`.
    func update(from state: AdminState) {
        if state.game?.id != lastGameID {
            lastGameID = state.game?.id
            down.reset()
            result.clear()
            choices = SuggestionChoices()
            fix = nil
            pickedGame = nil
        }
        if state.play?.id != lastPlayID {
            // A new play (perhaps opened from another console): start its result entry fresh.
            lastPlayID = state.play?.id
            result.clear()
        }
        down.prefill(from: state.feed?.nextDown, gameID: state.game?.id, lastPlayID: state.play?.id,
                     hasActivePlay: state.hasActivePlay)
        choices.sync(to: state.feed?.suggestion)
        if let fix, !state.history.contains(where: { $0.id == fix.playID && $0.isFixable }) { self.fix = nil }
    }

    /// The host started choosing a result by hand: stop the feed suggestion's countdown so the two can't race.
    func manualEditStarted(feed: FeedState?, host: HostState) {
        guard let suggestion = feed?.suggestion, suggestion.countsDown, let at = suggestion.autoAt else { return }
        let key = "\(suggestion.playId):\(at)"
        guard holdKey != key else { return }
        holdKey = key
        Task { _ = await host.feedHold() }
    }

    /// "Change...": put the suggestion into the result boxes so the host can adjust it and score by hand.
    func load(_ suggestion: FeedSuggestion) {
        guard !suggestion.isVoid else { return }
        result = choices.merged(with: suggestion)
    }
}
