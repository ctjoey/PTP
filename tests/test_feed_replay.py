"""Live data end to end: the whole recorded game through the real controller (Tank01 path and the practice game),
the HTTP client, and the demo feed."""

import asyncio

import pytest

from app import FeedAcceptIn
from feed import DemoFeed, Tank01Client
from models import score_prediction
from playparse import classify
from tests.fakefeed import ENTRIES, FEED_ID, SENTINEL_KEY, Clock, FakeTank01, Rig, run_rig
from tests.test_feed import dd

# What the host does with the three plays the feed leaves to them.
HOST_CHOICES = {
    # (a sack and a quarterback scramble are no play now: the feed voids them by itself, like a penalty's no play)
    "interception": {},                                        # the prefill (a pass for 0) is right
    "aborted_snap": {"play_type": "RUN", "direction": "MIDDLE", "yards": 1},
}
PICKS = [("RUN", "LEFT", "SHORT"), ("PASS", "RIGHT", "MEDIUM"), ("PASS", "MIDDLE", "LONG"), ("RUN", "RIGHT", "LONG")]


async def replay(rig: Rig, lag: float, reveal_on_fake_server: bool):
    """Open, pick, lock and wait for every scrimmage entry of the recorded game, like a host would."""
    users = [rig.store.create_user(n) for n in ("alice", "bob", "carol", "dave")]
    await rig.new_game(FEED_ID if reveal_on_fake_server else "demo")
    results = []
    for i, entry in enumerate(ENTRIES):
        parsed = classify(entry)
        if parsed.kind == "skip":
            continue
        play = await rig.open(*dd(i))
        picks = {}
        for n, user in enumerate(users):
            if n == 0 and parsed.kind == "play":                # alice always calls it right
                pick = (parsed.play_type, parsed.direction, parsed.yardage if parsed.yardage != "LOSS" else "SHORT")
            else:
                pick = PICKS[(i + n) % len(PICKS)]
            rig.store.submit_prediction(user["id"], play["id"], *pick)
            picks[user["id"]] = pick
        await rig.lock()
        locked, shown = rig.clock(), False
        for _ in range(400):
            await rig.step(1)
            if reveal_on_fake_server and not shown and rig.clock() - locked >= lag:
                rig.server.reveal_through_next_play()
                shown = True
            sug = rig.state()["suggestion"]
            if sug and sug["status"] in ("review", "held"):
                choice = HOST_CHOICES[parsed.reason]
                await rig.ctrl.feed_accept(FeedAcceptIn(play_id=sug["play_id"], **choice))
            if rig.play()["state"] == "RESOLVED":
                break
        play = rig.play()
        assert play["state"] == "RESOLVED", (i, parsed.reason, rig.state())
        results.append((i, parsed, play, picks))
    return users, results


def check_replay(rig: Rig, users, results):
    plays = [r for r in results if r[1].kind == "play"]
    voids = [r for r in results if r[1].kind == "void"]
    reviews = [r for r in results if r[1].kind == "review"]
    assert (len(plays), len(voids), len(reviews)) == (94, 18, 3)
    totals = {u["id"]: 0 for u in users}
    for i, parsed, play, picks in results:
        if parsed.kind == "void":
            assert play["voided"] == 1 and play["resolved_by"] == "void"
            if parsed.reason == "no_play":
                assert "No Play" in play["feed_text"]
            else:                                       # a sack or a QB scramble: no play, nobody scores
                assert parsed.reason in ("sack", "scramble") and parsed.text in play["feed_text"]
            expected = {uid: 0 for uid in picks}
        else:
            assert not play["voided"] and play["resolved_by"] == "feed" and play["feed_text"] == parsed.text
            if parsed.kind == "play":
                assert (play["correct_play_type"], play["correct_direction"], play["correct_yardage"],
                        play["yards_gained"]) == (parsed.play_type, parsed.direction, parsed.yardage, parsed.yards), i
                result = (parsed.play_type, parsed.direction, parsed.yardage)
            else:
                result = (play["correct_play_type"], play["correct_direction"], play["correct_yardage"])
            expected = {uid: score_prediction(*pick, *result).points for uid, pick in picks.items()}
        stored = rig.store.predictions_for_play(play["id"])
        assert {uid: stored[uid]["points_earned"] for uid in picks} == expected, i
        for uid, pts in expected.items():
            totals[uid] += pts
    assert {u["id"]: rig.store.get_user(u["id"])["total_score"] for u in users} == totals
    assert totals[users[0]["id"]] > max(totals[u["id"]] for u in users[1:])        # alice, who calls them all, wins
    # Sacks and QB scrambles were voided: no points for anyone (checked above, they are voids with 0 for every pick).
    assert sorted(r[1].reason for r in voids).count("sack") == 3 and sorted(r[1].reason for r in voids).count("scramble") == 3
    # The review kinds went through the host: prefill respected, direction/yards supplied by the host.
    by_reason = {r[1].reason: r[2] for r in reviews}
    assert (by_reason["interception"]["correct_play_type"], by_reason["interception"]["yards_gained"]) == ("PASS", 0)
    assert (by_reason["aborted_snap"]["correct_direction"], by_reason["aborted_snap"]["yards_gained"]) == ("MIDDLE", 1)
    # Clean plays were scored with no host action: only the review plays needed one.
    assert rig.store.feed_log(rig.game["id"])


