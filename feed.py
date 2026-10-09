"""Live data for a game: follow a real NFL game with Tank01's play-by-play, and suggest each play's result.

How it works (the "smart method"):

* The host runs the game as always: open a play, it locks (host or the 15 s timer), the play happens.
* While a play is LOCKED and waiting for its result, ``LiveFeed`` polls Tank01 (and only then: zero requests
  between plays). The first entry that is a scrimmage play belongs to that play. ``playparse.classify`` reads it.
* A clean run or pass becomes a *suggestion*; after a short grace period it is scored automatically (the host can
  Hold, change, or score it at once). A sack, a quarterback scramble or a penalty's "No Play" is voided (nobody scores). Anything else odd (interception, accepted penalty, no direction) waits for the host.
* The host can always score by hand exactly as before. If the feed is wrong, slow, capped or down, nothing blocks.

``LiveFeed`` keeps the game's state in memory (rebuilt from the database after a restart) and asks its host, the
``GameController``, to apply results. All timing goes through an injectable ``clock`` so tests run in milliseconds.

Pieces: ``Tank01Client`` (the real HTTP client), ``DemoFeed`` (a recorded game for rehearsal, no requests),
``LiveFeed`` (poller, matcher, suggestions, budget guards and recorder).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from models import GameError, GameStatus, PlayState, Store, resolve_yardage
from playparse import Parsed, classify, next_down_and_distance, parse_down_and_distance
from teams import feed_matchup, feed_team

log = logging.getLogger("pick_the_play.feed")

DEFAULT_BASE_URL = "https://tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"
DEMO_ID = "demo"
DEMO_FILE = Path(__file__).resolve().parent / "demo" / "box_CAR_WSH_20241020.json"
FEED_ID_RE = re.compile(r"^(?:demo|\d{8}_[A-Z]{2,4}@[A-Z]{2,4})$")
DATE_RE = re.compile(r"^\d{8}$")
MAX_RESPONSE_BYTES = 4_000_000

# Polling schedule after a play locks: (seconds since lock below which the interval applies, interval).
# The first check comes after ``first_delay``; the fast interval is a setting.
INTERVALS = ((60.0, None), (180.0, 10.0), (600.0, 20.0))  # None: the fast interval
SLOW_INTERVAL = 30.0
QUIET_AFTER = 600.0
NOT_STARTED_RETRY = 120.0
FIRST_DELAY_MIN, FIRST_DELAY_MAX = 6.0, 45.0   # a slow feed (lag 45 s+) must not be polled from second 25 on
BACKOFF_MAX = 30.0
ERROR_AFTER, AUTOPAUSE_AFTER = 5, 10
VERIFY_TTL = 150.0   # a play the host scored first waits this long for its feed entry
TWIN_TTL = 150.0
SCHEDULE_CACHE_TTL = 300.0
MAX_AUTO_OPEN_DOWN = 3
LAG_SAMPLES = 5
BASELINE_GAP = 2     # the feed this many plays ahead of the app game on first read means a late start
DISTRUST_FEED_CHANGED = "The feed's list of plays changed earlier: check that this is the right play"
DISTRUST_RESTARTED = "Live data was connected or restarted mid-game: check that this is the right play"
DISTRUST_LATE_START = "Live data started late: check that this is the game's first play"
DISTRUST_CAUGHT_UP = "Caught up to the newest play the feed has: check that it is the play you opened"

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


@dataclass
class FeedResult:
    """What a request to the feed produced. ``error_kind`` is one of network, http, auth, quota, not_started,
    bad_json, shape (None when ``ok``). ``error_text`` never contains the API key."""

    ok: bool = False
    http_status: int | None = None
    body: Any = None
    remaining: int | None = None
    limit: int | None = None
    elapsed_ms: int = 0
    error_kind: str | None = None
    error_text: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the API key header must only ever go to the configured host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _int_header(headers: Any, name: str) -> int | None:
    try:
        return int(str(headers.get(name)).strip())
    except (TypeError, ValueError, AttributeError):
        return None


class Tank01Client:
    """Tank01 (RapidAPI) over plain ``urllib`` in a worker thread. The key is only ever sent as a header."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0) -> None:
        self._key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)

    def __repr__(self) -> str:
        return f"Tank01Client(base_url={self.base_url!r}, key={'set' if self._key else 'missing'})"

    @property
    def available(self) -> bool:
        return bool(self._key)

    async def box_score(self, game_id: str) -> FeedResult:
        """The game's box score with play-by-play (one request)."""
        params = {"gameID": game_id, "playByPlay": "true", "fantasyPoints": "false"}
        result = await asyncio.to_thread(self._fetch, "getNFLBoxScore", params)
        if result.ok and isinstance(result.body, dict):
            body = result.body
            if "allPlayByPlay" not in body and "error" in body:  # the game has not started (or has no data yet)
                result.ok, result.error_kind = False, "not_started"
                result.error_text = self._clean(str(body.get("error")))
                result.body = {"error": result.error_text}   # never keep the service's own words: they might echo the key
            elif "allPlayByPlay" in body and not isinstance(body["allPlayByPlay"], list):
                result.ok, result.error_kind, result.error_text = False, "shape", "The feed's play list was not a list."
        elif result.ok:
            result.ok, result.error_kind, result.error_text = False, "shape", "The feed's answer was not what we expected."
        return result

    async def games_for_date(self, date: str) -> FeedResult:
        """The day's schedule (one request): ``date`` is YYYYMMDD."""
        result = await asyncio.to_thread(self._fetch, "getNFLGamesForDate", {"gameDate": date})
        if result.ok and not isinstance(result.body, list):
            if isinstance(result.body, dict) and "error" in result.body:
                result.body = []   # a day with no games can be answered with an error text
            else:
                result.ok, result.error_kind = False, "shape"
                result.error_text = "The feed's schedule was not what we expected."
        return result

    # -- internals -------------------------------------------------------- #

    def _clean(self, text: str) -> str:
        text = " ".join(str(text).split())
        if self._key:
            text = text.replace(self._key, "[key]")
        return text[:200]

    def _opener(self) -> urllib.request.OpenerDirector:
        host = urllib.parse.urlsplit(self.base_url).hostname or ""
        handlers: list[Any] = [_NoRedirect()]
        if host in ("localhost", "127.0.0.1", "::1"):
            handlers.append(urllib.request.ProxyHandler({}))  # a local test server is never behind a proxy
        return urllib.request.build_opener(*handlers)

    def _fetch(self, path: str, params: dict[str, str]) -> FeedResult:
        """One blocking request. Never raises."""
        started = time.monotonic()
        url = f"{self.base_url}/{path}?{urllib.parse.urlencode(params)}"
        headers = {"x-rapidapi-host": urllib.parse.urlsplit(self.base_url).netloc, "x-rapidapi-key": self._key,
                   "accept": "application/json", "accept-encoding": "identity", "user-agent": "PickThePlay/1.0"}
        status: int | None = None
        resp_headers: Any = {}
        raw = b""
        try:
            with self._opener().open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as resp:
                status, resp_headers, raw = resp.status, resp.headers, resp.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:
            status, resp_headers = exc.code, exc.headers
            try:
                raw = exc.read(MAX_RESPONSE_BYTES)
            except Exception:  # noqa: BLE001
                raw = b""
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            kind = "timed out" if isinstance(reason, TimeoutError) or "timed out" in str(reason) else "could not connect"
            return FeedResult(http_status=None, elapsed_ms=_ms(started), error_kind="network",
                              error_text=self._clean(f"Tank01 {kind}: {type(reason).__name__}"))
        except Exception as exc:  # noqa: BLE001 - nothing may escape the worker thread
            return FeedResult(elapsed_ms=_ms(started), error_kind="network",
                              error_text=self._clean(f"Tank01 request failed: {type(exc).__name__}"))
        result = FeedResult(http_status=status, elapsed_ms=_ms(started),
                            remaining=_int_header(resp_headers, "x-ratelimit-requests-remaining"),
                            limit=_int_header(resp_headers, "x-ratelimit-requests-limit"))
        if "gzip" in str(getattr(resp_headers, "get", lambda *_: "")("content-encoding") or "").lower():
            try:   # asked for plain text, but a proxy in front of the service may compress anyway
                raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, MAX_RESPONSE_BYTES)
            except zlib.error:
                raw = b""
        self._interpret(result, status, raw)
        return result

    def _interpret(self, result: FeedResult, status: int | None, raw: bytes) -> None:
        text = raw.decode("utf-8", "replace")
        payload: Any = None
        try:
            payload = json.loads(text) if text.strip() else None
        except ValueError:
            payload = None
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("message") or (payload.get("body") if isinstance(payload.get("body"), str) else "")
                          or payload.get("error") or "")
        lowered = message.lower()
        if status in (401, 403):
            result.error_kind, result.error_text = "auth", "Tank01 rejected the API key."
        elif status == 429 or "quota" in lowered or "exceeded" in lowered or "rate limit" in lowered:
            result.error_kind, result.error_text = "quota", self._clean(message) or "Tank01 says the request limit is used up."
        elif status is None or not 200 <= status < 300:
            result.error_kind, result.error_text = "http", f"Tank01 answered with an error (HTTP {status})."
        elif payload is None:
            result.error_kind, result.error_text = "bad_json", "Tank01 sent something that was not JSON."
        elif not isinstance(payload, dict) or "body" not in payload:
            result.error_kind, result.error_text = "shape", "Tank01's answer was not what we expected."
        else:
            inner = payload.get("statusCode")
            if isinstance(inner, int) and inner not in (200,):
                result.error_kind = "auth" if inner in (401, 403) else "quota" if inner == 429 else "http"
                result.error_text = "Tank01 rejected the API key." if result.error_kind == "auth" else \
                    f"Tank01 answered with an error (status {inner})."
            else:
                result.ok, result.body = True, payload["body"]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# --------------------------------------------------------------------------- #
