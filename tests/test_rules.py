"""The all-three bonus, CENTER -> MIDDLE (input alias, database migration) and the Rules of the Game page."""

import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import SCORING, Settings, create_app
from models import EXACT_POINTS, Store
from tests.conftest import ADMIN_KEY, GAME
from tests.test_app import player_socket, recv_until, register, state_event
from tests.test_yardage import OLD_SCHEMA, auth

# --------------------------------------------------------------------------- #
# Scoring in the snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_scoring_has_the_bonus(client):
    expected = {"type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40}
    assert SCORING == expected
    assert client.get("/api/state").json()["scoring"] == expected


def test_perfect_call_is_40_and_counts_as_exact(client, admin_headers):
    joey, sam, kim = register(client, "JoeyC"), register(client, "Sam"), register(client, "Kim")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    cm, sock, _ = player_socket(client, joey["token"])
    play = client.post("/api/admin/play/open", json={"down": 3, "distance": "7"}, headers=admin_headers).json()
    for user, pick in ((joey, ("RUN", "MIDDLE", "MEDIUM")), (sam, ("RUN", "MIDDLE", "SHORT")),
                       (kim, ("PASS", "MIDDLE", "MEDIUM"))):
        res = client.post("/api/predictions", headers=auth(user),
                          json={"play_id": play["id"], "play_type": pick[0], "direction": pick[1], "yardage": pick[2]})
        assert res.status_code == 200, res.text
    client.post("/api/admin/play/lock", headers=admin_headers)
    client.post("/api/admin/play/resolve", headers=admin_headers,
                json={"play_type": "RUN", "direction": "MIDDLE", "yards": 8})
    resolved = recv_until(sock, state_event("play_resolved"))
    mine = resolved["my_prediction"]
    assert (mine["points_earned"], mine["type_correct"], mine["direction_correct"], mine["yardage_correct"]) \
        == (40, True, True, True)
    assert resolved["crowd"]["exact"] == 1 and resolved["crowd"]["scored"] == 3
    assert [(r["username"], r["score"], r["exact_hits"]) for r in resolved["leaderboard"]] == [
        ("JoeyC", 40, 1), ("Kim", 20, 0), ("Sam", 20, 0),
    ]
    assert resolved["me"]["total_score"] == 40 and resolved["me"]["exact_hits"] == 1
    cm.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# CENTER is accepted as an alias and always comes back as MIDDLE
# --------------------------------------------------------------------------- #


def test_center_alias_over_rest(client, admin_headers):
    joey = register(client, "Joey")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play = client.post("/api/admin/play/open", json={}, headers=admin_headers).json()
    body = {"play_id": play["id"], "play_type": "RUN", "direction": "CENTER", "yardage": "SHORT"}
    res = client.post("/api/predictions", headers=auth(joey), json=body)
    assert res.status_code == 200 and res.json()["direction"] == "MIDDLE"
    store = client.app.state.ctrl.store
    assert store.predictions_for_play(play["id"])[joey["id"]]["direction"] == "MIDDLE"
    # Anything else is still rejected, and the error names the new value.
    bad = client.post("/api/predictions", headers=auth(joey), json={**body, "direction": "center"})
    assert bad.status_code == 422 and "'LEFT', 'MIDDLE' or 'RIGHT'" in bad.text

    client.post("/api/admin/play/lock", headers=admin_headers)
    stats = client.get("/api/admin/state", headers=admin_headers).json()["pick_stats"]
    assert stats["MIDDLE"] == 1 and "CENTER" not in stats
    res = client.post("/api/admin/play/resolve", headers=admin_headers,
                      json={"play_type": "RUN", "direction": "CENTER", "yards": 2})
    assert res.status_code == 200 and res.json()["correct_direction"] == "MIDDLE"
    assert store.get_play(play["id"])["correct_direction"] == "MIDDLE"
    assert client.get("/api/me", headers=auth(joey)).json()["user"]["total_score"] == EXACT_POINTS
    history = client.get("/api/admin/state", headers=admin_headers).json()["history"]
    assert history[0]["correct_direction"] == "MIDDLE" and history[0]["exact_hits"] == 1


def test_center_alias_over_websockets(client):
    joey = register(client, "Joey")
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        recv_until(admin, lambda m: m["type"] == "admin_state")

        def act(action, **payload):
            admin.send_json({"action": action, "request_id": action, **payload})
            return recv_until(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == action)

        assert act("create_game", **GAME)["ok"]
        cm, sock, _ = player_socket(client, joey["token"])
        play = act("open_play", down=1, distance="10")["result"]
        sock.send_json({"type": "predict", "play_id": play["id"], "play_type": "PASS", "direction": "CENTER",
                        "yardage": "LONG"})
        saved = recv_until(sock, lambda m: m["type"] in ("prediction_saved", "error"))
        assert saved["type"] == "prediction_saved" and saved["prediction"]["direction"] == "MIDDLE"
        assert act("lock_play")["ok"]
        locked = recv_until(sock, state_event("play_locked"))
        assert locked["my_prediction"]["direction"] == "MIDDLE"
        assert locked["crowd"]["MIDDLE"] == 1 and "CENTER" not in locked["crowd"]

        bad = act("resolve_play", play_type="PASS", direction="UP", yards=20)
        assert not bad["ok"] and "'LEFT', 'MIDDLE' or 'RIGHT'" in bad["error"]
        good = act("resolve_play", play_type="PASS", direction="CENTER", yards=20)
        assert good["ok"] and good["result"]["correct_direction"] == "MIDDLE"
        resolved = recv_until(sock, state_event("play_resolved"))
        assert resolved["play"]["correct_direction"] == "MIDDLE"
        assert resolved["my_prediction"]["points_earned"] == 40
        cm.__exit__(None, None, None)


def test_store_converts_center_on_the_way_in(store):
    store.create_game(**GAME)
    user = store.create_user("joey")
    _, play = store.open_next_play(1, "10", 15)
    assert store.submit_prediction(user["id"], play["id"], "RUN", "CENTER", "SHORT")["direction"] == "MIDDLE"
    store.lock_play()
    assert store.resolve_play("RUN", "CENTER", "SHORT")["correct_direction"] == "MIDDLE"
    assert store.predictions_for_play(play["id"])[user["id"]]["points_earned"] == 40
    # A new database never allows CENTER.
    for sql in ("UPDATE predictions SET direction = 'CENTER'", "UPDATE plays SET correct_direction = 'CENTER'"):
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(sql)


# --------------------------------------------------------------------------- #
# Migrating databases written by older versions
# --------------------------------------------------------------------------- #

# The CREATE statements of the distance-pick release (commit 9539bd9), verbatim.
YARDAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    home_name       TEXT NOT NULL,
    home_primary    TEXT NOT NULL,
    home_secondary  TEXT NOT NULL,
    away_name       TEXT NOT NULL,
    away_primary    TEXT NOT NULL,
    away_secondary  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'SCHEDULED'
                    CHECK (status IN ('SCHEDULED', 'LIVE', 'FINAL')),
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS plays (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id            INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    play_number        INTEGER NOT NULL,
    down               INTEGER CHECK (down BETWEEN 1 AND 4),
    distance           TEXT,
    state              TEXT NOT NULL CHECK (state IN ('OPEN', 'LOCKED', 'RESOLVED')),
    correct_play_type  TEXT CHECK (correct_play_type IN ('RUN', 'PASS')),
    correct_direction  TEXT CHECK (correct_direction IN ('LEFT', 'CENTER', 'RIGHT')),
    correct_yardage    TEXT CHECK (correct_yardage IN ('SHORT', 'MEDIUM', 'LONG', 'LOSS')),
    yards_gained       INTEGER,  -- optional; when given, correct_yardage is derived from it
    voided             INTEGER NOT NULL DEFAULT 0,
    opened_at          REAL NOT NULL,
    locks_at           REAL NOT NULL,
    locked_at          REAL,
    resolved_at        REAL,
    UNIQUE (game_id, play_number)
);
-- At most one OPEN/LOCKED play per game.
CREATE UNIQUE INDEX IF NOT EXISTS ux_plays_one_active
    ON plays(game_id) WHERE state != 'RESOLVED';

CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    token        TEXT NOT NULL UNIQUE,
    total_score  INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    play_id        INTEGER NOT NULL REFERENCES plays(id) ON DELETE CASCADE,
    play_type      TEXT NOT NULL CHECK (play_type IN ('RUN', 'PASS')),
    direction      TEXT NOT NULL CHECK (direction IN ('LEFT', 'CENTER', 'RIGHT')),
    yardage        TEXT CHECK (yardage IN ('SHORT', 'MEDIUM', 'LONG')),  -- NULL: picked before distance picks
    points_earned  INTEGER,  -- NULL until the play is resolved
    submitted_at   REAL NOT NULL,
    UNIQUE (user_id, play_id)
);
CREATE INDEX IF NOT EXISTS ix_predictions_play ON predictions(play_id);