def test_full_game_replay_through_the_tank01_client(tmp_path):
    async def scenario(rig: Rig):
        users, results = await replay(rig, lag=17, reveal_on_fake_server=True)
        check_replay(rig, users, results)
        # Frugal: a few checks per play, and nothing at all between plays.
        polls = len(rig.server.box_hits)
        assert len(results) == 115 and polls <= 3 * len(results), polls
        assert rig.state()["requests"]["game"] == polls and rig.state()["lag"]["samples"] >= 100
        assert 12 <= rig.state()["lag"]["median"] <= 22
        n = polls
        await rig.step(3600)
        assert len(rig.server.box_hits) == n

    run_rig(tmp_path, scenario)


def test_full_game_replay_through_the_practice_game(tmp_path):
    async def scenario(rig: Rig):
        users, results = await replay(rig, lag=0, reveal_on_fake_server=False)
        check_replay(rig, users, results)
        s = rig.state()
        assert rig.server.hits == [] and s["requests"]["today"] == 0 and s["requests"]["plan_remaining"] is None
        assert s["requests"]["game"] > 100                      # the meter still shows the practice checks
        assert s["state"] == "done" and s["message"] == "The feed says the game is over."

    run_rig(tmp_path, scenario, tank01_api_key="")


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #


def client_for(server: FakeTank01, key: str = SENTINEL_KEY, timeout: float = 5.0) -> Tank01Client:
    return Tank01Client(key, server.url, timeout)


def test_client_box_score_result_object():
    async def main():
        server = FakeTank01()
        try:
            server.reveal(upto=2)
            res = await client_for(server).box_score(FEED_ID)
            assert res.ok and res.error_kind is None and res.http_status == 200 and res.error_text == ""
            assert (res.remaining, res.limit) == (899, 1000) and res.elapsed_ms >= 0
            assert len(res.body["allPlayByPlay"]) == 3 and res.body["home"] == "WSH"
            hit = server.hits[0]
            assert hit["raw"] == "/getNFLBoxScore?gameID=20241020_CAR%40WSH&playByPlay=true&fantasyPoints=false"
            assert hit["headers"]["x-rapidapi-key"] == SENTINEL_KEY and hit["headers"]["x-rapidapi-host"].startswith("127.0.0.1")
            assert SENTINEL_KEY not in hit["raw"]
        finally:
            server.close()

    asyncio.run(main())


def test_client_games_for_date():
    async def main():
        server = FakeTank01()
        try:
            res = await client_for(server).games_for_date("20261008")
            assert res.ok and res.body[0]["gameID"] == "20261008_TB@DAL"
            assert server.hits[0]["raw"] == "/getNFLGamesForDate?gameDate=20261008"
            server.games = {"error": "no games"}                          # a day with no games
            res = await client_for(server).games_for_date("20261009")
            assert res.ok and res.body == []
            server.games = "garbage"
            res = await client_for(server).games_for_date("20261010")
            assert not res.ok and res.error_kind == "shape"
        finally:
            server.close()

    asyncio.run(main())