# Demo feed
# --------------------------------------------------------------------------- #


class DemoFeed:
    """A recorded game for rehearsal: the host practices the whole flow without spending a request.

    Looks like the real client. When a play is locked, ``lag`` seconds later every entry up to and including
    the next scrimmage entry becomes visible (so kickoffs, punts and timeouts are skipped automatically).
    """

    available = True

    def __init__(self, lag: float = 12.0, clock: Callable[[], float] = time.time) -> None:
        raw = json.loads(DEMO_FILE.read_text(encoding="utf-8"))["body"]
        self.meta = {k: v for k, v in raw.items() if k != "allPlayByPlay"}
        self.entries: list[dict[str, Any]] = list(raw["allPlayByPlay"])
        self.lag, self.clock = float(lag), clock
        self.revealed = 0
        self.requests = 0
        self._due: list[float] = []

    def on_play_locked(self) -> None:
        """A play locked: its entry becomes visible after the demo lag."""
        self._due.append(self.clock() + self.lag)

    def on_play_cancelled(self) -> None:
        """The host voided the play before its entry appeared: the recorded play stays for the next one."""
        if self._due:
            self._due.pop()

    def _reveal_due(self) -> None:
        now = self.clock()
        while self._due and self._due[0] <= now:
            self._due.pop(0)
            i = self.revealed
            while i < len(self.entries) and classify(self.entries[i]).kind == "skip":
                i += 1
            self.revealed = min(len(self.entries), i + 1)
            if i >= len(self.entries) - 1 or all(classify(e).kind == "skip" for e in self.entries[self.revealed:]):
                self.revealed = len(self.entries)  # nothing but the end-of-game marker is left

    async def box_score(self, game_id: str) -> FeedResult:
        self.requests += 1
        self._reveal_due()
        done = self.revealed >= len(self.entries)
        entries = self.entries[: self.revealed]
        body = dict(self.meta)
        body.update(gameStatus="Completed" if done else "In Progress", gameStatusCode="2" if done else "1",
                    currentPeriod="Final" if done else (entries[-1]["playPeriod"] if entries else "Q1"),
                    allPlayByPlay=entries)
        return FeedResult(ok=True, http_status=200, body=body, elapsed_ms=1)

    async def games_for_date(self, date: str) -> FeedResult:
        self.requests += 1
        return FeedResult(ok=True, http_status=200, body=[{"gameID": DEMO_ID, "away": "CAR", "home": "WSH"}], elapsed_ms=1)


# --------------------------------------------------------------------------- #
# State held while following a game
# --------------------------------------------------------------------------- #


@dataclass
class Pending:
    """An app play that is waiting for (or has been matched to) a feed entry."""

    play_id: int
    number: int
    down: int | None
    distance: str | None
    locked_at: float
    resolved: bool = False                       # the host scored it first: verification only
    resolved_at: float = 0.0
    scored: tuple[str, str, str | None] | None = None   # (type, direction, yardage) as scored
    checks: int = 0
    next_check: float = 0.0
    lag_valid: bool = True
    waited_for_start: bool = False               # locked while the feed had no plays yet (game not started, or an empty list)


@dataclass
class Suggestion:
    """The feed's answer for the waiting play, shown to the host."""

    play_id: int
    entry_index: int
    entry: dict[str, Any]
    parsed: Parsed
    status: str                                   # ready | review | void | held
    warning: str | None = None
    auto_at: float | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.parsed.kind


def ordinal_dd(down: int | None, to_go: str | None) -> str:
    """"2nd & 3", "1st & Goal"; either part may be unknown."""
    parts = []
    if down is not None:
        parts.append(ORDINAL.get(down, f"{down}th"))
    if to_go:
        parts.append(("Goal" if to_go.lower() == "goal" else to_go))
        return " & ".join(parts) if len(parts) == 2 else f"{parts[0]} to go"
    return f"{parts[0]} down" if parts else "unknown"


def _norm_distance(value: str | None) -> str | None:
    text = (value or "").strip().lower().replace(" ", "")
    if not text:
        return None
    return str(int(text)) if text.isdigit() else text


def result_label(play_type: str | None, direction: str | None, yardage: str | None) -> str:
    return " - ".join(x or "?" for x in (play_type, direction, yardage))


class FeedHost(Protocol):
    """What ``LiveFeed`` needs from the ``GameController``."""

    def feed_resolve(self, play_id: int, play_type: str, direction: str, yardage: str | None, yards: int | None,
                     feed_text: str) -> dict[str, Any]: ...

    def feed_void(self, play_id: int, feed_text: str) -> dict[str, Any]: ...

    async def feed_broadcast(self, event: str) -> None: ...

    async def feed_open(self, down: int | None, distance: str | None) -> None: ...

    def feed_changed(self) -> None: ...


# --------------------------------------------------------------------------- #
# The live feed
# --------------------------------------------------------------------------- #