CREATE TABLE IF NOT EXISTS lounges (
    id            TEXT PRIMARY KEY,  -- the 4-digit join code
    name          TEXT NOT NULL,
    host_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lounge_members (
    lounge_id  TEXT NOT NULL REFERENCES lounges(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at  REAL NOT NULL,
    PRIMARY KEY (lounge_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_lounge_members_user ON lounge_members(user_id);
"""

# What the distance-pick release ran on an original database (its MIGRATIONS): the same columns,
# appended at the end of the tables instead of in the middle.
ADD_YARDAGE_COLUMNS = """
ALTER TABLE plays ADD COLUMN correct_yardage TEXT CHECK (correct_yardage IN ('SHORT', 'MEDIUM', 'LONG', 'LOSS'));
ALTER TABLE plays ADD COLUMN yards_gained INTEGER;
ALTER TABLE predictions ADD COLUMN yardage TEXT CHECK (yardage IN ('SHORT', 'MEDIUM', 'LONG'));
"""

SCHEMAS = {
    "original": OLD_SCHEMA,                                  # commit 8a4cdb9
    "yardage": YARDAGE_SCHEMA,                               # commit 9539bd9, new database
    "original-then-yardage": OLD_SCHEMA + ADD_YARDAGE_COLUMNS,  # commit 9539bd9, upgraded database
}


def build_old_db(path, schema):
    """An older game.db: a finished game, a live one with a scored play and a LOCKED play mid-upgrade.

    Uses CENTER on both sides, and deletes the newest play and pick so the AUTOINCREMENT counters are
    ahead of the highest ids. Returns the rows as stored, by table.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(schema)
    has_yardage = "yardage" in {r["name"] for r in con.execute("PRAGMA table_info(predictions)")}
    now = time.time()
    con.executemany("INSERT INTO games VALUES (?, 'Detroit', '#0076B6', '#B0B7BC', 'Chicago', '#0B162A', '#C83803', ?, ?)",
                    [(1, "FINAL", now - 900), (2, "LIVE", now)])
    con.executemany("INSERT INTO users (id, username, token, total_score, created_at) VALUES (?, ?, ?, ?, ?)",
                    [(1, "alice", "tok-a", 30 if has_yardage else 20, now), (2, "bob", "tok-b", 10, now),
                     (3, "carol", "tok-c", 0, now)])
    plays = [  # id, game, number, state, type, direction, yardage, yards
        (1, 1, 1, "RESOLVED", "PASS", "CENTER", "MEDIUM", 7),
        (2, 2, 1, "RESOLVED", "RUN", "LEFT", "SHORT", 3),
        (3, 2, 2, "LOCKED", None, None, None, None),
        (4, 2, 3, "RESOLVED", "RUN", "RIGHT", "LONG", 12),  # deleted below
    ]
    for pid, game, number, state, ptype, pdir, pyard, yards in plays:
        cols = {"id": pid, "game_id": game, "play_number": number, "down": 1, "distance": "10", "state": state,
                "correct_play_type": ptype, "correct_direction": pdir, "opened_at": now, "locks_at": now,
                "locked_at": now, "resolved_at": now if state == "RESOLVED" else None}
        if has_yardage:
            cols |= {"correct_yardage": pyard, "yards_gained": yards}
        con.execute(f"INSERT INTO plays ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    preds = [  # id, user, play, type, direction, yardage, points (scored under that release's rules)
        (1, 1, 1, "PASS", "CENTER", "MEDIUM", 30 if has_yardage else 20),
        (2, 2, 1, "RUN", "CENTER", "SHORT", 10),
        (3, 1, 3, "RUN", "CENTER", "SHORT", None),
        (4, 2, 3, "PASS", "LEFT", "LONG", None),
        (5, 3, 3, "RUN", "RIGHT", "MEDIUM", None),
        (6, 3, 4, "RUN", "RIGHT", "LONG", 30),  # deleted with its play
    ]
    for pid, user, play, ptype, pdir, pyard, pts in preds:
        cols = {"id": pid, "user_id": user, "play_id": play, "play_type": ptype, "direction": pdir,
                "points_earned": pts, "submitted_at": now}
        if has_yardage:
            cols["yardage"] = pyard
        con.execute(f"INSERT INTO predictions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    tuple(cols.values()))
    con.execute("DELETE FROM plays WHERE id = 4")  # cascades to prediction 6
    con.execute("INSERT INTO lounges VALUES ('1234', 'Sunday Crew', 1, ?)", (now,))
    con.executemany("INSERT INTO lounge_members VALUES ('1234', ?, ?)", [(1, now), (2, now)])
    con.commit()
    rows = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in ("games", "plays", "users", "predictions", "lounges", "lounge_members")}
    con.close()
    return rows


def schema_objects(store):
    """Every table and index with its columns, comparable between two databases."""
    objects = {}
    for r in store._all("SELECT type, name, tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_sequence'"):
        info = "table_info" if r["type"] == "table" else "index_info"
        cols = [(c["name"], c.get("type"), c.get("notnull"), c.get("dflt_value"), c.get("pk"))
                for c in store._all(f"PRAGMA {info}('{r['name']}')")]
        objects[(r["type"], r["name"], r["tbl_name"])] = cols
    return objects


def middle(rows, column):
    return [{**r, column: "MIDDLE" if r[column] == "CENTER" else r[column]} for r in rows]


@pytest.mark.parametrize("schema", SCHEMAS)
def test_old_database_is_migrated_to_middle(tmp_path, schema):
    path = str(tmp_path / "old.db")
    before = build_old_db(path, SCHEMAS[schema])
    store = Store(path)
    fresh = Store(":memory:")

    # Exactly the schema of a new database: same columns, CHECKs without CENTER, every index.
    assert schema_objects(store) == schema_objects(fresh)
    for table in ("plays", "predictions"):
        sql = store._one("SELECT sql FROM sqlite_master WHERE name = ?", (table,))["sql"]
        assert "CENTER" not in sql and "'LEFT', 'MIDDLE', 'RIGHT'" in sql
    indexes = {r["name"]: r["sql"] for r in store._all("SELECT name, sql FROM sqlite_master WHERE type = 'index'")}
    assert "WHERE state != 'RESOLVED'" in indexes["ux_plays_one_active"]
    assert "ix_predictions_play" in indexes and "ix_lounge_members_user" in indexes
    assert store._all("PRAGMA foreign_key_check") == []
    assert store._one("PRAGMA foreign_keys")["foreign_keys"] == 1
    assert not store._all("SELECT name FROM sqlite_master WHERE name LIKE '%__new%'")

    # Every row, id and value kept; CENTER became MIDDLE; old points are not recalculated.
    def now(table):
        return store._all(f"SELECT * FROM {table} ORDER BY id" if table != "lounge_members"
                          else "SELECT * FROM lounge_members ORDER BY rowid")
    added = {"plays": {"correct_yardage": None, "yards_gained": None, "resolved_by": None, "feed_text": None},
             "predictions": {"yardage": None}}
    live_data = {"feed_game_id": None, "feed_auto_score": 1, "feed_auto_open": 0, "feed_paused": 0,
                 "feed_cursor": 0, "feed_requests": 0, "feed_cap_extra": 0}  # what the live-data release added
    assert now("games") == [{**r, **live_data} for r in before["games"]]
    for table in ("users", "lounges", "lounge_members"):
        assert now(table) == before[table], table
    expected_plays = middle([{**added["plays"], **r} for r in before["plays"]], "correct_direction")
    expected_preds = middle([{**added["predictions"], **r} for r in before["predictions"]], "direction")
    assert now("plays") == expected_plays
    assert now("predictions") == expected_preds
    assert [r["id"] for r in now("plays")] == [1, 2, 3] and [r["id"] for r in now("predictions")] == [1, 2, 3, 4, 5]
    assert store.get_user(1)["total_score"] == before["users"][0]["total_score"]  # a 30 stays 30
    assert store.predictions_for_play(1)[1]["points_earned"] == before["predictions"][0]["points_earned"]

    # The new constraints hold: no CENTER, still one active play per game, foreign keys cascade.
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE predictions SET direction = 'CENTER' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE plays SET correct_direction = 'CENTER' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("INSERT INTO plays (game_id, play_number, state, opened_at, locks_at) "
                            "VALUES (2, 9, 'OPEN', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("INSERT INTO predictions (user_id, play_id, play_type, direction, submitted_at) "
                            "VALUES (1, 99, 'RUN', 'LEFT', 0)")

    # A full round on the upgraded database. The LOCKED play resolves (with the old word, even).
    resolved = store.resolve_play("RUN", "CENTER", yards=2)
    assert (resolved["id"], resolved["correct_direction"], resolved["correct_yardage"]) == (3, "MIDDLE", "SHORT")
    preds = store.predictions_for_play(3)
    if schema == "original":  # picks saved before distance picks existed: distance wrong, no bonus
        assert (preds[1]["points_earned"], preds[2]["points_earned"], preds[3]["points_earned"]) == (20, 0, 10)
    else:
        assert (preds[1]["points_earned"], preds[2]["points_earned"], preds[3]["points_earned"]) == (40, 0, 10)
    _, play = store.open_next_play(1, "10", 15)
    assert play["id"] == 5  # AUTOINCREMENT kept: the deleted play 4's id is not reused
    pick = store.submit_prediction(2, play["id"], "PASS", "MIDDLE", "LONG")
    assert pick["direction"] == "MIDDLE"
    assert store._one("SELECT MAX(id) AS id FROM predictions")["id"] == 7  # 6 was deleted before the upgrade
    store.lock_play()
    store.resolve_play("PASS", "MIDDLE", yards=25)
    assert store.predictions_for_play(play["id"])[2]["points_earned"] == 40
    board = {r["username"]: (r["score"], r["exact_hits"]) for r in store.game_leaderboard(2)}
    assert board["bob"] == (40, 1)
    assert board["alice"] == ((20, 0) if schema == "original" else (40, 1))
    # Old all-three-right picks still count as perfect (exact) at their old points.
    first_game = {r["username"]: r for r in store.game_leaderboard(1)}
    if schema == "original":
        assert (first_game["alice"]["score"], first_game["alice"]["exact_hits"]) == (20, 0)
    else:
        assert (first_game["alice"]["score"], first_game["alice"]["exact_hits"]) == (30, 1)
        assert store.pick_stats(1)["exact"] == 1
    assert store.pick_stats(1)["MIDDLE"] == 2 and "CENTER" not in store.pick_stats(1)

    # Deleting an account still cascades through the rebuilt tables.
    store.delete_user(3)
    assert 3 not in store.predictions_for_play(3)
    after_first_open = store._all("SELECT name, sql FROM sqlite_master ORDER BY name")
    store.close()

    # Opening it again changes nothing (runs once).
    again = Store(path)
    assert again._all("SELECT name, sql FROM sqlite_master ORDER BY name") == after_first_open
    assert again.get_play(3)["correct_direction"] == "MIDDLE"
    again.close()
    fresh.close()


def test_failed_migration_rolls_back(tmp_path):
    """A broken reference stops the rebuild before anything is changed (and the server won't start on it)."""
    path = str(tmp_path / "broken.db")
    build_old_db(path, YARDAGE_SCHEMA)
    con = sqlite3.connect(path)  # foreign keys off: write a pick for a play that doesn't exist
    con.execute("INSERT INTO predictions (user_id, play_id, play_type, direction, submitted_at) "
                "VALUES (1, 42, 'RUN', 'CENTER', 0)")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="broken references"):
        Store(path)
    con = sqlite3.connect(path)
    assert "CENTER" in con.execute("SELECT sql FROM sqlite_master WHERE name = 'predictions'").fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM predictions WHERE direction = 'CENTER'").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%__new%'").fetchone()[0] == 0
    con.close()


CRASH_MID_REBUILD = """
import os, sys
sys.path.insert(0, sys.argv[2])
import models
rebuild = models.Store._rebuild_table
def rebuild_then_die(c, table, definition, column):
    rebuild(c, table, definition, column)
    if table == sys.argv[3]:
        os._exit(3)  # killed mid-transaction: no ROLLBACK, no COMMIT
models.Store._rebuild_table = staticmethod(rebuild_then_die)
models.Store(sys.argv[1])
"""


@pytest.mark.parametrize("killed_after", ["plays", "predictions"])
@pytest.mark.parametrize("schema", ["original", "yardage"])
def test_server_killed_mid_migration_leaves_the_old_database(tmp_path, schema, killed_after):
    """A hard kill between the table rebuilds never leaves a half-migrated database; the next start finishes."""
    path = str(tmp_path / "old.db")
    before = build_old_db(path, SCHEMAS[schema])
    repo = str(Path(__file__).resolve().parents[1])
    child = subprocess.run([sys.executable, "-c", CRASH_MID_REBUILD, path, repo, killed_after])
    assert child.returncode == 3

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    for table in ("plays", "predictions"):  # both CHECKs still say CENTER: neither rebuild was kept
        assert "CENTER" in con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%__new%'").fetchone()[0] == 0
    for table, rows in before.items():
        stored = [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        # An original database keeps the distance columns the first (committed) step added.
        assert [{k: r[k] for k in rows[0]} for r in stored] == rows
    con.close()

    store = Store(path)  # the next start migrates as if nothing happened
    assert store._one("SELECT COUNT(*) AS n FROM predictions WHERE direction = 'CENTER'")["n"] == 0
    assert [r["direction"] for r in store._all("SELECT direction FROM predictions ORDER BY id")] \
        == [r["direction"] for r in middle(before["predictions"], "direction")]
    assert store._all("PRAGMA foreign_key_check") == []
    store.close()


def test_migrated_database_serves_middle(tmp_path):
    path = str(tmp_path / "old.db")
    build_old_db(path, YARDAGE_SCHEMA)
    with TestClient(create_app(Settings(db_path=path, admin_key=ADMIN_KEY))) as client:
        state = client.get("/api/state").json()
        assert state["play"]["id"] == 3 and state["crowd"]["MIDDLE"] == 1 and "CENTER" not in state["crowd"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "token": "tok-a"})
            snap = recv_until(ws, state_event("sync"))
            assert snap["my_prediction"]["direction"] == "MIDDLE"


# --------------------------------------------------------------------------- #
# Rules of the Game
# --------------------------------------------------------------------------- #


def page_text(html):
    """Visible words of a page: block tags become spaces, inline tags vanish, whitespace collapsed."""
    html = re.sub(r"</?(?:t[hdr]|li|p|h\d|div|ul|ol|section|figure|figcaption|title|desc|text)\b[^>]*>", " ", html)
    return " ".join(re.sub(r"<[^>]+>", "", html).split())


RULES_SENTENCES = (
    "Rules of the Game",
    "Pick the Play follows a live pro football game, one play at a time.",
    "Before each snap the host opens the play. You have a short window (usually 15 seconds; the countdown is on "
    "screen) to make three calls: Run or Pass, Left, Middle or Right, and Short, Medium or Long.",
    "You can change your pick until it locks at the snap or when the countdown ends.",
    "After the play, the official result is read from the live play-by-play data and scored automatically. The host checks it and can correct it, and points land within seconds.",
    "Only scrimmage plays are called. Kickoffs, punts, field goals, extra points and kneel-downs are skipped. "
    "A play wiped out by a penalty, a sack or a quarterback scramble is no play: it is voided and nobody scores.",
    "Play for the live leaderboard, or head-to-head with friends in a private lounge.",
    "Call Points Play type right (Run / Pass) +10 Direction right (Left / Middle / Right) +10 "
    "Distance right (Short / Medium / Long) +10 Bonus: all three right +10 Perfect call 40",
    "Example: you call Run · Left · Short and the play is Run · Left · Medium: 20 points. "
    "Call Run · Left · Medium: 40.",
    "Distance is the total yards gained on the play: Short 0-5 yards, Medium 6-10, Long 11 or more. An incomplete "
    "pass or no gain is 0 yards, so it counts as Short. A loss of yards scores no distance points, and so no bonus.",
    "Left, Middle and Right",
    "Direction is always from the offense's point of view: as the quarterback looks downfield. It doesn't change "
    "with the TV camera angle.",
    "Running plays are called by where the run is directed (the official run location and run gap):",
    "Middle: up the middle. Any run between the left guard and the right guard, including straight through the "
    "A-gaps on either side of the center.",
    "Left: any run directed outside the center to the left: at the left guard (inside run), left tackle "
    "(off-tackle) or left end (outside sweep or bounce toward the left sideline).",
    "Right: the same to the right: right guard, right tackle or right end.",
    "Passing plays are called by where the ball is thrown as it crosses the line of scrimmage, using the hash marks:",
    "Left: thrown outside the left hash mark, out to the left sideline.",
    "Middle: thrown between the hash marks.",
    "Right: thrown outside the right hash mark, out to the right sideline.",
    "A sack or a quarterback scramble is no play and scores no points for anyone: there was no throw to call. "
    "When no direction is charted, the host makes the call.",
    "All official play calls are derived from the official game statistics: play type, run location, pass "
    "location and yards gained.",
    "All final calls are at the host's discretion.",
    "Pick the Play is an independent fan game. It is not affiliated with, endorsed by or sponsored by the NFL or "
    "any club.",
)


def test_rules_page(client):
    res = client.get("/rules")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    text = page_text(res.text)
    for sentence in RULES_SENTENCES:
        assert sentence in text, sentence
    assert "Center" not in text and "CENTER" not in text  # the position is "the center"; the call is Middle
    # The bonus math and both diagrams, labelled for screen readers, left to right as the QB sees it.
    assert 'class="bonus-math" role="img"' in res.text and "equals 40" in res.text
    # (the theme toggle's sun and moon are decorative: aria-hidden)
    assert len(re.findall(r"<svg(?![^>]*aria-hidden)", res.text)) == 2 and res.text.count('role="img" aria-labelledby=') == 2
    runs = res.text[res.text.index('id="runs-title"'):res.text.index('id="passes-title"')]
    assert [m for m in re.findall(r'class="player-label"[^>]*>(\w+)<', runs)] == ["LE", "LT", "LG", "C", "RG", "RT", "RE"]
    for svg in (runs, res.text[res.text.index('id="passes-title"'):]):
        labels = re.findall(r'class="zone-label lbl-\w+" x="(\d+)"[^>]*>(\w+)<', svg)
        assert [name for _, name in sorted(labels, key=lambda l: int(l[0]))] == ["LEFT", "MIDDLE", "RIGHT"]
    assert 'href="/support"' in res.text and 'href="/privacy"' in res.text and "data-back" in res.text


def test_rules_is_linked_everywhere(client, admin_headers):
    joey = register(client, "Joey")
    lounge = client.post("/api/lounges", json={"name": "Sunday Crew"}, headers=auth(joey)).json()
    for path in ("/", f"/lounge/{lounge['id']}"):
        html = client.get(path).text
        assert '<a class="btn btn-sm" id="rules-btn" href="/rules">Rules</a>' in html  # header, next to H2H
        assert html.index('id="rules-btn"') < html.index('id="lounge-btn"')
        assert '<a href="/rules">Read the full rules</a>' in html  # welcome modal
        assert re.search(r'href="/privacy"[^>]*>Privacy</a> · <a href="/support"[^>]*>Support</a> · '
                         r'<a href="/rules"[^>]*>Rules</a>', html)  # footer
    assert 'href="/rules"' in client.get("/admin").text
    support = client.get("/support").text
    how_to_play = support[support.index("How to play"):support.index("Head-to-head lounges")]
    assert 'href="/rules">Rules of the Game</a>' in how_to_play
    assert 'href="/rules"' in client.get("/privacy").text


def test_no_center_left_in_player_copy(client):
    """Directions are Left / Middle / Right everywhere players and the host read them."""
    for path in ("/", "/admin", "/support", "/rules", "/privacy", "/static/js/player.js", "/static/js/admin.js",
                 "/static/manifest.webmanifest"):
        body = client.get(path).text
        # (The center, the lineman, is still the center.)
        assert "CENTER" not in body and not re.search(r"(?i)left,? center|center or right|center<small", body), path
    html = client.get("/").text
    assert 'data-dir="MIDDLE"' in html and ">MIDDLE<small>▲</small>" in html and "Left, Middle or Right" in html
    admin = client.get("/admin").text
    assert 'data-rdir="MIDDLE"' in admin and ">MIDDLE<small>↑</small>" in admin
