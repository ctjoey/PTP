"""Upgrading an existing game.db to the live-data release keeps every game, player and score."""

import sqlite3
import time

import pytest

from models import Store
from tests.test_rules import SCHEMAS, build_old_db, schema_objects

# The database the release before live data created (LEFT/MIDDLE/RIGHT, distance picks, no feed columns or tables).
PREVIOUS_SCHEMA = """
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT, home_name TEXT NOT NULL, home_primary TEXT NOT NULL,
    home_secondary TEXT NOT NULL, away_name TEXT NOT NULL, away_primary TEXT NOT NULL, away_secondary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED', 'LIVE', 'FINAL')), created_at REAL NOT NULL
);
CREATE TABLE plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    play_number INTEGER NOT NULL, down INTEGER CHECK (down BETWEEN 1 AND 4), distance TEXT,
    state TEXT NOT NULL CHECK (state IN ('OPEN', 'LOCKED', 'RESOLVED')),
    correct_play_type TEXT CHECK (correct_play_type IN ('RUN', 'PASS')),
    correct_direction TEXT CHECK (correct_direction IN ('LEFT', 'MIDDLE', 'RIGHT')),
    correct_yardage TEXT CHECK (correct_yardage IN ('SHORT', 'MEDIUM', 'LONG', 'LOSS')),
    yards_gained INTEGER, voided INTEGER NOT NULL DEFAULT 0, opened_at REAL NOT NULL, locks_at REAL NOT NULL,
    locked_at REAL, resolved_at REAL, UNIQUE (game_id, play_number)
);
CREATE UNIQUE INDEX ux_plays_one_active ON plays(game_id) WHERE state != 'RESOLVED';
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, token TEXT NOT NULL UNIQUE,
    total_score INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    play_id INTEGER NOT NULL REFERENCES plays(id) ON DELETE CASCADE,
    play_type TEXT NOT NULL CHECK (play_type IN ('RUN', 'PASS')),
    direction TEXT NOT NULL CHECK (direction IN ('LEFT', 'MIDDLE', 'RIGHT')),
    yardage TEXT CHECK (yardage IN ('SHORT', 'MEDIUM', 'LONG')), points_earned INTEGER, submitted_at REAL NOT NULL,
    UNIQUE (user_id, play_id)
);
CREATE INDEX ix_predictions_play ON predictions(play_id);
CREATE TABLE lounges (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, host_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE TABLE lounge_members (
    lounge_id TEXT NOT NULL REFERENCES lounges(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at REAL NOT NULL,
    PRIMARY KEY (lounge_id, user_id)
);
CREATE INDEX ix_lounge_members_user ON lounge_members(user_id);
"""

LIVE_DATA_GAME_COLUMNS = {"feed_game_id": None, "feed_auto_score": 1, "feed_auto_open": 0, "feed_paused": 0,
                          "feed_cursor": 0, "feed_requests": 0, "feed_cap_extra": 0}


