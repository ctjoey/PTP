"""Live data: alignment between the host's plays and the feed's entries (host faster than the feed, voids, restarts,
late starts, orphans, revisions)."""

import asyncio

from app import GameController, Hub, ResolveIn
from models import Store
from tests.fakefeed import Rig, run_rig
from tests.test_feed import (FIRST_PASS, RUN_LEFT_END, dd, lock_entry, new_game_midway, show_and_wait)


def kinds(rig: Rig) -> list[str]:
    return [r["kind"] for r in rig.store.feed_log(rig.game["id"])]


# --------------------------------------------------------------------------- #
# The host is faster than the feed
# --------------------------------------------------------------------------- #


def test_host_scored_play_waits_as_verification_and_alignment_survives(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))   # faster than the feed
        assert [p.resolved for p in rig.feed.pending] == [True] and rig.state()["waiting"] is None
        await lock_entry(rig, RUN_LEFT_END)                    # the feed has shown NOTHING yet
        rig.server.reveal(upto=RUN_LEFT_END)                   # now both entries appear at once
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["play_id"] == rig.play()["id"] and sug["yards"] == 4 and sug["warning"] is None   # entry 2, not entry 1
        assert rig.state()["disagreement"] is None             # the feed agreed with the host about play 1
        assert "verify" in kinds(rig)
        await rig.step(8)
        assert (rig.play()["correct_direction"], rig.play()["yards_gained"]) == ("LEFT", 4)

    run_rig(tmp_path, scenario)


def test_disagreement_notice_when_the_feed_differs_from_the_hosts_score(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=7))    # the feed says PASS RIGHT
        pid = rig.play()["id"]
        await lock_entry(rig, RUN_LEFT_END)
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        d = rig.state()["disagreement"]
        assert d["play_id"] == pid and d["feed"] == "PASS - RIGHT - MEDIUM" and d["scored"] == "RUN - LEFT - MEDIUM"
        assert d["text"].startswith("A.Dalton pass short right") and d["feed_result"]["yards"] == 7
        rig.ctrl.feed.dismiss()
        assert rig.state()["disagreement"] is None

    run_rig(tmp_path, scenario)


def test_fix_result_clears_the_disagreement(tmp_path):
    async def scenario(rig: Rig):
        from app import CorrectPlayIn

        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=7))
        pid = rig.play()["id"]
        await lock_entry(rig, RUN_LEFT_END)
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["disagreement"] is not None)
        await rig.ctrl.correct_play(CorrectPlayIn(play_id=pid, play_type="PASS", direction="RIGHT", yards=7))
        assert rig.state()["disagreement"] is None
        assert rig.store.get_play(pid)["resolved_by"] == "host-fix"

    run_rig(tmp_path, scenario)


def test_host_scoring_while_a_suggestion_is_showing_compares_right_away(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        pid = rig.play()["id"]
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="MIDDLE", yardage="SHORT"))
        s = rig.state()
        assert s["suggestion"] is None and s["disagreement"]["play_id"] == pid
        assert s["disagreement"]["scored"] == "RUN - MIDDLE - SHORT" and rig.feed.pending == []   # its entry was used up
        assert rig.play()["resolved_by"] == "host"
        # the next play is matched to the NEXT entry, not to the one already read
        await lock_entry(rig, RUN_LEFT_END)
        rig.server.reveal(upto=RUN_LEFT_END)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["yards"] == 4

    run_rig(tmp_path, scenario)


def test_host_scoring_with_the_same_answer_as_the_suggestion_raises_no_notice(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=9))   # same bucket (MEDIUM)
        assert rig.state()["disagreement"] is None and rig.state()["suggestion"] is None

    run_rig(tmp_path, scenario)


def test_stale_verification_is_dropped_when_the_situation_contradicts(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await rig.open_lock(3, "9")                            # the host's play 1: a play the feed never lists (a punt?)
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=2))
        await lock_entry(rig, FIRST_PASS)                     # "1st & 10"
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        s = rig.state()
        assert s["suggestion"]["play_id"] == rig.play()["id"] and s["suggestion"]["warning"] is None
        assert s["disagreement"] is None
        assert "verify_dropped" in kinds(rig)

    run_rig(tmp_path, scenario)


def test_old_verification_is_dropped_so_rehearsal_plays_cannot_steal_entries(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)                      # a warm-up play scored by hand before the game ...
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        await rig.step(400)                                    # ... long before the real first play
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["play_id"] == rig.play()["id"]
        assert "verify_dropped" in kinds(rig) and "verify" not in kinds(rig)

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Host voids
# --------------------------------------------------------------------------- #


