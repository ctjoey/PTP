"""Adversarial matcher and budget scenarios: alignment must never silently drift, and requests must stay frugal."""

import pytest

from app import ResolveIn
from tests.fakefeed import ENTRIES, Rig, run_rig
from tests.test_feed import FIRST_PASS, dd, lock_entry, new_game_midway, show_and_wait

FALSE_START = 18          # "PENALTY ... False Start ... - No Play." at "2nd & 5"; the replayed down is entry 19 ("2nd & 10")


def kinds(rig: Rig) -> list[str]:
    return [r["kind"] for r in rig.store.feed_log(rig.game["id"])]


# --------------------------------------------------------------------------- #
# Stale or empty answers must never make the poller re-read entries it has already matched
# --------------------------------------------------------------------------------------- #


def test_a_stale_shorter_answer_never_replays_a_matched_entry(tmp_path):
    """A lagging cache returns fewer entries for one poll. Re-reading from there would hand the first play's entry to
    the second play (and, with no down and distance typed in, score it by itself)."""
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)                                       # play 1 scored from entry 1
        assert rig.play()["state"] == "RESOLVED"
        await rig.open_lock(None, None)                         # play 2: the host typed no down and distance at all
        rig.server.visible = 1                                  # the next answer is a stale one: the kickoff only
        await rig.until(lambda: len(rig.server.box_hits) >= 3)
        assert rig.state()["suggestion"] is None                # entry 1 was NOT handed to play 2
        assert "stale" in kinds(rig)
        rig.server.reveal(upto=2)                               # the real answer
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["yards"] == 4 and sug["play_type"] == "RUN"  # entry 2, as it should be

    run_rig(tmp_path, scenario)