def build_previous_db(path):
    """A live game from the previous release: a finished game, a live one with a scored play and a LOCKED play."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(PREVIOUS_SCHEMA)
    now = time.time()
    con.executemany("INSERT INTO games VALUES (?, 'Detroit', '#0076B6', '#B0B7BC', 'Chicago', '#0B162A', '#C83803', ?, ?)",
                    [(1, "FINAL", now - 900), (2, "LIVE", now)])
    con.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    [(1, "alice", "tok-a", 40, now), (2, "bob", "tok-b", 10, now)])
    con.execute("""INSERT INTO plays (id, game_id, play_number, down, distance, state, correct_play_type, correct_direction,
                   correct_yardage, yards_gained, opened_at, locks_at, locked_at, resolved_at)
                   VALUES (1, 2, 1, 1, '10', 'RESOLVED', 'PASS', 'MIDDLE', 'MEDIUM', 8, ?, ?, ?, ?)""", (now, now, now, now))
    con.execute("""INSERT INTO plays (id, game_id, play_number, down, distance, state, opened_at, locks_at, locked_at)
                   VALUES (2, 2, 2, 2, '2', 'LOCKED', ?, ?, ?)""", (now, now, now))
    con.executemany("INSERT INTO predictions (user_id, play_id, play_type, direction, yardage, points_earned, submitted_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(1, 1, "PASS", "MIDDLE", "MEDIUM", 40, now), (2, 1, "RUN", "MIDDLE", "SHORT", 10, now),
                     (1, 2, "RUN", "LEFT", "SHORT", None, now)])
    con.execute("INSERT INTO lounges VALUES ('1234', 'Sunday Crew', 1, ?)", (now,))
    con.execute("INSERT INTO lounge_members VALUES ('1234', 1, ?)", (now,))
    con.commit()
    rows = {t: [dict(zip([c[0] for c in con.execute(f"SELECT * FROM {t}").description], r))
                for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in ("games", "plays", "users", "predictions", "lounges", "lounge_members")}
    con.close()
    return rows


def rows_of(store, table):
    return store._all(f"SELECT * FROM {table} ORDER BY rowid")


def test_previous_release_database_upgrades_in_place_and_matches_a_new_one(tmp_path):
    path = str(tmp_path / "game.db")
    before = build_previous_db(path)
    store = Store(path)
    fresh = Store(":memory:")
    assert schema_objects(store) == schema_objects(fresh)          # same columns in the same order, same tables and indexes
    assert {"feed_log", "feed_usage"} <= {r["name"] for r in store._all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert store._all("PRAGMA foreign_key_check") == [] and store._one("PRAGMA foreign_keys")["foreign_keys"] == 1

    # Every row survives; the new columns take their defaults.
    assert rows_of(store, "games") == [{**r, **LIVE_DATA_GAME_COLUMNS} for r in before["games"]]
    assert rows_of(store, "plays") == [{**r, "resolved_by": None, "feed_text": None} for r in before["plays"]]
    for table in ("users", "predictions", "lounges", "lounge_members"):
        assert rows_of(store, table) == before[table], table

    # It keeps working: the LOCKED play resolves, history carries the new admin fields, correction re-scores.
    game = store.current_game()
    assert game["feed_game_id"] is None and game["feed_auto_score"] == 1 and game["feed_auto_open"] == 0
    history = {h["play_number"]: h for h in store.play_history(game["id"])}
    assert history[1]["resolved_by"] is None and history[1]["feed_text"] is None
    store.resolve_play("RUN", "LEFT", yards=3, resolved_by="feed", feed_text="C.Hubbard left end for 3 yards")
    assert store.get_play(2)["resolved_by"] == "feed" and store.get_user(1)["total_score"] == 80
    store.correct_play(1, "RUN", "MIDDLE", yards=2)                # an old play (resolved_by NULL) can be corrected
    assert store.get_play(1)["resolved_by"] == "host-fix" and store.get_user(1)["total_score"] == 50
    assert store.get_user(2)["total_score"] == 40
    # live data can be linked, counted and logged on the upgraded database
    store.set_feed_link(game["id"], "demo")
    store.count_feed_request(game["id"], "2026-10-08", plan_remaining=900, plan_limit=1000)
    store.log_feed(game["id"], "poll", {"ok": True})
    assert store.feed_usage("2026-10-08", game["id"])["game"] == 1 and len(store.feed_log(game["id"])) == 1
    store.close()
    fresh.close()


def test_the_upgrade_is_idempotent(tmp_path):
    path = str(tmp_path / "game.db")
    build_previous_db(path)
    first = Store(path)
    snapshot = first._all("SELECT name, sql FROM sqlite_master ORDER BY name")
    data = {t: rows_of(first, t) for t in ("games", "plays", "users", "predictions")}
    first.close()
    for _ in range(2):
        again = Store(path)
        assert again._all("SELECT name, sql FROM sqlite_master ORDER BY name") == snapshot
        assert {t: rows_of(again, t) for t in data} == data
        again.close()


@pytest.mark.parametrize("schema", SCHEMAS)
def test_older_databases_reach_the_live_data_schema_with_all_their_data(tmp_path, schema):
    """The original release, the distance-pick release (new or upgraded): CENTER becomes MIDDLE and the live-data
    columns and tables arrive, in one go, with no data lost."""
    path = str(tmp_path / "old.db")
    before = build_old_db(path, SCHEMAS[schema])
    store = Store(path)
    fresh = Store(":memory:")
    assert schema_objects(store) == schema_objects(fresh)
    assert [r["id"] for r in rows_of(store, "games")] == [r["id"] for r in before["games"]]
    for row in rows_of(store, "games"):
        assert {k: row[k] for k in LIVE_DATA_GAME_COLUMNS} == LIVE_DATA_GAME_COLUMNS
    for row in rows_of(store, "plays"):
        assert row["resolved_by"] is None and row["feed_text"] is None
    assert store.get_user(1)["total_score"] == before["users"][0]["total_score"]
    assert store._all("PRAGMA foreign_key_check") == []
    # The unique "one active play" index and the CHECKs survived the rebuild.
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE plays SET resolved_by = 'robot' WHERE id = 1")
    store.close()
    fresh.close()


def test_a_new_database_has_the_live_data_columns_and_defaults(tmp_path):
    store = Store(str(tmp_path / "new.db"))
    cols = {r["name"]: r for r in store._all("PRAGMA table_info(games)")}
    assert LIVE_DATA_GAME_COLUMNS.keys() <= cols.keys()
    assert cols["feed_auto_score"]["dflt_value"] == "1" and cols["feed_auto_open"]["dflt_value"] == "0"
    game = store.create_game("Detroit", "#0076B6", "#B0B7BC", "Chicago", "#0B162A", "#C83803")
    assert {k: game[k] for k in LIVE_DATA_GAME_COLUMNS} == LIVE_DATA_GAME_COLUMNS      # off, auto-score on, auto-open off
    linked = store.create_game("Dallas", "#003594", "#869397", "Tampa Bay", "#D50A0A", "#FF7900", feed_game_id="demo")
    assert linked["feed_game_id"] == "demo"
    assert {r["name"] for r in store._all("PRAGMA table_info(plays)")} >= {"resolved_by", "feed_text"}
    store.close()


def test_a_failed_resolved_by_check_and_feed_log_columns(tmp_path):
    store = Store(str(tmp_path / "new.db"))
    assert {r["name"] for r in store._all("PRAGMA table_info(feed_log)")} == \
        {"id", "game_id", "ts", "kind", "play_id", "feed_index", "data"}
    store.create_game("Detroit", "#0076B6", "#B0B7BC", "Chicago", "#0B162A", "#C83803")
    _, play = store.open_next_play(1, "10", 15)
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE plays SET resolved_by = 'robot' WHERE id = ?", (play["id"],))
    for who in ("host", "feed", "void", "host-fix"):
        store._conn.execute("UPDATE plays SET resolved_by = ? WHERE id = ?", (who, play["id"]))
    store.close()