def test_host_void_removes_the_play_from_the_queue(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.void_play()
        assert rig.feed.pending == [] and rig.state()["waiting"] is None
        n = len(rig.server.hits)
        for _ in range(5):
            await rig.step(30)
        assert len(rig.server.hits) == n
        assert rig.play()["resolved_by"] == "void" and rig.play()["feed_text"] is None

    run_rig(tmp_path, scenario)


def test_the_penalty_entry_of_a_play_the_host_voided_is_not_given_to_the_next_play(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 18)
        await rig.open_lock(2, "5")                           # the false start: feed entry 18 is its "No Play"
        await rig.ctrl.void_play()                            # the host voided it by hand first
        await lock_entry(rig, 19)                             # the replayed down, "2nd & 10"
        rig.server.reveal(upto=19)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "ready" and sug["yards"] == 9 and sug["warning"] is None
        assert "void_twin" in kinds(rig)

    run_rig(tmp_path, scenario)


def test_an_unrelated_void_entry_still_warns_the_host(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 18)
        await rig.open_lock(3, "3")
        await rig.ctrl.void_play()                            # voided for some other reason
        await lock_entry(rig, 19)                             # "2nd & 10"
        rig.server.reveal(upto=19)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]                       # entry 18 ("2nd & 5", No Play) is NOT this play
        assert sug["status"] == "review" and sug["warning"] == "Feed shows 2nd & 5 but this play is 2nd & 10"
        assert "void_twin" not in kinds(rig)

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Restart recovery
# --------------------------------------------------------------------------- #


def test_restart_rebuilds_the_queue_and_rereads_the_unfinished_entry(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        play_id, requests = rig.play()["id"], rig.state()["requests"]["game"]
        assert rig.feed.cursor == 2 and rig.store.current_game()["feed_cursor"] == 1   # saved: its entry is not done yet
        # "restart": a brand-new controller on the same database
        store2 = Store(rig.settings.db_path)
        ctrl2 = GameController(store2, Hub(), rig.settings)
        ctrl2.feed.clock, ctrl2.feed.autorun = rig.clock, False
        await ctrl2.recover()
        s = ctrl2.feed.state()
        assert s["linked"] and s["game_id"] == "20241020_CAR@WSH" and s["requests"]["game"] == requests
        assert [p.play_id for p in ctrl2.feed.pending] == [play_id] and ctrl2.feed.cursor == 1
        assert s["waiting"]["play_id"] == play_id and s["suggestion"] is None
        await asyncio.sleep(0)
        for _ in range(30):
            rig.clock.advance(1)
            await ctrl2.feed.tick()
            if ctrl2.feed.suggestion:
                break
        assert ctrl2.feed.suggestion and ctrl2.feed.suggestion.play_id == play_id   # the same entry, re-suggested
        assert ctrl2.feed.suggestion.parsed.yards == 7
        await ctrl2.shutdown()
        store2.close()

    run_rig(tmp_path, scenario)


def test_restart_keeps_the_switches_pause_and_per_game_allowance(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.ctrl.feed.set_options(auto_score=False, auto_open=True)
        await rig.ctrl.feed.pause()
        rig.ctrl.feed.allow_more(100)
        store2 = Store(rig.settings.db_path)
        ctrl2 = GameController(store2, Hub(), rig.settings)
        ctrl2.feed.autorun = False
        await ctrl2.recover()
        s = ctrl2.feed.state()
        assert (s["auto_score"], s["auto_open"], s["paused"], s["state"]) == (False, True, True, "paused")
        assert s["requests"]["game_cap"] == 1000
        await ctrl2.shutdown()
        store2.close()

    run_rig(tmp_path, scenario)


def test_request_counts_survive_a_restart_so_caps_cannot_be_reset(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) >= 3)
        s = rig.state()["requests"]
        assert s["game"] == s["today"] == len(rig.server.hits)
        store2 = Store(rig.settings.db_path)
        ctrl2 = GameController(store2, Hub(), rig.settings)
        ctrl2.feed.clock, ctrl2.feed.autorun = rig.clock, False
        await ctrl2.recover()
        again = ctrl2.feed.state()["requests"]
        assert (again["game"], again["today"], again["plan_remaining"]) == (s["game"], s["today"], s["plan_remaining"])
        await ctrl2.shutdown()
        store2.close()

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Late start, orphans, revisions
# --------------------------------------------------------------------------- #


def test_linking_late_skips_the_plays_already_played_and_the_host_scores_that_one(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await rig.open_lock(*dd(7))                            # the host's first play: the feed is already at play 7
        rig.server.reveal(upto=7)
        await rig.until(lambda: len(rig.server.hits) >= 1)
        s = rig.state()
        assert s["suggestion"] is None and "joined late" in s["message"] and rig.feed.cursor == 8
        assert "baseline" in kinds(rig)
        await rig.step(300)
        assert rig.play()["state"] == "LOCKED"                  # nothing is auto-scored with a wrong entry
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=1))   # the host scores it by hand
        rig.server.reveal(upto=8)                              # then the feed's next play is entry 8
        await rig.open_lock(*dd(8))
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["play_id"] == rig.play()["id"] and sug["flags"][0] == "Interception" and sug["warning"] is None

    run_rig(tmp_path, scenario)


