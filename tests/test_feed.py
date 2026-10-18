"""The live-data poller and matcher, against a fake Tank01 and a manual clock (no real requests, no real waiting)."""

import pytest

from app import FeedAcceptIn
from models import GameError
from tests.fakefeed import ENTRIES, Rig, run_rig

# Entry indexes in the recorded game (see demo/box_CAR_WSH_20241020.json).
FIRST_PASS, RUN_LEFT_END = 1, 2        # "1st & 10 at CAR 14" pass right for 7; "2nd & 3 at CAR 21" run left end for 4
INTERCEPTION, SACK, VOID, FG = 8, 34, 18, 27


def dd(i):
    """The down and distance the broadcast would show for entry ``i``: what the host types."""
    from playparse import parse_down_and_distance

    d = parse_down_and_distance(ENTRIES[i]["downAndDistance"])
    return d["down"], "Goal" if d["to_go"] == "goal" else d["to_go"]


async def new_game_midway(rig: Rig, start: int):
    """A game whose first play is entry ``start`` of the recorded feed (tests jump around): start the feed's
    reading position there and skip the late-start catch-up (which has its own test)."""
    await rig.new_game()
    rig.feed.cursor, rig.feed._baselined = start, True


async def lock_entry(rig: Rig, i: int):
    """Open and lock a play for entry ``i`` (its down and distance), without showing the entry yet."""
    return await rig.open_lock(*dd(i))


async def show_and_wait(rig: Rig, upto: int, limit=60):
    """Reveal entries through index ``upto`` and tick until a suggestion exists."""
    rig.server.reveal(upto=upto)
    await rig.until(lambda: rig.state()["suggestion"] is not None, limit=limit)


# --------------------------------------------------------------------------- #
# Smart polling
# --------------------------------------------------------------------------- #