@pytest.mark.parametrize("override, kind, text", [
    (dict(status=401, body={"message": "Invalid API key"}), "auth", "Tank01 rejected the API key."),
    (dict(status=403, body={"message": "You are not subscribed to this API."}), "auth", "Tank01 rejected the API key."),
    (dict(status=429, body={"message": "You have exceeded the MONTHLY quota for Requests on your current plan, BASIC."}),
     "quota", "exceeded the MONTHLY quota"),
    (dict(status=200, body={"message": "You have exceeded the DAILY quota"}), "quota", "DAILY quota"),
    (dict(status=500, raw=b"oops"), "http", "HTTP 500"),
    (dict(status=503, body={"message": "unavailable"}), "http", "HTTP 503"),
    (dict(status=404, body={"message": "Endpoint does not exist"}), "http", "HTTP 404"),
    (dict(status=200, raw=b"<html>not json</html>"), "bad_json", "not JSON"),
    (dict(status=200, raw=b""), "bad_json", "not JSON"),
    (dict(status=200, body=[1, 2, 3]), "shape", "not what we expected"),
    (dict(status=200, body={"hello": "world"}), "shape", "not what we expected"),
    (dict(status=200, body={"statusCode": 200, "body": [1, 2]}), "shape", "not what we expected"),
    (dict(status=200, body={"statusCode": 200, "body": {"allPlayByPlay": {"a": 1}}}), "shape", "not a list"),
    (dict(status=200, body={"statusCode": 500, "body": {}}), "http", "status 500"),
    (dict(status=200, body={"statusCode": 200, "body": {"error": "Game hasn't started yet, it will start at 8:15p(ET)."}}),
     "not_started", "8:15p(ET)"),
])
def test_client_error_kinds(override, kind, text):
    async def main():
        server = FakeTank01()
        try:
            server.queue(**override)
            res = await client_for(server).box_score(FEED_ID)
            assert not res.ok and res.error_kind == kind, (res.error_kind, res.error_text)
            assert text in res.error_text
            assert SENTINEL_KEY not in res.error_text
        finally:
            server.close()

    asyncio.run(main())


def test_client_never_raises_for_network_trouble_and_never_follows_redirects():
    async def main():
        server = FakeTank01()
        other = FakeTank01()
        try:
            server.queue(status=302, headers={"Location": other.url + "/steal"})
            res = await client_for(server).box_score(FEED_ID)
            assert not res.ok and res.error_kind == "http" and other.hits == []     # the key never went to the other host
            server.queue(delay=0.5)
            res = await Tank01Client(SENTINEL_KEY, server.url, 0.1).box_score(FEED_ID)
            assert not res.ok and res.error_kind == "network" and "timed out" in res.error_text and res.http_status is None
        finally:
            server.close()
            other.close()
        res = await Tank01Client(SENTINEL_KEY, "http://127.0.0.1:9", 1).box_score(FEED_ID)    # nothing listens there
        assert not res.ok and res.error_kind == "network" and SENTINEL_KEY not in res.error_text
        res = await Tank01Client(SENTINEL_KEY, "not a url at all", 1).games_for_date("20261008")
        assert not res.ok and res.error_kind == "network"
        res = await Tank01Client("", server.url, 1).box_score(FEED_ID)
        assert not res.ok

    asyncio.run(main())


def test_client_repr_hides_the_key():
    client = Tank01Client(SENTINEL_KEY, "https://example.invalid")
    assert SENTINEL_KEY not in repr(client) and client.available
    assert not Tank01Client("   ", "https://example.invalid").available


# --------------------------------------------------------------------------- #
# The practice game
# --------------------------------------------------------------------------- #


def test_demo_feed_reveals_the_next_scrimmage_entry_after_the_lag():
    async def main():
        clock = Clock()
        demo = DemoFeed(lag=12, clock=clock)
        res = await demo.box_score("demo")
        assert res.ok and res.body["allPlayByPlay"] == [] and res.body["gameStatusCode"] == "1"
        assert res.remaining is None and res.limit is None and res.http_status == 200
        demo.on_play_locked()
        clock.advance(11)
        assert (await demo.box_score("demo")).body["allPlayByPlay"] == []
        clock.advance(1)
        shown = (await demo.box_score("demo")).body["allPlayByPlay"]
        assert [e["play"] for e in shown] == [e["play"] for e in ENTRIES[:2]]      # the kickoff and the first play
        demo.on_play_locked()
        demo.on_play_locked()                                                       # two locks: two reveals
        clock.advance(12)
        shown = (await demo.box_score("demo")).body["allPlayByPlay"]
        assert len(shown) == 4
        demo.on_play_locked()
        demo.on_play_cancelled()                                                    # voided before it showed
        clock.advance(100)
        assert len((await demo.box_score("demo")).body["allPlayByPlay"]) == 4
        assert demo.requests == 5
        assert demo.meta["home"] == "WSH" and demo.meta["away"] == "CAR"

    asyncio.run(main())


def test_demo_feed_finishes_the_game_after_the_last_play():
    async def main():
        clock = Clock()
        demo = DemoFeed(lag=0, clock=clock)
        scrimmage = sum(1 for e in ENTRIES if classify(e).kind != "skip")
        for _ in range(scrimmage):
            demo.on_play_locked()
        body = (await demo.box_score("demo")).body
        assert len(body["allPlayByPlay"]) == len(ENTRIES) and body["gameStatusCode"] == "2" and body["currentPeriod"] == "Final"
        res = await demo.games_for_date("20261008")
        assert res.ok and res.body[0]["gameID"] == "demo"

    asyncio.run(main())