def test_first_play_locked_before_the_feed_went_live_is_matched_to_the_first_snap(tmp_path):
    """Game night: the host opened play 1 shortly before the snap (the README's advice), Tank01 kept saying "not
    started" for minutes, and its first real answer already held four plays. That play must not be orphaned."""
    async def scenario(rig: Rig):
        from app import FeedAcceptIn

        await rig.new_game()
        rig.server.not_started = True
        await rig.open_lock(1, "10")
        pid = rig.play()["id"]
        await rig.until(lambda: len(rig.server.hits) == 4, limit=700)       # four "not started" answers, 2 minutes apart
        assert rig.state()["state"] == "not_started"
        rig.server.not_started = False
        rig.server.reveal(upto=4)                                          # the kickoff and four plays at once
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=300)
        s = rig.state()
        sug = s["suggestion"]
        assert len(rig.server.hits) == 5 and "joined late" not in s["message"]
        assert sug["play_id"] == pid and sug["status"] == "review" and sug["yards"] == 7     # entry 1: the first snap
        assert "started late" in sug["warning"] and sug["text"].startswith("A.Dalton pass short right")
        base = [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "baseline"]
        assert base and base[0]["data"]["first_play"] == pid and base[0]["data"]["unmatched"] == []
        await rig.step(60)
        assert rig.play()["state"] == "LOCKED"                             # a review never scores by itself
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=pid))              # the host confirms with one tap
        assert (rig.play()["state"], rig.play()["correct_direction"], rig.play()["yards_gained"]) == ("RESOLVED", "RIGHT", 7)
        # plays 2 to 4 happened while play 1 waited: play 2 is matched to what comes AFTER them
        await rig.open_lock(*dd(5))
        assert rig.feed.cursor == 5 and "skipped" in kinds(rig)
        rig.server.reveal(upto=5)
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=300)
        nxt = rig.state()["suggestion"]
        assert nxt["play_id"] == rig.play()["id"] and nxt["yardage"] == "LONG" and nxt["warning"] is None
        assert nxt["status"] == "ready"

    run_rig(tmp_path, scenario)


def test_first_play_locked_before_the_feed_had_any_plays_also_covers_an_empty_play_list(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()                                               # the feed answers, but with no plays yet
        await rig.open_lock(1, "10")
        pid = rig.play()["id"]
        await rig.until(lambda: len(rig.server.hits) >= 3, limit=300)
        rig.server.reveal(upto=4)
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=300)
        sug = rig.state()["suggestion"]
        assert sug["play_id"] == pid and sug["status"] == "review" and "started late" in sug["warning"]
        assert sug["text"].startswith("A.Dalton pass short right") and "joined late" not in rig.state()["message"]

    run_rig(tmp_path, scenario)


def test_hand_scored_plays_while_the_feed_was_not_started_keep_the_late_start_rule(tmp_path):
    """The host kept the game going by hand while Tank01 said "not started": the app is in step with the TV, so when
    the feed finally shows four plays the locked play is the placeholder, not the first snap."""
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.server.not_started = True
        await rig.open_lock(1, "10")
        await rig.until(lambda: len(rig.server.hits) >= 1)
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        await rig.open_lock(2, "3")
        rig.server.not_started = False
        rig.server.reveal(upto=4)
        await rig.until(lambda: "baseline" in kinds(rig), limit=300)
        s = rig.state()
        assert s["suggestion"] is None and "joined late" in s["message"] and "Score it by hand" in s["message"]
        assert rig.play()["state"] == "LOCKED"
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=4))
        assert "joined late" not in rig.state()["message"]                 # the instruction goes away once it is done

    run_rig(tmp_path, scenario)


