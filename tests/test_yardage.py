"""Distance picks: protocol fields, validation, legacy rows, old-database migration, team presets."""

import json
import re
import sqlite3
import time

import pytest

from models import EXACT_POINTS, GameError, Store, validate_color, validate_team_name
from teams import DEFAULT_AWAY, DEFAULT_HOME, TEAM_PRESETS, preset
from tests.conftest import ADMIN_KEY, GAME
from tests.test_app import player_socket, recv_until, register, state_event


def auth(user):
    return {"Authorization": f"Bearer {user['token']}"}


def open_locked_play(client, admin_headers, picks=()):
    """Create the game, open a play, submit ``[(user, type, dir, yardage)]`` and lock it."""
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play = client.post("/api/admin/play/open", json={"down": 3, "distance": "7"}, headers=admin_headers).json()
    for user, ptype, pdir, pyard in picks:
        res = client.post("/api/predictions", headers=auth(user),
                          json={"play_id": play["id"], "play_type": ptype, "direction": pdir, "yardage": pyard})
        assert res.status_code == 200, res.text
    assert client.post("/api/admin/play/lock", headers=admin_headers).status_code == 200
    return play


# --------------------------------------------------------------------------- #
# Predictions must carry a distance
# --------------------------------------------------------------------------- #


def test_predict_requires_yardage_over_rest(client, admin_headers):
    user = register(client, "Joey")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play = client.post("/api/admin/play/open", json={}, headers=admin_headers).json()
    body = {"play_id": play["id"], "play_type": "RUN", "direction": "LEFT"}
    assert client.post("/api/predictions", headers=auth(user), json=body).status_code == 422
    for bad in ("LOSS", "FAR", "", None):
        assert client.post("/api/predictions", headers=auth(user), json={**body, "yardage": bad}).status_code == 422
    res = client.post("/api/predictions", headers=auth(user), json={**body, "yardage": "LONG"})
    assert res.status_code == 200 and res.json()["yardage"] == "LONG"
    # Re-sending while OPEN changes the pick.
    res = client.post("/api/predictions", headers=auth(user), json={**body, "yardage": "SHORT"})
    assert res.json()["yardage"] == "SHORT"
    pick = client.app.state.ctrl.store.predictions_for_play(play["id"])
    assert [p["yardage"] for p in pick.values()] == ["SHORT"]


def test_store_rejects_missing_or_bad_yardage(store):
    store.create_game(**GAME)
    user = store.create_user("joey")
    _, play = store.open_next_play(1, "10", 15)
    for bad in (None, "LOSS", "FAR"):
        with pytest.raises(ValueError):
            store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", bad)
    assert store.predictions_for_play(play["id"]) == {}


# --------------------------------------------------------------------------- #
# Resolving: yardage and/or yards
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload, bucket, yards",
    [
        ({"yardage": "MEDIUM"}, "MEDIUM", None),
        ({"yards": 7}, "MEDIUM", 7),
        ({"yardage": "MEDIUM", "yards": 7}, "MEDIUM", 7),
        ({"yards": 0}, "SHORT", 0),          # incomplete pass
        ({"yards": 11}, "LONG", 11),
        ({"yards": -4}, "LOSS", -4),
        ({"yardage": "LOSS"}, "LOSS", None),
    ],
)
def test_resolve_over_rest(client, admin_headers, payload, bucket, yards):
    joey = register(client, "Joey")
    open_locked_play(client, admin_headers, [(joey, "PASS", "LEFT", "MEDIUM")])
    res = client.post("/api/admin/play/resolve", headers=admin_headers,
                      json={"play_type": "PASS", "direction": "LEFT", **payload})
    assert res.status_code == 200, res.text
    assert (res.json()["correct_yardage"], res.json()["yards_gained"]) == (bucket, yards)
    expected = 40 if bucket == "MEDIUM" else 20  # all three + bonus
    assert client.get("/api/me", headers=auth(joey)).json()["user"]["total_score"] == expected


