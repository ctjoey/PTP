"""Live data: errors, backoff, caps, pause/resume/check-now, auto-open, and the API key staying secret."""

import json
import logging

import pytest

from app import FeedAcceptIn, ResolveIn
from models import GameError
from tests.fakefeed import FEED_ID, SENTINEL_KEY, Rig, run_rig
from tests.test_feed import FIRST_PASS, RUN_LEFT_END, dd, lock_entry, new_game_midway, show_and_wait


def log_rows(rig: Rig, kind: str):
    return [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == kind]


# --------------------------------------------------------------------------- #
# The game has not started
# --------------------------------------------------------------------------- #


def test_not_started_body_waits_two_minutes_and_says_when_the_game_starts(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.server.not_started = True
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) == 1)
        s = rig.state()
        assert s["state"] == "not_started" and "8:15p(ET)" in s["message"]
        assert s["waiting"]["next_check_at"] - rig.clock() == 120
        assert log_rows(rig, "poll")[0]["data"]["error_kind"] == "not_started"
        await rig.step(119)
        assert len(rig.server.hits) == 1
        rig.server.not_started = False
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["state"] == "idle" and rig.state()["suggestion"]["yards"] == 7

    run_rig(tmp_path, scenario)


def test_warmup_plays_scored_by_hand_before_kickoff_cannot_steal_the_first_entries(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.server.not_started = True
        for _ in range(2):                                    # two rehearsal plays, scored by hand, with the game not on yet
            await rig.open_lock(1, "10")
            await rig.until(lambda: rig.state()["state"] == "not_started")
            await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=3))
        await rig.open_lock(1, "10")
        await rig.until(lambda: len(rig.server.hits) >= 3)    # a poll while it has still not started drops what is left
        assert [p.resolved for p in rig.feed.pending] == [False]
        rig.server.not_started = False
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["play_id"] == rig.play()["id"] and rig.state()["suggestion"]["yards"] == 7

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Errors and backoff
# --------------------------------------------------------------------------- #


def test_bad_json_and_wrong_shape_are_failures_not_crashes(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(raw=b"<html>gateway</html>")
        rig.server.queue(body={"statusCode": 200, "body": "weird"})
        rig.server.queue(body={"nothing": "useful"})
        rig.server.queue(body={"statusCode": 200, "body": {"allPlayByPlay": "not a list"}})
        await rig.until(lambda: len(rig.server.hits) >= 4)
        kinds = [r["data"]["error_kind"] for r in log_rows(rig, "poll")]
        assert kinds == ["bad_json", "shape", "shape", "shape"]
        s = rig.state()
        assert s["state"] == "waiting" and "Trouble reaching Tank01" in s["message"]
        rig.server.reveal(upto=FIRST_PASS)                    # and it recovers by itself
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=200)
        assert rig.state()["suggestion"]["yards"] == 7

    run_rig(tmp_path, scenario)


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_stops_polling_and_says_so(tmp_path, status):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(status=status, body={"message": "You are not subscribed to this API."})
        await rig.until(lambda: len(rig.server.hits) == 1)
        s = rig.state()
        assert s["state"] == "error" and s["message"].startswith("Tank01 rejected the API key")
        for _ in range(10):
            await rig.step(60)
        assert len(rig.server.hits) == 1                      # no retries
        assert rig.play()["state"] == "LOCKED"                # the host just scores by hand
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        assert rig.play()["state"] == "RESOLVED"
        # Fixing the key and tapping Resume tries again.
        await rig.open_lock(*dd(RUN_LEFT_END))
        await rig.ctrl.feed.resume()
        assert len(rig.server.hits) == 2 and rig.state()["state"] == "waiting"

    run_rig(tmp_path, scenario)


def test_a_wrong_key_is_rejected_by_the_service_and_never_echoed(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.server.key = "some-other-key"                      # the fake service expects a different key
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) == 1)
        assert rig.state()["state"] == "error"
        assert SENTINEL_KEY not in json.dumps(rig.ctrl.admin_message("sync"))

    run_rig(tmp_path, scenario)