def test_check_now_gets_to_the_present_when_the_app_is_behind(tmp_path):
    """The host is live but the app works through old plays: Check now must jump to the newest play, not the oldest."""
    async def scenario(rig: Rig):
        await new_game_midway(rig, 1)
        await lock_entry(rig, FIRST_PASS)
        pid = rig.play()["id"]
        await show_and_wait(rig, 4)                            # four plays are in the feed; in order, play 1 gets the oldest
        assert rig.state()["suggestion"]["yards"] == 7
        await rig.ctrl.feed.check_now()                        # "Check now": catch up
        sug = rig.state()["suggestion"]
        assert sug["play_id"] == pid and sug["status"] == "review" and "newest play" in sug["warning"]
        assert sug["text"].startswith("C.Hubbard left tackle") and rig.feed.cursor == 5
        assert "caught_up" in kinds(rig)

    run_rig(tmp_path, scenario)


def test_check_now_with_nothing_locked_skips_everything_and_fills_the_next_down(tmp_path):
    async def scenario(rig: Rig):
        from app import FeedAcceptIn

        await new_game_midway(rig, 1)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=rig.play()["id"]))
        rig.server.reveal(upto=4)                              # three more plays happened that nobody opened
        await rig.ctrl.feed.check_now()
        assert rig.feed.cursor == 5 and kinds(rig).count("orphan") == 3
        assert rig.state()["suggestion"] is None
        assert rig.state()["next_down"] == {"down": 2, "distance": "4"}   # entry 4 was 1st & 10 for 6: the situation now

    run_rig(tmp_path, scenario)


def test_a_review_the_host_accepted_as_read_still_fills_the_next_down(tmp_path):
    async def scenario(rig: Rig):
        from app import FeedAcceptIn

        await new_game_midway(rig, 1)
        await rig.open_lock(3, "9")                            # the host's down and distance disagree with the feed
        await show_and_wait(rig, FIRST_PASS)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["warning"]
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=sug["play_id"]))
        assert rig.state()["next_down"] == {"down": 2, "distance": "3"}
        assert rig.state()["auto_open_at"] is None             # but only a clean play opens the next one by itself

    run_rig(tmp_path, scenario)


