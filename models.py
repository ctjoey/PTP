"""Database models, persistence layer and scoring engine for Pick the Play.

Everything is stored in a single SQLite file (``game.db`` by default). The
``Store`` class is the only thing that talks to the database; ``app.py`` calls
into it and is responsible for broadcasting changes over WebSockets.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterator

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class GameStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINAL = "FINAL"


class PlayState(StrEnum):
    OPEN = "OPEN"
    LOCKED = "LOCKED"
    RESOLVED = "RESOLVED"


class PlayType(StrEnum):
    RUN = "RUN"
    PASS = "PASS"


class Direction(StrEnum):
    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"


# --------------------------------------------------------------------------- #
# Scoring engine
# --------------------------------------------------------------------------- #

TYPE_POINTS = 10
DIRECTION_POINTS = 10
EXACT_POINTS = 30


@dataclass(frozen=True, slots=True)
class ScoreResult:
    points: int
    type_correct: bool
    direction_correct: bool

    @property
    def exact(self) -> bool:
        return self.type_correct and self.direction_correct


def score_prediction(
    predicted_type: PlayType | str,
    predicted_direction: Direction | str,
    actual_type: PlayType | str,
    actual_direction: Direction | str,
) -> ScoreResult:
    """Score one prediction against the actual play outcome.

    * Exact match (type + direction): 30 points
    * Correct play type only:         10 points
    * Correct direction only:         10 points
    * Neither:                         0 points
    """
    type_ok = PlayType(predicted_type) == PlayType(actual_type)
    dir_ok = Direction(predicted_direction) == Direction(actual_direction)
    if type_ok and dir_ok:
        points = EXACT_POINTS
    elif type_ok:
        points = TYPE_POINTS
    elif dir_ok:
        points = DIRECTION_POINTS
    else:
        points = 0
    return ScoreResult(points=points, type_correct=type_ok, direction_correct=dir_ok)


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


class GameError(Exception):
    """A rule violation that should be reported back to the caller."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9 .'&-]{2,24}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-](?:[A-Za-z0-9_. -]{0,18}[A-Za-z0-9_.-])?$")
LOUNGE_NAME_RE = re.compile(r"^[A-Za-z0-9 .,'!&#-]{2,32}$")
LOUNGE_CODE_RE = re.compile(r"^\d{4}$")

# Legal safeguard: block official league marks and club nicknames so the admin
# can only create games with generic identifiers (e.g. city names) and colors.
PROTECTED_MARKS = (
    "nfl", "national football league", "super bowl", "superbowl",
    "49ers", "niners", "bears", "bengals", "bills", "broncos", "browns",
    "buccaneers", "bucs", "cardinals", "chargers", "chiefs", "colts",
    "commanders", "cowboys", "dolphins", "eagles", "falcons", "giants",
    "jaguars", "jets", "lions", "packers", "panthers", "patriots", "raiders",
    "rams", "ravens", "saints", "seahawks", "steelers", "texans", "titans",
    "vikings",
)
_PROTECTED_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in PROTECTED_MARKS) + r")\b", re.IGNORECASE
)


def validate_team_name(name: str) -> str:
    name = " ".join(name.split())
    if not TEAM_NAME_RE.match(name):
        raise GameError(
            "Team names must be 2-24 characters (letters, numbers, spaces, . ' & -)."
        )
    if m := _PROTECTED_RE.search(name):
        raise GameError(
            f"'{m.group(0)}' is a protected league/club mark. Use a generic "
            "identifier such as a city or region name instead."
        )
    return name


def validate_color(color: str) -> str:
    if not HEX_COLOR_RE.match(color):
        raise GameError(f"'{color}' is not a #RRGGBB hex color.")
    return color.upper()


def validate_username(username: str) -> str:
    username = " ".join(username.split())
    if not USERNAME_RE.match(username) or len(username) < 2:
        raise GameError(
            "Usernames must be 2-20 characters: letters, numbers, spaces, _ . -"
        )
    return username


