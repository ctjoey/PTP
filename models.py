"""Database models, persistence layer and scoring engine for Pick the Play.

Everything is stored in a single SQLite file (``game.db`` by default). The
``Store`` class is the only thing that talks to the database; ``app.py`` calls
into it and is responsible for broadcasting changes over WebSockets.
"""

from __future__ import annotations

import json
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


# What earlier versions called the middle direction; accepted on input and converted to MIDDLE.
LEGACY_MIDDLE = "CENTER"


class Direction(StrEnum):
    """Where the play goes, as the quarterback looks downfield (the offense's left/right).

    The NFL charts runs and passes as left, middle or right. ``"CENTER"`` (what earlier versions
    sent and stored) is still accepted as an input alias for ``MIDDLE``; the server only ever
    outputs ``"MIDDLE"``.
    """

    LEFT = "LEFT"
    MIDDLE = "MIDDLE"
    RIGHT = "RIGHT"

    @classmethod
    def _missing_(cls, value: object) -> "Direction | None":
        return cls.MIDDLE if value == LEGACY_MIDDLE else None


class Yardage(StrEnum):
    """A player's distance pick: total yards gained on the play."""

    SHORT = "SHORT"    # 0-5 yards (an incomplete pass or no gain is 0 = SHORT)
    MEDIUM = "MEDIUM"  # 6-10 yards
    LONG = "LONG"      # 11 or more yards


class YardageOutcome(StrEnum):
    """The actual distance bucket of a play. LOSS (negative yards) never matches a pick."""

    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"
    LOSS = "LOSS"