@pytest.mark.parametrize(
    "payload, message",
    [
        ({}, "or enter the yards gained"),
        ({"yardage": "SHORT", "yards": 7}, "7 yards is MEDIUM, not SHORT"),
        ({"yardage": "LONG", "yards": -2}, "-2 yards is LOSS, not LONG"),
        ({"yardage": "LOSS", "yards": 0}, "0 yards is SHORT, not LOSS"),
        ({"yards": 100}, "less than or equal to 99"),
        ({"yards": -100}, "greater than or equal to -99"),
        ({"yards": 7.5}, "valid integer"),
        ({"yards": True}, "valid integer"),
        ({"yards": "7"}, "valid integer"),
        ({"yardage": "HUGE"}, "SHORT"),
    ],
)
def test_resolve_validation_over_rest(client, admin_headers, payload, message):
    open_locked_play(client, admin_headers)
    res = client.post("/api/admin/play/resolve", headers=admin_headers,
                      json={"play_type": "RUN", "direction": "LEFT", **payload})
    assert res.status_code == 422
    assert message in json.dumps(res.json())
    # Nothing was resolved: the play is still LOCKED and can be resolved properly.
    assert client.get("/api/admin/state", headers=admin_headers).json()["play"]["state"] == "LOCKED"


def test_store_resolve_validation(store):
    store.create_game(**GAME)
    store.open_next_play(1, "10", 15)
    store.lock_play()
    with pytest.raises(GameError) as exc:
        store.resolve_play("RUN", "LEFT")
    assert exc.value.status_code == 422
    with pytest.raises(GameError, match="12 yards is LONG, not MEDIUM"):
        store.resolve_play("RUN", "LEFT", "MEDIUM", 12)
    assert store.resolve_play("RUN", "LEFT", "LONG", 12)["correct_yardage"] == "LONG"


def test_resolve_mismatch_over_admin_socket(client):
    joey = register(client, "Joey")
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        recv_until(admin, lambda m: m["type"] == "admin_state")

        def act(action, **payload):
            admin.send_json({"action": action, "request_id": action, **payload})
            return recv_until(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == action)

        assert act("create_game", **GAME)["ok"]
        play = act("open_play", down=1, distance="10")["result"]
        client.post("/api/predictions", headers=auth(joey),
                    json={"play_id": play["id"], "play_type": "RUN", "direction": "LEFT", "yardage": "SHORT"})
        assert act("lock_play")["ok"]

        bad = act("resolve_play", play_type="RUN", direction="LEFT", yardage="SHORT", yards=7)
        assert bad == {**bad, "ok": False, "error": "7 yards is MEDIUM, not SHORT. Fix the distance or the yards gained."}
        missing = act("resolve_play", play_type="RUN", direction="LEFT")
        assert not missing["ok"] and "Choose the distance" in missing["error"]

        good = act("resolve_play", play_type="RUN", direction="LEFT", yards=7)
        assert good["ok"]
        assert (good["result"]["correct_yardage"], good["result"]["yards_gained"]) == ("MEDIUM", 7)
    # The owner's example: picked Run Left Short, it was Run Left Medium -> 20 points.
    assert client.get("/api/me", headers=auth(joey)).json()["user"]["total_score"] == 20


# --------------------------------------------------------------------------- #
# What players see
# --------------------------------------------------------------------------- #


