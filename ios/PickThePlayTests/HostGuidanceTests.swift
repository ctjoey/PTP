import XCTest
#if canImport(FoundationNetworking)
import FoundationNetworking  // Linux: URLSession and friends live here
#endif
@testable import PickThePlay

/// What the host console tells the host when a locked play is left to them, the Timer box, the drafts that survive
/// tab switches, and the rule that the admin key only ever goes to the built-in game server.
/// The three admin_state fixtures were captured from the real server (its live-data rig): a locked play with the
/// game not started yet, a locked play that live data joined too late to follow, and a "no play" it downgraded to review.
final class HostGuidanceTests: XCTestCase {
    // MARK: - Fixtures

    private func fixtureData(_ name: String) throws -> Data {
        #if SWIFT_PACKAGE
        let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")
        #else
        let url = Bundle(for: HostGuidanceTests.self).url(forResource: name, withExtension: "json")
        #endif
        return try Data(contentsOf: try XCTUnwrap(url, "missing fixture \(name).json"))
    }

    private func decode(_ data: Data) throws -> AdminState {
        guard case .state(let state) = try AdminServerMessage.decode(data) else {
            XCTFail("not an admin_state message")
            throw CancellationError()
        }
        return state
    }

    private func adminState(_ name: String) throws -> AdminState {
        try decode(fixtureData(name))
    }

