"""A fake Tank01 for tests: a local HTTP server (stdlib, in a thread), recorded fixtures, and a manual clock.

Nothing here ever talks to rapidapi.com. ``FakeTank01`` answers ``getNFLBoxScore`` and ``getNFLGamesForDate`` the
way the real service does (``{"statusCode": 200, "body": ...}`` plus the rate-limit headers) from data the test
controls: reveal entries one at a time, queue a one-off error, change the plan allowance.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from app import GameController, Hub, Settings
from models import Store

FIXTURE = Path(__file__).resolve().parents[1] / "demo" / "box_CAR_WSH_20241020.json"
GAME_BODY = json.loads(FIXTURE.read_text())["body"]
ENTRIES: list[dict[str, Any]] = GAME_BODY["allPlayByPlay"]
META = {k: v for k, v in GAME_BODY.items() if k != "allPlayByPlay"}
FEED_ID = "20241020_CAR@WSH"
SENTINEL_KEY = "SENTINEL-KEY-1f9e3c0d77aa"

START = 1_800_000_000.0  # a fixed "now" (a Wednesday evening, UTC)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEDULE = json.loads((FIXTURES / "games_for_date_20261008.json").read_text())["body"]   # real: one game, TB at DAL
NOT_STARTED = json.loads((FIXTURES / "box_not_started.json").read_text())["body"]        # real: {"error": "Game hasn't ..."}


class Clock:
    """A manual clock: ``advance`` it, then call ``feed.tick()``."""

    def __init__(self, start: float = START) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeTank01:
    """The service, on 127.0.0.1. ``visible`` entries of ``entries`` are shown; ``queue`` makes the next answers odd."""

    def __init__(self, entries: list[dict[str, Any]] | None = None, key: str = SENTINEL_KEY) -> None:
        self.entries = list(ENTRIES if entries is None else entries)
        self.visible = 0
        self.key = key
        self.remaining: int | None = 900     # the plan's allowance as the headers report it
        self.limit: int | None = 1000
        self.status = "In Progress"
        self.status_code = "1"
        self.period = "Q1"
        self.games: Any = list(SCHEDULE)
        self.not_started = False
        self.hits: list[dict[str, Any]] = []
        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer._handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    # -- test controls -------------------------------------------------- #

    def reveal(self, n: int = 1, upto: int | None = None) -> None:
        """Show ``n`` more entries (or everything up to index ``upto`` inclusive)."""
        self.visible = min(len(self.entries), (upto + 1) if upto is not None else self.visible + n)

    def reveal_through_next_play(self) -> int:
        """Show entries up to and including the next one that is not a skip. Returns that entry's index."""
        from playparse import classify

        i = self.visible
        while i < len(self.entries) and classify(self.entries[i]).kind == "skip":
            i += 1
        self.visible = min(len(self.entries), i + 1)
        return i

    def queue(self, status: int = 200, body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None,
              delay: float = 0.0, times: int = 1) -> None:
        """The next ``times`` requests get this answer instead of the normal one."""
        with self._lock:
            self._queue.extend({"status": status, "body": body, "raw": raw, "headers": headers or {}, "delay": delay}
                               for _ in range(times))

    @property
    def box_hits(self) -> list[dict[str, Any]]:
        return [h for h in self.hits if h["path"] == "/getNFLBoxScore"]

    def close(self) -> None:
        if not getattr(self, "_closed", False):
            self._closed = True
            self.server.shutdown()
            self.server.server_close()

    # -- the service ------------------------------------------------------ #

    def box(self) -> dict[str, Any]:
        if self.not_started:
            return NOT_STARTED
        return {**META, "gameStatus": self.status, "gameStatusCode": self.status_code,
                "currentPeriod": self.period if self.status_code != "2" else "Final",
                "allPlayByPlay": self.entries[: self.visible]}

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        url = urlsplit(h.path)
        record = {"raw": h.path, "path": url.path, "query": {k: v[0] for k, v in parse_qs(url.query).items()},
                  "headers": {k.lower(): v for k, v in h.headers.items()}, "time": time.time()}
        with self._lock:
            self.hits.append(record)
            override = self._queue.pop(0) if self._queue else None
            if self.remaining is not None:
                self.remaining -= 1
            remaining = self.remaining
        if override and override["delay"]:
            time.sleep(override["delay"])
        if record["headers"].get("x-rapidapi-key") != self.key and not override:
            status, payload = 403, json.dumps({"message": "You are not subscribed to this API."}).encode()
        elif override:
            status = override["status"]
            payload = override["raw"] if override["raw"] is not None else json.dumps(
                override["body"] if override["body"] is not None else {"statusCode": status, "body": {}}).encode()
        elif url.path == "/getNFLBoxScore":
            status, payload = 200, json.dumps({"statusCode": 200, "body": self.box()}).encode()
        elif url.path == "/getNFLGamesForDate":
            status, payload = 200, json.dumps({"statusCode": 200, "body": self.games}).encode()
        else:
            status, payload = 404, json.dumps({"message": "Endpoint does not exist"}).encode()
        h.send_response(status)
        h.send_header("content-type", "application/json")
        h.send_header("content-length", str(len(payload)))
        if remaining is not None:
            h.send_header("x-ratelimit-requests-remaining", str(remaining))
        if self.limit is not None:
            h.send_header("x-ratelimit-requests-limit", str(self.limit))
        for k, v in (override or {}).get("headers", {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(payload)


# --------------------------------------------------------------------------- #
# A rig: store + controller + fake service + clock, driven by hand
# --------------------------------------------------------------------------- #

GAME = {"home_name": "Washington", "home_primary": "#5A1414", "home_secondary": "#FFB612",
        "away_name": "Carolina", "away_primary": "#0085CA", "away_secondary": "#101820"}


class Rig:
    """Everything a poller test needs. Build it with ``run_rig``."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.tmp_path = tmp_path
        self.server = FakeTank01()
        self.clock = Clock()
        defaults = dict(db_path=str(tmp_path / "rig.db"), admin_key="k", window_seconds=15,
                        tank01_api_key=SENTINEL_KEY, tank01_base_url=self.server.url)
        self.settings = Settings(**{**defaults, **overrides})
        self.store = Store(self.settings.db_path)
        self.ctrl = GameController(self.store, Hub(), self.settings)
        self.feed = self.ctrl.feed
        self.feed.clock = self.clock
        self.feed.autorun = False   # the test calls tick() itself

    @property
    def game(self) -> dict[str, Any]:
        return self.store.current_game()  # type: ignore[return-value]

    def state(self) -> dict[str, Any]:
        return self.feed.state()

    async def new_game(self, feed_game_id: str | None = FEED_ID) -> dict[str, Any]:
        from app import CreateGameIn

        return await self.ctrl.create_game(CreateGameIn(**GAME, feed_game_id=feed_game_id))

    async def open(self, down: int | None = 1, distance: str | None = "10") -> dict[str, Any]:
        from app import OpenPlayIn

        return await self.ctrl.open_play(OpenPlayIn(down=down, distance=distance))

    async def lock(self) -> dict[str, Any]:
        return await self.ctrl.lock_play()

    async def open_lock(self, down: int | None = 1, distance: str | None = "10") -> dict[str, Any]:
        await self.open(down, distance)
        return await self.lock()

    async def step(self, seconds: float = 1.0) -> float | None:
        self.clock.advance(seconds)
        return await self.feed.tick()

    async def until(self, predicate: Callable[[], bool], limit: float = 700.0, step: float = 1.0) -> float:
        """Advance the clock ``step`` at a time (ticking the feed) until ``predicate()``; returns seconds advanced."""
        spent = 0.0
        while not predicate():
            if spent >= limit:
                raise AssertionError(f"condition not reached in {limit}s; feed={self.state()}")
            await self.step(step)
            spent += step
        return spent

    def play(self, number: int | None = None) -> dict[str, Any]:
        game = self.game
        return self.store.latest_play(game["id"]) if number is None else self.store._one(
            "SELECT * FROM plays WHERE game_id = ? AND play_number = ?", (game["id"], number))

    def poll_offsets(self, since: float | None = None) -> list[float]:
        """Seconds (on the manual clock) of each request, relative to ``since``, from the recorder."""
        rows = [r for r in self.store.feed_log(self.game["id"]) if r["kind"] == "poll"]
        base = since if since is not None else rows[0]["ts"] if rows else 0.0
        return [round(r["ts"] - base, 3) for r in rows]

    def close(self) -> None:
        self.server.close()
        self.store.close()


def run_rig(tmp_path: Path, scenario: Callable[[Rig], Awaitable[Any]], **overrides: Any) -> Any:
    """Run ``scenario(rig)`` in a fresh event loop, then clean up."""

    async def main() -> Any:
        rig = Rig(tmp_path, **overrides)
        try:
            return await scenario(rig)
        finally:
            await rig.ctrl.shutdown()
            rig.close()

    return asyncio.run(main())