class GameError(Exception):
    """A rule violation that should be reported back to the caller."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# Yards gained the admin (or a data feed) may report.
MIN_YARDS, MAX_YARDS = -99, 99


# --------------------------------------------------------------------------- #
# Scoring engine
# --------------------------------------------------------------------------- #

TYPE_POINTS = 10
DIRECTION_POINTS = 10
YARDAGE_POINTS = 10
BONUS_POINTS = 10  # extra for getting all three right
EXACT_POINTS = TYPE_POINTS + DIRECTION_POINTS + YARDAGE_POINTS + BONUS_POINTS  # a perfect call: 40


@dataclass(frozen=True, slots=True)
class ScoreResult:
    points: int
    type_correct: bool
    direction_correct: bool
    yardage_correct: bool

    @property
    def exact(self) -> bool:
        return self.type_correct and self.direction_correct and self.yardage_correct


def score_prediction(
    predicted_type: PlayType | str,
    predicted_direction: Direction | str,
    predicted_yardage: Yardage | str | None,
    actual_type: PlayType | str,
    actual_direction: Direction | str,
    actual_yardage: YardageOutcome | str | None,
) -> ScoreResult:
    """Score one prediction against the actual play outcome: 10 points per correct part.

    * Correct play type (RUN / PASS):             +10
    * Correct direction (LEFT / MIDDLE / RIGHT):  +10
    * Correct distance (SHORT / MEDIUM / LONG):   +10
    * Bonus for all three right (``exact``):      +10, so a perfect call is 40

    Possible totals are 0, 10, 20 and 40 (30 can't happen). A LOSS never matches a pick, so a loss
    of yards scores no distance points and no bonus. A pick with no distance (``None``: made before
    distance picks existed) scores its distance as wrong, and so does a play with no recorded
    distance.
    """
    type_ok = PlayType(predicted_type) == PlayType(actual_type)
    dir_ok = Direction(predicted_direction) == Direction(actual_direction)
    picked = Yardage(predicted_yardage) if predicted_yardage is not None else None
    actual = YardageOutcome(actual_yardage) if actual_yardage is not None else None
    yardage_ok = picked is not None and actual is not None and picked.value == actual.value
    points = TYPE_POINTS * type_ok + DIRECTION_POINTS * dir_ok + YARDAGE_POINTS * yardage_ok
    if type_ok and dir_ok and yardage_ok:
        points += BONUS_POINTS
    return ScoreResult(points=points, type_correct=type_ok, direction_correct=dir_ok,
                       yardage_correct=yardage_ok)


def yardage_for_yards(yards: int) -> YardageOutcome:
    """The distance bucket for a play's total yards gained.

    Negative = LOSS, 0-5 = SHORT (an incomplete pass or no gain is 0), 6-10 = MEDIUM, 11+ = LONG.
    """
    if isinstance(yards, bool) or not isinstance(yards, int):
        raise GameError("Yards gained must be a whole number.", 422)
    if not MIN_YARDS <= yards <= MAX_YARDS:
        raise GameError(f"Yards gained must be between {MIN_YARDS} and {MAX_YARDS}.", 422)
    if yards < 0:
        return YardageOutcome.LOSS
    if yards <= 5:
        return YardageOutcome.SHORT
    if yards <= 10:
        return YardageOutcome.MEDIUM
    return YardageOutcome.LONG


def resolve_yardage(
    yardage: YardageOutcome | str | None, yards: int | None
) -> tuple[YardageOutcome, int | None]:
    """Work out a play's distance bucket from the admin's ``yardage`` and/or ``yards``.

    At least one is required. With ``yards`` alone the bucket is derived; with both they must
    agree. Returns ``(bucket, yards)``. Raises ``GameError`` (422) otherwise.
    """
    if yardage is None and yards is None:
        raise GameError("Choose the distance (Short, Medium, Long or Loss) or enter the yards gained.", 422)
    if yardage is not None:
        try:
            yardage = YardageOutcome(yardage)
        except ValueError:
            raise GameError("Distance must be SHORT, MEDIUM, LONG or LOSS.", 422) from None
    if yards is None:
        return yardage, None  # type: ignore[return-value]
    derived = yardage_for_yards(yards)
    if yardage is not None and yardage != derived:
        raise GameError(
            f"{yards} yards is {derived}, not {yardage}. Fix the distance or the yards gained.", 422
        )
    return derived, yards


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


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


# Offensive-name filter (App Store Guideline 1.2: usernames and lounge names are
# shown to other players). Names are lower-cased and common leetspeak is undone
# (0->o, 1->i, 3->e, 4->a, 5->s, 7->t, @->a, $->s). Anything else that isn't a
# letter separates words. Words written apart ("dick head") or spelled out
# ("f u c k", "f.u.c.k") are joined back up, and stretched letters are squeezed
# ("fuuuck").
#
# Trade-off: the lists are matched against WHOLE words, so ordinary names that
# merely contain a rude string pass: Scunthorpe, Cassidy, Hancock, Essex,
# Dickens, Arsenal, Cumming, Shiitake, Therapist, Grapes. BLOCKED_STEMS also
# match with a common ending ("-s", "-er", "-ing", "-y", "-head", ...);
# BLOCKED_EXACT words don't, because their endings are innocent words ("spicy",
# "Spicer"). Only a few slurs with no innocent use are matched anywhere inside a
# name (BLOCKED_ANYWHERE). Deliberately left out: words that are also real names
# or everyday words ("Dick", "Cox", "Willy", "Fanny", "Coon", "Dyke"/"Van Dyke",
# Spanish "Kike", "cum laude"), so a player called Dick Butkus can still sign up.
# Determined trolls can always find a spelling a list misses; the aim is to stop
# the obvious ones without blocking real names.
_LEET = str.maketrans("013457@$", "oieastas")
BLOCKED_STEMS = frozenset({
    "fuck", "motherfuck", "fucktard", "shit", "bullshit", "cunt", "twat",
    "wank", "bitch", "bastard", "asshole", "arsehole", "dickhead", "cocksucker",
    "pussy", "whore", "slut", "jizz", "dildo", "porn", "piss", "blowjob",
    "handjob", "rape", "rapist", "nazi",
})
BLOCKED_EXACT = frozenset({
    "fuk", "fck", "fuckin", "shite", "bollocks", "pussies", "porno", "cumshot",
    "tits", "raped", "hitler", "fag", "fags", "tranny", "trannies", "spic",
    "spics", "chink", "chinks", "gook", "gooks", "wetback", "wetbacks", "paki",
    "pakis", "raghead", "ragheads", "towelhead", "towelheads",
    "retard", "retards", "retarded",  # not "retarder"/"retarding" (brakes, slowing)
})
BLOCKED_ENDINGS = ("s", "es", "er", "ers", "ing", "ed", "y", "ty", "head", "face", "hole")
BLOCKED_ANYWHERE = ("nigger", "nigga", "faggot")
# Innocent words that contain a BLOCKED_ANYWHERE string.
_ANYWHERE_EXCEPTIONS = ("snigger", "niggard")
_REPEATS_RE = re.compile(r"(.)\1{2,}")


def _spellings(word: str) -> set[str]:
    """``word`` plus stretched letters squeezed: 'fuuuck' -> 'fuck', 'asssss' -> 'ass'."""
    return {word, _REPEATS_RE.sub(r"\1", word), _REPEATS_RE.sub(r"\1\1", word)}


def _is_blocked_word(word: str) -> bool:
    if word in BLOCKED_EXACT or word in BLOCKED_STEMS:
        return True
    for end in BLOCKED_ENDINGS:
        stem = word[: -len(end)]
        if word.endswith(end) and (
            stem in BLOCKED_STEMS
            # doubled last letter: "shitting", "shitter"
            or (len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in BLOCKED_STEMS)
        ):
            return True
    return False


def is_offensive_name(name: str) -> bool:
    """True if ``name`` contains strong profanity or a slur (see notes above)."""
    words = re.findall(r"[a-z]+", name.lower().translate(_LEET))
    candidates = set(words)
    # Two words written apart ("dick head", "mother fucker").
    candidates.update(a + b for a, b in zip(words, words[1:]))
    # Letters spelled out one at a time ("f u c k", "f.u.c.k").
    run = ""
    for w in [*words, ""]:
        if len(w) == 1:
            run += w
            continue
        if len(run) > 1:
            candidates.add(run)
        run = ""
    if any(_is_blocked_word(s) for c in candidates for s in _spellings(c)):
        return True
    for squashed in _spellings("".join(words)):
        for ok in _ANYWHERE_EXCEPTIONS:
            squashed = squashed.replace(ok, " ")
        if any(slur in squashed for slur in BLOCKED_ANYWHERE):
            return True
    return False


def _check_name_is_clean(name: str) -> str:
    if is_offensive_name(name):
        raise GameError("Please choose a different name.")
    return name


def validate_username(username: str) -> str:
    username = " ".join(username.split())
    if not USERNAME_RE.match(username) or len(username) < 2:
        raise GameError(
            "Usernames must be 2-20 characters: letters, numbers, spaces, _ . -"
        )
    return _check_name_is_clean(username)


def validate_lounge_name(name: str) -> str:
    name = " ".join(name.split())
    if not LOUNGE_NAME_RE.match(name):
        raise GameError("Lounge names must be 2-32 characters.")
    return _check_name_is_clean(name)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

# The two tables whose CHECK constraints name a direction. Defined once so the CENTER -> MIDDLE
# migration (``Store._rebuild_direction_tables``) recreates them exactly as a new database has them.
_PLAYS = """(
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id            INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    play_number        INTEGER NOT NULL,
    down               INTEGER CHECK (down BETWEEN 1 AND 4),
    distance           TEXT,
    state              TEXT NOT NULL CHECK (state IN ('OPEN', 'LOCKED', 'RESOLVED')),
    correct_play_type  TEXT CHECK (correct_play_type IN ('RUN', 'PASS')),
    correct_direction  TEXT CHECK (correct_direction IN ('LEFT', 'MIDDLE', 'RIGHT')),
    correct_yardage    TEXT CHECK (correct_yardage IN ('SHORT', 'MEDIUM', 'LONG', 'LOSS')),
    yards_gained       INTEGER,  -- optional; when given, correct_yardage is derived from it
    voided             INTEGER NOT NULL DEFAULT 0,
    opened_at          REAL NOT NULL,
    locks_at           REAL NOT NULL,
    locked_at          REAL,
    resolved_at        REAL,
    resolved_by        TEXT CHECK (resolved_by IN ('host', 'feed', 'void', 'host-fix')),  -- NULL on old rows
    feed_text          TEXT,  -- the live-data text this play was scored from (admin only)
    UNIQUE (game_id, play_number)
)"""

_PREDICTIONS = """(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    play_id        INTEGER NOT NULL REFERENCES plays(id) ON DELETE CASCADE,
    play_type      TEXT NOT NULL CHECK (play_type IN ('RUN', 'PASS')),
    direction      TEXT NOT NULL CHECK (direction IN ('LEFT', 'MIDDLE', 'RIGHT')),
    yardage        TEXT CHECK (yardage IN ('SHORT', 'MEDIUM', 'LONG')),  -- NULL: picked before distance picks
    points_earned  INTEGER,  -- NULL until the play is resolved
    submitted_at   REAL NOT NULL,
    UNIQUE (user_id, play_id)
)"""

SCHEMA = f"""
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
    created_at      REAL NOT NULL,
    feed_game_id    TEXT,                           -- live data: the Tank01 game id, or 'demo'
    feed_auto_score INTEGER NOT NULL DEFAULT 1,
    feed_auto_open  INTEGER NOT NULL DEFAULT 0,
    feed_paused     INTEGER NOT NULL DEFAULT 0,
    feed_cursor     INTEGER NOT NULL DEFAULT 0,     -- feed entries already examined
    feed_requests   INTEGER NOT NULL DEFAULT 0,     -- real requests made for this game
    feed_cap_extra  INTEGER NOT NULL DEFAULT 0      -- requests the host allowed beyond the per-game cap
);