def test_resolved_snapshot_marks_each_part(client, admin_headers):
    joey, sam, kim = register(client, "JoeyC"), register(client, "Sam"), register(client, "Kim")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    cm, sock, first = player_socket(client, joey["token"])
    assert first["scoring"] == {"type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40}
    play = client.post("/api/admin/play/open", json={}, headers=admin_headers).json()
    for user, pick in ((joey, ("PASS", "RIGHT", "SHORT")), (sam, ("PASS", "RIGHT", "LONG")), (kim, ("RUN", "LEFT", "LONG"))):
        client.post("/api/predictions", headers=auth(user),
                    json={"play_id": play["id"], "play_type": pick[0], "direction": pick[1], "yardage": pick[2]})
    client.post("/api/admin/play/lock", headers=admin_headers)
    locked = recv_until(sock, state_event("play_locked"))
    assert locked["my_prediction"]["yardage"] == "SHORT" and "yardage_correct" not in locked["my_prediction"]
    assert (locked["crowd"]["SHORT"], locked["crowd"]["MEDIUM"], locked["crowd"]["LONG"]) == (1, 0, 2)

    # A sack: Pass, Right, loss of 4. Type and direction still score; distance can't.
    client.post("/api/admin/play/resolve", headers=admin_headers,
                json={"play_type": "PASS", "direction": "RIGHT", "yards": -4})
    resolved = recv_until(sock, state_event("play_resolved"))
    outcome = {k: resolved["play"][k] for k in ("correct_play_type", "correct_direction", "correct_yardage", "yards_gained")}
    assert outcome == {"correct_play_type": "PASS", "correct_direction": "RIGHT", "correct_yardage": "LOSS",
                       "yards_gained": -4}
    mine = resolved["my_prediction"]
    assert (mine["points_earned"], mine["type_correct"], mine["direction_correct"], mine["yardage_correct"]) \
        == (20, True, True, False)
    assert resolved["crowd"]["exact"] == 0 and resolved["crowd"]["scored"] == 2
    assert [(r["username"], r["score"], r["exact_hits"]) for r in resolved["leaderboard"]] == [
        ("JoeyC", 20, 0), ("Sam", 20, 0), ("Kim", 0, 0),
    ]
    cm.__exit__(None, None, None)


def test_crowd_exact_counts_perfect_calls(store):
    game = store.create_game(**GAME)
    users = [store.create_user(n) for n in ("u1", "u2", "u3", "u4")]
    _, play = store.open_next_play(2, "4", 15)
    picks = [("RUN", "MIDDLE", "SHORT"), ("RUN", "MIDDLE", "SHORT"), ("RUN", "MIDDLE", "MEDIUM"), ("PASS", "LEFT", "LONG")]
    for user, pick in zip(users, picks):
        store.submit_prediction(user["id"], play["id"], *pick)
    store.lock_play()
    store.resolve_play("RUN", "MIDDLE", yards=3)
    stats = store.pick_stats(play["id"])
    assert (stats["exact"], stats["scored"], stats["total"]) == (2, 3, 4)
    board = store.game_leaderboard(game["id"])
    assert [(r["username"], r["score"], r["exact_hits"], r["rank"]) for r in board] == [
        ("u1", EXACT_POINTS, 1, 1), ("u2", EXACT_POINTS, 1, 1), ("u3", 20, 0, 3), ("u4", 0, 0, 4),
    ]


# --------------------------------------------------------------------------- #
# Legacy rows and old databases
# --------------------------------------------------------------------------- #

# The CREATE TABLE statements of the previous release (no distance columns).
OLD_SCHEMA = """
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT, home_name TEXT NOT NULL, home_primary TEXT NOT NULL,
    home_secondary TEXT NOT NULL, away_name TEXT NOT NULL, away_primary TEXT NOT NULL,
    away_secondary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED', 'LIVE', 'FINAL')),
    created_at REAL NOT NULL
);
CREATE TABLE plays (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id            INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    play_number        INTEGER NOT NULL,
    down               INTEGER CHECK (down BETWEEN 1 AND 4),
    distance           TEXT,
    state              TEXT NOT NULL CHECK (state IN ('OPEN', 'LOCKED', 'RESOLVED')),
    correct_play_type  TEXT CHECK (correct_play_type IN ('RUN', 'PASS')),
    correct_direction  TEXT CHECK (correct_direction IN ('LEFT', 'CENTER', 'RIGHT')),
    voided             INTEGER NOT NULL DEFAULT 0,
    opened_at          REAL NOT NULL,
    locks_at           REAL NOT NULL,
    locked_at          REAL,
    resolved_at        REAL,
    UNIQUE (game_id, play_number)
);
CREATE UNIQUE INDEX ux_plays_one_active ON plays(game_id) WHERE state != 'RESOLVED';
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    token TEXT NOT NULL UNIQUE, total_score INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    play_id        INTEGER NOT NULL REFERENCES plays(id) ON DELETE CASCADE,
    play_type      TEXT NOT NULL CHECK (play_type IN ('RUN', 'PASS')),
    direction      TEXT NOT NULL CHECK (direction IN ('LEFT', 'CENTER', 'RIGHT')),
    points_earned  INTEGER,
    submitted_at   REAL NOT NULL,
    UNIQUE (user_id, play_id)
);
CREATE INDEX ix_predictions_play ON predictions(play_id);
CREATE TABLE lounges (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    host_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at REAL NOT NULL
);
CREATE TABLE lounge_members (
    lounge_id TEXT NOT NULL REFERENCES lounges(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at REAL NOT NULL, PRIMARY KEY (lounge_id, user_id)
);
CREATE INDEX ix_lounge_members_user ON lounge_members(user_id);
"""


@pytest.fixture
def old_db(tmp_path):
    """A game.db written by the previous release: one scored play, one LOCKED play with picks."""
    path = str(tmp_path / "old.db")
    now = time.time()
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("""INSERT INTO games VALUES (1, 'Detroit', '#0076B6', '#B0B7BC', 'Chicago', '#0B162A', '#C83803',
                                            'LIVE', ?)""", (now,))
    con.executemany("INSERT INTO users (id, username, token, total_score, created_at) VALUES (?, ?, ?, ?, ?)",
                    [(1, "alice", "tok-a", 30, now), (2, "bob", "tok-b", 10, now)])
    con.execute("""INSERT INTO plays (id, game_id, play_number, down, distance, state, correct_play_type,
                                      correct_direction, opened_at, locks_at, locked_at, resolved_at)
                   VALUES (1, 1, 1, 1, '10', 'RESOLVED', 'PASS', 'LEFT', ?, ?, ?, ?)""", (now, now, now, now))
    con.execute("""INSERT INTO plays (id, game_id, play_number, down, distance, state, opened_at, locks_at, locked_at)
                   VALUES (2, 1, 2, 3, '7', 'LOCKED', ?, ?, ?)""", (now, now, now))
    con.executemany("""INSERT INTO predictions (user_id, play_id, play_type, direction, points_earned, submitted_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [(1, 1, "PASS", "LEFT", 30, now), (2, 1, "RUN", "LEFT", 10, now),
                     (1, 2, "RUN", "RIGHT", None, now), (2, 2, "PASS", "LEFT", None, now)])
    con.commit()
    con.close()
    return path


def columns(store, table):
    return {r["name"]: r for r in store._all(f"PRAGMA table_info({table})")}


def test_old_database_is_migrated_and_keeps_working(old_db):
    store = Store(old_db)
    assert {"correct_yardage", "yards_gained"} <= columns(store, "plays").keys()
    assert "yardage" in columns(store, "predictions")

    # Existing data reads back with the new fields empty.
    assert store.get_user(1)["total_score"] == 30
    old_play = store.get_play(1)
    assert (old_play["correct_play_type"], old_play["correct_yardage"], old_play["yards_gained"]) == ("PASS", None, None)
    assert store.predictions_for_play(1)[1]["yardage"] is None
    # Old perfect calls (type + direction) no longer count as all three right.
    board = store.game_leaderboard(1)
    assert [(r["username"], r["score"], r["exact_hits"]) for r in board] == [("alice", 30, 0), ("bob", 10, 0)]

    # The play that was LOCKED during the upgrade resolves; its picks have no distance (scored as wrong).
    resolved = store.resolve_play("PASS", "LEFT", yards=8)
    assert (resolved["correct_yardage"], resolved["yards_gained"]) == ("MEDIUM", 8)
    preds = store.predictions_for_play(2)
    assert (preds[1]["points_earned"], preds[2]["points_earned"]) == (0, 20)
    assert store.get_user(2)["total_score"] == 30

    # New plays take distance picks, and the new CHECK constraints hold.
    _, play = store.open_next_play(1, "10", 15)
    store.submit_prediction(1, play["id"], "RUN", "MIDDLE", "SHORT")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE predictions SET yardage = 'LOSS' WHERE play_id = ?", (play["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE plays SET correct_yardage = 'HUGE' WHERE id = 1")
    store.lock_play()
    store.resolve_play("RUN", "MIDDLE", "SHORT", 2)
    assert store.game_leaderboard(1)[0] == {**store.game_leaderboard(1)[0], "username": "alice", "exact_hits": 1}
    store.close()

    # Opening the migrated database again changes nothing (the migration is idempotent).
    again, fresh = Store(old_db), Store(":memory:")
    for table in ("plays", "predictions"):
        assert columns(again, table).keys() == columns(fresh, table).keys()
    assert again.get_play(2)["correct_yardage"] == "MEDIUM"
    again.close()
    fresh.close()


def test_migrated_database_serves_the_app(old_db, tmp_path):
    from fastapi.testclient import TestClient

    from app import Settings, create_app

    settings = Settings(db_path=old_db, admin_key=ADMIN_KEY)
    with TestClient(create_app(settings)) as client:
        state = client.get("/api/state").json()
        assert state["play"]["state"] == "LOCKED" and state["play"]["correct_yardage"] is None
        assert [r["username"] for r in state["leaderboard"]] == ["alice", "bob"]
        res = client.post("/api/admin/play/resolve", headers={"X-Admin-Key": ADMIN_KEY},
                          json={"play_type": "RUN", "direction": "RIGHT", "yards": 15})
        assert res.status_code == 200 and res.json()["correct_yardage"] == "LONG"
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "token": "tok-a"})
            snap = recv_until(ws, state_event("sync"))
            # alice's legacy pick (RUN RIGHT, no distance): 20 points, distance marked wrong.
            mine = snap["my_prediction"]
            assert (mine["yardage"], mine["points_earned"], mine["yardage_correct"]) == (None, 20, False)
            assert mine["type_correct"] and mine["direction_correct"]


def test_legacy_null_yardage_pick_in_a_new_database(store):
    """A pick row with NULL yardage (e.g. saved by an old client mid-upgrade) scores distance as wrong."""
    store.create_game(**GAME)
    user = store.create_user("legacy")
    _, play = store.open_next_play(1, "10", 15)
    store._conn.execute(
        "INSERT INTO predictions (user_id, play_id, play_type, direction, submitted_at) VALUES (?, ?, 'RUN', 'LEFT', ?)",
        (user["id"], play["id"], time.time()),
    )
    store.lock_play()
    store.resolve_play("RUN", "LEFT", "SHORT")
    assert store.predictions_for_play(play["id"])[user["id"]]["points_earned"] == 20
    assert store.game_leaderboard(store.current_game()["id"])[0]["exact_hits"] == 0


# --------------------------------------------------------------------------- #
# Team presets: city names with real colors
# --------------------------------------------------------------------------- #


def test_team_presets_are_sane():
    assert len(TEAM_PRESETS) == 32
    labels = [t["label"] for t in TEAM_PRESETS]
    assert len(set(labels)) == 32
    for team in TEAM_PRESETS:
        assert set(team) == {"name", "label", "primary", "secondary", "feed_abbr"}
        assert re.fullmatch(r"[A-Z]{2,3}", team["feed_abbr"])
        assert validate_team_name(team["name"]) == team["name"]
        for color in (team["primary"], team["secondary"]):
            assert re.fullmatch(r"#[0-9A-F]{6}", color) and validate_color(color) == color
        assert team["label"].startswith(team["name"])
    names = [t["name"] for t in TEAM_PRESETS]
    assert names.count("New York") == 2 and names.count("Los Angeles") == 2
    assert len(set(names)) == 30
    assert len({t["feed_abbr"] for t in TEAM_PRESETS}) == 32
    assert preset("Chicago") == {"name": "Chicago", "label": "Chicago", "primary": "#0B162A", "secondary": "#C83803",
                                 "feed_abbr": "CHI"}
    assert preset("Detroit")["primary"] == "#0076B6"
    assert (DEFAULT_AWAY, DEFAULT_HOME) == ("Chicago", "Detroit")


def test_every_preset_creates_a_game(store):
    for i, team in enumerate(TEAM_PRESETS):
        other = TEAM_PRESETS[(i + 1) % len(TEAM_PRESETS)]
        if other["name"] == team["name"]:
            other = TEAM_PRESETS[(i + 2) % len(TEAM_PRESETS)]
        game = store.create_game(team["name"], team["primary"], team["secondary"],
                                 other["name"], other["primary"], other["secondary"])
        assert (game["home_name"], game["home_primary"]) == (team["name"], team["primary"])


def test_admin_page_offers_presets_and_real_default_matchup(client):
    html = client.get("/admin").text
    data = re.search(r'<script type="application/json" id="team-presets">(.*?)</script>', html, re.S)
    assert json.loads(data.group(1)) == TEAM_PRESETS
    assert html.count('data-preset="') == 2
    assert 'name="away_name" value="Chicago"' in html and 'name="home_name" value="Detroit"' in html
    assert 'name="away_primary" value="#0B162A"' in html and 'name="home_secondary" value="#B0B7BC"' in html
    assert ">Chicago</option>" in html and ">New York (green &amp; white)</option>" in html
    assert re.search(r'<option value="\d+" selected>Chicago</option>', html)
    assert re.search(r'<option value="\d+" selected>Detroit</option>', html)
    assert "As the QB looks downfield" in html and 'data-ryard="LOSS"' in html and 'id="yards"' in html
    assert "City or region names with team colors only" in html


def test_player_page_has_three_pick_groups(client):
    html = client.get("/").text
    for marker in ('data-type="RUN"', 'data-dir="LEFT"', 'data-yard="SHORT"', 'data-yard="MEDIUM"',
                   'data-yard="LONG"', "How far?", "As the QB looks downfield", "0-5 yds", "6-10 yds",
                   "11+ yds", "Distance +10", "Bonus +10", "A loss of yards scores no distance points",
                   'id="reveal-yard"'):
        assert marker in html, marker


def test_web_app_manifest_describes_all_three_picks(client):
    # Shown when the web app is installed to a home screen.
    manifest = client.get("/static/manifest.webmanifest").json()
    assert manifest["description"] == "Call every play live: Run or Pass, Left, Middle or Right, and how far."