def test_quota_error_caps_live_data_and_manual_scoring_continues(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(status=429, body={"message": "You have exceeded the MONTHLY quota for Requests on your current plan, BASIC."})
        await rig.until(lambda: len(rig.server.hits) == 1)
        s = rig.state()
        assert s["state"] == "capped" and "request limit is used up" in s["message"] and s["requests"]["plan_remaining"] == 0
        for _ in range(10):
            await rig.step(60)
        assert len(rig.server.hits) == 1
        with pytest.raises(GameError):
            await rig.ctrl.feed.check_now()
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


def test_a_quota_message_with_an_ok_status_is_still_a_quota(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(status=200, body={"message": "You have exceeded the DAILY quota for Requests on your current plan."})
        await rig.until(lambda: rig.state()["state"] == "capped")

    run_rig(tmp_path, scenario)


def test_server_errors_back_off_then_show_an_error_then_pause(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        locked = rig.clock()
        rig.server.queue(status=503, times=12)
        await rig.until(lambda: len(rig.server.hits) >= 10, limit=900)
        offsets = rig.poll_offsets(locked)
        gaps = [b - a for a, b in zip(offsets, offsets[1:])]
        assert offsets[0] == 10 and gaps[:5] == [10, 20, 30, 30, 30]       # doubling, capped at 30 s
        # five in a row: "error" (still retrying slowly); ten: paused with a message
        assert log_rows(rig, "error")[4]["data"]["streak"] == 5
        s = rig.state()
        assert s["state"] == "paused" and s["paused"] is True and "trouble reaching tank01" in s["message"].lower()
        assert rig.store.current_game()["feed_paused"] == 1
        n = len(rig.server.hits)
        for _ in range(10):
            await rig.step(60)
        assert len(rig.server.hits) == n
        rig.server.queue(status=200, times=0)
        await rig.ctrl.feed.resume()                                      # the host taps Resume: it works again
        assert rig.state()["state"] in ("waiting", "idle") and not rig.state()["paused"]

    run_rig(tmp_path, scenario)


def test_state_is_error_after_five_failures(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(status=500, times=5)
        await rig.until(lambda: len(rig.server.hits) >= 5, limit=900)
        s = rig.state()
        assert s["state"] == "error" and "trouble" in s["message"].lower()
        assert s["waiting"]["next_check_at"] - rig.clock() == pytest.approx(30)

    run_rig(tmp_path, scenario)


def test_a_timeout_is_a_network_failure(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(delay=0.6)
        await rig.until(lambda: len(rig.server.hits) >= 1)
        row = log_rows(rig, "poll")[0]["data"]
        assert row["ok"] is False and row["error_kind"] == "network" and "timed out" in row["error"]
        assert rig.state()["requests"]["game"] == 1            # an attempt that timed out still counts

    run_rig(tmp_path, scenario, tank01_timeout=0.2)


def test_unreachable_service_is_a_network_failure(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.server.close()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(log_rows(rig, "poll")) >= 1)
        row = log_rows(rig, "poll")[0]["data"]
        assert row["error_kind"] == "network" and row["http"] is None

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Caps
# --------------------------------------------------------------------------- #


def test_per_game_cap_stops_polling_and_allow_more_continues(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: rig.state()["state"] == "capped")
        assert len(rig.server.hits) == 3
        s = rig.state()
        assert s["requests"]["game"] == 3 and s["requests"]["game_cap"] == 3
        assert "used its 3 requests" in s["message"] and "Allow 100 more" in s["message"]
        for _ in range(10):
            await rig.step(60)
        assert len(rig.server.hits) == 3
        with pytest.raises(GameError, match="used its 3 requests"):
            await rig.ctrl.feed.check_now()
        rig.server.reveal(upto=FIRST_PASS)
        await rig.ctrl.feed_allow_more(__import__("app").FeedMoreIn())    # "Allow 100 more"
        assert rig.state()["requests"]["game_cap"] == 103 and rig.state()["state"] == "waiting"
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert len(rig.server.hits) == 4

    run_rig(tmp_path, scenario, tank01_max_requests_per_game=3)


def test_the_extra_allowance_survives_a_restart(tmp_path):
    async def scenario(rig: Rig):
        from app import GameController, Hub
        from models import Store

        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: rig.state()["state"] == "capped")
        rig.ctrl.feed.allow_more(100)
        ctrl2 = GameController(Store(rig.settings.db_path), Hub(), rig.settings)
        ctrl2.feed.clock, ctrl2.feed.autorun = rig.clock, False
        await ctrl2.recover()
        assert ctrl2.feed.state()["requests"]["game_cap"] == 103 and ctrl2.feed.state()["state"] == "waiting"
        await ctrl2.shutdown()

    run_rig(tmp_path, scenario, tank01_max_requests_per_game=3)


def test_per_day_cap_counts_every_game_and_resets_at_midnight_utc(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: rig.state()["state"] == "capped")
        s = rig.state()
        assert len(rig.server.hits) == 2 and s["requests"]["today"] == 2 and s["requests"]["day_cap"] == 2
        assert "Today's limit of 2 requests" in s["message"]
        # a different game the same day shares the day's count
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        await rig.new_game()
        assert rig.state()["requests"]["game"] == 0 and rig.state()["requests"]["today"] == 2
        assert rig.state()["state"] == "capped"
        await rig.step(86400)                                  # the next UTC day: counting starts over
        assert rig.state()["requests"]["today"] == 0 and rig.state()["state"] == "idle"

    run_rig(tmp_path, scenario, tank01_max_requests_per_day=2)


def test_plan_reserve_stops_live_data_unless_overage_is_allowed(tmp_path):
    async def scenario(rig: Rig):
        rig.server.remaining = 17                              # the plan's allowance as the headers will show it
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: rig.state()["state"] == "capped")
        s = rig.state()
        assert s["requests"]["plan_remaining"] == 15 and s["requests"]["plan_limit"] == 1000
        assert len(rig.server.hits) == 2
        assert "only 15 requests left" in s["message"] and "15 spare" in s["message"]
        for _ in range(10):
            await rig.step(60)
        assert len(rig.server.hits) == 2
        with pytest.raises(GameError):
            await rig.ctrl.feed.check_now()
        # "Allow 100 more" raises the per-game cap only; the plan guard still holds.
        rig.ctrl.feed.allow_more(100)
        assert rig.state()["state"] == "capped"

    run_rig(tmp_path, scenario)


def test_allow_overage_lets_polling_continue_below_the_reserve(tmp_path):
    async def scenario(rig: Rig):
        rig.server.remaining = 17
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) >= 6)
        assert rig.state()["state"] == "waiting" and rig.state()["requests"]["plan_remaining"] <= 12

    run_rig(tmp_path, scenario, tank01_allow_overage=True)


def test_a_remembered_plan_allowance_survives_a_restart_and_goes_stale_after_a_day(tmp_path):
    async def scenario(rig: Rig):
        from app import GameController, Hub
        from models import Store

        rig.server.remaining = 16
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: rig.state()["state"] == "capped")
        ctrl2 = GameController(Store(rig.settings.db_path), Hub(), rig.settings)
        ctrl2.feed.clock, ctrl2.feed.autorun = rig.clock, False
        await ctrl2.recover()
        assert ctrl2.feed.state()["state"] == "capped"          # a restart does not forget the plan is nearly used up
        rig.clock.advance(2 * 86400)                            # but a number a day old is not trusted forever
        ctrl3 = GameController(Store(rig.settings.db_path), Hub(), rig.settings)
        ctrl3.feed.clock, ctrl3.feed.autorun = rig.clock, False
        await ctrl3.recover()
        assert ctrl3.feed.state()["requests"]["plan_remaining"] is None and ctrl3.feed.state()["state"] != "capped"
        await ctrl2.shutdown()
        await ctrl3.shutdown()

    run_rig(tmp_path, scenario)