def validate_lounge_name(name: str) -> str:
    name = " ".join(name.split())
    if not LOUNGE_NAME_RE.match(name):
        raise GameError("Lounge names must be 2-32 characters.")
    return name


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA = """
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

MAX_LOUNGE_MEMBERS = 50

# Exact-match test shared by the leaderboard queries (aliases: pr, pl).
_EXACT_SQL = (
    "CASE WHEN pl.voided = 0 AND pl.state = 'RESOLVED'"
    " AND pr.play_type = pl.correct_play_type"
    " AND pr.direction = pl.correct_direction THEN 1 ELSE 0 END"
)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign standard competition ranks (1, 2, 2, 4) by score."""
    prev_score: int | None = None
    rank = 0
    for i, row in enumerate(rows, start=1):
        if row["score"] != prev_score:
            rank, prev_score = i, row["score"]
        row["rank"] = rank
    return rows


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class Store:
    """Thread-safe SQLite repository holding all game state."""

    def __init__(self, path: str = "game.db") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low level -------------------------------------------------------- #

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # -- users ------------------------------------------------------------ #

    def create_user(self, username: str) -> dict[str, Any]:
        username = validate_username(username)
        token = secrets.token_urlsafe(32)
        try:
            with self._tx() as c:
                cur = c.execute(
                    "INSERT INTO users (username, token, created_at) VALUES (?, ?, ?)",
                    (username, token, time.time()),
                )
        except sqlite3.IntegrityError:
            raise GameError("That username is taken. Try another.", 409) from None
        return {"id": cur.lastrowid, "username": username, "token": token, "total_score": 0}

    def get_user_by_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return self._one(
            "SELECT id, username, total_score FROM users WHERE token = ?", (token,)
        )

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self._one("SELECT id, username, total_score FROM users WHERE id = ?", (user_id,))

    def total_scores(self, user_ids: list[int]) -> dict[int, int]:
        if not user_ids:
            return {}
        marks = ",".join("?" * len(user_ids))
        rows = self._all(f"SELECT id, total_score FROM users WHERE id IN ({marks})", tuple(user_ids))
        return {r["id"]: r["total_score"] for r in rows}

    # -- games ------------------------------------------------------------ #

    def current_game(self) -> dict[str, Any] | None:
        return self._one("SELECT * FROM games ORDER BY id DESC LIMIT 1")

    def get_game(self, game_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM games WHERE id = ?", (game_id,))

    def create_game(
        self,
        home_name: str,
        home_primary: str,
        home_secondary: str,
        away_name: str,
        away_primary: str,
        away_secondary: str,
    ) -> dict[str, Any]:
        home_name = validate_team_name(home_name)
        away_name = validate_team_name(away_name)
        if home_name.lower() == away_name.lower():
            raise GameError("Home and away teams must be different.")
        colors = [validate_color(c) for c in (home_primary, home_secondary, away_primary, away_secondary)]
        with self._tx() as c:
            current = _row(c.execute("SELECT * FROM games ORDER BY id DESC LIMIT 1").fetchone())
            if current and self._active_play_in(c, current["id"]):
                raise GameError("Resolve or void the current play before starting a new game.", 409)
            if current and current["status"] != GameStatus.FINAL:
                c.execute("UPDATE games SET status = ? WHERE id = ?", (GameStatus.FINAL, current["id"]))
            cur = c.execute(
                """INSERT INTO games (home_name, home_primary, home_secondary,
                                      away_name, away_primary, away_secondary, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (home_name, colors[0], colors[1], away_name, colors[2], colors[3],
                 GameStatus.SCHEDULED, time.time()),
            )
            game_id = cur.lastrowid
        return self.get_game(game_id)  # type: ignore[return-value]

    def set_game_status(self, status: GameStatus) -> dict[str, Any]:
        allowed = {
            GameStatus.SCHEDULED: {GameStatus.LIVE, GameStatus.FINAL},
            GameStatus.LIVE: {GameStatus.FINAL},
            GameStatus.FINAL: set(),
        }
        with self._tx() as c:
            game = _row(c.execute("SELECT * FROM games ORDER BY id DESC LIMIT 1").fetchone())
            if not game:
                raise GameError("Create a game first.", 404)
            current = GameStatus(game["status"])
            if status == current:
                return game
            if status not in allowed[current]:
                raise GameError(f"Cannot move a {current} game to {status}.", 409)
            if status == GameStatus.FINAL and self._active_play_in(c, game["id"]):
                raise GameError("Resolve or void the current play before ending the game.", 409)
            c.execute("UPDATE games SET status = ? WHERE id = ?", (status, game["id"]))
        return self.get_game(game["id"])  # type: ignore[return-value]

    # -- plays ------------------------------------------------------------ #

    @staticmethod
    def _active_play_in(c: sqlite3.Connection, game_id: int) -> dict[str, Any] | None:
        return _row(
            c.execute(
                "SELECT * FROM plays WHERE game_id = ? AND state != 'RESOLVED'", (game_id,)
            ).fetchone()
        )

    def get_play(self, play_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM plays WHERE id = ?", (play_id,))

    def latest_play(self, game_id: int) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM plays WHERE game_id = ? ORDER BY play_number DESC LIMIT 1", (game_id,)
        )

    def open_plays(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM plays WHERE state = 'OPEN'")

    def open_next_play(
        self, down: int | None, distance: str | None, window_seconds: float
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Open a new play for predictions. Returns ``(game, play)``.

        A SCHEDULED game automatically goes LIVE when its first play opens.
        """
        if down is not None and not 1 <= down <= 4:
            raise GameError("Down must be 1-4.")
        distance = (distance or "").strip()[:8] or None
        with self._tx() as c:
            game = _row(c.execute("SELECT * FROM games ORDER BY id DESC LIMIT 1").fetchone())
            if not game:
                raise GameError("Create a game first.", 404)
            if game["status"] == GameStatus.FINAL:
                raise GameError("This game is FINAL. Create a new game.", 409)
            if active := self._active_play_in(c, game["id"]):
                raise GameError(
                    f"Play #{active['play_number']} is still {active['state']}. "
                    "Resolve or void it first.",
                    409,
                )
            if game["status"] == GameStatus.SCHEDULED:
                c.execute("UPDATE games SET status = 'LIVE' WHERE id = ?", (game["id"],))
            number = c.execute(
                "SELECT COALESCE(MAX(play_number), 0) + 1 FROM plays WHERE game_id = ?",
                (game["id"],),
            ).fetchone()[0]
            now = time.time()
            cur = c.execute(
                """INSERT INTO plays (game_id, play_number, down, distance, state, opened_at, locks_at)
                   VALUES (?, ?, ?, ?, 'OPEN', ?, ?)""",
                (game["id"], number, down, distance, now, now + window_seconds),
            )
            play_id = cur.lastrowid
        return self.get_game(game["id"]), self.get_play(play_id)  # type: ignore[return-value]

    def lock_play(self, play_id: int | None = None) -> dict[str, Any]:
        """Lock the active play (or the given one) so no more picks are accepted."""
        with self._tx() as c:
            if play_id is None:
                game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
                play = self._active_play_in(c, game["id"]) if game else None
            else:
                play = _row(c.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone())
            if not play:
                raise GameError("There is no open play to lock.", 409)
            if play["state"] != PlayState.OPEN:
                raise GameError(f"Play #{play['play_number']} is already {play['state']}.", 409)
            c.execute(
                "UPDATE plays SET state = 'LOCKED', locked_at = ? WHERE id = ?",
                (time.time(), play["id"]),
            )
        return self.get_play(play["id"])  # type: ignore[return-value]

    def resolve_play(self, play_type: PlayType, direction: Direction) -> dict[str, Any]:
        """Record the actual outcome of the LOCKED play and score every prediction."""
        play_type, direction = PlayType(play_type), Direction(direction)
        with self._tx() as c:
            game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
            play = self._active_play_in(c, game["id"]) if game else None
            if not play:
                raise GameError("There is no play to resolve.", 409)
            if play["state"] != PlayState.LOCKED:
                raise GameError("Lock predictions before resolving the play.", 409)
            c.execute(
                """UPDATE plays SET state = 'RESOLVED', correct_play_type = ?,
                          correct_direction = ?, resolved_at = ? WHERE id = ?""",
                (play_type, direction, time.time(), play["id"]),
            )
            preds = c.execute(
                "SELECT id, user_id, play_type, direction FROM predictions WHERE play_id = ?",
                (play["id"],),
            ).fetchall()
            scored = [
                (score_prediction(p["play_type"], p["direction"], play_type, direction).points,
                 p["id"], p["user_id"])
                for p in preds
            ]
            c.executemany("UPDATE predictions SET points_earned = ? WHERE id = ?",
                          [(pts, pid) for pts, pid, _ in scored])
            c.executemany("UPDATE users SET total_score = total_score + ? WHERE id = ?",
                          [(pts, uid) for pts, _, uid in scored if pts])
        return self.get_play(play["id"])  # type: ignore[return-value]

    def void_play(self) -> dict[str, Any]:
        """Cancel the active play (penalty / no play). Nobody scores."""
        with self._tx() as c:
            game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
            play = self._active_play_in(c, game["id"]) if game else None
            if not play:
                raise GameError("There is no active play to void.", 409)
            now = time.time()
            c.execute(
                """UPDATE plays SET state = 'RESOLVED', voided = 1,
                          locked_at = COALESCE(locked_at, ?), resolved_at = ? WHERE id = ?""",
                (now, now, play["id"]),
            )
            c.execute("UPDATE predictions SET points_earned = 0 WHERE play_id = ?", (play["id"],))
        return self.get_play(play["id"])  # type: ignore[return-value]

    def play_history(self, game_id: int, limit: int = 25) -> list[dict[str, Any]]:
        return self._all(
            f"""SELECT pl.id, pl.play_number, pl.down, pl.distance, pl.state, pl.voided,
                       pl.correct_play_type, pl.correct_direction,
                       COUNT(pr.id) AS picks,
                       COALESCE(SUM({_EXACT_SQL}), 0) AS exact_hits
                FROM plays pl LEFT JOIN predictions pr ON pr.play_id = pl.id
                WHERE pl.game_id = ?
                GROUP BY pl.id ORDER BY pl.play_number DESC LIMIT ?""",
            (game_id, limit),
        )

    # -- predictions ------------------------------------------------------ #

    def submit_prediction(
        self,
        user_id: int,
        play_id: int,
        play_type: PlayType,
        direction: Direction,
        grace_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Create or update a user's pick while the play is OPEN and the timer runs."""
        play_type, direction = PlayType(play_type), Direction(direction)
        now = time.time()
        with self._tx() as c:
            play = _row(c.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone())
            if not play:
                raise GameError("Unknown play.", 404)
            if play["state"] != PlayState.OPEN or now > play["locks_at"] + grace_seconds:
                raise GameError("Too late — predictions for this play are locked.", 409)
            c.execute(
                """INSERT INTO predictions (user_id, play_id, play_type, direction, submitted_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, play_id) DO UPDATE SET
                       play_type = excluded.play_type,
                       direction = excluded.direction,
                       submitted_at = excluded.submitted_at""",
                (user_id, play_id, play_type, direction, now),
            )
        return {"play_id": play_id, "play_type": str(play_type), "direction": str(direction),
                "points_earned": None}

    def predictions_for_play(self, play_id: int) -> dict[int, dict[str, Any]]:
        rows = self._all(
            "SELECT user_id, play_id, play_type, direction, points_earned FROM predictions WHERE play_id = ?",
            (play_id,),
        )
        return {r["user_id"]: r for r in rows}

    def pick_stats(self, play_id: int) -> dict[str, int]:
        row = self._one(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(play_type = 'RUN'), 0)     AS "RUN",
                      COALESCE(SUM(play_type = 'PASS'), 0)    AS "PASS",
                      COALESCE(SUM(direction = 'LEFT'), 0)    AS "LEFT",
                      COALESCE(SUM(direction = 'CENTER'), 0)  AS "CENTER",
                      COALESCE(SUM(direction = 'RIGHT'), 0)   AS "RIGHT",
                      COALESCE(SUM(points_earned = ?), 0)     AS exact,
                      COALESCE(SUM(points_earned > 0), 0)     AS scored
               FROM predictions WHERE play_id = ?""",
            (EXACT_POINTS, play_id),
        )
        return row or {}

    # -- leaderboards ----------------------------------------------------- #

    def game_leaderboard(self, game_id: int) -> list[dict[str, Any]]:
        """Everyone who has made a pick in this game, ranked by game points."""
        rows = self._all(
            f"""SELECT u.id AS user_id, u.username,
                       COALESCE(SUM(pr.points_earned), 0) AS score,
                       COALESCE(SUM({_EXACT_SQL}), 0)     AS exact_hits,
                       COUNT(pr.id)                       AS picks
                FROM predictions pr
                JOIN plays pl ON pl.id = pr.play_id
                JOIN users u  ON u.id = pr.user_id
                WHERE pl.game_id = ?
                GROUP BY u.id
                ORDER BY score DESC, exact_hits DESC, u.username COLLATE NOCASE ASC""",
            (game_id,),
        )
        return _rank(rows)

    # -- lounges ---------------------------------------------------------- #

    def create_lounge(self, host_user_id: int, name: str) -> dict[str, Any]:
        name = validate_lounge_name(name)
        with self._tx() as c:
            for _ in range(100):
                code = f"{secrets.randbelow(10_000):04d}"
                if not c.execute("SELECT 1 FROM lounges WHERE id = ?", (code,)).fetchone():
                    break
            else:
                raise GameError("No lounge codes available right now. Try again.", 503)
            now = time.time()
            c.execute(
                "INSERT INTO lounges (id, name, host_user_id, created_at) VALUES (?, ?, ?, ?)",
                (code, name, host_user_id, now),
            )
            c.execute(
                "INSERT INTO lounge_members (lounge_id, user_id, joined_at) VALUES (?, ?, ?)",
                (code, host_user_id, now),
            )
        return self.get_lounge(code)  # type: ignore[return-value]

    def get_lounge(self, code: str) -> dict[str, Any] | None:
        if not LOUNGE_CODE_RE.match(code or ""):
            return None
        lounge = self._one(
            """SELECT l.id, l.name, l.host_user_id, u.username AS host_username,
                      (SELECT COUNT(*) FROM lounge_members m WHERE m.lounge_id = l.id) AS member_count
               FROM lounges l JOIN users u ON u.id = l.host_user_id WHERE l.id = ?""",
            (code,),
        )
        return lounge

    def join_lounge(self, code: str, user_id: int) -> dict[str, Any]:
        if not LOUNGE_CODE_RE.match(code or ""):
            raise GameError("Lounge codes are 4 digits.")
        with self._tx() as c:
            if not c.execute("SELECT 1 FROM lounges WHERE id = ?", (code,)).fetchone():
                raise GameError("No lounge found with that code.", 404)
            already = c.execute(
                "SELECT 1 FROM lounge_members WHERE lounge_id = ? AND user_id = ?", (code, user_id)
            ).fetchone()
            if not already:
                count = c.execute(
                    "SELECT COUNT(*) FROM lounge_members WHERE lounge_id = ?", (code,)
                ).fetchone()[0]
                if count >= MAX_LOUNGE_MEMBERS:
                    raise GameError("That lounge is full.", 409)
                c.execute(
                    "INSERT INTO lounge_members (lounge_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (code, user_id, time.time()),
                )
        return self.get_lounge(code)  # type: ignore[return-value]

    def is_lounge_member(self, code: str, user_id: int) -> bool:
        return bool(self._one(
            "SELECT 1 AS ok FROM lounge_members WHERE lounge_id = ? AND user_id = ?", (code, user_id)
        ))

    def user_lounges(self, user_id: int) -> list[dict[str, Any]]:
        return self._all(
            """SELECT l.id, l.name, l.host_user_id,
                      (SELECT COUNT(*) FROM lounge_members x WHERE x.lounge_id = l.id) AS member_count
               FROM lounge_members m JOIN lounges l ON l.id = m.lounge_id
               WHERE m.user_id = ? ORDER BY m.joined_at DESC""",
            (user_id,),
        )

    def lounge_leaderboard(self, code: str, game_id: int | None) -> list[dict[str, Any]]:
        """All lounge members ranked by points in the given game (0 if none)."""
        rows = self._all(
            f"""SELECT u.id AS user_id, u.username, u.total_score,
                       (u.id = l.host_user_id) AS is_host,
                       COALESCE(s.score, 0) AS score,
                       COALESCE(s.exact_hits, 0) AS exact_hits,
                       COALESCE(s.picks, 0) AS picks
                FROM lounge_members m
                JOIN lounges l ON l.id = m.lounge_id
                JOIN users u   ON u.id = m.user_id
                LEFT JOIN (
                    SELECT pr.user_id,
                           SUM(COALESCE(pr.points_earned, 0)) AS score,
                           SUM({_EXACT_SQL}) AS exact_hits,
                           COUNT(*) AS picks
                    FROM predictions pr JOIN plays pl ON pl.id = pr.play_id
                    WHERE pl.game_id = ?
                    GROUP BY pr.user_id
                ) s ON s.user_id = u.id
                WHERE m.lounge_id = ?
                ORDER BY score DESC, exact_hits DESC, u.username COLLATE NOCASE ASC""",
            (game_id if game_id is not None else -1, code),
        )
        for r in rows:
            r["is_host"] = bool(r["is_host"])
        return _rank(rows)