def test_an_ok_answer_with_no_play_list_changes_nothing(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        cursor = rig.feed.cursor
        await rig.open_lock(2, "3")
        rig.server.queue(200, {"statusCode": 200, "body": {"gameStatus": "In Progress", "gameStatusCode": "1"}})
        await rig.until(lambda: len(rig.server.box_hits) >= 2)
        assert rig.feed.cursor == cursor and rig.state()["suggestion"] is None
        rig.server.queue(200, {"statusCode": 200, "body": {"allPlayByPlay": []}})
        await rig.until(lambda: len(rig.server.box_hits) >= 3)
        assert rig.feed.cursor == cursor and rig.state()["suggestion"] is None

    run_rig(tmp_path, scenario)


def test_a_rewritten_shorter_feed_sends_the_next_suggestion_to_the_host(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.ctrl.feed.check_now()
        await rig.step(8)                                       # scored
        await rig.open_lock(None, None)                         # nothing typed: only the distrust rule can protect it
        rig.server.entries = [dict(e) for e in rig.server.entries]
        rig.server.entries[0]["play"] = "A.Seibert kicks 61 yards from WAS 35 to CAR 4."   # rewritten ...
        rig.server.visible = 1                                  # ... and shorter than before
        await rig.ctrl.feed.check_now()
        rig.server.reveal(upto=2)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and "changed" in sug["warning"] and sug["auto_at"] is None

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# A void cannot be undone: it needs proof
# --------------------------------------------------------------------------- #


def test_a_no_play_entry_never_voids_a_play_with_no_down_and_distance(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, FALSE_START)
        await rig.open_lock(None, None)                         # the host typed nothing
        await show_and_wait(rig, FALSE_START)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None
        assert any("down and distance" in f for f in sug["flags"])
        for _ in range(10):
            await rig.step(30)
        assert rig.play()["state"] == "LOCKED"                  # waiting for the host, as a review should
        await rig.ctrl.feed_accept(__import__("app").FeedAcceptIn(play_id=sug["play_id"], void=True))
        assert rig.play()["voided"] == 1

    run_rig(tmp_path, scenario)


def test_a_no_play_entry_with_the_same_down_and_distance_still_auto_voids(tmp_path):
    async def scenario(rig: Rig):
        await new_game_midway(rig, FALSE_START)
        await rig.open_lock(*dd(FALSE_START))                   # the false-started play itself: 2nd & 5
        await show_and_wait(rig, FALSE_START)
        assert rig.state()["suggestion"]["status"] == "void"
        await rig.step(8)
        assert rig.play()["voided"] == 1
        await lock_entry(rig, FALSE_START + 1)                  # the replayed down is 2nd & 10 ...
        rig.server.reveal(upto=FALSE_START + 1)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "ready" and sug["yards"] == 9   # ... and gets its own entry

    run_rig(tmp_path, scenario)


def test_a_host_void_with_no_down_and_distance_cannot_swallow_a_real_no_play_entry(tmp_path):
    """The twin rule matches on down and distance: with none typed in, nothing may be absorbed (the penalty entry
    then reaches the host as a review, never as an automatic void of the next play)."""
    async def scenario(rig: Rig):
        await new_game_midway(rig, FALSE_START)
        await rig.open_lock(None, None)
        await rig.ctrl.void_play()
        await rig.open_lock(None, None)                         # the next play, also blank
        rig.server.reveal(upto=FALSE_START + 1)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review"                        # NOT "void"
        await rig.step(60)
        assert rig.play()["state"] == "LOCKED"

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Revisions after the fact
# --------------------------------------------------------------------------- #


def test_a_revised_entry_for_the_suggestion_on_screen_goes_back_to_the_host(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["suggestion"]["auto_at"] is not None
        rig.server.entries = [dict(e) for e in rig.server.entries]
        rig.server.entries[FIRST_PASS]["play"] = "A.Dalton pass incomplete short right to J.Sanders (B.St-Juste)."
        await rig.ctrl.feed.check_now()
        sug = rig.state()["suggestion"]
        assert sug["status"] == "review" and sug["auto_at"] is None and sug["yards"] == 0
        assert "changed" in " ".join(sug["flags"])
        await rig.step(60)
        assert rig.play()["state"] == "LOCKED"                   # no automatic score of the old reading

    run_rig(tmp_path, scenario)


def test_a_revision_after_scoring_tells_the_host_when_the_result_differs(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)                                       # scored PASS RIGHT 7 MEDIUM from entry 1
        play_id = rig.play()["id"]
        await rig.open_lock(*dd(2))
        rig.server.entries = [dict(e) for e in rig.server.entries]
        rig.server.entries[FIRST_PASS]["play"] = "A.Dalton pass short right to J.Sanders to CAR 21 for 12 yards (B.St-Juste)."
        rig.server.reveal(upto=2)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        d = rig.state()["disagreement"]
        assert d and d["play_id"] == play_id and d["scored"] == "PASS - RIGHT - MEDIUM" and d["feed"] == "PASS - RIGHT - LONG"
        # ... and a fix removes the notice, comparing later revisions with the corrected result.
        from app import CorrectPlayIn

        await rig.ctrl.correct_play(CorrectPlayIn(play_id=play_id, play_type="PASS", direction="RIGHT", yards=12))
        assert rig.state()["disagreement"] is None

    run_rig(tmp_path, scenario)


def test_an_unchanged_revision_raises_no_notice(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        await rig.open_lock(*dd(2))
        rig.server.entries = [dict(e) for e in rig.server.entries]
        rig.server.entries[FIRST_PASS]["play"] += " (extra words)"
        rig.server.reveal(upto=2)
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        assert rig.state()["disagreement"] is None

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Requests stay frugal, whatever happens
# --------------------------------------------------------------------------- #


def test_a_bug_while_reading_an_answer_backs_off_instead_of_polling_every_five_seconds(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)

        def boom() -> None:
            raise RuntimeError("unexpected")

        rig.feed._consume = boom                                # whatever breaks while reading an answer
        await rig.until(lambda: rig.clock() - rig.state()["waiting"]["since"] >= 120)
        assert len(rig.server.box_hits) <= 10                   # backed off; not 24 requests in two minutes
        assert rig.play()["state"] == "LOCKED"                  # and the host can still score by hand
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        assert rig.play()["state"] == "RESOLVED"

    run_rig(tmp_path, scenario)


def test_a_rate_limit_answer_without_headers_is_a_pause_in_polling_not_the_end_of_it(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(429, {"message": "Too many requests"}, times=2)
        await rig.until(lambda: len(rig.server.box_hits) >= 2)
        assert rig.state()["state"] != "capped"
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=200)

    run_rig(tmp_path, scenario)


def test_requests_per_play_over_a_whole_stretch_of_the_game_are_frugal(tmp_path):
    """Seven consecutive plays with a realistic 15 s feed delay: 2-4 requests per play, none while idle."""
    async def scenario(rig: Rig):
        await rig.new_game()
        counts = []
        for i in (1, 2, 3, 4, 5, 6, 7):
            before = len(rig.server.box_hits)
            await lock_entry(rig, i)
            locked = rig.clock()
            await rig.until(lambda: rig.clock() - locked >= 15, step=1)   # the entry shows up 15 s after the lock
            rig.server.reveal(upto=i)
            await rig.until(lambda: rig.play()["state"] == "RESOLVED", limit=120)
            counts.append(len(rig.server.box_hits) - before)
            for _ in range(6):                                  # 30 s of idle between plays: no requests at all
                n = len(rig.server.box_hits)
                await rig.step(5)
                assert len(rig.server.box_hits) == n
        assert all(1 <= c <= 8 for c in counts), counts
        assert sum(counts[2:]) / len(counts[2:]) <= 4, counts   # once the lag is known the first check lands on target

    run_rig(tmp_path, scenario)


def test_two_entries_in_one_answer_cost_no_extra_request(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, 1)
        rig.server.reveal(upto=2)                               # the feed already shows the next play too
        await rig.until(lambda: rig.state()["suggestion"] is not None)
        await rig.step(8)
        n = len(rig.server.box_hits)
        await lock_entry(rig, 2)
        sug = rig.state()["suggestion"]
        assert sug and sug["yards"] == 4 and len(rig.server.box_hits) == n   # matched from memory

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# Live data can never break the host's own actions
# --------------------------------------------------------------------------- #


def test_a_bug_in_live_data_never_stops_manual_scoring_or_the_console(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()

        def boom(*args, **kwargs):
            raise RuntimeError("live data bug")

        for name in ("on_play_opened", "on_play_locked", "on_play_resolved", "on_play_voided", "on_play_corrected",
                     "on_game_status"):
            setattr(rig.feed, name, boom)
        play = await rig.open(1, "10")
        await rig.lock()
        await rig.ctrl.resolve_play(ResolveIn(play_type="RUN", direction="LEFT", yards=3))
        assert rig.play()["state"] == "RESOLVED" and rig.play()["id"] == play["id"]
        await rig.open_lock(2, "7")
        await rig.ctrl.void_play()
        from app import CorrectPlayIn, StatusIn

        await rig.ctrl.correct_play(CorrectPlayIn(play_id=play["id"], play_type="RUN", direction="LEFT", yards=4))
        await rig.ctrl.set_status(StatusIn(status="LIVE"))
        rig.feed.state = boom                                   # even a broken state() only shows as an error
        msg = rig.ctrl.admin_message("sync")
        assert msg["feed"]["state"] == "error" and msg["game"]["status"] == "LIVE"

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# The key stays secret, whatever the service (or the network) does
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status, payload", [
    (401, {"message": "Invalid API key SENTINEL-KEY-1f9e3c0d77aa for this API"}),
    (403, {"message": "You are not subscribed. key=SENTINEL-KEY-1f9e3c0d77aa"}),
    (429, {"message": "You have exceeded the MONTHLY quota; key SENTINEL-KEY-1f9e3c0d77aa"}),
    (429, {"message": "Too many requests from SENTINEL-KEY-1f9e3c0d77aa"}),
    (500, {"message": "boom SENTINEL-KEY-1f9e3c0d77aa"}),
    (200, {"statusCode": 500, "body": "SENTINEL-KEY-1f9e3c0d77aa"}),
    (200, {"statusCode": 200, "body": {"error": "Game hasn't started. SENTINEL-KEY-1f9e3c0d77aa"}}),
    (200, "SENTINEL-KEY-1f9e3c0d77aa is not json"),
])
def test_an_echoing_service_cannot_leak_the_key_into_results_state_or_the_recorder(tmp_path, status, payload):
    from tests.fakefeed import SENTINEL_KEY

    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        raw = payload.encode() if isinstance(payload, str) else None
        rig.server.queue(status, None if raw else payload, raw=raw, times=3)
        for _ in range(4):
            await rig.step(10)
        direct = await rig.feed.tank01.box_score("20241020_CAR@WSH")
        sched = await rig.feed.tank01.games_for_date("20261008")
        outputs = [repr(direct), repr(sched), repr(rig.feed.tank01), repr(rig.settings), repr(rig.state()),
                   str(rig.store.feed_log(rig.game["id"])), str(rig.ctrl.admin_message("sync")),
                   str(await rig.feed.games_for_date("20261008"))]
        assert all(SENTINEL_KEY not in text for text in outputs)

    run_rig(tmp_path, scenario)


def test_a_redirect_is_never_followed_so_the_key_only_goes_to_the_configured_host(tmp_path):
    from tests.fakefeed import FakeTank01, SENTINEL_KEY

    async def scenario(rig: Rig):
        elsewhere = FakeTank01()
        try:
            rig.server.queue(302, {"statusCode": 302, "body": {}}, headers={"Location": elsewhere.url + "/getNFLBoxScore"})
            result = await rig.feed.tank01.box_score("20241020_CAR@WSH")
            assert not result.ok and result.error_kind == "http"
            assert elsewhere.hits == []                         # the key was not sent anywhere else
            assert SENTINEL_KEY not in repr(result)
        finally:
            elsewhere.close()

    run_rig(tmp_path, scenario)


def test_the_key_is_not_sent_in_the_url_and_a_dead_host_error_does_not_mention_it(tmp_path):
    from feed import Tank01Client
    from tests.fakefeed import SENTINEL_KEY

    async def scenario(rig: Rig):
        await rig.feed.tank01.box_score("20241020_CAR@WSH")
        assert all(SENTINEL_KEY not in h["raw"] and h["headers"]["x-rapidapi-key"] == SENTINEL_KEY for h in rig.server.hits)
        dead = Tank01Client(SENTINEL_KEY, "http://127.0.0.1:1", timeout=2)
        result = await dead.box_score("20241020_CAR@WSH")
        assert result.error_kind == "network" and SENTINEL_KEY not in result.error_text and SENTINEL_KEY not in repr(result)

    run_rig(tmp_path, scenario)


def test_every_admin_route_rejects_a_missing_or_wrong_key(tmp_path):
    """Walk the real route table: nothing under /api/admin answers without the admin key, whatever the method."""
    from fastapi.testclient import TestClient

    from app import Settings, create_app
    from tests.conftest import ADMIN_KEY

    app = create_app(Settings(db_path=str(tmp_path / "sweep.db"), admin_key=ADMIN_KEY, window_seconds=15,
                              tank01_api_key="SENTINEL-KEY-1f9e3c0d77aa", tank01_base_url="http://127.0.0.1:1"))
    with TestClient(app) as client:
        paths = {p: ms for p, ms in app.openapi()["paths"].items() if p.startswith("/api/admin")}
        assert len(paths) >= 10
        for path, methods in paths.items():
            url = path.replace("{action}", "pause")
            for method in methods:
                for headers in ({}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": "SENTINEL-KEY-1f9e3c0d77aa"}):
                    resp = client.request(method.upper(), url, headers=headers, json={} if method != "get" else None)
                    assert resp.status_code == 401, (method, path, headers, resp.status_code)
                    assert "SENTINEL" not in resp.text


# --------------------------------------------------------------------------- #
# Counting: every request exactly once; no hot loops
# --------------------------------------------------------------------------- #


def test_every_request_is_counted_exactly_once_whatever_it_returned(tmp_path):
    async def scenario(rig: Rig):
        rig.feed.tank01.timeout = 0.3
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        rig.server.queue(500)
        rig.server.queue(200, raw=b"not json at all")
        rig.server.queue(200, {"statusCode": 200, "body": {"gameStatus": "x"}}, delay=0.0)
        rig.server.queue(429, {"message": "Too many requests"}, headers={"x-ratelimit-requests-remaining": "800"})
        rig.server.queue(200, {"statusCode": 200, "body": {"allPlayByPlay": []}}, delay=0.6)     # a timeout on our side
        await rig.until(lambda: len(rig.server.hits) >= 6, limit=400)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.state()["suggestion"] is not None, limit=400)
        await rig.feed.check_now()
        await rig.step(60)
        game = rig.state()["requests"]
        polls = [r for r in rig.store.feed_log(rig.game["id"]) if r["kind"] == "poll"]
        assert game["game"] == len(rig.server.hits) == len(polls) == game["today"]
        stored = rig.store.feed_usage(rig.feed._today(), rig.game["id"], now=rig.clock())
        assert stored["game"] == stored["today"] == len(rig.server.hits)

    run_rig(tmp_path, scenario)


def test_the_background_loop_sleeps_when_nothing_is_due_and_never_spins(tmp_path):
    import asyncio

    async def scenario(rig: Rig):
        rig.feed.autorun = True
        ticks = []
        original = rig.feed.tick

        async def counting():
            result = await original()
            ticks.append(result)
            return result

        rig.feed.tick = counting
        await rig.new_game()                                    # attach starts the loop
        await asyncio.sleep(0.4)
        assert len(ticks) <= 3 and ticks[-1] is None            # idle: parked on the wake event, no timer at all
        await rig.open_lock(1, "10")                            # a locked play: one timer (the first check)
        await asyncio.sleep(0.4)
        assert len(ticks) <= 8 and 9 <= ticks[-1] <= 10
        assert len(rig.server.hits) == 0                        # the clock is manual: nothing is due yet

    run_rig(tmp_path, scenario)