CREATE TABLE IF NOT EXISTS plays {_PLAYS};
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

CREATE TABLE IF NOT EXISTS predictions {_PREDICTIONS};
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

-- Live data (Tank01): the recorder, so the feature can be tuned after a real game.
CREATE TABLE IF NOT EXISTS feed_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     INTEGER,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    play_id     INTEGER,
    feed_index  INTEGER,
    data        TEXT
);
CREATE INDEX IF NOT EXISTS ix_feed_log_game ON feed_log(game_id, id);

-- Names the host removed and blocked: nobody can sign up with one again (case-insensitive).
CREATE TABLE IF NOT EXISTS blocked_names (
    name        TEXT PRIMARY KEY COLLATE NOCASE,
    created_at  REAL NOT NULL
);

-- Real requests per UTC day (and the plan's last-seen allowance), so restarts cannot reset the caps.
CREATE TABLE IF NOT EXISTS feed_usage (
    day             TEXT PRIMARY KEY,
    requests        INTEGER NOT NULL DEFAULT 0,
    plan_remaining  INTEGER,   -- the plan's allowance as its last response showed it
    plan_limit      INTEGER,
    plan_seen_at    REAL       -- when that response arrived
);
"""

# Columns added after the first release. ``Store`` adds any that an older database (for example a
# game.db on a host's persistent disk) is missing, so upgrading keeps every game, player and score.
MIGRATIONS: dict[str, dict[str, str]] = {
    "games": {
        "feed_game_id": "TEXT",
        "feed_auto_score": "INTEGER NOT NULL DEFAULT 1",
        "feed_auto_open": "INTEGER NOT NULL DEFAULT 0",
        "feed_paused": "INTEGER NOT NULL DEFAULT 0",
        "feed_cursor": "INTEGER NOT NULL DEFAULT 0",
        "feed_requests": "INTEGER NOT NULL DEFAULT 0",
        "feed_cap_extra": "INTEGER NOT NULL DEFAULT 0",
    },
    "plays": {
        "correct_yardage": "TEXT CHECK (correct_yardage IN ('SHORT', 'MEDIUM', 'LONG', 'LOSS'))",
        "yards_gained": "INTEGER",
        "resolved_by": "TEXT CHECK (resolved_by IN ('host', 'feed', 'void', 'host-fix'))",
        "feed_text": "TEXT",
    },
    "predictions": {
        "yardage": "TEXT CHECK (yardage IN ('SHORT', 'MEDIUM', 'LONG'))",
    },
}

# Tables whose CHECK constraints name a direction, parents first: (table, definition, direction column).
# Databases written before LEFT/MIDDLE/RIGHT allow 'CENTER' there; ``Store`` rebuilds them once.
DIRECTION_TABLES: tuple[tuple[str, str, str], ...] = (
    ("plays", _PLAYS, "correct_direction"),
    ("predictions", _PREDICTIONS, "direction"),
)

MAX_LOUNGE_MEMBERS = 50

# Live-data recorder: rows kept per game, and how many writes pass between clean-ups.
FEED_LOG_KEEP = 5000
FEED_LOG_PRUNE_EVERY = 100
PLAN_INFO_MAX_AGE = 86400.0  # seconds a remembered plan allowance is trusted

# All-three-right test shared by the leaderboard queries (aliases: pr, pl). A LOSS or a NULL
# distance never matches.
_EXACT_SQL = (
    "CASE WHEN pl.voided = 0 AND pl.state = 'RESOLVED'"
    " AND pr.play_type = pl.correct_play_type"
    " AND pr.direction = pl.correct_direction"
    " AND pr.yardage = pl.correct_yardage THEN 1 ELSE 0 END"
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
        self._feed_log_writes = 0
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Bring a database created by an older version up to date (idempotent).

        First add the columns it is missing (the distance columns), then rename the direction
        CENTER to MIDDLE. In that order a database from the very first release ends up exactly
        like a new one.
        """
        with self._tx() as c:
            for table, columns in MIGRATIONS.items():
                have = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns.items():
                    if name not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self._rebuild_direction_tables()

    def _rebuild_direction_tables(self) -> None:
        """CENTER -> MIDDLE: rebuild each table whose CHECK constraint still says 'CENTER' (runs once).

        SQLite can't alter a CHECK constraint, so this follows https://sqlite.org/lang_altertable.html
        ("other kinds of table schema changes"): with foreign keys off, in one transaction, create
        the new table, copy every row and id (turning 'CENTER' into 'MIDDLE'), drop the old table,
        rename the new one, recreate its indexes and AUTOINCREMENT counter, then check foreign keys.
        Any failure rolls the whole thing back and leaves the database as it was.
        """
        def table_sql(name: str) -> str:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
            return row["sql"] if row else ""

        stale = [t for t in DIRECTION_TABLES if LEGACY_MIDDLE in table_sql(t[0])]
        if not stale:
            return
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction
            try:
                with self._tx() as c:
                    for table, definition, column in stale:
                        self._rebuild_table(c, table, definition, column)
                    for table, _, _ in DIRECTION_TABLES:
                        if broken := c.execute(f"PRAGMA foreign_key_check({table})").fetchall():
                            raise RuntimeError(f"Migration aborted: {len(broken)} broken references in {table}.")
            finally:
                self._conn.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _rebuild_table(c: sqlite3.Connection, table: str, definition: str, column: str) -> None:
        indexes = [r["sql"] for r in c.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,)
        )]
        seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
        old_cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]
        c.execute(f"CREATE TABLE {table}__new {definition}")
        new_cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table}__new)")]
        if lost := [n for n in old_cols if n not in new_cols]:
            raise RuntimeError(f"Migration aborted: {table} has unexpected columns {lost}.")
        cols = [n for n in new_cols if n in old_cols]  # copied by name: older tables order them differently
        select = ", ".join(
            f"CASE {n} WHEN '{LEGACY_MIDDLE}' THEN '{Direction.MIDDLE}' ELSE {n} END" if n == column else n
            for n in cols
        )
        c.execute(f"INSERT INTO {table}__new ({', '.join(cols)}) SELECT {select} FROM {table}")
        c.execute(f"DROP TABLE {table}")
        c.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
        for sql in indexes:
            c.execute(sql)
        if seq is not None:  # AUTOINCREMENT never reuses an id, even one deleted before the upgrade
            if not c.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?",
                             (seq["seq"], table)).rowcount:
                c.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, seq["seq"]))

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
        if self.is_name_blocked(username):
            raise GameError("Please choose a different name.")
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

    def count_users(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM users")["n"]  # type: ignore[index]

    def list_players(self, game_id: int | None, limit: int = 500) -> list[dict[str, Any]]:
        """Newest sign-ups first, with their points in ``game_id`` (the current game): for the host's Players list."""
        return self._all(
            """SELECT u.id, u.username, u.total_score, u.created_at,
                      COALESCE(g.picks, 0) AS picks, COALESCE(g.score, 0) AS game_score
               FROM users u
               LEFT JOIN (
                   SELECT pr.user_id, COUNT(*) AS picks, SUM(COALESCE(pr.points_earned, 0)) AS score
                   FROM predictions pr JOIN plays pl ON pl.id = pr.play_id
                   WHERE pl.game_id = ?
                   GROUP BY pr.user_id
               ) g ON g.user_id = u.id
               ORDER BY u.id DESC LIMIT ?""",
            (game_id if game_id is not None else -1, limit),
        )

    def is_name_blocked(self, name: str) -> bool:
        return self._one("SELECT 1 AS hit FROM blocked_names WHERE name = ?", (" ".join(name.split()),)) is not None

    def block_name(self, name: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO blocked_names (name, created_at) VALUES (?, ?)",
                      (" ".join(name.split()), time.time()))

    def unblock_name(self, name: str) -> bool:
        with self._tx() as c:
            cur = c.execute("DELETE FROM blocked_names WHERE name = ?", (" ".join(name.split()),))
        return cur.rowcount > 0

    def blocked_names(self) -> list[str]:
        return [r["name"] for r in self._all("SELECT name FROM blocked_names ORDER BY created_at")]

    def delete_user(self, user_id: int) -> bool:
        """Permanently delete an account (App Store Guideline 5.1.1(v)).

        ``ON DELETE CASCADE`` (with ``PRAGMA foreign_keys = ON``) removes the
        user's predictions, their lounge memberships, and every lounge they
        host together with that lounge's memberships. Other players' scores are
        untouched, and the username becomes available again. Returns False if
        the user was already gone.
        """
        with self._tx() as c:
            cur = c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0

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
        feed_game_id: str | None = None,
    ) -> dict[str, Any]:
        """Start a new game (the previous one becomes FINAL). ``feed_game_id`` links live data from the start."""
        home_name = validate_team_name(home_name)
        away_name = validate_team_name(away_name)
        if home_name.lower() == away_name.lower():
            raise GameError(
                "Home and away teams must be different. For two teams from one city, add a word "
                'to each, e.g. "New York Blue" / "New York Green".'
            )
        colors = [validate_color(c) for c in (home_primary, home_secondary, away_primary, away_secondary)]
        with self._tx() as c:
            current = _row(c.execute("SELECT * FROM games ORDER BY id DESC LIMIT 1").fetchone())
            if current and self._active_play_in(c, current["id"]):
                raise GameError("Resolve or void the current play before starting a new game.", 409)
            if current and current["status"] != GameStatus.FINAL:
                c.execute("UPDATE games SET status = ? WHERE id = ?", (GameStatus.FINAL, current["id"]))
            cur = c.execute(
                """INSERT INTO games (home_name, home_primary, home_secondary,
                                      away_name, away_primary, away_secondary, status, created_at, feed_game_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (home_name, colors[0], colors[1], away_name, colors[2], colors[3],
                 GameStatus.SCHEDULED, time.time(), feed_game_id or None),
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

    def resolve_play(
        self,
        play_type: PlayType,
        direction: Direction,
        yardage: YardageOutcome | str | None = None,
        yards: int | None = None,
        resolved_by: str = "host",
        feed_text: str | None = None,
    ) -> dict[str, Any]:
        """Record the actual outcome of the LOCKED play and score every prediction.

        The distance comes from ``yardage`` (SHORT/MEDIUM/LONG/LOSS), ``yards`` (total yards
        gained, from which the bucket is derived) or both, which must then agree. ``resolved_by``
        says who scored it ('host', or 'feed' for live data, with the feed's ``feed_text``).
        """
        play_type, direction = PlayType(play_type), Direction(direction)
        yardage, yards = resolve_yardage(yardage, yards)
        with self._tx() as c:
            game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
            play = self._active_play_in(c, game["id"]) if game else None
            if not play:
                raise GameError("There is no play to resolve.", 409)
            if play["state"] != PlayState.LOCKED:
                raise GameError("Lock predictions before resolving the play.", 409)
            c.execute(
                """UPDATE plays SET state = 'RESOLVED', correct_play_type = ?, correct_direction = ?,
                          correct_yardage = ?, yards_gained = ?, resolved_at = ?,
                          resolved_by = ?, feed_text = ? WHERE id = ?""",
                (play_type, direction, yardage, yards, time.time(), resolved_by, feed_text, play["id"]),
            )
            preds = c.execute(
                "SELECT id, user_id, play_type, direction, yardage FROM predictions WHERE play_id = ?",
                (play["id"],),
            ).fetchall()
            scored = [
                (score_prediction(p["play_type"], p["direction"], p["yardage"],
                                  play_type, direction, yardage).points,
                 p["id"], p["user_id"])
                for p in preds
            ]
            c.executemany("UPDATE predictions SET points_earned = ? WHERE id = ?",
                          [(pts, pid) for pts, pid, _ in scored])
            c.executemany("UPDATE users SET total_score = total_score + ? WHERE id = ?",
                          [(pts, uid) for pts, _, uid in scored if pts])
        return self.get_play(play["id"])  # type: ignore[return-value]

    def void_play(self, feed_text: str | None = None) -> dict[str, Any]:
        """Cancel the active play (penalty / no play). Nobody scores. ``feed_text`` is set when live data did it."""
        with self._tx() as c:
            game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
            play = self._active_play_in(c, game["id"]) if game else None
            if not play:
                raise GameError("There is no active play to void.", 409)
            now = time.time()
            c.execute(
                """UPDATE plays SET state = 'RESOLVED', voided = 1, resolved_by = 'void', feed_text = ?,
                          locked_at = COALESCE(locked_at, ?), resolved_at = ? WHERE id = ?""",
                (feed_text, now, now, play["id"]),
            )
            c.execute("UPDATE predictions SET points_earned = 0 WHERE play_id = ?", (play["id"],))
        return self.get_play(play["id"])  # type: ignore[return-value]

    def play_history(self, game_id: int, limit: int = 25) -> list[dict[str, Any]]:
        return self._all(
            f"""SELECT pl.id, pl.play_number, pl.down, pl.distance, pl.state, pl.voided,
                       pl.correct_play_type, pl.correct_direction,
                       pl.correct_yardage, pl.yards_gained, pl.resolved_by, pl.feed_text,
                       COUNT(pr.id) AS picks,
                       COALESCE(SUM({_EXACT_SQL}), 0) AS exact_hits
                FROM plays pl LEFT JOIN predictions pr ON pr.play_id = pl.id
                WHERE pl.game_id = ?
                GROUP BY pl.id ORDER BY pl.play_number DESC LIMIT ?""",
            (game_id, limit),
        )

    def played_count(self, game_id: int) -> int:
        """How many of the game's plays have locked (including voided ones)."""
        row = self._one("SELECT COUNT(*) AS n FROM plays WHERE game_id = ? AND state != 'OPEN'", (game_id,))
        return row["n"] if row else 0

    def locked_plays(self, game_id: int) -> list[dict[str, Any]]:
        """The game's LOCKED plays (waiting for a result), oldest first."""
        return self._all(
            "SELECT * FROM plays WHERE game_id = ? AND state = 'LOCKED' ORDER BY play_number", (game_id,)
        )

    def correct_play(
        self,
        play_id: int,
        play_type: PlayType | str,
        direction: Direction | str,
        yardage: YardageOutcome | str | None = None,
        yards: int | None = None,
    ) -> dict[str, Any]:
        """Fix the result of a scored play: re-score every pick and move each player's total by the difference.

        Only a RESOLVED, non-voided play of the current game can be corrected. The distance follows the same
        rules as ``resolve_play``. All-or-nothing: any failure leaves every score as it was.
        """
        play_type, direction = PlayType(play_type), Direction(direction)
        yardage, yards = resolve_yardage(yardage, yards)
        with self._tx() as c:
            game = _row(c.execute("SELECT id FROM games ORDER BY id DESC LIMIT 1").fetchone())
            play = _row(c.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone())
            if not play:
                raise GameError("Unknown play.", 404)
            if not game or play["game_id"] != game["id"]:
                raise GameError("Only plays from the current game can be corrected.", 409)
            if play["state"] != PlayState.RESOLVED:
                raise GameError("That play has not been scored yet.", 409)
            if play["voided"]:
                raise GameError("A voided play has no result to correct.", 409)
            c.execute(
                """UPDATE plays SET correct_play_type = ?, correct_direction = ?, correct_yardage = ?,
                          yards_gained = ?, resolved_by = 'host-fix' WHERE id = ?""",
                (play_type, direction, yardage, yards, play_id),
            )
            preds = c.execute(
                "SELECT id, user_id, play_type, direction, yardage, points_earned FROM predictions WHERE play_id = ?",
                (play_id,),
            ).fetchall()
            for p in preds:
                points = score_prediction(p["play_type"], p["direction"], p["yardage"],
                                          play_type, direction, yardage).points
                delta = points - (p["points_earned"] or 0)
                c.execute("UPDATE predictions SET points_earned = ? WHERE id = ?", (points, p["id"]))
                if delta:
                    c.execute("UPDATE users SET total_score = total_score + ? WHERE id = ?", (delta, p["user_id"]))
        return self.get_play(play_id)  # type: ignore[return-value]

    # -- live data (Tank01) ------------------------------------------------ #

    def set_feed_link(self, game_id: int, feed_game_id: str | None) -> dict[str, Any]:
        """Link (or, with None, unlink) a game to a live-data source. Starts over at the first feed entry."""
        with self._tx() as c:
            c.execute("UPDATE games SET feed_game_id = ?, feed_cursor = 0, feed_paused = 0 WHERE id = ?",
                      (feed_game_id or None, game_id))
        return self.get_game(game_id)  # type: ignore[return-value]

    def set_feed_options(
        self, game_id: int, auto_score: bool | None = None, auto_open: bool | None = None,
        paused: bool | None = None,
    ) -> dict[str, Any]:
        """Change the per-game live-data switches that are given (None leaves one as it is)."""
        changes = {"feed_auto_score": auto_score, "feed_auto_open": auto_open, "feed_paused": paused}
        changes = {k: int(bool(v)) for k, v in changes.items() if v is not None}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            with self._tx() as c:
                c.execute(f"UPDATE games SET {sets} WHERE id = ?", (*changes.values(), game_id))
        return self.get_game(game_id)  # type: ignore[return-value]

    def set_feed_cursor(self, game_id: int, cursor: int) -> None:
        with self._tx() as c:
            c.execute("UPDATE games SET feed_cursor = ? WHERE id = ?", (max(0, int(cursor)), game_id))

    def raise_feed_cap(self, game_id: int, extra: int) -> int:
        """Allow ``extra`` more requests for this game; returns the total allowed beyond the default cap."""
        with self._tx() as c:
            c.execute("UPDATE games SET feed_cap_extra = feed_cap_extra + ? WHERE id = ?", (int(extra), game_id))
            row = c.execute("SELECT feed_cap_extra FROM games WHERE id = ?", (game_id,)).fetchone()
        return row["feed_cap_extra"] if row else 0

    def count_feed_request(
        self, game_id: int | None, day: str, *, spent: bool = True,
        plan_remaining: int | None = None, plan_limit: int | None = None, now: float | None = None,
    ) -> dict[str, int | None]:
        """Record one live-data request: for the game, for the UTC ``day``, and the plan's allowance if it was shown.

        ``spent=False`` (the recorded demo game) counts it for the game's meter only. Returns the new totals.
        """
        now = time.time() if now is None else now
        with self._tx() as c:
            if game_id is not None:
                c.execute("UPDATE games SET feed_requests = feed_requests + 1 WHERE id = ?", (game_id,))
            if spent:
                c.execute(
                    """INSERT INTO feed_usage (day, requests, plan_remaining, plan_limit, plan_seen_at)
                       VALUES (?, 1, ?, ?, ?)
                       ON CONFLICT (day) DO UPDATE SET
                           requests = requests + 1,
                           plan_remaining = COALESCE(excluded.plan_remaining, plan_remaining),
                           plan_limit = COALESCE(excluded.plan_limit, plan_limit),
                           plan_seen_at = CASE WHEN excluded.plan_remaining IS NOT NULL
                                               THEN excluded.plan_seen_at ELSE plan_seen_at END""",
                    (day, plan_remaining, plan_limit, now if plan_remaining is not None else None),
                )
        return self.feed_usage(day, game_id, now=now)

    def feed_usage(self, day: str, game_id: int | None = None, now: float | None = None) -> dict[str, int | None]:
        """Requests made today (UTC), for ``game_id``, and the plan's allowance as last seen (None when never seen or
        older than a day: a plan changes, and a stale number must not block live data forever)."""
        now = time.time() if now is None else now
        today = self._one("SELECT requests FROM feed_usage WHERE day = ?", (day,))
        plan = self._one("SELECT plan_remaining, plan_limit FROM feed_usage WHERE plan_remaining IS NOT NULL "
                         "AND plan_seen_at > ? ORDER BY plan_seen_at DESC LIMIT 1", (now - PLAN_INFO_MAX_AGE,)) or {}
        game = self._one("SELECT feed_requests, feed_cap_extra FROM games WHERE id = ?", (game_id,)) if game_id else None
        return {"today": today["requests"] if today else 0,
                "game": game["feed_requests"] if game else 0,
                "cap_extra": game["feed_cap_extra"] if game else 0,
                "plan_remaining": plan.get("plan_remaining"), "plan_limit": plan.get("plan_limit")}

    def log_feed(
        self, game_id: int | None, kind: str, data: dict[str, Any] | None = None,
        play_id: int | None = None, feed_index: int | None = None, ts: float | None = None,
    ) -> None:
        """Append one row to the live-data recorder; each game keeps its most recent ``FEED_LOG_KEEP`` rows."""
        row = (game_id, ts if ts is not None else time.time(), kind, play_id, feed_index,
               json.dumps(data or {}, separators=(",", ":"), default=str))
        with self._tx() as c:
            c.execute("INSERT INTO feed_log (game_id, ts, kind, play_id, feed_index, data) VALUES (?, ?, ?, ?, ?, ?)", row)
            self._feed_log_writes += 1
            if self._feed_log_writes % FEED_LOG_PRUNE_EVERY == 0:
                c.execute(
                    """DELETE FROM feed_log WHERE game_id IS ? AND id <= (
                           SELECT id FROM feed_log WHERE game_id IS ? ORDER BY id DESC LIMIT 1 OFFSET ?)""",
                    (game_id, game_id, FEED_LOG_KEEP),
                )

    def feed_log(self, game_id: int | None) -> list[dict[str, Any]]:
        """The recorder rows for a game, oldest first, with ``data`` decoded."""
        rows = self._all("SELECT id, ts, kind, play_id, feed_index, data FROM feed_log WHERE game_id IS ? ORDER BY id",
                         (game_id,))
        for row in rows:
            try:
                row["data"] = json.loads(row["data"] or "{}")
            except ValueError:
                row["data"] = {}
        return rows

    # -- predictions ------------------------------------------------------ #

    def submit_prediction(
        self,
        user_id: int,
        play_id: int,
        play_type: PlayType,
        direction: Direction,
        yardage: Yardage,
        grace_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Create or update a user's pick while the play is OPEN and the timer runs."""
        play_type, direction, yardage = PlayType(play_type), Direction(direction), Yardage(yardage)
        now = time.time()
        with self._tx() as c:
            play = _row(c.execute("SELECT * FROM plays WHERE id = ?", (play_id,)).fetchone())
            if not play:
                raise GameError("Unknown play.", 404)
            if play["state"] != PlayState.OPEN or now > play["locks_at"] + grace_seconds:
                raise GameError("Too late — predictions for this play are locked.", 409)
            if not c.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise GameError("Your account was deleted.", 401)  # deleted mid-session
            c.execute(
                """INSERT INTO predictions (user_id, play_id, play_type, direction, yardage, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, play_id) DO UPDATE SET
                       play_type = excluded.play_type,
                       direction = excluded.direction,
                       yardage = excluded.yardage,
                       submitted_at = excluded.submitted_at""",
                (user_id, play_id, play_type, direction, yardage, now),
            )
        return {"play_id": play_id, "play_type": str(play_type), "direction": str(direction),
                "yardage": str(yardage), "points_earned": None}

    def predictions_for_play(self, play_id: int) -> dict[int, dict[str, Any]]:
        rows = self._all(
            """SELECT user_id, play_id, play_type, direction, yardage, points_earned
               FROM predictions WHERE play_id = ?""",
            (play_id,),
        )
        return {r["user_id"]: r for r in rows}

    def pick_stats(self, play_id: int) -> dict[str, int]:
        """How everyone picked a play, plus ``exact`` (all three right) and ``scored`` (any points)."""
        row = self._one(
            f"""SELECT COUNT(pr.id) AS total,
                       COALESCE(SUM(pr.play_type = 'RUN'), 0)     AS "RUN",
                       COALESCE(SUM(pr.play_type = 'PASS'), 0)    AS "PASS",
                       COALESCE(SUM(pr.direction = 'LEFT'), 0)    AS "LEFT",
                       COALESCE(SUM(pr.direction = 'MIDDLE'), 0)  AS "MIDDLE",
                       COALESCE(SUM(pr.direction = 'RIGHT'), 0)   AS "RIGHT",
                       COALESCE(SUM(pr.yardage = 'SHORT'), 0)     AS "SHORT",
                       COALESCE(SUM(pr.yardage = 'MEDIUM'), 0)    AS "MEDIUM",
                       COALESCE(SUM(pr.yardage = 'LONG'), 0)      AS "LONG",
                       COALESCE(SUM({_EXACT_SQL}), 0)             AS exact,
                       COALESCE(SUM(pr.points_earned > 0), 0)     AS scored
                FROM predictions pr JOIN plays pl ON pl.id = pr.play_id
                WHERE pr.play_id = ?""",
            (play_id,),
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