class LiveFeed:
    """Follows the current game's feed: polls while a locked play waits, matches entries, suggests results."""

    def __init__(
        self,
        store: Store,
        host: FeedHost,
        settings: Any,
        *,
        client: Tank01Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        autorun: bool = True,
    ) -> None:
        self.store, self.host, self.settings = store, host, settings
        self.clock, self.sleep, self.autorun = clock, sleep, autorun
        self.tank01 = client or Tank01Client(settings.tank01_api_key, settings.tank01_base_url, settings.tank01_timeout)
        self._demo: DemoFeed | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._poll_lock = asyncio.Lock()
        self._gen = 0
        self._schedule_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._reset(None)

    # -- configuration --------------------------------------------------- #

    @property
    def available(self) -> bool:
        """Live data from Tank01 is possible (a key is set). The recorded demo game always is."""
        return self.tank01.available

    def check_linkable(self, feed_game_id: str | None) -> str | None:
        """Validate a feed game id before using it (raises ``GameError``); returns it cleaned, or None."""
        if feed_game_id in (None, ""):
            return None
        feed_game_id = str(feed_game_id).strip()
        if not FEED_ID_RE.match(feed_game_id):
            raise GameError("That is not a game the live feed knows. Pick one from today's list.", 422)
        if feed_game_id != DEMO_ID and not self.available:
            raise GameError("Live data is not set up on this server (no Tank01 key). "
                            "Use the practice game, or score by hand.", 409)
        return feed_game_id

    def _reset(self, game: dict[str, Any] | None) -> None:
        """Forget everything about the previous game and load this one's settings."""
        self._gen += 1
        self.game_id: int | None = game["id"] if game else None
        self.feed_id: str | None = (game or {}).get("feed_game_id") or None
        self.source: str | None = None if not self.feed_id else ("demo" if self.feed_id == DEMO_ID else "tank01")
        self.auto_score = bool(game["feed_auto_score"]) if game else True
        self.auto_open = bool(game["feed_auto_open"]) if game else False
        self.paused = bool(game["feed_paused"]) if game else False
        self.cursor = int(game["feed_cursor"]) if game else 0
        self.cap_extra = int(game["feed_cap_extra"]) if game else 0
        self.game_final = bool(game and game["status"] == GameStatus.FINAL)
        self.entries: list[dict[str, Any]] = []
        self.pending: list[Pending] = []
        self.suggestion: Suggestion | None = None
        self.disagreement: dict[str, Any] | None = None
        self.next_down: dict[str, Any] | None = None
        self.auto_open_at: float | None = None
        self.last_scored: dict[str, Any] | None = None
        self.lags: list[float] = []
        self._seen_at: dict[int, float] = {}
        self._texts: list[str] = []
        self._scored: dict[int, tuple[int, tuple[str | None, str | None, str | None]]] = {}  # entry -> (play, result)
        self._distrust: str | None = None   # why the next suggestion must go to the host instead of the timer
        self._horizon = 0                   # entries below this were already played when the feed first showed any
        self._horizon_play: int | None = None   # the host's first play, which was matched to the first of them
        self._twins: list[tuple[float, int | None, str | None]] = []
        self._fail_streak = self._read_failures = 0
        self._auth_error = False
        self._quota_hit = False
        self._not_started: str | None = None
        self._complete = False
        self._auto_paused = False
        self._baselined = self.cursor > 0
        self._notice: str | None = None
        self._persisted_cursor = self.cursor
        self._req_game = self._req_day = 0
        self._plan_remaining: int | None = None
        self._plan_limit: int | None = None
        self._day_key = ""
        self._polling = False
        self._demo = None
        self._box_meta: dict[str, Any] | None = None
        self._load_usage()

    def attach(self, game: dict[str, Any] | None) -> None:
        """Start following ``game`` (a new game, or the current one after a restart). Rebuilds what was in flight."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._reset(game)
        if not game or not self.feed_id:
            self.host.feed_changed()
            return
        if self.source == "demo":
            self._demo = DemoFeed(getattr(self.settings, "tank01_demo_lag", 12.0), self.clock)
            self._demo.revealed = min(self.cursor, len(self._demo.entries))   # after a restart: carry on from the same play
        elif not self.available:
            self._auth_error = True   # linked to a real game, but this server has no key (it was removed?)
        now = self.clock()
        first = self._first_delay()
        for play in self.store.locked_plays(game["id"]):
            self.pending.append(Pending(play["id"], play["play_number"], play["down"], play["distance"], now,
                                        next_check=now + first, lag_valid=False))
            if self._demo:
                self._demo.on_play_locked()
        if self.store.played_count(game["id"]):
            # A restart (or a link made mid-game) has lost what only lived in memory: plays the host scored before their
            # entries arrived, and voided plays whose "No Play" entry is still to come. Either could shift the next
            # entry onto the wrong play, so the first suggestion goes to the host instead of the timer.
            self._distrust = DISTRUST_RESTARTED
        self._log("attach", {"feed_game_id": self.feed_id, "source": self.source, "cursor": self.cursor,
                             "pending": [p.play_id for p in self.pending]})
        if self.autorun:
            self._task = asyncio.get_running_loop().create_task(self.run())
        self._wake.set()
        self.host.feed_changed()

    async def stop(self) -> None:
        """Stop the polling task (shutdown)."""
        task, self._task = self._task, None
        self._gen += 1
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @property
    def linked(self) -> bool:
        return self.game_id is not None and self.feed_id is not None

    @property
    def _client(self) -> Tank01Client | DemoFeed:
        return self._demo if self.source == "demo" and self._demo else self.tank01

    # -- usage and caps -------------------------------------------------- #

    def _today(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")

    def _load_usage(self) -> None:
        """Counters survive restarts (they live in the database), so a restart cannot reset a cap."""
        self._day_key = self._today()
        usage = self.store.feed_usage(self._day_key, self.game_id, now=self.clock())
        self._req_day, self._req_game = int(usage["today"] or 0), int(usage["game"] or 0)
        self._plan_remaining, self._plan_limit = usage["plan_remaining"], usage["plan_limit"]

    def _refresh_day(self) -> None:
        if self._day_key != self._today():
            self._day_key = self._today()
            self._req_day = int(self.store.feed_usage(self._day_key)["today"] or 0)

    @property
    def game_cap(self) -> int:
        return int(self.settings.tank01_max_requests_per_game) + self.cap_extra

    def _blocked(self) -> tuple[str, str] | None:
        """(code, plain-English message) when no more real requests may be made now, else None."""
        if self.source != "tank01":
            return None  # the recorded demo game costs nothing
        s = self.settings
        self._refresh_day()
        if self._quota_hit:
            return "quota", "Tank01 says the plan's request limit is used up. Score by hand."
        if (self._plan_remaining is not None and self._plan_remaining <= s.tank01_reserve
                and not s.tank01_allow_overage):
            return "plan", (f"The plan has only {self._plan_remaining} requests left, so live data stopped to keep "
                            f"{s.tank01_reserve} spare. Score by hand.")
        if self._req_game >= self.game_cap:
            return "game", (f"This game has used its {self.game_cap} requests. Score by hand, "
                            "or tap Allow 100 more.")
        if self._req_day >= s.tank01_max_requests_per_day:
            return "day", f"Today's limit of {s.tank01_max_requests_per_day} requests is used up. Score by hand."
        return None

    def _count(self, result: FeedResult, *, spent: bool = True, game_id: int | None = None) -> None:
        """Record one real request (every attempt counts) and the plan allowance the headers showed.
        ``game_id`` is the game it was made for (None: the schedule, which belongs to no game)."""
        self._refresh_day()
        if result.remaining is not None:
            self._plan_remaining = result.remaining
        if result.limit is not None:
            self._plan_limit = result.limit
        try:
            usage = self.store.count_feed_request(game_id, self._day_key, spent=spent, plan_remaining=result.remaining,
                                                  plan_limit=result.limit, now=self.clock())
            if game_id is not None and game_id == self.game_id:
                self._req_game = int(usage["game"] or 0)
            if spent:
                self._req_day = int(usage["today"] or 0)
        except Exception:  # noqa: BLE001 - counting must never break the game
            log.exception("could not record a live-data request")
            self._req_game += int(game_id is not None and game_id == self.game_id)
            self._req_day += int(spent)

    # -- recorder --------------------------------------------------------- #

    def _log(self, kind: str, data: dict[str, Any] | None = None, play_id: int | None = None,
             feed_index: int | None = None, game_id: int | None = -1) -> None:
        try:
            self.store.log_feed(self.game_id if game_id == -1 else game_id, kind, data, play_id, feed_index,
                                ts=self.clock())
        except Exception:  # noqa: BLE001 - the recorder must never break the game
            log.exception("could not write the live-data log")

    def _changed(self) -> None:
        self.host.feed_changed()
        self._wake.set()

    # -- the schedule ------------------------------------------------------ #

    def _first_delay(self) -> float:
        """Seconds after a lock until the first check: 0.8 x the typical lag, kept sensible."""
        base = float(self.settings.tank01_first_delay)
        recent = self.lags[-LAG_SAMPLES:]
        if not recent:
            return base
        lo, hi = min(FIRST_DELAY_MIN, base), max(FIRST_DELAY_MAX, base)
        return min(hi, max(lo, 0.8 * statistics.median(recent)))

    def _interval_after(self, since_lock: float) -> float:
        for limit, interval in INTERVALS:
            if since_lock < limit:
                return interval if interval is not None else float(self.settings.tank01_fast_interval)
        return SLOW_INTERVAL

    def _waiting(self) -> Pending | None:
        """The locked play that is waiting for its feed entry (None while a suggestion is up or nothing waits)."""
        if self.suggestion is not None:
            return None
        return next((p for p in self.pending if not p.resolved), None)

    def _want_poll(self) -> bool:
        return (self.linked and not self.paused and not self.game_final and not self._auth_error
                and not self._complete and self._blocked() is None and self._waiting() is not None)

    # -- main loop --------------------------------------------------------- #

    async def run(self) -> None:
        """The polling task: do what is due, sleep until the next thing is due (or something changes)."""
        gen = self._gen
        while gen == self._gen:
            try:
                delay = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("live data loop error")
                delay = 5.0
            await self._nap(delay)

    async def _nap(self, delay: float | None) -> None:
        tasks: set[asyncio.Future] = {asyncio.ensure_future(self._wake.wait())}
        if delay is not None:
            tasks.add(asyncio.ensure_future(self.sleep(max(0.02, delay))))
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    async def tick(self) -> float | None:
        """Do everything that is due now; return seconds until the next thing is due (None: nothing scheduled)."""
        self._wake.clear()
        if not self.linked:
            return None
        gen = self._gen
        now = self.clock()
        sug = self.suggestion
        if sug and sug.auto_at is not None and now >= sug.auto_at and not self.paused:
            await self._auto_apply(sug)
            now = self.clock()
        if gen != self._gen:
            return None
        if self.auto_open_at is not None and now >= self.auto_open_at:
            await self._auto_open()
        if self._want_poll():
            waiting = self._waiting()
            if waiting and now >= waiting.next_check:
                await self._poll()
        return self._time_to_next()

    def _time_to_next(self) -> float | None:
        now = self.clock()
        due: list[float] = []
        if self.suggestion and self.suggestion.auto_at is not None and not self.paused:
            due.append(self.suggestion.auto_at)
        if self.auto_open_at is not None:
            due.append(self.auto_open_at)
        if self._want_poll() and (waiting := self._waiting()):
            due.append(waiting.next_check)
        return max(0.0, min(due) - now) if due else None

    # -- polling ---------------------------------------------------------- #

    async def check_now(self) -> None:
        """The host asked for a check right now (commercials, halftime, injuries ...)."""
        if not self.linked:
            raise GameError("Live data is not connected to this game.", 409)
        if blocked := self._blocked():
            raise GameError(blocked[1], 409)
        await self._poll(manual=True)

    async def _poll(self, manual: bool = False) -> None:
        """One request, then match whatever it showed."""
        if self._polling or self._poll_lock.locked():
            return
        async with self._poll_lock:
            self._polling = True
            gen, game_id = self._gen, self.game_id
            try:
                if not self.linked or self._blocked() is not None:
                    return
                started = self.clock()
                waiting = self._waiting()
                try:
                    result = await self._client.box_score(self.feed_id or "")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a client bug must not stop the game
                    result = FeedResult(error_kind="network", error_text=f"Live data failed: {type(exc).__name__}")
                self._count(result, spent=self.source == "tank01", game_id=game_id)   # it was made, whatever happened next
                if gen != self._gen:
                    return  # the game changed while we waited: this answer is not ours any more
                try:
                    self._after_poll(result, waiting, started, manual)
                    self._read_failures = 0
                except Exception:  # noqa: BLE001 - a bug in reading one answer must not turn into a request every few seconds
                    log.exception("live data could not read the feed's answer")
                    self._read_failures += 1
                    for p in self.pending:
                        if not p.resolved:
                            p.next_check = self.clock() + min(BACKOFF_MAX, float(self.settings.tank01_fast_interval)
                                                              * (2 ** self._read_failures))
            finally:
                self._polling = False
                self._changed()

    def _after_poll(self, result: FeedResult, waiting: Pending | None, started: float, manual: bool) -> None:
        now = self.clock()
        body = result.body if isinstance(result.body, dict) else {}
        entries = body.get("allPlayByPlay") if result.ok else None
        info = {"ok": result.ok, "http": result.http_status, "ms": result.elapsed_ms,
                "n_entries": len(entries) if isinstance(entries, list) else None,
                "game_status": body.get("gameStatus"), "status_code": body.get("gameStatusCode"),
                "period": body.get("currentPeriod"), "clock": body.get("gameClock"),
                "remaining": result.remaining, "limit": result.limit, "error_kind": result.error_kind,
                "error": result.error_text or None, "manual": manual,
                "check": (waiting.checks + 1) if waiting else None,
                "requests": self._req_game}
        self._log("poll", info, play_id=waiting.play_id if waiting else None)
        log.info("feed poll game=%s ok=%s http=%s ms=%s entries=%s remaining=%s kind=%s", self.game_id, result.ok,
                 result.http_status, result.elapsed_ms, info["n_entries"], result.remaining, result.error_kind)
        if waiting:
            waiting.checks += 1

        if result.ok:
            self._fail_streak, self._auth_error, self._not_started = 0, False, None
            self._quota_hit = False
            self._ingest(entries or [], body, now)
            if not self._baselined:   # still no plays at all: whatever is locked now was locked before the game began
                self._mark_waited_for_start()
            self._consume(catch_up=manual)
            if waiting and waiting.play_id in {p.play_id for p in self.pending if not p.resolved} \
                    and self.suggestion is None:
                waiting.next_check = now + self._interval_after(now - waiting.locked_at)
            return
        kind = result.error_kind
        if kind == "not_started":
            self._fail_streak = 0
            self._not_started = result.error_text or "The game has not started yet."
            dropped = [p.play_id for p in self.pending if p.resolved]
            self.pending = [p for p in self.pending if not p.resolved]   # nothing can have happened yet
            if dropped:
                self._log("verify_dropped", {"reason": "game not started", "plays": dropped})
            for p in self.pending:
                p.next_check = now + NOT_STARTED_RETRY
            self._mark_waited_for_start()
        elif kind == "auth":
            self._auth_error = True
            self._log("error", {"kind": kind, "text": result.error_text})
        elif kind == "quota" and not self._burst_limit(result):
            self._quota_hit = True
            if "quota" in result.error_text.lower() or result.remaining is None:
                self._plan_remaining = 0   # the service says the plan is used up whatever its headers claimed
            self._log("error", {"kind": kind, "text": result.error_text})
        else:  # network, http, bad_json, shape (or a short-lived rate limit): back off and keep trying
            self._fail_streak += 1
            delay = min(BACKOFF_MAX, float(self.settings.tank01_fast_interval) * (2 ** self._fail_streak))
            if self._fail_streak >= ERROR_AFTER:
                delay = BACKOFF_MAX
            for p in self.pending:
                if not p.resolved:
                    p.next_check = now + delay
            self._log("error", {"kind": kind, "text": result.error_text, "streak": self._fail_streak})
            if self._fail_streak >= AUTOPAUSE_AFTER and not self.paused:
                self._set_paused(True)
                self._auto_paused = True
                self._log("pause", {"auto": True, "reason": "too many failures"})

    def _burst_limit(self, result: FeedResult) -> bool:
        """A 429 that is about going too fast (the plan still has plenty left), not about the plan being used up."""
        return (result.http_status == 429 and "quota" not in result.error_text.lower()
                and (result.remaining is None or result.remaining > self.settings.tank01_reserve))

    # -- reading the feed ---------------------------------------------------- #

    def _ingest(self, entries: list[Any], body: dict[str, Any], now: float) -> None:
        """Take the feed's entries: note new ones (for the recorder), spot revisions, remember the status."""
        entries = [e if isinstance(e, dict) else {"play": e} for e in entries]
        if len(entries) < len(self._texts):
            if all(str(e.get("play")) == t for e, t in zip(entries, self._texts)):
                # Missing entries we have already read, the rest unchanged: a stale answer (a cache lagging behind,
                # a hiccup). The feed only grows, so keep what we know; re-reading would match old plays to new ones.
                self._log("stale", {"was": len(self._texts), "now": len(entries)})
                return
            self._log("revised", {"reason": "feed got shorter", "was": len(self._texts), "now": len(entries)})
            self._distrust = DISTRUST_FEED_CHANGED   # entries moved: the next suggestion goes to the host, never to the timer
            del self._texts[len(entries):]
            self.cursor = min(self.cursor, len(entries))
        for i in range(min(len(self._texts), len(entries))):
            text = str(entries[i].get("play"))
            if text != self._texts[i]:
                self._log("revised", {"index": i, "was": self._texts[i][:300], "now": text[:300],
                                      "consumed": i < self.cursor}, feed_index=i)
                self._texts[i] = text
                self._entry_revised(i, entries[i])
        waiting = next((p for p in self.pending if not p.resolved), None)
        for i in range(len(self._texts), len(entries)):
            entry = entries[i]
            parsed = classify(entry)
            self._seen_at[i] = now
            self._texts.append(str(entry.get("play")))
            self._log("entry", {"entry": entry, "kind": parsed.kind, "reason": parsed.reason, "type": parsed.play_type,
                                "direction": parsed.direction, "yards": parsed.yards, "flags": parsed.flags,
                                "since_lock": round(now - waiting.locked_at, 1) if waiting else None},
                      feed_index=i)
        self.entries = entries
        self._box_meta = {k: body.get(k) for k in ("home", "away", "teamIDHome", "teamIDAway")}
        code, status = str(body.get("gameStatusCode", "")), str(body.get("gameStatus", "")).lower()
        period = str(body.get("currentPeriod", "")).lower()
        self._complete = code == "2" and (period in ("final", "") or "final" in status or "complete" in status)
        if not self._baselined and entries:
            self._baseline(entries)

    def _entry_revised(self, idx: int, entry: dict[str, Any]) -> None:
        """The feed changed the text of an entry we had already seen. If it is the one on the host's screen, take it
        off the timer and let the host look again; if a play was already scored from it, say so when the result differs."""
        parsed = classify(entry)
        sug = self.suggestion
        if sug and sug.entry_index == idx:
            sug.entry, sug.parsed, sug.status, sug.auto_at = entry, parsed, "review", None
            sug.flags = [*parsed.flags, "The feed changed this play: check it"]
            return
        scored = self._scored.get(idx)
        if scored and parsed.kind == "play":
            feed = (parsed.play_type, parsed.direction, parsed.yardage)
            if feed != scored[1]:
                self.disagreement = {
                    "play_id": scored[0], "feed": result_label(*feed), "scored": result_label(*scored[1]),
                    "text": parsed.text,
                    "feed_result": {"play_type": parsed.play_type, "direction": parsed.direction, "yards": parsed.yards,
                                    "yardage": parsed.yardage}}

    def _mark_waited_for_start(self) -> None:
        """The feed has no plays at all yet: the play waiting now was locked before the game's first play."""
        for p in self.pending:
            if not p.resolved:
                p.waited_for_start = True

    def _baseline(self, entries: list[dict[str, Any]]) -> None:
        """The first time the feed is read: if it is already well ahead of the app game (linked mid-game, or the host
        started late), skip what has already happened instead of matching old plays to new ones.

        A play the host has locked meanwhile becomes a placeholder: the host scores it by hand, and the next entry
        that fits it (same down and distance) is treated as its own so the following plays stay in step.

        One case is not a late start: the host's first play was locked before the feed had a single play (the usual
        "open the first play shortly before the snap") and the feed only went live minutes later, showing several plays
        in one answer. That play is the game's first snap, so it keeps its place at the front of the queue and goes to
        the host as a suggestion to confirm; the plays that came after it are dropped once it is settled.
        """
        self._baselined = True
        if self.cursor:
            return
        played = self.store.played_count(self.game_id) if self.game_id else 0
        seen = sum(1 for e in entries if classify(e).kind != "skip")
        if seen - played < BASELINE_GAP:
            return
        now = self.clock()
        first = next((p for p in self.pending if not p.resolved and p.waited_for_start), None)
        if first is not None and played <= 1:
            self._horizon, self._horizon_play = len(entries), first.play_id
            self._distrust = DISTRUST_LATE_START
            self._log("baseline", {"entries": len(entries), "feed_plays": seen, "app_plays": played, "unmatched": [],
                                   "first_play": first.play_id})
            return
        self.cursor = len(entries)
        unmatched = [p.play_id for p in self.pending if not p.resolved]
        for p in self.pending:
            p.resolved, p.resolved_at = True, now
        hand = " Score it by hand (or void it), then open the next play and live data takes over." if unmatched else ""
        self._notice = f"Live data joined late, so it can't tell which play this one was.{hand}"
        self._log("baseline", {"entries": len(entries), "feed_plays": seen, "app_plays": played, "unmatched": unmatched})
        self._persist_cursor()

    def _skip_to_horizon(self, next_play_id: int) -> None:
        """The plays the feed already had on its first answer are older than anything the host opens after the first
        play was settled: leave them behind, so the next play is not matched to one of them."""
        if self._horizon and next_play_id != self._horizon_play:
            self.cursor = max(self.cursor, self._horizon)
            self._log("skipped", {"to": self.cursor, "reason": "plays that came before live data started"})
            self._horizon, self._horizon_play = 0, None
            self._persist_cursor()

    # -- matching ---------------------------------------------------------- #

    def _newest_play_index(self, start: int) -> int | None:
        """The index of the newest entry from ``start`` on that is not a skip (None if there is none)."""
        return next((i for i in range(len(self.entries) - 1, start - 1, -1)
                     if classify(self.entries[i]).kind != "skip"), None)

    def _consume(self, catch_up: bool = False) -> None:
        """Match unexamined entries to waiting plays, in order, until one produces a suggestion.

        ``catch_up`` (the host pressed Check now) means "get to the present": the oldest entries are no longer taken in
        order. A waiting play is matched to the newest play the feed has (for the host to confirm), and an old
        suggestion that is no longer the newest is dropped.
        """
        now = self.clock()
        self._twins = [t for t in self._twins if now - t[0] < TWIN_TTL]
        if catch_up and self.suggestion is not None:
            stale = self.suggestion
            if self._newest_play_index(stale.entry_index + 1) is not None:
                self.suggestion = None
                self.cursor = stale.entry_index + 1
                self._log("skip", {"reason": "caught up", "text": stale.parsed.text[:200]}, play_id=stale.play_id,
                          feed_index=stale.entry_index)
        newest_orphan: tuple[Any, Parsed] | None = None
        while self.cursor < len(self.entries) and self.suggestion is None:
            idx = self.cursor
            entry = self.entries[idx]
            parsed = classify(entry)
            if parsed.kind == "skip":
                self._log("skip", {"reason": parsed.reason, "text": parsed.text[:200]}, feed_index=idx)
                self.cursor += 1
                continue
            self._drop_stale_verifications(now)
            if not self.pending:
                if self._belongs_to_open_play(idx):
                    break   # the host's open play has just happened: its entry waits for the lock, it is not an orphan
                self._log("orphan", {"text": parsed.text[:200], "kind": parsed.kind}, feed_index=idx)
                if parsed.kind == "play":
                    newest_orphan = (entry, parsed)
                self.cursor += 1
                continue
            p = self.pending[0]
            if parsed.kind == "void" and self._absorb_twin(entry):
                self._log("void_twin", {"text": parsed.text[:200]}, feed_index=idx)
                self.cursor += 1
                continue
            newest = self._newest_play_index(idx + 1) if catch_up else None
            if p.resolved:
                if newest is not None:   # the feed is further on than this play: nothing left to compare it with
                    self._log("verify_dropped", {"reason": "caught up", "play": p.play_id}, play_id=p.play_id)
                    self.pending.pop(0)
                    continue
                if self._dd_conflict(p, entry):
                    self._log("verify_dropped", {"reason": "down and distance differ", "play": p.play_id}, play_id=p.play_id)
                    self.pending.pop(0)
                    continue
                self._verify(p, idx, entry, parsed)
                self.pending.pop(0)
                self.cursor += 1
                continue
            if newest is not None:   # the host is live: the older entries are plays they never opened
                self._log("caught_up", {"from": idx, "to": newest}, play_id=p.play_id, feed_index=newest)
                idx, entry, parsed = newest, self.entries[newest], classify(self.entries[newest])
                self._distrust = self._distrust or DISTRUST_CAUGHT_UP
            self._suggest(p, idx, entry, parsed)
            self.cursor = idx + 1
            break
        if newest_orphan is not None and not self.pending and self.game_id:
            latest = self.store.latest_play(self.game_id)
            nd = self._next_down_of(*newest_orphan)
            if nd and (latest is None or latest["state"] == PlayState.RESOLVED):
                self.next_down = nd   # the situation the feed ended on: the form follows the game after a catch-up
        self._persist_cursor()

    def _belongs_to_open_play(self, idx: int) -> bool:
        """With nothing locked, the newest scrimmage entry may be the result of the play the host has open but has not
        locked yet (a "Check now" tapped after the whistle). Throwing it away would shift every later play onto the
        wrong entry; older entries can only be plays nobody opened, so they are still orphans."""
        play = self.store.latest_play(self.game_id) if self.game_id else None
        if not play or play["state"] != PlayState.OPEN:
            return False
        return all(classify(e).kind == "skip" for e in self.entries[idx + 1:])

    def _drop_stale_verifications(self, now: float) -> None:
        stale = [p for p in self.pending if p.resolved and now - p.resolved_at > VERIFY_TTL]
        if stale:
            self.pending = [p for p in self.pending if p not in stale]
            self._log("verify_dropped", {"reason": "no feed entry in time", "plays": [p.play_id for p in stale]})

    def _absorb_twin(self, entry: dict[str, Any]) -> bool:
        """A "No Play" entry whose down and distance equals a play the host voided is that play's penalty."""
        feed = parse_down_and_distance(entry.get("downAndDistance"))
        if not feed:
            return False
        for twin in self._twins:
            _, down, dist = twin
            if down == feed["down"] and _norm_distance(dist) == _norm_distance(feed["to_go"]):
                self._twins.remove(twin)
                return True
        return False

    @staticmethod
    def _dd_conflict(p: Pending, entry: dict[str, Any]) -> str | None:
        """A warning when the feed's down and distance contradicts the window's (spot ignored), else None."""
        feed = parse_down_and_distance(entry.get("downAndDistance"))
        if not feed or (p.down is None and not (p.distance or "").strip()):
            return None
        app_dist = _norm_distance(p.distance)
        bad = (p.down is not None and p.down != feed["down"]) or (app_dist is not None and app_dist != _norm_distance(feed["to_go"]))
        if not bad:
            return None
        return (f"Feed shows {ordinal_dd(feed['down'], feed['to_go'])} but this play is "
                f"{ordinal_dd(p.down, (p.distance or '').strip() or None)}")

    @staticmethod
    def _dd_known_and_equal(p: Pending, entry: dict[str, Any]) -> bool:
        """The window has both a down and a distance and the feed's entry says the same (the spot is ignored)."""
        feed = parse_down_and_distance(entry.get("downAndDistance"))
        dist = _norm_distance(p.distance)
        return bool(feed and p.down == feed["down"] and dist is not None and dist == _norm_distance(feed["to_go"]))

    def _verify(self, p: Pending, idx: int, entry: dict[str, Any], parsed: Parsed) -> None:
        """The host scored this play before the feed showed it: compare, and tell the host if they differ."""
        scored = p.scored
        feed = (parsed.play_type, parsed.direction, parsed.yardage)
        agree = scored == feed if parsed.kind == "play" else None
        self._log("verify", {"agree": agree, "kind": parsed.kind, "scored": scored, "feed": feed}, play_id=p.play_id,
                  feed_index=idx)
        if scored is not None:
            self._scored[idx] = (p.play_id, scored)
        if parsed.kind == "play" and scored is not None and not agree:
            self.disagreement = {
                "play_id": p.play_id, "feed": result_label(*feed), "scored": result_label(*scored), "text": parsed.text,
                "feed_result": {"play_type": parsed.play_type, "direction": parsed.direction, "yards": parsed.yards,
                                "yardage": parsed.yardage},
            }

    def _suggest(self, p: Pending, idx: int, entry: dict[str, Any], parsed: Parsed) -> None:
        """Bind the entry to the waiting play and show the host the answer."""
        seen = self._seen_at.get(idx, self.clock())
        lag = max(0.0, seen - p.locked_at)
        if p.lag_valid and seen >= p.locked_at:   # an entry the feed already had says nothing about its delay
            self.lags.append(lag)
        self._twins.clear()
        status = {"play": "ready", "void": "void"}.get(parsed.kind, "review")
        warning = self._dd_conflict(p, entry)
        flags = list(parsed.flags)
        if warning:
            status = "review"
        elif status == "void" and not self._dd_known_and_equal(p, entry):
            # A void cannot be undone, so it only runs by itself when the down and distance prove it is this play's
            # penalty: with nothing to compare, the host decides.
            status = "review"
            flags.append("Check the down and distance before voiding this play")
        if self._distrust:
            status, warning = "review", warning or self._distrust
            self._distrust = None
        sug = Suggestion(p.play_id, idx, entry, parsed, status, warning, None, flags)
        self.suggestion = sug
        self._notice = None
        self._log("bind", {"lag": round(lag, 1), "kind": parsed.kind}, play_id=p.play_id, feed_index=idx)
        self._log("suggest", {"status": status, "warning": warning, "type": parsed.play_type, "direction": parsed.direction,
                              "yards": parsed.yards, "yardage": parsed.yardage, "flags": flags, "text": parsed.text[:300]},
                  play_id=p.play_id, feed_index=idx)
        self._arm_auto(sug)

    def _arm_auto(self, sug: Suggestion) -> None:
        """Start the grace countdown for a clean suggestion (only when auto-score is on and not paused)."""
        if sug.status in ("ready", "void") and self.auto_score and not self.paused:
            sug.auto_at = self.clock() + float(self.settings.tank01_auto_score_grace)
        else:
            sug.auto_at = None

    # -- applying a suggestion ----------------------------------------------- #

    async def _auto_apply(self, sug: Suggestion) -> None:
        try:
            await self._apply(sug, auto=True)
        except GameError as exc:  # the play is no longer waiting (the host got there first): forget the suggestion
            self._log("error", {"kind": "auto_apply", "text": exc.message}, play_id=sug.play_id)
            if self.suggestion is sug:
                self.suggestion = None
            self._changed()

    async def accept(self, play_id: int, play_type: str | None = None, direction: str | None = None,
                     yardage: str | None = None, yards: int | None = None, void: bool = False) -> dict[str, Any]:
        """Apply the suggestion now, with optional overrides (the host's Score now / Change)."""
        sug = self.suggestion
        if not sug or sug.play_id != play_id:
            raise GameError("That suggestion is no longer current.", 409)
        overrides = any(v is not None for v in (play_type, direction, yardage, yards))
        if void or (sug.kind == "void" and not overrides):
            return await self._apply(sug, void=True)
        ptype = play_type or sug.parsed.play_type
        pdir = direction or sug.parsed.direction
        if yardage is not None or yards is not None:
            dist_yardage, dist_yards = yardage, yards
        else:
            dist_yardage, dist_yards = sug.parsed.yardage, sug.parsed.yards
        if not ptype:
            raise GameError("Pick Run or Pass first.", 422)
        if not pdir:
            raise GameError("Pick Left, Middle or Right first.", 422)
        bucket, yds = resolve_yardage(dist_yardage, dist_yards)  # raises 422 if missing or inconsistent
        return await self._apply(sug, result=(ptype, pdir, bucket.value, yds), changed=overrides)

    async def _apply(self, sug: Suggestion, *, result: tuple[str, str, str, int | None] | None = None,
                     void: bool = False, auto: bool = False, changed: bool = False) -> dict[str, Any]:
        """Score (or void) the play from a suggestion. Bookkeeping first, then one broadcast."""
        parsed = sug.parsed
        if not void and result is None:
            if sug.kind == "void":
                void = True
            else:
                bucket, yds = resolve_yardage(parsed.yardage, parsed.yards)
                result = (parsed.play_type or "", parsed.direction or "", bucket.value, yds)
        play_id = sug.play_id
        text = parsed.text
        if void:
            play = self.host.feed_void(play_id, text)
        else:
            assert result is not None
            play = self.host.feed_resolve(play_id, result[0], result[1], result[2], result[3], text)
        self.suggestion = None
        p = next((x for x in self.pending if x.play_id == play_id), None)
        if p:
            self.pending.remove(p)
        self._persist_cursor()
        if not void and result is not None:
            self._scored[sug.entry_index] = (play_id, result[:3])
        clean = (not void and not changed and sug.kind == "play" and sug.status in ("ready", "held")
                 and result is not None)
        # The form is filled for any play the host accepted as the feed read it (also after a check); only a clean one
        # opens the next play by itself.
        self.next_down = self._next_down(sug) if not void and not changed and sug.kind == "play" and result is not None else None
        if void:
            summary = "No play (voided)"
        else:
            assert result is not None
            summary = f"{result_label(*result[:3])}" + (f" ({result[3]} yds)" if result[3] is not None else "")
        self.last_scored = {"play_id": play_id, "by": "feed", "auto": auto, "summary": summary, "at": self.clock(),
                            "voided": void}
        self._log("score", {"by": "auto" if auto else "host", "void": void, "result": result, "changed": changed,
                            "next_down": self.next_down}, play_id=play_id, feed_index=sug.entry_index)
        self._schedule_open(clean)
        await self.host.feed_broadcast("play_voided" if void else "play_resolved")
        self._changed()
        return play

    def _next_down(self, sug: Suggestion) -> dict[str, Any] | None:
        return self._next_down_of(sug.entry, sug.parsed)

    def _next_down_of(self, entry: Any, parsed: Parsed) -> dict[str, Any] | None:
        meta = self._box_meta  # team abbreviations and ids from the last box score
        if not meta:
            return None
        return next_down_and_distance(entry, parsed, meta.get("home"), meta.get("away"),
                                      meta.get("teamIDHome"), meta.get("teamIDAway"))

    # -- auto-open ----------------------------------------------------------- #

    def _schedule_open(self, clean: bool) -> None:
        self.auto_open_at = None
        nd = self.next_down
        if clean and self.auto_open and not self.paused and nd and nd["down"] <= MAX_AUTO_OPEN_DOWN:
            self.auto_open_at = self.clock() + float(self.settings.tank01_open_delay)

    async def _auto_open(self) -> None:
        """Open the next play for the host, if everything still looks as it did when this was scheduled."""
        nd = self.next_down
        self.auto_open_at = None
        game = self.store.get_game(self.game_id) if self.game_id else None
        latest = self.store.latest_play(self.game_id) if game else None
        if (not game or game["status"] != GameStatus.LIVE or not self.auto_open or self.paused or not nd
                or (latest and latest["state"] != PlayState.RESOLVED) or any(not p.resolved for p in self.pending)):
            self._changed()
            return
        try:
            await self.host.feed_open(nd["down"], nd["distance"])
            self._log("auto_open", {"down": nd["down"], "distance": nd["distance"]})
        except GameError as exc:
            self._log("error", {"kind": "auto_open", "text": exc.message})
        self._changed()

    # -- hooks from the game controller (host actions) ------------------------- #

    def on_play_opened(self, play: dict[str, Any]) -> None:
        self.next_down = None
        self.auto_open_at = None
        self._notice = None
        self._changed()

    def on_play_locked(self, play: dict[str, Any]) -> None:
        """A play locked (host or timer): it now waits for its feed entry."""
        if not self.linked:
            return
        now = self.clock()
        self._complete = False  # a play after a "completed" game (overtime?) deserves a fresh look
        self.auto_open_at = None
        self.last_scored = None
        self._skip_to_horizon(play["id"])
        self.pending = [p for p in self.pending if p.play_id != play["id"]]
        self.pending.append(Pending(play["id"], play["play_number"], play["down"], play["distance"], now,
                                    next_check=now + self._first_delay()))
        self._log("lock", {"number": play["play_number"], "down": play["down"], "distance": play["distance"]},
                  play_id=play["id"])
        if self._demo:
            self._demo.on_play_locked()
        self._consume()   # the entry may already be known from an earlier check: no request needed
        self._changed()

    def on_play_resolved(self, play: dict[str, Any]) -> None:
        """The host scored the play by hand."""
        if not self.linked:
            return
        self._notice = None
        p = next((x for x in self.pending if x.play_id == play["id"]), None)
        scored = (play["correct_play_type"], play["correct_direction"], play["correct_yardage"])
        sug = self.suggestion
        if sug and sug.play_id == play["id"]:
            # Its entry was already read: compare right now instead of waiting for an entry that will not come.
            self.suggestion = None
            if sug.kind == "play" and scored != (sug.parsed.play_type, sug.parsed.direction, sug.parsed.yardage):
                feed = (sug.parsed.play_type, sug.parsed.direction, sug.parsed.yardage)
                self.disagreement = {
                    "play_id": play["id"], "feed": result_label(*feed), "scored": result_label(*scored),
                    "text": sug.parsed.text,
                    "feed_result": {"play_type": sug.parsed.play_type, "direction": sug.parsed.direction,
                                    "yards": sug.parsed.yards, "yardage": sug.parsed.yardage}}
            self._scored[sug.entry_index] = (play["id"], scored)
            self._log("host_override", {"scored": scored, "suggested": sug.status}, play_id=play["id"])
            if p:
                self.pending.remove(p)
        elif p:
            p.resolved, p.resolved_at, p.scored = True, self.clock(), scored
        self._persist_cursor()
        self._changed()

    def on_play_voided(self, play: dict[str, Any]) -> None:
        """The host voided the play: it leaves the queue (its feed entry, if any, is not ours to match)."""
        if not self.linked:
            return
        self._notice = None
        p = next((x for x in self.pending if x.play_id == play["id"]), None)
        sug = self.suggestion
        had_entry = bool(sug and sug.play_id == play["id"])
        if had_entry:
            self.suggestion = None
        if p:
            self.pending.remove(p)
            if not had_entry:   # a "No Play" entry for it may still arrive: remember its situation
                self._twins.append((self.clock(), p.down, p.distance))
                if self._demo and not p.resolved:
                    self._demo.on_play_cancelled()
        self.auto_open_at = None
        self._log("host_void", {"had_entry": had_entry}, play_id=play["id"])
        self._persist_cursor()
        self._changed()

    def on_play_corrected(self, play_id: int) -> None:
        if self.disagreement and self.disagreement.get("play_id") == play_id:
            self.disagreement = None
        play = self.store.get_play(play_id)
        if play:   # the corrected result is now what a later revision of the feed must be compared with
            fixed = (play["correct_play_type"], play["correct_direction"], play["correct_yardage"])
            for idx, (pid, _) in list(self._scored.items()):
                if pid == play_id:
                    self._scored[idx] = (pid, fixed)
        self._log("fix", {}, play_id=play_id)
        self._changed()

    def on_game_status(self, game: dict[str, Any]) -> None:
        self.game_final = game["status"] == GameStatus.FINAL
        if self.game_final:
            self.auto_open_at = None
            self.next_down = None
        self._changed()

    # -- admin actions ----------------------------------------------------------- #

    async def link(self, feed_game_id: str | None) -> None:
        """Connect the current game to a feed game (or disconnect it with None)."""
        game = self.store.current_game()
        if not game:
            raise GameError("Create a game first.", 404)
        if game["status"] == GameStatus.FINAL:
            raise GameError("This game is over. Create a new game.", 409)
        feed_game_id = self.check_linkable(feed_game_id)
        game = self.store.set_feed_link(game["id"], feed_game_id)
        self.attach(game)
        self._log("link", {"feed_game_id": feed_game_id})
        self._changed()

    def _need_linked(self) -> None:
        if not self.linked:
            raise GameError("Live data is not connected to this game.", 409)

    def _set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self.game_id:
            self.store.set_feed_options(self.game_id, paused=paused)

    async def pause(self) -> None:
        self._need_linked()
        if not self.paused:
            self._set_paused(True)
            self._auto_paused = False
            self.auto_open_at = None
            if self.suggestion:
                self.suggestion.auto_at = None
            self._log("pause", {"auto": False})
        self._changed()

    async def resume(self) -> None:
        """Back to normal; if a play is waiting, check right away."""
        self._need_linked()
        was_paused = self.paused
        self._set_paused(False)
        self._auto_paused = False
        self._auth_error = False
        self._fail_streak = 0
        if was_paused:
            self._log("resume", {})
        if self.suggestion:
            self._arm_auto(self.suggestion)
        self._changed()
        if self._want_poll():
            await self._poll(manual=True)

    def set_options(self, auto_score: bool | None = None, auto_open: bool | None = None) -> None:
        self._need_linked()
        if self.game_id:
            self.store.set_feed_options(self.game_id, auto_score=auto_score, auto_open=auto_open)
        if auto_score is not None:
            self.auto_score = auto_score
            if self.suggestion:
                self._arm_auto(self.suggestion)  # starts the countdown when turned on, stops it when off
        if auto_open is not None:
            self.auto_open = auto_open
            if not auto_open:
                self.auto_open_at = None
        self._log("options", {"auto_score": self.auto_score, "auto_open": self.auto_open})
        self._changed()

    def hold(self) -> None:
        sug = self.suggestion
        if not sug:
            raise GameError("There is nothing to hold right now.", 409)
        if sug.status in ("ready", "void"):   # a review suggestion never counts down: nothing to hold
            sug.status = "held"
        sug.auto_at = None
        self._log("hold", {}, play_id=sug.play_id)
        self._changed()

    def skip(self, play_id: int) -> None:
        """Throw away the feed entry that was matched to this play and keep waiting for the right one."""
        sug = self.suggestion
        if not sug or sug.play_id != play_id:
            raise GameError("That suggestion is no longer current.", 409)
        self.suggestion = None
        p = next((x for x in self.pending if x.play_id == play_id), None)
        if p:
            p.next_check = self.clock() + float(self.settings.tank01_fast_interval)
        self._log("skip", {"reason": "host skipped", "text": sug.parsed.text[:200]}, play_id=play_id,
                  feed_index=sug.entry_index)
        self._consume()
        self._changed()

    def dismiss(self) -> None:
        self.disagreement = None
        self._changed()

    def allow_more(self, n: int = 100) -> None:
        self._need_linked()
        if self.source != "tank01":
            return
        n = max(1, min(int(n), 1000))
        self.cap_extra = self.store.raise_feed_cap(self.game_id, n)  # type: ignore[arg-type]
        self._log("allow_more", {"n": n, "game_cap": self.game_cap})
        self._changed()

    def _persist_cursor(self) -> None:
        """Save how far we have read. While a suggestion is up its own entry is not yet done with, so a restart
        re-reads (and re-suggests) it instead of losing it."""
        if not self.game_id:
            return
        value = self.suggestion.entry_index if self.suggestion else self.cursor
        if value != self._persisted_cursor:
            self._persisted_cursor = value
            try:
                self.store.set_feed_cursor(self.game_id, value)
            except Exception:  # noqa: BLE001
                log.exception("could not save the feed position")

    # -- the schedule of games ------------------------------------------------------ #

    async def games_for_date(self, date: str | None) -> dict[str, Any]:
        """Today's (or ``date``'s) games for the "Pick today's game" list. One real request, cached for a few
        minutes. The practice game is always offered; any problem is reported as text, never as a failure."""
        if date in (None, ""):
            date = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y%m%d")  # roughly Eastern time
        date = str(date).replace("-", "")
        if not DATE_RE.match(date):
            raise GameError("Dates look like 20261008.", 422)
        away, home = feed_team("CAR"), feed_team("WSH")
        out: dict[str, Any] = {
            "date": date, "games": [], "available": self.available, "error": None, "cached": False,
            "demo": {"feed_game_id": DEMO_ID, "away": away, "home": home, "time": "Anytime",
                     "status": "Practice game (no requests used)", "status_code": "demo"},
        }
        if not self.available:
            out["error"] = "Live data is not set up on this server (no Tank01 key), so only the practice game is offered."
            return out
        cached = self._schedule_cache.get(date)
        if cached and self.clock() - cached[0] < SCHEDULE_CACHE_TTL:
            return {**cached[1], "cached": True}
        self._refresh_day()
        s = self.settings
        if self._quota_hit or (self._plan_remaining is not None and self._plan_remaining <= s.tank01_reserve
                               and not s.tank01_allow_overage) or self._req_day >= s.tank01_max_requests_per_day:
            out["error"] = "Live data is out of requests right now, so the schedule can't be loaded."
            return out
        result = await self.tank01.games_for_date(date)
        self._count(result)
        self._log("schedule", {"date": date, "ok": result.ok, "http": result.http_status, "ms": result.elapsed_ms,
                               "error_kind": result.error_kind, "remaining": result.remaining}, game_id=None)
        if not result.ok:
            out["error"] = {"auth": "Tank01 rejected the API key.", "quota": "Tank01's request limit is used up."}.get(
                result.error_kind or "", "Could not reach Tank01 just now. Try again in a moment.")
            return out
        games = []
        for g in result.body or []:
            if not isinstance(g, dict) or not FEED_ID_RE.match(str(g.get("gameID", ""))):
                continue
            away, home = feed_matchup(g.get("away"), g.get("home"))
            try:
                epoch = float(g.get("gameTime_epoch"))
            except (TypeError, ValueError):
                epoch = 0.0
            games.append({"feed_game_id": g["gameID"], "away": away, "home": home, "time": str(g.get("gameTime") or ""),
                          "status": str(g.get("gameStatus") or ""), "status_code": str(g.get("gameStatusCode") or ""),
                          "epoch": epoch})
        games.sort(key=lambda g: (g["epoch"], g["feed_game_id"]))
        out["games"] = games
        self._schedule_cache[date] = (self.clock(), dict(out))
        self._changed()
        return out

    # -- what the host sees ------------------------------------------------------------ #

    def state(self) -> dict[str, Any]:
        """The ``feed`` object of ``admin_state``."""
        now = self.clock()
        blocked = self._blocked() if self.linked else None
        waiting_play = self._waiting() if self.linked else None
        waiting = None
        if waiting_play:
            can_poll = self._want_poll()
            waiting = {"play_id": waiting_play.play_id, "checks": waiting_play.checks, "since": waiting_play.locked_at,
                       "next_check_at": waiting_play.next_check if can_poll else None}
        code, message = self._state_and_message(blocked, waiting_play, now)
        lag = self.lags[-LAG_SAMPLES:]
        return {
            "available": self.available,
            "linked": self.linked, "source": self.source, "game_id": self.feed_id,
            "state": code, "message": message,
            "paused": self.paused, "auto_score": self.auto_score, "auto_open": self.auto_open,
            "requests": {"game": self._req_game, "today": self._req_day, "game_cap": self.game_cap,
                         "day_cap": int(self.settings.tank01_max_requests_per_day),
                         "plan_remaining": self._plan_remaining, "plan_limit": self._plan_limit},
            "lag": {"median": round(statistics.median(lag), 1) if lag else None,
                    "last": round(self.lags[-1], 1) if self.lags else None, "samples": len(self.lags)},
            "waiting": waiting,
            "suggestion": self._suggestion_view() if self.suggestion else None,
            "disagreement": self.disagreement,
            "next_down": self.next_down,
            "auto_open_at": self.auto_open_at,
            "last_scored": self.last_scored,
        }

    def _suggestion_view(self) -> dict[str, Any]:
        sug = self.suggestion
        assert sug is not None
        parsed = sug.parsed
        return {"play_id": sug.play_id, "status": sug.status, "text": parsed.text, "clock": parsed.clock,
                "down_and_distance": parsed.down_and_distance, "play_type": parsed.play_type,
                "direction": parsed.direction, "yards": parsed.yards, "yardage": parsed.yardage,
                "flags": list(sug.flags), "warning": sug.warning,
                "auto_at": None if self.paused else sug.auto_at, "kind": parsed.kind}

    def _state_and_message(self, blocked: tuple[str, str] | None, waiting: Pending | None,
                           now: float) -> tuple[str, str]:
        if not self.linked:
            return "off", ("Live data is off. Pick a game when you create it, or keep scoring by hand."
                           if self.available else "Live data is off (no Tank01 key). The practice game still works.")
        if self._auth_error:
            return "error", ("Live data needs the Tank01 key, which is not set on this server. Score by hand."
                             if not self.available and self.source == "tank01" else
                             "Tank01 rejected the API key. Score by hand until it is fixed.")
        if self.paused:
            return "paused", ("Paused after repeated trouble reaching Tank01. Tap Resume to try again."
                              if self._auto_paused else "Paused. Tap Resume when play restarts.")
        if blocked:
            return "capped", blocked[1]
        if self.game_final:
            return "done", "The game is over."
        if self._not_started and waiting:
            return "not_started", (f"Waiting for kickoff. {self._not_started} Checking every 2 minutes. If the game is "
                                   "already on, Tank01 is running late: score by hand, or wait and it catches up.")
        if self._fail_streak >= ERROR_AFTER:
            return "error", "Having trouble reaching Tank01. Still trying; score by hand if you need to."
        if self.suggestion:
            return "idle", ("The feed has this play. Review it below." if self.suggestion.status in ("review", "held")
                            else "The feed has this play. It will score by itself.")
        if waiting:
            if self._complete:
                return "done", "The feed says the game is over."
            if now - waiting.locked_at >= QUIET_AFTER:
                return "waiting", "Feed is quiet: long delay (injury or review?)"
            if self._fail_streak:
                return "waiting", "Trouble reaching Tank01. Trying again."
            return "waiting", f"Waiting for the result of play #{waiting.number} (check {waiting.checks + 1})."
        if self._notice:
            return "idle", self._notice
        if self._complete:
            return "done", "The feed says the game is over."
        return "idle", "Connected. It checks the feed only while a play is locked."