def test_a_voided_late_start_play_clears_the_notice_too(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await rig.open_lock(*dd(7))
        rig.server.reveal(upto=7)
        await rig.until(lambda: "joined late" in rig.state()["message"])
        await rig.ctrl.void_play()
        assert "joined late" not in rig.state()["message"]

    run_rig(tmp_path, scenario)


def test_a_feed_one_play_ahead_is_not_a_late_start(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=RUN_LEFT_END)                   # the feed shows two plays, the host has locked one
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert "baseline" not in kinds(rig) and rig.state()["suggestion"]["yards"] == 7

    run_rig(tmp_path, scenario)


def test_entries_nobody_opened_a_play_for_are_discarded_as_orphans(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 0)
        rig.server.reveal(upto=4)                              # four plays happen while the host has nothing locked
        await rig.ctrl.feed.check_now()                        # "Check now" with nothing waiting
        assert rig.feed.cursor == 5 and kinds(rig).count("orphan") == 4
        assert rig.state()["suggestion"] is None
        await lock_entry(rig, 5)                               # the next play lines up with entry 5
        rig.server.reveal(upto=5)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["text"].startswith("A.Dalton pass deep right to Di.Johnson")

    run_rig(tmp_path, scenario)


def test_revisions_of_earlier_entries_are_recorded(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=3)
        await rig.ctrl.feed.check_now()
        rig.server.entries = [dict(e) for e in rig.server.entries]
        rig.server.entries[3]["play"] = "REVISED TEXT for the third play."
        await rig.ctrl.feed.check_now()
        rows = [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "revised"]
        assert rows and rows[0]["data"]["index"] == 3 and rows[0]["data"]["now"].startswith("REVISED")
        before = rig.feed.cursor
        rig.server.visible = 2                                 # the feed even got shorter: a stale answer, ignored
        await rig.ctrl.feed.check_now()
        assert [r["data"] for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "stale"] == [{"was": 4, "now": 2}]
        assert rig.feed.cursor == before                       # never re-read what was already matched
        rig.server.entries[1]["play"] = "A different first play."   # shorter AND rewritten: no longer just a stale answer
        await rig.ctrl.feed.check_now()
        assert any(r["data"].get("reason") == "feed got shorter" for r in
                   [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "revised"])
        assert rig.feed.cursor <= 2

    run_rig(tmp_path, scenario)


def test_each_new_entry_is_recorded_once_with_its_classification(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=2)
        await rig.ctrl.feed.check_now()
        await rig.ctrl.feed.check_now()
        entries = [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "entry"]
        assert [r["feed_index"] for r in entries] == [0, 1, 2]
        assert entries[0]["data"]["kind"] == "skip" and entries[1]["data"]["kind"] == "play"
        assert entries[1]["data"]["entry"]["downAndDistance"] == "1st & 10 at CAR 14"
        assert entries[1]["data"]["since_lock"] is not None
        polls = [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "poll"]
        assert len(polls) == 2 and polls[0]["data"]["n_entries"] == 3 and polls[0]["data"]["game_status"] == "In Progress"
        assert polls[0]["data"]["remaining"] is not None and polls[0]["data"]["http"] == 200

    run_rig(tmp_path, scenario)


def test_the_recorder_keeps_the_last_5000_rows_per_game(tmp_path):
    import models

    store = Store(str(tmp_path / "log.db"))
    store.create_game("Detroit", "#0076B6", "#B0B7BC", "Chicago", "#0B162A", "#C83803")
    game = store.current_game()["id"]
    for i in range(models.FEED_LOG_KEEP + 250):
        store.log_feed(game, "poll", {"i": i}, ts=float(i))
    store.log_feed(None, "schedule", {"x": 1})
    rows = store.feed_log(game)
    assert models.FEED_LOG_KEEP <= len(rows) <= models.FEED_LOG_KEEP + models.FEED_LOG_PRUNE_EVERY
    assert rows[-1]["data"]["i"] == models.FEED_LOG_KEEP + 249 and rows[0]["data"]["i"] >= 100
    assert len(store.feed_log(None)) == 1
    store.close()


def test_the_game_clock_the_feed_is_at_and_where_the_app_is(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 1)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, 4)                            # four plays are in the feed, the app is on the first
        c = rig.state()["clock"]
        assert c["app"] == "Q1 14:55" and c["feed"] == "Q1 13:16" and c["behind"] == 3
        assert c["feed_at"] <= rig.clock()
        await rig.ctrl.feed.check_now()                        # catch up: the newest play, nothing beyond it
        c = rig.state()["clock"]
        assert c["app"] == c["feed"] and c["behind"] == 0

    run_rig(tmp_path, scenario)


def test_the_box_score_clock_reads_like_the_plays_clock(tmp_path):
    async def scenario(rig: Rig):
        from feed import LiveFeed

        assert [LiveFeed._period_label(p) for p in ("3rd", "Q3", "2", "4th", "OT", None)] == ["Q3", "Q3", "Q2", "Q4", "OT", ""]
        await new_game_midway(rig, 1)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, 4)
        rig.feed._note_clock({"currentPeriod": "3rd", "gameClock": "10:43"}, rig.clock())
        c = rig.state()["clock"]
        assert c["live"] == "Q3 10:43" and c["feed"] == "Q1 13:16"       # live clock beside the newest play's clock

    run_rig(tmp_path, scenario)


def test_the_live_score_rides_along_with_the_game_for_players_and_the_host(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 1)
        assert rig.ctrl.admin_message("x")["game"]["home_score"] is None          # nothing read yet
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        host = rig.ctrl.admin_message("x")["game"]
        player = rig.ctrl._snapshot([]).game
        for game in (host, player):
            assert (game["home_score"], game["away_score"]) == (40, 7) and game["score_at"] <= rig.clock()
        assert "feed_game_id" not in player                                       # live-data settings stay the host's

    run_rig(tmp_path, scenario)


def test_players_are_told_when_the_score_moves(tmp_path):
    async def scenario(rig: Rig):
        sent: list[str] = []
        original = rig.ctrl.feed_broadcast

        async def spy(event: str) -> None:
            sent.append(event)
            await original(event)

        rig.ctrl.feed_broadcast = spy
        await new_game_midway(rig, 1)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        assert sent.count("score") == 1                        # the first score the feed reads
        await rig.ctrl.feed.check_now()
        assert sent.count("score") == 1                        # the same score again: nothing to say
        import tests.fakefeed as ff
        original_meta = ff.META
        ff.META = {**ff.META, "homePts": "47"}
        try:
            await rig.ctrl.feed.check_now()
        finally:
            ff.META = original_meta
        assert sent.count("score") == 2

    run_rig(tmp_path, scenario)