def test_no_requests_while_nothing_is_locked(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        assert rig.state()["state"] == "idle" and rig.server.hits == []
        await rig.open(1, "10")        # open is not locked: still no requests
        for _ in range(20):
            await rig.step(30)
        assert rig.server.hits == []
        assert rig.state()["requests"]["game"] == 0

    run_rig(tmp_path, scenario)


def test_polls_only_while_a_locked_play_waits_and_follows_the_schedule(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        locked_at = rig.clock()
        s = rig.state()
        assert s["state"] == "waiting" and s["waiting"]["play_id"] == rig.play()["id"]
        assert s["waiting"]["next_check_at"] == locked_at + 5 and s["waiting"]["since"] == locked_at
        await rig.step(4)
        assert rig.server.hits == []                           # first check only after the first delay
        await rig.until(lambda: len(rig.server.hits) == 1, step=0.5)
        await rig.until(lambda: rig.clock() - locked_at >= 700, step=0.5)
        offsets = rig.poll_offsets(locked_at)
        expected = [*(5 + 2.5 * i for i in range(23)), *range(65, 181, 5), *range(190, 601, 10), 615, 630, 645, 660, 675, 690]
        assert offsets[: len(expected)] == expected
        # An empty feed never produced anything, and the host still can score by hand.
        assert rig.play()["state"] == "LOCKED"
        assert "quiet" in rig.state()["message"]

    run_rig(tmp_path, scenario)


def test_quiet_message_and_slow_polling_after_ten_minutes(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(300)
        assert "quiet" not in rig.state()["message"].lower()
        await rig.until(lambda: rig.clock() - rig.state()["waiting"]["since"] >= 601, limit=400)
        assert rig.state()["message"] == "Feed is quiet: long delay (injury or review?)"
        n = len(rig.server.hits)
        await rig.step(29)
        assert len(rig.server.hits) <= n + 1               # then one check every 30 s

    run_rig(tmp_path, scenario)


def test_host_scoring_by_hand_stops_all_polling(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) >= 2)
        n = len(rig.server.hits)
        from app import ResolveIn

        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        assert rig.state()["waiting"] is None
        for _ in range(10):
            await rig.step(30)
        assert len(rig.server.hits) == n               # nothing waits: zero requests

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Binding, suggestions, auto-score
# --------------------------------------------------------------------------- #


def test_clean_play_is_suggested_then_scored_after_the_grace_period(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)                      # the kickoff and the first play
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        s = rig.state()
        sug = s["suggestion"]
        assert sug["status"] == "ready" and sug["play_id"] == rig.play()["id"]
        assert (sug["play_type"], sug["direction"], sug["yards"], sug["yardage"]) == ("PASS", "RIGHT", 7, "MEDIUM")
        assert sug["text"].startswith("A.Dalton pass short right") and sug["clock"] == "Q1 14:55"
        assert sug["down_and_distance"] == "1st & 10 at CAR 14" and sug["flags"] == [] and sug["warning"] is None
        assert sug["auto_at"] == rig.clock() + 4 and s["waiting"] is None
        assert s["lag"] == {"median": 5.0, "last": 5.0, "samples": 1}
        assert rig.play()["state"] == "LOCKED"
        await rig.step(3)
        assert rig.play()["state"] == "LOCKED"
        await rig.step(1)
        play = rig.play()
        assert play["state"] == "RESOLVED" and not play["voided"]
        assert (play["correct_play_type"], play["correct_direction"], play["correct_yardage"], play["yards_gained"]) == \
            ("PASS", "RIGHT", "MEDIUM", 7)
        assert play["resolved_by"] == "feed" and play["feed_text"].startswith("A.Dalton pass short right")
        s = rig.state()
        assert s["suggestion"] is None and s["next_down"] == {"down": 2, "distance": "3"}
        assert s["last_scored"]["summary"] == "PASS - RIGHT - MEDIUM (7 yds)" and s["last_scored"]["auto"] is True
        n = len(rig.server.hits)
        for _ in range(5):
            await rig.step(30)
        assert len(rig.server.hits) == n                       # scored: back to zero requests

    run_rig(tmp_path, scenario)


def test_scoring_through_the_feed_scores_every_pick(tmp_path):
    async def scenario(rig: Rig):
        a, b = rig.store.create_user("alice"), rig.store.create_user("bob")
        await rig.new_game()
        play = await rig.open(*dd(FIRST_PASS))
        rig.store.submit_prediction(a["id"], play["id"], "PASS", "RIGHT", "MEDIUM")   # exact: 40
        rig.store.submit_prediction(b["id"], play["id"], "RUN", "RIGHT", "SHORT")      # direction only: 10
        await rig.lock()
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)
        assert rig.store.get_user(a["id"])["total_score"] == 40 and rig.store.get_user(b["id"])["total_score"] == 10
        preds = rig.store.predictions_for_play(play["id"])
        assert preds[a["id"]]["points_earned"] == 40 and preds[b["id"]]["points_earned"] == 10

    run_rig(tmp_path, scenario)


def test_entries_bind_to_plays_in_order_skipping_kickoffs_punts_and_timeouts(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        seen = []
        for i in (1, 2, 3, 4, 5, 6, 7):
            await lock_entry(rig, i)
            rig.server.reveal(upto=i)
            await rig.until(lambda: rig.play()["state"] == "RESOLVED")
            seen.append((rig.play()["correct_play_type"], rig.play()["correct_direction"], rig.play()["yards_gained"]))
        assert seen == [("PASS", "RIGHT", 7), ("RUN", "LEFT", 4), ("RUN", "LEFT", 26), ("RUN", "LEFT", 6),
                        ("PASS", "RIGHT", 17), ("PASS", "LEFT", 0), ("RUN", "LEFT", 1)]
        # Then past a turnover, a timeout, a kickoff: next scrimmage entry is #11.
        await lock_entry(rig, 11)
        await show_and_wait(rig, 8)               # the interception is entry 8 (review) -> bound to this play
        assert rig.state()["suggestion"]["status"] == "review"

    run_rig(tmp_path, scenario)


ABORTED_SNAP = 89


def test_review_needs_the_host_and_never_scores_by_itself(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, ABORTED_SNAP)
        await lock_entry(rig, ABORTED_SNAP)
        await show_and_wait(rig, ABORTED_SNAP)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None
        assert (sug["direction"], sug["yards"]) == (None, None)
        assert "Aborted snap" in sug["flags"]
        for _ in range(10):
            await rig.step(30)
        assert rig.play()["state"] == "LOCKED"                  # still waiting for the host
        # Score now without choosing anything is refused (nothing changes) ...
        with pytest.raises(GameError, match="Left, Middle or Right|Run or Pass"):
            await rig.ctrl.feed_accept(FeedAcceptIn(play_id=sug["play_id"]))
        assert rig.state()["suggestion"] is not None and rig.play()["state"] == "LOCKED"
        # ... the host completes it (an aborted snap has no charted result) and scores.
        out = await rig.ctrl.feed_accept(FeedAcceptIn(play_id=sug["play_id"], play_type="RUN", direction="MIDDLE", yards=1))
        play = rig.play()
        assert (play["correct_play_type"], play["correct_direction"], play["correct_yardage"], play["yards_gained"]) == \
            ("RUN", "MIDDLE", "SHORT", 1)
        assert play["resolved_by"] == "feed" and out["feed"]["suggestion"] is None
        assert rig.state()["next_down"] is None                 # not a clean play: nothing to prefill

    run_rig(tmp_path, scenario)


def test_a_sack_is_no_play_and_voids_itself_after_the_grace_period(tmp_path):
    """Nobody could have called a throw that never happened: a sack is voided, like a penalty's no play (and, like it,
    only by itself when the down and distance prove it is this play)."""
    async def scenario(rig: Rig):
        await new_game_midway(rig, SACK)
        await lock_entry(rig, SACK)
        await show_and_wait(rig, SACK)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "void" and sug["auto_at"]
        assert sug["flags"] == ["Sack: counts as no play, nobody scores"]
        assert (sug["play_type"], sug["direction"], sug["yards"]) == (None, None, None)
        await rig.step(8)
        play = rig.play()
        assert play["state"] == "RESOLVED" and play["voided"] == 1 and play["resolved_by"] == "void"
        assert "sacked" in play["feed_text"]
        assert rig.state()["last_scored"]["voided"] is True and rig.state()["next_down"] == {"down": 3, "distance": "10"}

    run_rig(tmp_path, scenario)


def test_void_suggestion_auto_voids_after_the_grace_period(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, VOID)
        await lock_entry(rig, VOID)
        await show_and_wait(rig, VOID)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "void" and sug["auto_at"] and "Penalty" in sug["flags"][0]
        await rig.step(8)
        play = rig.play()
        assert play["state"] == "RESOLVED" and play["voided"] == 1 and play["resolved_by"] == "void"
        assert "No Play" in play["feed_text"]
        assert rig.state()["next_down"] == {"down": 2, "distance": "10"}   # a no-play moves the game on: the replay of the down

    run_rig(tmp_path, scenario)


def test_hold_cancels_the_timer_until_the_host_acts(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        pid = rig.play()["id"]
        rig.ctrl.feed.hold()
        sug = rig.state()["suggestion"]
        assert sug["status"] == "held" and sug["auto_at"] is None
        for _ in range(10):
            await rig.step(30)
        assert rig.play()["state"] == "LOCKED"
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid))      # Score now
        assert rig.play()["state"] == "RESOLVED" and rig.play()["yards_gained"] == 7
        assert rig.state()["next_down"] == {"down": 2, "distance": "3"}   # a held, then accepted clean play still prefills
        with pytest.raises(GameError):
            rig.ctrl.feed.hold()                                   # nothing to hold any more

    run_rig(tmp_path, scenario)


def test_accept_with_overrides_scores_the_hosts_version(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        pid = rig.play()["id"]
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid, play_type="RUN", direction="LEFT", yards=12))
        play = rig.play()
        assert (play["correct_play_type"], play["correct_direction"], play["correct_yardage"], play["yards_gained"]) == \
            ("RUN", "LEFT", "LONG", 12)
        assert play["resolved_by"] == "feed"
        assert rig.state()["next_down"] is None                    # changed by the host: not clean, so no prefill
        assert rig.state()["auto_open_at"] is None

    run_rig(tmp_path, scenario)