def test_the_practice_game_is_never_capped_and_spends_nothing(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game("demo")
        await rig.open_lock(1, "10")
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        for _ in range(3):
            await rig.open_lock(*dd(RUN_LEFT_END))
            await rig.until(lambda: rig.play()["state"] == "RESOLVED", limit=100)
            break
        s = rig.state()
        assert s["source"] == "demo" and s["game_id"] == "demo" and rig.server.hits == []
        assert s["requests"]["today"] == 0 and s["requests"]["plan_remaining"] is None and s["state"] != "capped"

    run_rig(tmp_path, scenario, tank01_max_requests_per_game=1, tank01_max_requests_per_day=1, tank01_api_key="")


# --------------------------------------------------------------------------- #
# Pause, resume, check now
# --------------------------------------------------------------------------- #


def test_pause_stops_requests_and_the_auto_timer_resume_checks_at_once(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.feed_pause()
        assert rig.state()["state"] == "paused" and rig.state()["paused"] and rig.state()["waiting"]["next_check_at"] is None
        for _ in range(10):
            await rig.step(60)
        assert rig.server.hits == []
        rig.server.reveal(upto=FIRST_PASS)
        out = await rig.ctrl.feed_resume()                     # an immediate check
        assert len(rig.server.hits) == 1 and out["feed"]["suggestion"]["yards"] == 7
        assert rig.state()["state"] == "idle" and not rig.state()["paused"]
        assert rig.store.current_game()["feed_paused"] == 0

    run_rig(tmp_path, scenario)


def test_pause_holds_a_running_countdown_and_resume_restarts_it(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        assert rig.state()["suggestion"]["auto_at"] is not None
        await rig.ctrl.feed_pause()
        assert rig.state()["suggestion"]["auto_at"] is None
        await rig.step(60)
        assert rig.play()["state"] == "LOCKED"
        await rig.ctrl.feed_resume()
        assert rig.state()["suggestion"]["auto_at"] == rig.clock() + 8
        await rig.step(8)
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


def test_check_now_makes_one_request_even_when_paused_or_nothing_waits(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await rig.ctrl.feed_check_now()                        # nothing is locked: one request, on the host's say-so
        assert len(rig.server.hits) == 1
        await rig.ctrl.feed_pause()
        await rig.ctrl.feed_check_now()
        assert len(rig.server.hits) == 2
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.ctrl.feed_check_now()                        # paused, but the host asked
        assert len(rig.server.hits) == 3 and rig.state()["suggestion"]["yards"] == 7
        assert rig.state()["suggestion"]["auto_at"] is None    # paused: no countdown

    run_rig(tmp_path, scenario)


def test_check_now_while_a_play_waits_resets_the_next_check(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(3)
        await rig.ctrl.feed_check_now()
        s = rig.state()
        assert len(rig.server.hits) == 1 and s["waiting"]["checks"] == 1
        assert s["waiting"]["next_check_at"] - rig.clock() == 5

    run_rig(tmp_path, scenario)


def test_feed_actions_need_a_linked_game(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game(None)
        for action in (rig.ctrl.feed_pause, rig.ctrl.feed_resume, rig.ctrl.feed_check_now):
            with pytest.raises(GameError, match="not connected"):
                await action()
        s = rig.state()
        assert s["linked"] is False and s["state"] == "off" and s["source"] is None and s["game_id"] is None
        assert s["available"] is True and s["suggestion"] is None and s["waiting"] is None

    run_rig(tmp_path, scenario)


def test_linking_and_unlinking_a_game(tmp_path):
    async def scenario(rig: Rig):
        from app import FeedLinkIn

        with pytest.raises(GameError, match="Create a game"):
            await rig.ctrl.feed_link(FeedLinkIn(feed_game_id=FEED_ID))
        await rig.new_game(None)
        out = await rig.ctrl.feed_link(FeedLinkIn(feed_game_id=FEED_ID))
        assert out["feed"]["linked"] and out["feed"]["source"] == "tank01" and rig.game["feed_game_id"] == FEED_ID
        await rig.ctrl.feed_link(FeedLinkIn(feed_game_id="demo"))
        assert rig.state()["source"] == "demo"
        await rig.ctrl.feed_link(FeedLinkIn(feed_game_id=None))
        assert rig.state()["state"] == "off" and rig.game["feed_game_id"] is None
        with pytest.raises(ValueError):
            FeedLinkIn(feed_game_id="../etc/passwd")

    run_rig(tmp_path, scenario)


def test_without_a_key_only_the_practice_game_can_be_linked(tmp_path):
    async def scenario(rig: Rig):
        from app import CreateGameIn, FeedLinkIn
        from tests.fakefeed import GAME

        assert rig.feed.available is False
        with pytest.raises(GameError, match="not set up"):
            await rig.ctrl.create_game(CreateGameIn(**GAME, feed_game_id=FEED_ID))
        assert rig.store.current_game() is None                # nothing was created
        await rig.ctrl.create_game(CreateGameIn(**GAME, feed_game_id="demo"))
        assert rig.state()["available"] is False and rig.state()["source"] == "demo"
        with pytest.raises(GameError, match="not set up"):
            await rig.ctrl.feed_link(FeedLinkIn(feed_game_id=FEED_ID))

    run_rig(tmp_path, scenario, tank01_api_key="")


# --------------------------------------------------------------------------- #
# Auto-open
# --------------------------------------------------------------------------- #


def test_auto_open_is_off_by_default(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        assert rig.state()["auto_open"] is False
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)
        assert rig.state()["auto_open_at"] is None and rig.state()["next_down"] == {"down": 2, "distance": "3"}
        await rig.step(120)
        assert rig.play()["state"] == "RESOLVED"               # nothing opened by itself

    run_rig(tmp_path, scenario)


def test_auto_open_opens_the_next_play_with_the_computed_down_and_distance(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)                                      # scored
        s = rig.state()
        assert s["auto_open_at"] == rig.clock() + 12
        await rig.step(11)
        assert rig.play()["state"] == "RESOLVED"
        await rig.step(1)
        play = rig.play()
        assert play["state"] == "OPEN" and (play["down"], play["distance"]) == (2, "3") and play["play_number"] == 2
        assert play["locks_at"] - play["opened_at"] == 15      # the normal window
        assert rig.state()["auto_open_at"] is None and rig.state()["next_down"] is None
        assert rig.game["status"] == "LIVE"

    run_rig(tmp_path, scenario)


@pytest.mark.parametrize("entry, why", [
    (13, "a 3rd-down incompletion leads to 4th down: likely a punt or field goal"),
    (48, "a touchdown"),
    (34, "a sack is a review play"),
])
def test_auto_open_skips_fourth_downs_touchdowns_and_review_plays(tmp_path, entry, why):
    async def scenario(rig: Rig):
        await new_game_midway(rig, entry)
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, entry)
        await show_and_wait(rig, entry)
        sug = rig.state()["suggestion"]
        if sug["status"] == "review":
            await rig.ctrl.feed_accept(FeedAcceptIn(play_id=sug["play_id"], direction="LEFT"))
        else:
            await rig.step(8)
        assert rig.play()["state"] == "RESOLVED"
        assert rig.state()["auto_open_at"] is None, why
        await rig.step(120)
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


def test_auto_open_is_cancelled_by_the_host_or_by_pausing(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)
        assert rig.state()["auto_open_at"] is not None
        await rig.ctrl.feed_pause()                            # pausing cancels it
        assert rig.state()["auto_open_at"] is None
        await rig.step(60)
        assert rig.play()["state"] == "RESOLVED"
        await rig.ctrl.feed_resume()
        # the host opens the next play themselves before the timer fires
        await lock_entry(rig, RUN_LEFT_END)
        await show_and_wait(rig, RUN_LEFT_END)
        await rig.step(8)
        assert rig.state()["auto_open_at"] is not None
        await rig.open(2, "3")                                 # the host got there first
        assert rig.state()["auto_open_at"] is None
        await rig.step(60)
        assert rig.play()["play_number"] == 3 and rig.play()["state"] == "OPEN"

    run_rig(tmp_path, scenario)


def test_auto_open_turned_off_cancels_the_pending_open(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)
        assert rig.state()["auto_open_at"] is not None
        rig.ctrl.feed.set_options(auto_open=False)
        assert rig.state()["auto_open_at"] is None

    run_rig(tmp_path, scenario)


def test_auto_open_does_not_fire_after_the_game_is_over(tmp_path):
    async def scenario(rig: Rig):
        from app import StatusIn

        await rig.new_game()
        rig.ctrl.feed.set_options(auto_open=True)
        await lock_entry(rig, FIRST_PASS)
        await show_and_wait(rig, FIRST_PASS)
        await rig.step(8)
        await rig.ctrl.set_status(StatusIn(status="FINAL"))
        assert rig.state()["auto_open_at"] is None and rig.state()["state"] == "done"
        await rig.step(60)
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# The API key never leaves the server
# --------------------------------------------------------------------------- #


def test_the_api_key_goes_only_in_the_request_header(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.until(lambda: len(rig.server.hits) >= 1)
        hit = rig.server.hits[0]
        assert hit["headers"]["x-rapidapi-key"] == SENTINEL_KEY and hit["headers"]["x-rapidapi-host"].startswith("127.0.0.1")
        assert SENTINEL_KEY not in hit["path"] and all(SENTINEL_KEY not in v for v in hit["query"].values())
        assert hit["query"] == {"gameID": FEED_ID, "playByPlay": "true", "fantasyPoints": "false"}
        assert hit["path"] == "/getNFLBoxScore"

    run_rig(tmp_path, scenario)


def test_the_key_never_appears_in_any_output(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(rig: Rig):
        outputs = []
        # errors whose text echoes the key (a misbehaving service), a quota message, odd bodies, a timeout
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(status=500, raw=f"internal error for key {SENTINEL_KEY}".encode())
        rig.server.queue(status=200, body={"statusCode": 200, "body": {"error": f"key {SENTINEL_KEY} is not subscribed"}})
        rig.server.queue(status=429, body={"message": f"quota exceeded for {SENTINEL_KEY}"})
        await rig.until(lambda: rig.state()["state"] == "capped")
        for _ in range(3):
            outputs.append(json.dumps(rig.ctrl.admin_message("sync"), default=str))
            outputs.append(json.dumps(rig.state(), default=str))
        outputs.append(json.dumps(rig.store.feed_log(rig.game["id"]), default=str))
        outputs.append(json.dumps(await rig.feed.games_for_date("20261008"), default=str))
        try:
            await rig.ctrl.feed.check_now()
        except GameError as exc:
            outputs.append(exc.message)
        outputs += [repr(rig.settings), repr(rig.feed.tank01), repr(rig.feed), repr(rig.ctrl)]
        outputs.append(json.dumps({"hits": [h["path"] for h in rig.server.hits]}))
        outputs.append(caplog.text)
        for text in outputs:
            assert SENTINEL_KEY not in text
        assert len(outputs) > 10

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Odds and ends
# --------------------------------------------------------------------------- #


def test_a_request_still_counts_when_the_game_changes_while_it_is_in_flight(tmp_path):
    async def scenario(rig: Rig):
        import asyncio

        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        old = rig.game["id"]
        rig.server.queue(delay=0.3)
        task = asyncio.create_task(rig.feed.check_now())
        await asyncio.sleep(0.1)                                # the request is out
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        await rig.new_game()                                    # the host starts another game meanwhile
        await task
        day = rig.feed._day_key
        assert rig.store.feed_usage(day, old)["game"] == 1 and rig.store.feed_usage(day, rig.game["id"])["game"] == 0
        assert rig.store.feed_usage(day)["today"] == 1
        assert rig.state()["requests"]["game"] == 0 and rig.state()["suggestion"] is None   # its answer was not applied

    run_rig(tmp_path, scenario)


def test_holding_a_review_suggestion_leaves_it_in_review(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 89)           # an aborted snap: the host must look (a sack is no play now)
        await lock_entry(rig, 89)
        await show_and_wait(rig, 89)
        rig.ctrl.feed.hold()
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None

    run_rig(tmp_path, scenario)


def test_a_held_void_suggestion_can_still_be_accepted_as_a_void(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, 18)
        await lock_entry(rig, 18)
        await show_and_wait(rig, 18)
        assert rig.state()["suggestion"]["status"] == "void"
        rig.ctrl.feed.hold()
        assert rig.state()["suggestion"]["status"] == "held"
        await rig.ctrl.feed_accept(FeedAcceptIn(play_id=rig.play()["id"]))
        assert rig.play()["voided"] == 1

    run_rig(tmp_path, scenario)


def test_the_practice_game_carries_on_from_the_same_play_after_a_restart(tmp_path):
    async def scenario(rig: Rig):
        from app import GameController, Hub
        from models import Store

        await rig.new_game("demo")
        await rig.open_lock(1, "10")
        await rig.until(lambda: rig.play()["state"] == "RESOLVED", limit=100)
        cursor = rig.store.current_game()["feed_cursor"]
        assert cursor == 2
        ctrl2 = GameController(Store(rig.settings.db_path), Hub(), rig.settings)
        ctrl2.feed.clock, ctrl2.feed.autorun = rig.clock, False
        await ctrl2.recover()
        from app import OpenPlayIn

        await ctrl2.open_play(OpenPlayIn(down=2, distance="3"))
        await ctrl2.lock_play()
        for _ in range(100):
            rig.clock.advance(1)
            await ctrl2.feed.tick()
            if ctrl2.feed.suggestion:
                break
        assert ctrl2.feed.suggestion and ctrl2.feed.suggestion.parsed.yards == 4        # the second recorded play
        await ctrl2.shutdown()

    run_rig(tmp_path, scenario, tank01_api_key="")