    /// A fixture with something changed, to reach the states the server can also be in.
    private func variant(_ name: String, _ edit: (inout [String: Any]) -> Void) throws -> AdminState {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: fixtureData(name)) as? [String: Any])
        edit(&object)
        return try decode(JSONSerialization.data(withJSONObject: object))
    }

    private func editFeed(_ object: inout [String: Any], _ edit: (inout [String: Any]) -> Void) {
        var feed = object["feed"] as? [String: Any] ?? [:]
        edit(&feed)
        object["feed"] = feed
    }

    private func editPlay(_ object: inout [String: Any], _ edit: (inout [String: Any]) -> Void) {
        var play = object["play"] as? [String: Any] ?? [:]
        edit(&play)
        object["play"] = play
    }

    // MARK: - The captured fixtures

    func testTheCapturedFixturesDecodeAsTheServerSentThem() throws {
        let notStarted = try adminState("admin_state_locked_not_started")
        XCTAssertEqual(notStarted.play?.state, .locked)
        XCTAssertEqual(notStarted.feed?.state, "not_started")
        XCTAssertEqual(notStarted.feed?.linked, true)
        XCTAssertEqual(notStarted.feed?.waiting?.playId, notStarted.play?.id)
        XCTAssertNotNil(notStarted.feed?.waiting?.nextCheckAt, "it checks again every minute")
        XCTAssertNil(notStarted.feed?.suggestion)

        let stranded = try adminState("admin_state_locked_stranded")
        XCTAssertEqual(stranded.play?.state, .locked)
        XCTAssertEqual(stranded.feed?.state, "idle")
        XCTAssertNil(stranded.feed?.waiting)
        XCTAssertNil(stranded.feed?.suggestion)
        XCTAssertEqual(stranded.feed?.message, HostText.leftToHostNotice, "the server's notice and the app's words are the same")

        let review = try adminState("admin_state_suggestion_void_review")
        XCTAssertEqual(review.feed?.suggestion?.status, .review)
        XCTAssertEqual(review.feed?.suggestion?.kind, "void")
    }

    // MARK: - Left to the host

    func testALockedPlayLiveDataCannotScoreIsLeftToTheHost() throws {
        let s = try adminState("admin_state_locked_stranded")
        XCTAssertTrue(s.lockedPlayIsLeftToHost)
        XCTAssertFalse(s.feedIsWatchingLockedPlay)
    }

    func testALockedPlayLiveDataIsFollowingIsNotLeftToTheHost() throws {
        // Waiting for kickoff, and it checks again soon: it is watching.
        let waiting = try adminState("admin_state_locked_not_started")
        XCTAssertFalse(waiting.lockedPlayIsLeftToHost)
        XCTAssertTrue(waiting.feedIsWatchingLockedPlay)
        // A suggestion for the play (even one that needs a check) means live data has it.
        let suggestion = try adminState("admin_state_suggestion")
        XCTAssertFalse(suggestion.lockedPlayIsLeftToHost)
        XCTAssertTrue(suggestion.feedIsWatchingLockedPlay)
        let review = try adminState("admin_state_suggestion_void_review")
        XCTAssertFalse(review.lockedPlayIsLeftToHost)
        XCTAssertTrue(review.feedIsWatchingLockedPlay)
    }

    func testTheTestOnlyAppliesToALockedPlayWithLiveDataLinked() throws {
        let name = "admin_state_locked_stranded"
        let open = try variant(name) { editPlay(&$0) { $0["state"] = "OPEN" } }
        XCTAssertFalse(open.lockedPlayIsLeftToHost, "players are still picking")
        let resolved = try variant(name) { editPlay(&$0) { $0["state"] = "RESOLVED" } }
        XCTAssertFalse(resolved.lockedPlayIsLeftToHost, "already scored")
        let unlinked = try variant(name) { editFeed(&$0) { $0["linked"] = false; $0["state"] = "off" } }
        XCTAssertFalse(unlinked.lockedPlayIsLeftToHost, "no live data at all: the host always scores by hand")
        XCTAssertFalse(unlinked.feedIsWatchingLockedPlay)
        let noFeed = try variant(name) { $0["feed"] = NSNull() }
        XCTAssertFalse(noFeed.lockedPlayIsLeftToHost)
        XCTAssertFalse(noFeed.feedIsWatchingLockedPlay)
        let noPlay = try variant(name) { $0["play"] = NSNull() }
        XCTAssertFalse(noPlay.lockedPlayIsLeftToHost)
    }

    func testOnlyAnIdleFeedWithNothingWaitingOrSuggestedLeavesThePlayToTheHost() throws {
        let name = "admin_state_locked_stranded"
        for state in ["paused", "capped", "error", "not_started", "waiting", "done", "off"] {
            let s = try variant(name) { editFeed(&$0) { $0["state"] = state } }
            XCTAssertFalse(s.lockedPlayIsLeftToHost, "\(state) is not the stranded state")
        }
        // Idle, but waiting for the play: it is not stranded.
        let waiting = try variant(name) {
            editFeed(&$0) { $0["waiting"] = ["play_id": 1, "checks": 0, "since": 1.0, "next_check_at": 5.0] }
        }
        XCTAssertFalse(waiting.lockedPlayIsLeftToHost)
        XCTAssertTrue(waiting.feedIsWatchingLockedPlay)
        // Waiting, but nothing will be checked (paused or out of requests): not stranded, and not being watched either.
        let notChecking = try variant(name) {
            editFeed(&$0) { $0["state"] = "paused"; $0["waiting"] = ["play_id": 1, "checks": 0, "since": 1.0, "next_check_at": NSNull()] }
        }
        XCTAssertFalse(notChecking.lockedPlayIsLeftToHost)
        XCTAssertFalse(notChecking.feedIsWatchingLockedPlay)
    }

    // MARK: - The chip, the tone and the words

    func testTheChipSaysScoreByHandInTheWaitTone() throws {
        let s = try adminState("admin_state_locked_stranded")
        let feed = try XCTUnwrap(s.feed)
        XCTAssertEqual(HostText.scoreByHand, "SCORE BY HAND")
        XCTAssertEqual(feed.label(at: s.serverTime), "READY", "without the play, the feed alone looks ready")
        XCTAssertEqual(feed.label(at: s.serverTime, leftToHost: s.lockedPlayIsLeftToHost), "SCORE BY HAND")
        XCTAssertEqual(feed.tone(at: s.serverTime), .good)
        XCTAssertEqual(feed.tone(at: s.serverTime, leftToHost: s.lockedPlayIsLeftToHost), .wait, "amber, not the green READY")
    }

    func testTheChipKeepsItsOtherWordsWhenThePlayIsNotLeftToTheHost() throws {
        let s = try adminState("admin_state_locked_not_started")
        let feed = try XCTUnwrap(s.feed)
        XCTAssertEqual(feed.label(at: s.serverTime, leftToHost: s.lockedPlayIsLeftToHost), "NOT STARTED")
        XCTAssertEqual(feed.tone(at: s.serverTime, leftToHost: s.lockedPlayIsLeftToHost), .wait)
        let review = try adminState("admin_state_suggestion_void_review")
        let reviewFeed = try XCTUnwrap(review.feed)
        XCTAssertEqual(reviewFeed.label(at: review.serverTime, leftToHost: review.lockedPlayIsLeftToHost), "READY")
        XCTAssertEqual(reviewFeed.tone(at: review.serverTime, leftToHost: review.lockedPlayIsLeftToHost), .good)
    }

    func testTheLineUnderTheChipSaysToScoreByHand() throws {
        let s = try adminState("admin_state_locked_stranded")
        let feed = try XCTUnwrap(s.feed)
        XCTAssertEqual(HostText.statusMessage(feed, leftToHost: true), HostText.leftToHostNotice,
                       "the server's own notice is shown as sent")
        XCTAssertEqual(HostText.leftToHostNotice,
                       "Live data joined late, so it can't tell which play this one was. Score it by hand (or void it), then open the next play and live data takes over.")
        // The server's generic words must not stand next to SCORE BY HAND.
        let generic = try variant("admin_state_locked_stranded") {
            editFeed(&$0) { $0["message"] = "Connected. It checks the feed only while a play is locked." }
        }
        XCTAssertEqual(HostText.statusMessage(try XCTUnwrap(generic.feed), leftToHost: true), HostText.leftToHostNotice)
        XCTAssertEqual(HostText.statusMessage(try XCTUnwrap(generic.feed), leftToHost: false),
                       "Connected. It checks the feed only while a play is locked.", "otherwise the server's words")
        let silent = try variant("admin_state_locked_stranded") { editFeed(&$0) { $0["message"] = NSNull() } }
        XCTAssertEqual(HostText.statusMessage(try XCTUnwrap(silent.feed), leftToHost: false), "Ready. Checking starts when a play locks.")
    }

    func testTheLockedCardTellsTheTruthAboutLiveData() throws {
        let stranded = try adminState("admin_state_locked_stranded")
        XCTAssertEqual(HostText.lockedGuidance(stranded),
                       "Live data can't score this play. Tap what happened (Run or Pass, direction, distance), then press Score Play. No play, or you missed it? Press Void play.")
        let watched = try adminState("admin_state_suggestion")
        XCTAssertEqual(HostText.lockedGuidance(watched),
                       "Picks are closed. Live data scores it when the result shows up. You can also tap what happened and press Score Play, or press Void play.")
        let notStarted = try adminState("admin_state_locked_not_started")
        XCTAssertEqual(HostText.lockedGuidance(notStarted), HostText.lockedWatched, "it is still checking")
        // Nothing is following the play (no live data, or it is paused): no promise that live data will score it.
        let unlinked = try variant("admin_state_locked_stranded") { editFeed(&$0) { $0["linked"] = false; $0["state"] = "off" } }
        XCTAssertEqual(HostText.lockedGuidance(unlinked), HostText.lockedByHand)
        XCTAssertFalse(HostText.lockedByHand.contains("Live data scores"))
        let paused = try variant("admin_state_locked_not_started") {
            editFeed(&$0) { $0["state"] = "paused"; $0["waiting"] = ["play_id": 1, "checks": 1, "since": 1.0, "next_check_at": NSNull()] }
        }
        XCTAssertEqual(HostText.lockedGuidance(paused), HostText.lockedByHand)
    }

    func testTheVoidButtonWords() {
        XCTAssertEqual(HostText.voidPlay, "Void play")
        XCTAssertEqual(HostText.voidHint, "Use it for a penalty, a sack, a QB scramble, no play, or if you missed it. Nobody scores.")
    }

    func testTheNotStartedLineGivesTheNextCheckAndASuggestion() {
        XCTAssertEqual(HostText.notStartedLine(nextCheckAt: 1100, now: 1040), "Next check in 60 s. Is the game on? Tap Check now.")
        XCTAssertEqual(HostText.notStartedLine(nextCheckAt: 1040.2, now: 1040), "Next check in 1 s. Is the game on? Tap Check now.", "rounded up")
        XCTAssertEqual(HostText.notStartedLine(nextCheckAt: 1000, now: 1040), "Next check in 0 s. Is the game on? Tap Check now.", "never negative")
        XCTAssertEqual(HostText.notStartedLine(nextCheckAt: nil, now: 1040), "Is the game on? Tap Check now.")
        XCTAssertEqual(HostText.notStartedLine(nextCheckAt: .infinity, now: 0), "Next check in 604800 s. Is the game on? Tap Check now.",
                       "an odd number can't trap")
        XCTAssertEqual(HostText.wholeSeconds(.nan), 0)
    }

    func testTheDistanceButtonsGoInTheWebsitesOrder() {
        XCTAssertEqual(HostText.distanceChoices, [.short, .medium, .long, .loss])
        XCTAssertEqual(Set(HostText.distanceChoices), Set(YardageOutcome.allCases), "none left out")
    }

    // MARK: - A "no play" the server downgraded to review

    func testANoPlayTheServerDowngradedToReviewStillOffersVoid() throws {
        let s = try adminState("admin_state_suggestion_void_review")
        let sg = try XCTUnwrap(s.feed?.suggestion)
        XCTAssertEqual(sg.status, .review, "the server wants the host to check the down and distance first")
        XCTAssertEqual(sg.kind, "void")
        XCTAssertTrue(sg.isVoid, "so the card offers Void play, not three pickers")
        XCTAssertTrue(sg.missingParts.isEmpty)
        XCTAssertEqual(sg.badge, "NEEDS YOUR CHECK")
        XCTAssertFalse(sg.countsDown, "a review never scores or voids by itself")
        XCTAssertEqual(sg.note(missing: sg.missingParts, paused: false),
                       "The feed says no play, but check it against the TV first. Tap Void play if it's right, or score it yourself with Change.")
        let choices = SuggestionChoices()
        XCTAssertTrue(choices.missing(sg).isEmpty)
        XCTAssertTrue(choices.canScore(sg), "Void play is pressable straight away")
        let fields = choices.acceptFields(sg)
        XCTAssertEqual(fields["void"] as? Bool, true, "feed_accept voids it")
        XCTAssertEqual(fields["play_id"] as? Int, sg.playId)
    }

    func testAnOrdinaryReviewStillAsksForTheMissingParts() throws {
        let s = try variant("admin_state_suggestion_void_review") {
            editFeed(&$0) { feed in
                var sg = feed["suggestion"] as? [String: Any] ?? [:]
                sg["kind"] = "review"
                sg["flags"] = ["Could not tell what kind of play this was"]
                feed["suggestion"] = sg
            }
        }
        let sg = try XCTUnwrap(s.feed?.suggestion)
        XCTAssertFalse(sg.isVoid)
        XCTAssertEqual(sg.missingParts, [.playType, .direction, .yardage])
        XCTAssertEqual(sg.note(missing: sg.missingParts, paused: false), "Pick the play type, direction and distance above, then Score.")
        XCTAssertEqual(sg.note(missing: [], paused: false), "An unusual play, so it won't score by itself. Check it, then tap Score.")
    }

    func testAHeldNoPlayAndAReadyVoidAreStillVoids() throws {
        let ready = try variant("admin_state_suggestion_void_review") {
            editFeed(&$0) { feed in
                var sg = feed["suggestion"] as? [String: Any] ?? [:]
                sg["status"] = "void"
                sg["auto_at"] = 5.0
                feed["suggestion"] = sg
            }
        }
        let sg = try XCTUnwrap(ready.feed?.suggestion)
        XCTAssertTrue(sg.isVoid)
        XCTAssertTrue(sg.countsDown)
        XCTAssertNil(sg.note(missing: [], paused: false), "a countdown speaks for itself")
    }

    // MARK: - The Timer box

    func testTheTimerStartsAt15() {
        let timer = TimerDraft()
        XCTAssertEqual(timer.text, "15")
        XCTAssertEqual(timer.seconds, 15)
        XCTAssertTrue(timer.isValid)
        XCTAssertNil(timer.warning)
        XCTAssertEqual(TimerDraft.range, 5...60)
    }

    func testTheTimerIsHeldBetween5And60Seconds() {
        var timer = TimerDraft()
        for (typed, sent, ok) in [("5", 5, true), ("60", 60, true), ("30", 30, true), ("4", 5, false), ("0", 5, false),
                                  ("61", 60, false), ("999", 60, false), ("007", 7, true)] {
            timer.type(typed)
            XCTAssertEqual(timer.seconds, sent, "typed \(typed)")
            XCTAssertEqual(timer.isValid, ok, "typed \(typed)")
            XCTAssertEqual(timer.warning == nil, ok, "typed \(typed)")
        }
        XCTAssertEqual(TimerDraft.clamp(-20), 5)
        XCTAssertEqual(TimerDraft.clamp(Int.max), 60)
    }

    func testTheTimerBoxTakesDigitsOnly() {
        var timer = TimerDraft()
        timer.type("12abc")
        XCTAssertEqual(timer.text, "12")
        timer.type("")
        XCTAssertEqual(timer.seconds, 15, "an empty box means the usual 15")
        XCTAssertFalse(timer.isValid)
        XCTAssertEqual(timer.warning, "Type the seconds players get to pick, from 5 to 60. 15 will be used.")
        timer.type("-7")
        XCTAssertEqual(timer.text, "7")
        timer.type("12345678901234567890")
        XCTAssertEqual(timer.text, "123", "a few characters at most")
        XCTAssertEqual(timer.seconds, 60)
        timer.type("٣٠")   // Arabic-Indic digits are not what the server reads
        XCTAssertEqual(timer.text, "")
    }

    func testTheTimerBoxShowsWhatWillBeSent() {
        var timer = TimerDraft()
        timer.type("90")
        XCTAssertEqual(timer.warning, "The timer is 5 to 60 seconds. 60 will be used.")
        timer.normalize()
        XCTAssertEqual(timer.text, "60")
        XCTAssertNil(timer.warning)
        timer.type("")
        timer.normalize()
        XCTAssertEqual(timer.text, "15")
    }

    func testOpenPlaySendsTheTimerAsWindowSeconds() {
        let fields = HostState.openPlayFields(down: 2, distance: "7", windowSeconds: 20)
        XCTAssertEqual(fields["window_seconds"] as? Int, 20)
        XCTAssertEqual(fields["down"] as? Int, 2)
        XCTAssertEqual(fields["distance"] as? String, "7")
        XCTAssertEqual(HostState.openPlayFields(down: nil, distance: "", windowSeconds: 15).keys.sorted(), ["window_seconds"])
        XCTAssertEqual(HostState.openPlayFields(down: 1, distance: nil, windowSeconds: 3)["window_seconds"] as? Int, 5)
        XCTAssertEqual(HostState.openPlayFields(down: 1, distance: nil, windowSeconds: 61)["window_seconds"] as? Int, 60)
        XCTAssertEqual(HostState.openPlayFields(down: 1, distance: nil, windowSeconds: 14.6)["window_seconds"] as? Int, 15)
        XCTAssertNil(HostState.openPlayFields(down: 1, distance: nil, windowSeconds: nil)["window_seconds"], "the server's default")
        XCTAssertNil(HostState.openPlayFields(down: 1, distance: nil, windowSeconds: .nan)["window_seconds"])
        // Whatever the box holds, what goes out is something the server accepts.
        var timer = TimerDraft()
        for typed in ["", "0", "4", "5", "15", "60", "61", "999"] {
            timer.type(typed)
            let sent = HostState.openPlayFields(down: nil, distance: nil, windowSeconds: Double(timer.seconds))["window_seconds"] as? Int
            XCTAssertTrue((5...60).contains(sent ?? 0), "typed '\(typed)'")
        }
    }

    // MARK: - Drafts that survive tab switches

    @MainActor
    func testTheSearchTheMessageAndTheTimerOutliveTheTabs() throws {
        let host = HostState(keys: MemoryKeyStore())
        host.drafts.playerSearch = "joe"
        host.drafts.messageDraft = "Kickoff is delayed."
        host.drafts.timer.type("25")
        for tab in [HostState.Tab.log, .players, .message, .run] { host.tab = tab }
        XCTAssertEqual(host.drafts.playerSearch, "joe")
        XCTAssertEqual(host.drafts.messageDraft, "Kickoff is delayed.")
        XCTAssertEqual(host.drafts.timer.seconds, 25)
        // The server's updates (even a new play) leave them alone; only the result and the down are play-by-play.
        host.drafts.update(from: try adminState("admin_state_locked_stranded"))
        host.drafts.update(from: try adminState("admin_state_play_open"))
        host.drafts.update(from: try adminState("admin_state_nogame"))
        XCTAssertEqual(host.drafts.playerSearch, "joe")
        XCTAssertEqual(host.drafts.messageDraft, "Kickoff is delayed.")
        XCTAssertEqual(host.drafts.timer.seconds, 25, "the last timer is kept for the session")
        host.dismiss()
        XCTAssertEqual(host.drafts.messageDraft, "Kickoff is delayed.", "closing the console for a moment loses nothing")
    }

    // MARK: - The admin key goes to the built-in server only

    private let builtIn = URL(string: "https://pick-the-play.onrender.com")!

    func testTheKeyIsAllowedOnlyForTheBuiltInServer() throws {
        func allowed(_ text: String?, builtIn built: URL? = nil) -> Bool {
            ServerConfig.allowsAdminKey(for: text.flatMap { URL(string: $0) }, builtIn: built ?? builtIn)
        }
        XCTAssertTrue(allowed("https://pick-the-play.onrender.com"))
        XCTAssertTrue(allowed("https://PICK-the-play.onrender.com"), "capital letters in a host name don't matter")
        XCTAssertTrue(allowed("https://pick-the-play.onrender.com."), "nor a dot at the end")
        XCTAssertTrue(allowed("https://pick-the-play.onrender.com:443"), "443 is https's usual port")
        XCTAssertTrue(allowed("https://pick-the-play.onrender.com/some/path"), "only the server counts")
        XCTAssertTrue(allowed("wss://pick-the-play.onrender.com/ws/admin"), "the socket's address for the same server")
        XCTAssertFalse(allowed("https://evil.example"))
        XCTAssertFalse(allowed("https://pick-the-play.onrender.com.evil.example"), "a longer name that starts the same")
        XCTAssertFalse(allowed("https://evil.example/pick-the-play.onrender.com"))
        XCTAssertFalse(allowed("https://user@evil.example"), "no sneaking a host in as a user name")
        XCTAssertFalse(allowed("https://onrender.com"))
        XCTAssertFalse(allowed("http://pick-the-play.onrender.com"), "plain http would show the key to the network")
        XCTAssertFalse(allowed("ws://pick-the-play.onrender.com/ws/admin"))
        XCTAssertFalse(allowed("https://pick-the-play.onrender.com:8443"), "a different port is a different server")
        XCTAssertFalse(allowed("http://192.168.1.20:8000"), "a computer on the Wi-Fi")
        XCTAssertFalse(allowed("ftp://pick-the-play.onrender.com"))
        XCTAssertFalse(allowed(nil), "no address set")
        XCTAssertFalse(ServerConfig.allowsAdminKey(for: builtIn, builtIn: nil), "a build with no built-in server trusts nothing")
        XCTAssertFalse(ServerConfig.allowsAdminKey(for: nil, builtIn: nil))
    }

    func testABuildForTheLocalNetworkTrustsThatAddressOnly() {
        let local = URL(string: "http://192.168.1.20:8000")
        XCTAssertTrue(ServerConfig.allowsAdminKey(for: URL(string: "http://192.168.1.20:8000"), builtIn: local))
        XCTAssertTrue(ServerConfig.allowsAdminKey(for: URL(string: "ws://192.168.1.20:8000/ws/admin"), builtIn: local))
        XCTAssertFalse(ServerConfig.allowsAdminKey(for: URL(string: "http://192.168.1.21:8000"), builtIn: local))
        XCTAssertFalse(ServerConfig.allowsAdminKey(for: URL(string: "http://192.168.1.20:8001"), builtIn: local))
    }

    func testTheKeyIsNeverSentInARequestToAnotherServer() async throws {
        RecordingURLProtocol.reset(body: try fixtureData("rest_admin_players"))
        let key = "test-admin-key-8841"
        let wrong = APIClient(server: URL(string: "https://evil.example")!, adminKey: key, session: RecordingURLProtocol.session,
                              trustedServer: builtIn)
        do {
            _ = try await wrong.adminPlayers(query: "")
            XCTFail("a request to another server was allowed")
        } catch let error as APIError {
            XCTAssertEqual(error.message, HostText.wrongServer)
            XCTAssertEqual(error.message, "The host console only works with the built-in game server. Change the server back in Settings.")
            XCTAssertFalse(error.message.contains(key), "the key is never shown")
        }
        do {
            _ = try await wrong.adminFeedGames(date: "20261011")
            XCTFail("a request to another server was allowed")
        } catch is APIError {}
        XCTAssertEqual(RecordingURLProtocol.requests.count, 0, "nothing left the phone")
        // The same client with no built-in server to compare with sends nothing either.
        let nowhere = APIClient(server: builtIn, adminKey: key, session: RecordingURLProtocol.session, trustedServer: nil)
        do {
            _ = try await nowhere.adminPlayers(query: "")
            XCTFail("a request with nothing to trust was allowed")
        } catch is APIError {}
        XCTAssertEqual(RecordingURLProtocol.requests.count, 0)
    }

    func testTheKeyIsSentToTheBuiltInServerAsTheAdminHeader() async throws {
        RecordingURLProtocol.reset(body: try fixtureData("rest_admin_players"))
        let key = "test-admin-key-8841"
        let right = APIClient(server: builtIn, adminKey: key, session: RecordingURLProtocol.session, trustedServer: builtIn)
        let players = try await right.adminPlayers(query: "")
        XCTAssertFalse(players.players.isEmpty)
        XCTAssertEqual(RecordingURLProtocol.requests.count, 1)
        XCTAssertEqual(RecordingURLProtocol.requests.first?.value(forHTTPHeaderField: "X-Admin-Key"), key)
        XCTAssertEqual(RecordingURLProtocol.requests.first?.url?.host, "pick-the-play.onrender.com")
        // A player's request has no key and needs no trust.
        RecordingURLProtocol.reset(body: Data("{}".utf8))
        let player = APIClient(server: URL(string: "https://evil.example")!, token: "player-token", session: RecordingURLProtocol.session,
                               trustedServer: builtIn)
        _ = try? await player.me()
        XCTAssertEqual(RecordingURLProtocol.requests.count, 1)
        XCTAssertNil(RecordingURLProtocol.requests.first?.value(forHTTPHeaderField: "X-Admin-Key"))
    }

    func testTheKeyDoesNotFollowARedirectToAnotherServer() {
        // (Linux's URLSession can't be made to redirect in a test, so the redirect rule is asked directly.)
        let session = URLSession(configuration: .ephemeral)
        let task = session.dataTask(with: builtIn)
        let guardian = KeepAdminKeyOnTheServer(trusted: builtIn)
        func followed(_ to: String) -> Bool {
            let target = URL(string: to)!
            let moved = HTTPURLResponse(url: builtIn, statusCode: 302, httpVersion: nil, headerFields: ["Location": to])!
            var next = URLRequest(url: target)
            next.setValue("test-admin-key-8841", forHTTPHeaderField: "X-Admin-Key")
            var answer: URLRequest??
            guardian.urlSession(session, task: task, willPerformHTTPRedirection: moved, newRequest: next) { answer = .some($0) }
            return answer.flatMap { $0 } != nil
        }
        XCTAssertFalse(followed("https://evil.example/api/admin/players"), "the key stays home")
        XCTAssertFalse(followed("http://pick-the-play.onrender.com/api/admin/players"), "and is never downgraded to plain http")
        XCTAssertTrue(followed("https://pick-the-play.onrender.com/api/admin/players?again=1"), "a redirect inside the server is fine")
        session.invalidateAndCancel()
    }

    func testTheConsoleSendsNothingWhenThePhoneIsPointedAtAnotherServer() async throws {
        let defaults = UserDefaults.standard
        defaults.set("https://evil.example", forKey: ServerConfig.overrideKey)
        defer { defaults.removeObject(forKey: ServerConfig.overrideKey) }
        let trusted = builtIn
        let link = await MainActor.run { SpyLink() }
        let store = MemoryKeyStore("test-admin-key-8841")
        let host = await MainActor.run { HostState(keys: store, link: link, trustedServer: { trusted }) }
        await MainActor.run {
            XCTAssertFalse(host.serverIsTrusted)
            XCTAssertTrue(host.isOtherServer, "the key screen shows the explanation, not the key form")
            XCTAssertTrue(host.hasSavedKey)
            XCTAssertFalse(host.isConnecting, "no 'Connecting…' that never ends")

            host.present()
            XCTAssertEqual(link.starts, 0, "the saved key is not sent")
            XCTAssertEqual(host.notice?.text, HostText.wrongServer)
            XCTAssertEqual(host.notice?.isError, true)
            XCTAssertEqual(host.authError, HostText.wrongServer)
            XCTAssertTrue(host.hasSavedKey, "the key stays saved for when the server is right again")
            XCTAssertEqual(store.read(), "test-admin-key-8841")
            host.notice = nil
        }

        await host.signIn(key: "another-key", remember: true)
        await MainActor.run {
            XCTAssertEqual(link.starts, 0, "a typed key is not sent either")
            XCTAssertFalse(host.signingIn)
            XCTAssertEqual(host.notice?.text, HostText.wrongServer)
            XCTAssertEqual(store.read(), "test-admin-key-8841", "nothing new is saved")
            host.appBecameActive()
            XCTAssertEqual(link.starts, 0)
        }

        do {
            _ = try await host.loadPlayers(query: "")
            XCTFail("the players list was asked for")
        } catch let error as APIError {
            XCTAssertEqual(error.message, HostText.wrongServer)
        }
        do {
            _ = try await host.loadGames(date: "20261011")
            XCTFail("the schedule was asked for")
        } catch is APIError {}
    }

    @MainActor
    func testTheConsoleConnectsWhenThePhoneIsPointedAtTheBuiltInServer() {
        let defaults = UserDefaults.standard
        defaults.set("https://pick-the-play.onrender.com", forKey: ServerConfig.overrideKey)
        defer { defaults.removeObject(forKey: ServerConfig.overrideKey) }
        let link = SpyLink()
        let host = HostState(keys: MemoryKeyStore("test-admin-key-8841"), link: link, trustedServer: { self.builtIn })
        XCTAssertTrue(host.serverIsTrusted)
        XCTAssertFalse(host.isOtherServer)
        host.present()
        XCTAssertEqual(link.starts, 1)
        XCTAssertEqual(link.lastURL?.absoluteString, "wss://pick-the-play.onrender.com/ws/admin")
        XCTAssertEqual(link.lastKey, "test-admin-key-8841")
        XCTAssertNil(host.notice)
    }

    @MainActor
    func testTheSocketRefusesToOpenToAnotherServer() {
        let socket = AdminConnection(trustedServer: builtIn)
        socket.start(url: URL(string: "wss://evil.example/ws/admin")!, key: "test-admin-key-8841")
        XCTAssertEqual(socket.status, .offline, "no connection, so no key sent")
        socket.start(url: URL(string: "ws://pick-the-play.onrender.com/ws/admin")!, key: "test-admin-key-8841")
        XCTAssertEqual(socket.status, .offline, "not over plain ws either")
        let untrusting = AdminConnection(trustedServer: nil)
        untrusting.start(url: URL(string: "wss://pick-the-play.onrender.com/ws/admin")!, key: "test-admin-key-8841")
        XCTAssertEqual(untrusting.status, .offline)
    }
}

// MARK: - Test doubles

/// The admin channel, minus the network: remembers what the console asked it to open.
@MainActor
private final class SpyLink: AdminLink {
    var onMessage: ((AdminServerMessage) -> Void)?
    var onStatus: ((AdminConnection.Status) -> Void)?
    var onRejected: (() -> Void)?
    private(set) var status: AdminConnection.Status = .offline
    private(set) var starts = 0
    private(set) var lastURL: URL?
    private(set) var lastKey: String?

    func start(url: URL, key: String) {
        starts += 1
        lastURL = url
        lastKey = key
    }

    func stop() {}
    func send(_ object: [String: Any]) -> Bool { false }
}

/// Answers every request with a canned body and remembers it, so a test can see what was (not) sent.
private final class RecordingURLProtocol: URLProtocol {
    static var requests: [URLRequest] = []
    static var body = Data()

    static func reset(body: Data) {
        requests = []
        self.body = body
    }

    static var session: URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RecordingURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        let url = request.url ?? URL(string: "https://invalid.example")!
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