def test_accept_override_distance_by_bucket_drops_the_feeds_yards(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=rig.play()["id"], yardage="LONG"))
        play = rig.play()
        assert (play["correct_yardage"], play["yards_gained"]) == ("LONG", None)
        # inconsistent override is refused before anything changes
        await lock_entry(rig, RUN_LEFT_END)
        await show_and_wait(rig, RUN_LEFT_END)
        with pytest.raises(ValueError):
            FeedAcceptIn(play_id=1, yardage="LONG", yards=3)

    run_rig(tmp_path, scenario)


def test_accept_void_voids_and_accept_must_name_the_current_suggestion(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        pid = rig.play()["id"]
        with pytest.raises(GameError, match="no longer current"):
            await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid + 99))
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid, void=True))
        assert rig.play()["voided"] == 1 and rig.play()["resolved_by"] == "void"
        with pytest.raises(GameError, match="no longer current"):
            await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid))

    run_rig(tmp_path, scenario)


def test_auto_score_off_waits_for_the_host_and_can_be_turned_on(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.ctrl.feed.set_options(auto_score=False)
        assert rig.state()["auto_score"] is False
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        assert rig.state()["suggestion"]["status"] == "ready" and rig.state()["suggestion"]["auto_at"] is None
        for _ in range(5):
            await rig.step(30)
        assert rig.play()["state"] == "LOCKED"
        rig.ctrl.feed.set_options(auto_score=True)                 # turning it on starts the countdown
        assert rig.state()["suggestion"]["auto_at"] == rig.clock() + 4
        await rig.step(8)
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


def test_skip_discards_the_entry_and_keeps_waiting_for_the_next(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, RUN_LEFT_END)                  # the host's play is "2nd & 3"
        await show_and_wait(rig, FIRST_PASS)                 # but the feed's first entry is "1st & 10"
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None
        assert sug["warning"] == "Feed shows 1st & 10 but this play is 2nd & 3"
        for _ in range(4):
            await rig.step(30)
        assert rig.play()["state"] == "LOCKED"
        rig.ctrl.feed.skip(sug["play_id"])                   # [Skip this feed play]
        s = rig.state()
        assert s["suggestion"] is None or s["suggestion"]["text"] != sug["text"]
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["warning"] is None and sug["status"] == "ready" and sug["yards"] == 4
        await rig.step(8)
        assert (rig.play()["correct_direction"], rig.play()["yards_gained"]) == ("LEFT", 4)

    run_rig(tmp_path, scenario)


def test_down_and_distance_guard_ignores_the_spot_and_unknown_window_values(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        # The host typed nothing for down and distance: nothing to compare, so it is a clean suggestion.
        await rig.open_lock(None, None)
        await show_and_wait(rig, FIRST_PASS)
        assert rig.state()["suggestion"]["warning"] is None and rig.state()["suggestion"]["status"] == "ready"
        await rig.step(8)
        # Same down and distance as the feed (the spot never matters): "2nd & 3" is entry 2.
        await rig.open_lock(2, "3")
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["warning"] is None

    run_rig(tmp_path, scenario)


def test_entries_already_seen_match_without_another_request(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=RUN_LEFT_END)          # the feed already has the NEXT play too
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        assert rig.play()["state"] == "RESOLVED"
        n = len(rig.server.hits)
        await lock_entry(rig, RUN_LEFT_END)           # its entry is already known: no request needed
        s = rig.state()
        assert s["suggestion"] is not None and s["suggestion"]["yards"] == 4
        assert len(rig.server.hits) == n
        assert s["lag"]["samples"] == 1               # a cached match says nothing about the feed's delay

    run_rig(tmp_path, scenario)


def test_lag_is_measured_and_sets_the_next_first_check(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        locked = rig.clock()
        await rig.until(lambda: rig.clock() - locked >= 20)       # the entry shows up 20 s after the lock
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["lag"] == {"median": 20.0, "last": 20.0, "samples": 1} or rig.state()["lag"]["last"] in (20.0, 22.5, 23.0, 25.0)
        lag = rig.state()["lag"]["last"]
        await rig.step(8)
        await lock_entry(rig, RUN_LEFT_END)
        s = rig.state()
        assert s["waiting"]["next_check_at"] - s["waiting"]["since"] == pytest.approx(min(45.0, max(3.0, 0.8 * lag)))

    run_rig(tmp_path, scenario)


def test_first_check_delay_is_clamped_between_3_and_45_seconds(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.feed.lags = [1.0, 2.0, 1.5]
        await lock_entry(rig, FIRST_PASS)
        assert rig.state()["waiting"]["next_check_at"] - rig.state()["waiting"]["since"] == 3.0
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        rig.feed.lags = [80.0, 90.0, 70.0]
        await lock_entry(rig, RUN_LEFT_END)
        assert rig.state()["waiting"]["next_check_at"] - rig.state()["waiting"]["since"] == 45.0
        assert rig.state()["lag"]["median"] == 80.0
        await rig.step(8)
        rig.feed.lags = [50.0, 52.0, 48.0, 50.0, 51.0]            # a slow feed: 0.8 x 50 = 40 s, not the old 25 s ceiling
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        await lock_entry(rig, 3)
        assert rig.state()["waiting"]["next_check_at"] - rig.state()["waiting"]["since"] == pytest.approx(0.8 * 50.0)

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# A punt or field goal on 4th down
# --------------------------------------------------------------------------- #

PUNT = 14   # "4th & 4 at CAR 41 J.Hekker punts 49 yards to WAS 10 ... fair catch"


@pytest.mark.parametrize("entry, flag, opens", [
    (PUNT, "Punt: no play", True),
    (FG, "Field goal try: no play", False),        # a made field goal: the kickoff comes first
])
def test_a_fourth_down_kick_is_no_play_and_the_next_play_is_1st_and_10(tmp_path, entry, flag, opens):
    async def scenario(rig: Rig):
        await new_game_midway(rig, entry)
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, entry)
        await show_and_wait(rig, entry)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "void" and sug["auto_at"] and sug["flags"] == [flag]
        await rig.step(5)
        play = rig.play(1)
        assert play["state"] == "RESOLVED" and play["voided"] == 1 and play["resolved_by"] == "void"
        if opens:   # auto-open (on here) opens the other team's 1st & 10 at once
            nxt = rig.play()
            assert nxt["play_number"] == 2 and nxt["state"] == "OPEN" and (nxt["down"], nxt["distance"]) == (1, "10")
        else:
            assert rig.state()["next_down"] == {"down": 1, "distance": "10"} and rig.state()["auto_open_at"] is None
            await rig.step(60)
            assert rig.play()["play_number"] == 1

    run_rig(tmp_path, scenario)


def test_a_kick_whose_down_does_not_match_waits_for_the_host_and_their_void_still_gives_1st_and_10(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, PUNT)
        await rig.open_lock(4, "5")                     # the host typed 4th & 5: the feed says 4th & 4
        await show_and_wait(rig, PUNT)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None and sug["warning"]
        await rig.step(30)
        assert rig.play()["state"] == "LOCKED"           # never voided by itself
        await rig.ctrl.void_play()
        assert rig.state()["next_down"] == {"down": 1, "distance": "10"}

    run_rig(tmp_path, scenario)


def test_a_kick_the_host_never_opened_is_still_skipped(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, PUNT)
        await lock_entry(rig, PUNT + 2)                  # the host skipped the punt (and a timeout): the next snap
        await show_and_wait(rig, PUNT + 2)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "ready" and "punts" not in sug.get("text", "")
        assert any(r["kind"] == "skip" and r["data"]["reason"] == "punt" for r in rig.store.feed_log(rig.game["id"]))

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Who has the ball
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("upto, ball", [
    (1, "away"),        # CAR (away) passing
    (13, "away"),       # 3rd down incomplete: still CAR
    (14, "home"),       # CAR punts: WSH gets it next
    (15, "home"),       # a timeout (the punting team's) changes nothing
    (16, "home"),       # WSH running
    (27, "away"),       # WSH field goal: CAR gets the kickoff
    (29, "away"),       # the kickoff: CAR receives
    (8, "away"),        # CAR intercepted and returned for a touchdown: WSH kicks off, CAR has it again
])
def test_possession_follows_the_feed(upto, ball):
    from feed import possession

    assert possession(ENTRIES[: upto + 1], "32", "5") == ball


def test_possession_is_unknown_without_teams():
    from feed import possession

    assert possession([], "32", "5") is None
    assert possession(ENTRIES[:2], "99", "98") is None
    assert possession([{"play": "Timeout at 09:38.", "teamID": "5"}], "32", "5") is None


def test_the_game_says_who_has_the_ball(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, FIRST_PASS)
        assert rig.ctrl._with_score(rig.game)["possession"] is None
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        assert rig.ctrl._with_score(rig.game)["possession"] == "away"

    run_rig(tmp_path, scenario)


def test_a_fourth_down_voided_by_hand_before_the_feed_shows_it_fills_1st_and_10(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, PUNT)
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, PUNT)
        await rig.ctrl.void_play()                      # the host saw the punt on TV
        assert rig.state()["next_down"] == {"down": 1, "distance": "10"}
        assert rig.state()["auto_open_at"] is None      # a kickoff may come first: the host opens it

    run_rig(tmp_path, scenario)


def test_check_now_in_a_hurry_up_takes_the_waiting_play_not_the_newest(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, FIRST_PASS)
        await lock_entry(rig, FIRST_PASS)               # 1st & 10
        rig.server.reveal(upto=RUN_LEFT_END)            # the feed shows this play and the next one at once
        await rig.ctrl.feed.check_now()
        sug = rig.state()["suggestion"]
        assert sug["text"].startswith("A.Dalton pass short right") and sug["status"] == "ready" and not sug["warning"]
        assert not any(r["kind"] == "caught_up" for r in rig.store.feed_log(rig.game["id"]))

    run_rig(tmp_path, scenario)
