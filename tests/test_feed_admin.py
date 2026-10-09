"""Live data through the real app: admin protocol (WebSocket and REST), the schedule list, the recorder download,
what players do and do not see, and the API key staying secret."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import Settings, create_app
from tests.conftest import ADMIN_KEY, GAME
from tests.fakefeed import FEED_ID, SENTINEL_KEY, FakeTank01
from tests.test_app import recv_until, register, state_event

HEADERS = {"X-Admin-Key": ADMIN_KEY}
FAST = dict(tank01_first_delay=0.05, tank01_fast_interval=0.05, tank01_auto_score_grace=0.15, tank01_open_delay=0.1,
            tank01_demo_lag=0.05)
FEED_KEYS = {"available", "linked", "source", "game_id", "state", "message", "paused", "auto_score", "auto_open",
             "requests", "lag", "waiting", "suggestion", "disagreement", "next_down", "auto_open_at", "clock"}


@pytest.fixture
def fake():
    server = FakeTank01()
    yield server
    server.close()


def make_client(tmp_path, fake=None, **overrides):
    settings = Settings(db_path=str(tmp_path / "app.db"), admin_key=ADMIN_KEY, window_seconds=15,
                        tank01_api_key=SENTINEL_KEY if fake else "", tank01_base_url=fake.url if fake else "http://127.0.0.1:9",
                        **{**FAST, **overrides})
    return TestClient(create_app(settings))


def wait_for(client, predicate, timeout=8.0):
    """Poll the admin state over REST until ``predicate(admin_state)`` (the feed runs in real time, in the app)."""
    end = time.time() + timeout
    while time.time() < end:
        state = client.get("/api/admin/state", headers=HEADERS).json()
        if predicate(state):
            return state
        time.sleep(0.02)
    raise AssertionError(f"timed out; last feed state: {state['feed']}")


def post(client, path, **body):
    return client.post(f"/api/admin{path}", json=body, headers=HEADERS)


# --------------------------------------------------------------------------- #
# Auth and shape
# --------------------------------------------------------------------------- #

NEW_ENDPOINTS = [
    ("post", "/api/admin/feed/link"), ("post", "/api/admin/feed/pause"), ("post", "/api/admin/feed/resume"),
    ("post", "/api/admin/feed/check_now"), ("post", "/api/admin/feed/set"), ("post", "/api/admin/feed/hold"),
    ("post", "/api/admin/feed/accept"), ("post", "/api/admin/feed/skip"), ("post", "/api/admin/feed/dismiss"),
    ("post", "/api/admin/feed/allow_more"), ("post", "/api/admin/feed/correct_play"), ("post", "/api/admin/play/correct"),
    ("get", "/api/admin/feed/games?date=20261008"), ("get", "/api/admin/feed/log"),
]


@pytest.mark.parametrize("method, path", NEW_ENDPOINTS)
def test_every_new_endpoint_needs_the_admin_key(tmp_path, method, path):
    with make_client(tmp_path) as client:
        call = getattr(client, method)
        assert call(path).status_code == 401
        assert call(path, headers={"X-Admin-Key": "wrong"}).status_code == 401
        assert call(path, headers={"X-Admin-Key": SENTINEL_KEY}).status_code == 401


def test_new_websocket_actions_need_the_admin_login(tmp_path):
    with make_client(tmp_path) as client:
        with client.websocket_connect("/ws/admin") as ws:
            ws.send_json({"action": "feed_pause", "request_id": "x"})
            assert ws.receive_json()["type"] == "auth_error"


def test_admin_state_has_the_feed_object_in_every_situation(tmp_path):
    with make_client(tmp_path) as client:
        state = client.get("/api/admin/state", headers=HEADERS).json()
        feed = state["feed"]
        assert FEED_KEYS <= feed.keys() and feed["available"] is False and feed["linked"] is False
        assert feed["state"] == "off" and feed["source"] is None and feed["game_id"] is None and feed["message"]
        assert feed["requests"] == {"game": 0, "today": 0, "game_cap": 900, "day_cap": 1000, "plan_remaining": None,
                                    "plan_limit": None}
        assert feed["lag"] == {"median": None, "last": None, "samples": 0}
        assert feed["waiting"] is None and feed["suggestion"] is None and feed["disagreement"] is None
        assert feed["next_down"] is None and feed["auto_open_at"] is None
        assert (feed["paused"], feed["auto_score"], feed["auto_open"]) == (False, True, False)


def test_admin_state_feed_shape_while_linked_and_waiting(tmp_path, fake):
    with make_client(tmp_path, fake, tank01_first_delay=30) as client:
        assert post(client, "/game", **GAME, feed_game_id=FEED_ID).status_code == 201
        feed = client.get("/api/admin/state", headers=HEADERS).json()["feed"]
        assert feed["available"] and feed["linked"] and feed["source"] == "tank01" and feed["game_id"] == FEED_ID
        assert feed["state"] == "idle"
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        feed = client.get("/api/admin/state", headers=HEADERS).json()["feed"]
        assert feed["state"] == "waiting" and set(feed["waiting"]) == {"play_id", "checks", "since", "next_check_at"}
        assert feed["waiting"]["next_check_at"] - feed["waiting"]["since"] == pytest.approx(30, abs=0.5)
        assert isinstance(feed["waiting"]["since"], float) and feed["waiting"]["checks"] == 0


# --------------------------------------------------------------------------- #
# Creating and linking a game
# --------------------------------------------------------------------------- #


def test_create_game_with_the_practice_game_links_it(tmp_path):
    with make_client(tmp_path) as client:
        res = post(client, "/game", **GAME, feed_game_id="demo")
        assert res.status_code == 201 and res.json()["feed_game_id"] == "demo"
        feed = client.get("/api/admin/state", headers=HEADERS).json()["feed"]
        assert feed["linked"] and feed["source"] == "demo" and feed["game_id"] == "demo" and feed["available"] is False


def test_create_game_rejects_a_bad_or_unavailable_feed_game(tmp_path):
    with make_client(tmp_path) as client:
        for bad in ("../x", "20261008_TB@DAL; drop", "x" * 41, "demo2", "2026-10-08_TB@DAL"):
            assert post(client, "/game", **GAME, feed_game_id=bad).status_code == 422, bad
        res = post(client, "/game", **GAME, feed_game_id="20261008_TB@DAL")          # no key on this server
        assert res.status_code == 409 and "not set up" in res.json()["detail"]
        assert client.get("/api/admin/state", headers=HEADERS).json()["game"] is None  # nothing half-created
        assert post(client, "/game", **GAME).status_code == 201                       # without a link it still works
        assert post(client, "/game", **GAME, feed_game_id="").status_code == 201
        assert post(client, "/game", **GAME, feed_game_id=None).status_code == 201


def test_link_and_unlink_over_rest(tmp_path, fake):
    with make_client(tmp_path, fake) as client:
        post(client, "/game", **GAME)
        res = post(client, "/feed/link", feed_game_id=FEED_ID)
        assert res.status_code == 200 and res.json()["feed"]["game_id"] == FEED_ID and res.json()["feed"]["source"] == "tank01"
        res = post(client, "/feed/feed_link", feed_game_id="demo")                    # the long name works too
        assert res.json()["feed"]["source"] == "demo"
        res = post(client, "/feed/link", feed_game_id=None)
        assert res.json()["feed"]["linked"] is False and res.json()["feed"]["state"] == "off"
        assert post(client, "/feed/link", feed_game_id="nope!").status_code == 422


def test_feed_actions_on_a_game_without_live_data_explain_themselves(tmp_path):
    with make_client(tmp_path) as client:
        assert post(client, "/feed/pause").status_code == 409                          # no game at all
        post(client, "/game", **GAME)
        for action in ("pause", "resume", "check_now"):
            res = post(client, f"/feed/{action}")
            assert res.status_code == 409 and "not connected" in res.json()["detail"]
        assert post(client, "/feed/hold").status_code == 409
        assert post(client, "/feed/skip", play_id=1).status_code == 409
        assert post(client, "/feed/accept", play_id=1).status_code == 409
        assert post(client, "/feed/dismiss").status_code == 200


def test_unknown_or_malformed_feed_requests(tmp_path):
    with make_client(tmp_path) as client:
        assert post(client, "/feed/explode").status_code == 404
        assert post(client, "/feed/games").status_code == 404 or post(client, "/feed/games").status_code == 405
        assert post(client, "/feed/correct_play").status_code == 422                   # missing fields
        assert client.post("/api/admin/feed/pause", content=b"[1, 2]", headers=HEADERS).status_code == 422
        assert client.post("/api/admin/feed/pause", content=b"{not json", headers=HEADERS).status_code == 422
        assert post(client, "/feed/accept", play_id="x").status_code == 422
        assert post(client, "/feed/accept", play_id=1, play_type="BOMB").status_code == 422
        assert post(client, "/feed/accept", play_id=1, yardage="LONG", yards=3).status_code == 422
        assert post(client, "/feed/allow_more", n=0).status_code == 422
        assert post(client, "/feed/set", auto_score="maybe").status_code == 422


# --------------------------------------------------------------------------- #
# A real-time run in the app: the practice game over WebSocket
# --------------------------------------------------------------------------- #


def test_practice_game_scores_a_play_by_itself_over_the_admin_socket(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "alice")
        with client.websocket_connect("/ws/admin") as admin:
            admin.send_json({"type": "auth", "key": ADMIN_KEY})
            first = recv_until(admin, lambda m: m["type"] == "admin_state")
            assert first["feed"]["state"] == "off"
            seen = []

            def act(action, **payload):
                admin.send_json({"action": action, "request_id": action, **payload})

                def is_ack(m):
                    seen.append(m)
                    return m["type"] == "admin_ack" and m["request_id"] == action
                ack = recv_until(admin, is_ack, limit=80)
                assert ack["ok"], ack
                return ack["result"]

            act("create_game", **GAME, feed_game_id="demo")
            act("open_play", down=1, distance="10")
            play = act("lock_play")
            # The feed shows the first play after the demo lag, suggests it, and scores it after the grace period.
            def is_resolved(m):
                seen.append(m)
                return m["type"] == "admin_state" and m["event"] == "play_resolved"
            resolved = recv_until(admin, is_resolved, limit=400)
            assert resolved["play"]["state"] == "RESOLVED" and resolved["play"]["correct_play_type"] == "PASS"
            assert resolved["history"][0]["resolved_by"] == "feed" and resolved["history"][0]["yards_gained"] == 7
            assert resolved["feed"]["next_down"] == {"down": 2, "distance": "3"}
            assert any(m["type"] == "admin_state" and (m["feed"]["suggestion"] or {}).get("status") == "ready" for m in seen)
            # the new actions answer with the new feed object
            res = act("feed_pause")
            assert res["feed"]["paused"] is True and res["feed"]["state"] == "paused"
            assert act("feed_resume")["feed"]["paused"] is False
            assert act("feed_set", auto_open=True)["feed"]["auto_open"] is True
            assert act("feed_allow_more", n=100)["feed"]["requests"]["game_cap"] == 900   # the practice game has no cap
            admin.send_json({"action": "feed_accept", "request_id": "bad", "play_id": play["id"]})
            ack = recv_until(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == "bad")
            assert ack["ok"] is False and "no longer current" in ack["error"]
            admin.send_json({"action": "feed_accept", "request_id": "bad2", "play_id": "x"})
            ack = recv_until(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == "bad2")
            assert ack["ok"] is False and "play_id" in ack["error"]


def test_auto_score_countdown_hold_and_score_now_over_rest(tmp_path):
    with make_client(tmp_path, tank01_auto_score_grace=1.0) as client:
        post(client, "/game", **GAME, feed_game_id="demo")
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        state = wait_for(client, lambda s: s["feed"]["suggestion"] is not None)
        sug = state["feed"]["suggestion"]
        assert sug["status"] == "ready" and sug["auto_at"] > state["server_time"] and sug["auto_at"] - state["server_time"] <= 1.0
        res = post(client, "/feed/hold")
        assert res.json()["feed"]["suggestion"]["status"] == "held" and res.json()["feed"]["suggestion"]["auto_at"] is None
        time.sleep(1.4)
        assert client.get("/api/admin/state", headers=HEADERS).json()["play"]["state"] == "LOCKED"     # held: no auto-score
        res = post(client, "/feed/accept", play_id=sug["play_id"])
        assert res.status_code == 200 and res.json()["play"]["state"] == "RESOLVED" and res.json()["feed"]["suggestion"] is None
        history = client.get("/api/admin/state", headers=HEADERS).json()["history"][0]
        assert (history["resolved_by"], history["yards_gained"]) == ("feed", 7) and history["feed_text"].startswith("A.Dalton")


def test_skip_and_accept_with_changes_over_rest(tmp_path):
    with make_client(tmp_path, tank01_auto_score_grace=30) as client:
        post(client, "/game", **GAME, feed_game_id="demo")
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        sug = wait_for(client, lambda s: s["feed"]["suggestion"])["feed"]["suggestion"]
        res = post(client, "/feed/accept", play_id=sug["play_id"], play_type="RUN", direction="MIDDLE", yards=3)
        assert res.status_code == 200
        play = client.get("/api/admin/state", headers=HEADERS).json()["history"][0]
        assert (play["correct_play_type"], play["correct_direction"], play["yards_gained"]) == ("RUN", "MIDDLE", 3)
        # a second play whose entry the host skips
        post(client, "/play/open", down=2, distance="3")
        post(client, "/play/lock")
        sug = wait_for(client, lambda s: s["feed"]["suggestion"])["feed"]["suggestion"]
        res = post(client, "/feed/skip", play_id=sug["play_id"])
        assert res.status_code == 200 and (res.json()["feed"]["suggestion"] is None or res.json()["feed"]["suggestion"]["text"] != sug["text"])
        assert post(client, "/feed/skip", play_id=sug["play_id"] + 7).status_code == 409


def test_feed_pause_over_rest_stops_the_background_loop(tmp_path, fake):
    with make_client(tmp_path, fake) as client:
        post(client, "/game", **GAME, feed_game_id=FEED_ID)
        post(client, "/feed/pause")
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        time.sleep(0.5)
        assert fake.hits == []
        fake.reveal(upto=1)
        post(client, "/feed/resume")
        state = wait_for(client, lambda s: s["feed"]["suggestion"] is not None)
        assert len(fake.box_hits) >= 1 and state["feed"]["suggestion"]["yards"] == 7
        assert state["feed"]["requests"]["game"] == len(fake.box_hits)


def test_the_background_loop_polls_the_fake_tank01_and_scores(tmp_path, fake):
    with make_client(tmp_path, fake) as client:
        post(client, "/game", **GAME, feed_game_id=FEED_ID)
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        fake.reveal(upto=1)
        wait_for(client, lambda s: s["play"]["state"] == "RESOLVED")
        state = client.get("/api/admin/state", headers=HEADERS).json()
        assert state["history"][0]["resolved_by"] == "feed" and state["feed"]["requests"]["plan_remaining"] is not None
        assert state["feed"]["lag"]["samples"] == 1
        n = len(fake.box_hits)
        time.sleep(0.5)
        assert len(fake.box_hits) == n                          # nothing locked: no requests


# --------------------------------------------------------------------------- #
# Players do not notice
# --------------------------------------------------------------------------- #

PLAYER_GAME_KEYS = {"id", "home_name", "home_primary", "home_secondary", "away_name", "away_primary", "away_secondary",
                    "status", "created_at"}
PLAYER_PLAY_KEYS = {"id", "game_id", "play_number", "down", "distance", "state", "voided", "opened_at", "locks_at",
                    "correct_play_type", "correct_direction", "correct_yardage", "yards_gained"}
PLAYER_MESSAGE_KEYS = {"type", "event", "server_time", "game", "play", "my_prediction", "me", "leaderboard",
                       "ranked_players", "crowd", "lounge", "scoring"}


def test_players_never_see_live_data_fields(tmp_path):
    with make_client(tmp_path, tank01_auto_score_grace=0.2) as client:
        alice = register(client, "alice")
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "token": alice["token"]})
            recv_until(ws, state_event("sync"))
            post(client, "/game", **GAME, feed_game_id="demo")
            created = recv_until(ws, state_event("game_created"))
            post(client, "/play/open", down=1, distance="10")
            post(client, "/play/lock")
            wait_for(client, lambda s: s["play"]["state"] == "RESOLVED")
            events = [created]
            while True:
                msg = recv_until(ws, lambda m: m.get("type") == "state", limit=50)
                events.append(msg)
                if msg["event"] == "play_resolved":
                    break
        for msg in events:
            assert set(msg) == PLAYER_MESSAGE_KEYS, msg["event"]
            assert set(msg["game"]) == PLAYER_GAME_KEYS
            if msg["play"]:
                assert set(msg["play"]) == PLAYER_PLAY_KEYS
        public = client.get("/api/state").json()
        assert set(public["game"]) == PLAYER_GAME_KEYS and set(public["play"]) == PLAYER_PLAY_KEYS
        text = json.dumps([events, public])
        for secret in ("feed_game_id", "feed_text", "resolved_by", "feed_cursor", "demo", "Dalton"):
            assert secret not in text, secret
        admin_game = client.get("/api/admin/state", headers=HEADERS).json()["game"]
        assert admin_game["feed_game_id"] == "demo"            # the host does


# --------------------------------------------------------------------------- #
# The schedule list
# --------------------------------------------------------------------------- #


def test_schedule_lists_the_days_games_with_team_presets(tmp_path, fake):
    fake.games = [
        {"gameID": "20261008_TB@DAL", "away": "TB", "home": "DAL", "gameTime": "8:15p", "gameTime_epoch": "1791504900.0",
         "gameStatus": "Scheduled", "gameStatusCode": "0"},
        {"gameID": "20261008_NYJ@NYG", "away": "NYJ", "home": "NYG", "gameTime": "1:00p", "gameTime_epoch": "1791478800.0",
         "gameStatus": "Scheduled", "gameStatusCode": "0"},
        {"gameID": "20261008_LAC@LAR", "away": "LAC", "home": "LAR", "gameTime": "4:25p", "gameTime_epoch": "1791490000.0",
         "gameStatus": "In Progress", "gameStatusCode": "1"},
        {"gameID": "20261008_ZZZ@WAS", "away": "ZZZ", "home": "WAS", "gameTime": "9:00p", "gameTime_epoch": "bad",
         "gameStatus": "Scheduled", "gameStatusCode": "0"},
        {"gameID": "garbage id", "away": "AAA", "home": "BBB"},
        "not a game",
    ]
    with make_client(tmp_path, fake) as client:
        res = client.get("/api/admin/feed/games?date=20261008", headers=HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["date"] == "20261008" and data["available"] is True and data["error"] is None
        assert [g["feed_game_id"] for g in data["games"]] == ["20261008_ZZZ@WAS", "20261008_NYJ@NYG", "20261008_LAC@LAR",
                                                               "20261008_TB@DAL"]
        by_id = {g["feed_game_id"]: g for g in data["games"]}
        tb = by_id["20261008_TB@DAL"]
        assert tb["time"] == "8:15p" and tb["status"] == "Scheduled" and tb["status_code"] == "0"
        assert tb["away"]["abbr"] == "TB" and tb["away"]["name"] == "Tampa Bay" and tb["away"]["primary"] == "#D50A0A"
        assert tb["home"]["name"] == "Dallas" and tb["home"]["secondary"] == "#869397"
        ny = by_id["20261008_NYJ@NYG"]                          # same city: two names that pass the name rules
        assert (ny["away"]["name"], ny["home"]["name"]) == ("New York Green", "New York Blue")
        assert ny["away"]["label"] == "New York (green & white)"
        la = by_id["20261008_LAC@LAR"]
        assert (la["away"]["name"], la["home"]["name"]) == ("Los Angeles Powder", "Los Angeles Blue")
        odd = by_id["20261008_ZZZ@WAS"]                         # unknown abbreviation: plain name, neutral colours; WAS = WSH
        assert odd["away"]["name"] == "ZZZ" and odd["away"]["primary"].startswith("#") and odd["home"]["name"] == "Washington"
        assert data["demo"]["feed_game_id"] == "demo" and data["demo"]["away"]["name"] == "Carolina"
        assert data["demo"]["home"]["name"] == "Washington" and data["demo"]["status_code"] == "demo"
        # exactly one real request; a second ask within minutes is served from memory
        assert len(fake.hits) == 1 and fake.hits[0]["raw"] == "/getNFLGamesForDate?gameDate=20261008"
        again = client.get("/api/admin/feed/games?date=20261008", headers=HEADERS).json()
        assert again["cached"] is True and len(fake.hits) == 1 and again["games"] == data["games"]
        # every listed game can be used to create a game
        for g in data["games"]:
            body = {"home_name": g["home"]["name"], "home_primary": g["home"]["primary"], "home_secondary": g["home"]["secondary"],
                    "away_name": g["away"]["name"], "away_primary": g["away"]["primary"], "away_secondary": g["away"]["secondary"],
                    "feed_game_id": g["feed_game_id"]}
            assert post(client, "/game", **body).status_code == 201, body
        state = client.get("/api/admin/state", headers=HEADERS).json()
        assert state["requests"] if False else state["feed"]["requests"]["today"] == 1          # the schedule counted for the day
        assert state["feed"]["requests"]["game"] == 0                                           # but not for any game


def test_schedule_without_a_key_offers_only_the_practice_game(tmp_path):
    with make_client(tmp_path) as client:
        data = client.get("/api/admin/feed/games?date=20261008", headers=HEADERS).json()
        assert data["available"] is False and data["games"] == [] and "no Tank01 key" in data["error"]
        assert data["demo"]["feed_game_id"] == "demo"


def test_schedule_defaults_to_today_and_validates_the_date(tmp_path, fake):
    with make_client(tmp_path, fake) as client:
        assert client.get("/api/admin/feed/games?date=2026-10-8", headers=HEADERS).status_code == 422
        assert client.get("/api/admin/feed/games?date=soon", headers=HEADERS).status_code == 422
        data = client.get("/api/admin/feed/games", headers=HEADERS).json()
        assert len(data["date"]) == 8 and data["date"].isdigit() and fake.hits[0]["query"]["gameDate"] == data["date"]
        assert client.get("/api/admin/feed/games?date=2026-10-09", headers=HEADERS).json()["date"] == "20261009"


@pytest.mark.parametrize("override, expected", [
    (dict(status=401, body={"message": "bad key"}), "rejected the API key"),
    (dict(status=429, body={"message": "You have exceeded the MONTHLY quota"}), "limit is used up"),
    (dict(status=500, raw=b"oops"), "Could not reach Tank01"),
    (dict(status=200, raw=b"not json"), "Could not reach Tank01"),
])
def test_schedule_problems_come_back_as_plain_text(tmp_path, fake, override, expected):
    fake.queue(**override)
    with make_client(tmp_path, fake) as client:
        res = client.get("/api/admin/feed/games?date=20261008", headers=HEADERS)
        data = res.json()
        assert res.status_code == 200 and data["games"] == [] and expected in data["error"]
        assert data["demo"]["feed_game_id"] == "demo"
        assert SENTINEL_KEY not in res.text


def test_schedule_respects_the_daily_cap(tmp_path, fake):
    with make_client(tmp_path, fake, tank01_max_requests_per_day=1) as client:
        assert client.get("/api/admin/feed/games?date=20261008", headers=HEADERS).json()["error"] is None
        again = client.get("/api/admin/feed/games?date=20261009", headers=HEADERS).json()
        assert "out of requests" in again["error"] and len(fake.hits) == 1


# --------------------------------------------------------------------------- #
# The recorder
# --------------------------------------------------------------------------- #


def test_feed_log_endpoint_returns_the_recorder_as_json(tmp_path, fake):
    with make_client(tmp_path, fake) as client:
        assert client.get("/api/admin/feed/log", headers=HEADERS).json() == {"game_id": None, "count": 0, "rows": []}
        post(client, "/game", **GAME, feed_game_id=FEED_ID)
        gid = client.get("/api/admin/state", headers=HEADERS).json()["game"]["id"]
        post(client, "/play/open", down=1, distance="10")
        post(client, "/play/lock")
        fake.reveal(upto=1)
        wait_for(client, lambda s: s["play"]["state"] == "RESOLVED")
        res = client.get("/api/admin/feed/log", headers=HEADERS)
        data = res.json()
        assert data["game_id"] == gid and data["count"] == len(data["rows"]) > 5
        kinds = [r["kind"] for r in data["rows"]]
        for kind in ("attach", "lock", "poll", "entry", "bind", "suggest", "score"):
            assert kind in kinds, kind
        assert kinds.index("lock") < kinds.index("poll") < kinds.index("bind") < kinds.index("suggest") < kinds.index("score")
        assert all(set(r) == {"id", "ts", "kind", "play_id", "feed_index", "data"} for r in data["rows"])
        score = next(r for r in data["rows"] if r["kind"] == "score")
        assert score["data"]["by"] == "auto" and score["data"]["result"][:3] == ["PASS", "RIGHT", "MEDIUM"]
        poll = next(r for r in data["rows"] if r["kind"] == "poll")["data"]
        assert {"ok", "http", "ms", "n_entries", "game_status", "remaining", "limit"} <= poll.keys()
        assert SENTINEL_KEY not in res.text
        assert client.get(f"/api/admin/feed/log?game_id={gid}", headers=HEADERS).json()["count"] == data["count"]
        assert client.get("/api/admin/feed/log?game_id=999", headers=HEADERS).json()["rows"] == []
        dl = client.get("/api/admin/feed/log?download=1", headers=HEADERS)
        assert "attachment" in dl.headers["content-disposition"] and dl.headers["content-disposition"].endswith('.json"')
        assert client.get("/api/admin/feed/log?game_id=abc", headers=HEADERS).status_code == 422


# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #


def test_the_key_never_reaches_the_host_console_or_players(tmp_path, fake, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    with make_client(tmp_path, fake) as client:
        alice = register(client, "alice")
        seen = []
        with client.websocket_connect("/ws/admin") as admin, client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "token": alice["token"]})
            admin.send_json({"type": "auth", "key": ADMIN_KEY})
            post(client, "/game", **GAME, feed_game_id=FEED_ID)
            post(client, "/play/open", down=1, distance="10")
            fake.queue(status=500, raw=f"key {SENTINEL_KEY} exploded".encode(), times=2)
            post(client, "/play/lock")
            fake.reveal(upto=1)
            wait_for(client, lambda s: s["play"]["state"] == "RESOLVED")

            def collect(m):
                seen.append(json.dumps(m))
                return m.get("event") == "play_resolved"
            recv_until(admin, collect, limit=200)
            recv_until(ws, collect, limit=200)
        for path in ("/api/admin/state", "/api/admin/feed/log", "/api/admin/feed/games?date=20261008", "/api/state", "/admin",
                     "/", "/rules"):
            seen.append(client.get(path, headers=HEADERS).text)
        seen.append(post(client, "/feed/check_now").text)
        seen.append(caplog.text)
        assert all(SENTINEL_KEY not in text for text in seen)
        assert len(seen) > 12
        assert "feed poll" in caplog.text          # one line per poll goes to the normal logger, without secrets


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_settings_read_the_environment_with_safe_fallbacks(monkeypatch):
    for name, value in {"TANK01_API_KEY": "  k-123 ", "TANK01_BASE_URL": "http://x.test", "TANK01_MAX_REQUESTS_PER_GAME": "50",
                        "TANK01_MAX_REQUESTS_PER_DAY": "oops", "TANK01_RESERVE": "3", "TANK01_ALLOW_OVERAGE": "1",
                        "TANK01_FIRST_DELAY": "7.5", "TANK01_FAST_INTERVAL": "", "TANK01_AUTO_SCORE_GRACE": "9",
                        "TANK01_OPEN_DELAY": "11", "TANK01_TIMEOUT": "4", "TANK01_DEMO_LAG": "2"}.items():
        monkeypatch.setenv(name, value)
    s = Settings()
    assert (s.tank01_api_key, s.tank01_base_url) == ("k-123", "http://x.test")
    assert (s.tank01_max_requests_per_game, s.tank01_max_requests_per_day, s.tank01_reserve) == (50, 1000, 3)
    assert s.tank01_allow_overage is True and s.tank01_first_delay == 7.5 and s.tank01_fast_interval == 5.0
    assert (s.tank01_auto_score_grace, s.tank01_open_delay, s.tank01_timeout, s.tank01_demo_lag) == (9.0, 11.0, 4.0, 2.0)
    assert "k-123" not in repr(s)
    for name in list(__import__("os").environ):
        if name.startswith("TANK01_"):
            monkeypatch.delenv(name)
    d = Settings()
    assert (d.tank01_api_key, d.tank01_max_requests_per_game, d.tank01_max_requests_per_day, d.tank01_reserve) == ("", 900, 1000, 15)
    assert d.tank01_allow_overage is False and (d.tank01_first_delay, d.tank01_fast_interval) == (10.0, 5.0)
    assert (d.tank01_auto_score_grace, d.tank01_open_delay, d.tank01_timeout) == (8.0, 12.0, 15.0)
    assert d.tank01_base_url == "https://tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"
