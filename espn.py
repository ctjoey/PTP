"""ESPN's free public NFL play-by-play, for the side-by-side feed timing check (Tank01 vs ESPN).

ESPN's site API is unofficial and undocumented: no key, no promises, and its shape is only known from what people
have written down about it. So everything here is defensive: any field may be missing or odd, the readers never
raise, and neither does the client (an answer it can't use comes back with ``ok=False`` and a plain reason).

Live data uses it to note when ESPN first showed each play (``feed.LiveFeed`` notes when Tank01 did), so the owner
can see which feed is faster. It never decides anything in the game. It knows nothing about the game itself, so it
could become a play-by-play source of its own later.

Pieces: ``EspnClient`` (HTTP in a worker thread), ``find_event`` (a day's scoreboard -> the game's event id),
``summary_plays`` (a game summary -> ``EspnPlay`` list), and the play key both feeds share (``play_key``,
``tank01_play``, ``EspnPlay.key``): quarter, game clock at the snap, down, yards to go.
"""

from __future__ import annotations

import asyncio
import email.utils
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from playparse import classify, parse_down_and_distance
from teams import preset_for_abbr

DEFAULT_BASE_URL = "https://site.api.espn.com"
SCOREBOARD_PATH = "/apis/site/v2/sports/football/nfl/scoreboard"
SUMMARY_PATH = "/apis/site/v2/sports/football/nfl/summary"
TIMEOUT = 8.0
MAX_RESPONSE_BYTES = 8_000_000   # a whole game's summary is about 1 MB: anything far bigger is not one
USER_AGENT = "PickThePlay/1.0 (one game's play-by-play, at most one request every few seconds)"

# Spellings that differ between Tank01 (or older NFL data) and ESPN. Both sides are read through this.
ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}

TANK01_ID_RE = re.compile(r"^(\d{8})_([A-Z]{2,4})@([A-Z]{2,4})$")
EVENT_ID_RE = re.compile(r"^[0-9A-Za-z]{1,24}$")
_CLOCK_RE = re.compile(r"^(\d{0,2}):(\d{2})(?:\.\d+)?$")
_PERIOD_RE = re.compile(r"^(?:Q([1-9])|([1-4])(?:ST|ND|RD|TH)|(\d?)OT(\d?))$")
# ESPN play types that are never a snap the host opens a play for (the text is classified as well).
_SKIP_TYPES = re.compile(r"kickoff|punt|field goal|extra point|time ?out|end (?:of )?(?:period|quarter|half|game|regulation)"
                         r"|two[- ]minute|coin toss", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# The play key
# --------------------------------------------------------------------------- #


def period_number(value: Any) -> int | None:
    """"Q2" -> 2, "OT" -> 5 (overtime is ESPN's period 5), "OT2" or "2OT" -> 6; ESPN's own number as it is."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return value if 1 <= value <= 9 else None
    if not isinstance(value, str):
        return None
    text = "".join(value.split()).upper()
    if text.isdigit():
        return period_number(int(text))
    m = _PERIOD_RE.match(text)
    if not m:
        return None
    if m.group(1) or m.group(2):
        return int(m.group(1) or m.group(2))
    return 4 + int(m.group(3) or m.group(4) or 1)


def norm_clock(value: Any) -> str | None:
    """"03:42" -> "3:42", "0:05" stays, ":42" -> "0:42"; None when it is not a game clock."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    m = _CLOCK_RE.match(value.strip())
    if not m:
        return None
    minutes, seconds = int(m.group(1) or 0), int(m.group(2))
    return f"{minutes}:{seconds:02d}" if minutes <= 15 and seconds < 60 else None


def clock_seconds(clock: str) -> int:
    """"3:42" -> 222 (seconds left in the period)."""
    minutes, _, seconds = clock.partition(":")
    return int(minutes or 0) * 60 + int(seconds or 0)


def _to_go(value: Any, spot: Any = None) -> str | None:
    """Yards to go as the key writes them: 6 -> "6"; "Goal" -> the yard line it is at (ESPN gives that number)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "goal":
            return _to_go(spot)
        value = int(text) if text.isdigit() else None
    return str(value) if isinstance(value, int) and 1 <= value <= 99 else None


def play_key(period: Any, clock: Any, down: Any, distance: Any) -> str | None:
    """The key a play is matched on across feeds: ``"2|3:42|2|6"`` (quarter, game clock at the snap, down, yards to
    go). None when any part is missing or odd: such a play is simply not compared."""
    p, c = period_number(period), norm_clock(clock)
    try:
        d = int(down) if not isinstance(down, bool) else None
    except (TypeError, ValueError):
        d = None
    togo = _to_go(distance)
    if p is None or c is None or d is None or not 1 <= d <= 4 or togo is None:
        return None
    return f"{p}|{c}|{d}|{togo}"


def key_label(key: str, goal: bool = False) -> str:
    """"2|3:42|2|6" -> "Q2 3:42 · 2nd & 6" (what the host sees); "OT" for period 5."""
    period, clock, down, togo = key.split("|")
    n = int(period)
    quarter = f"Q{n}" if n <= 4 else "OT" if n == 5 else f"OT{n - 4}"
    return f"{quarter} {clock} · {ORDINAL.get(int(down), f'{down}th')} & {'Goal' if goal else togo}"


def tank01_play(entry: Any) -> tuple[str, str] | None:
    """``(key, label)`` for one Tank01 play-by-play entry, or None when it is not a scrimmage play (a kickoff, punt,
    timeout, quarter marker ...) or can't be keyed."""
    if not isinstance(entry, dict) or classify(entry).kind == "skip":
        return None
    dd = parse_down_and_distance(entry.get("downAndDistance"))
    if not dd:
        return None
    key = play_key(entry.get("playPeriod"), entry.get("playClock"), dd["down"], _to_go(dd["to_go"], dd["spot"]))
    return (key, key_label(key, dd["to_go"] == "goal")) if key else None


# --------------------------------------------------------------------------- #
# Reading ESPN's answers
# --------------------------------------------------------------------------- #


def parse_wallclock(value: Any) -> float | None:
    """ESPN's ``wallclock`` ("2026-10-13T02:31:05Z", when the play happened) as epoch seconds, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


def _text(value: Any, limit: int = 400) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _dig(value: Any, *path: str) -> Any:
    for name in path:
        value = value.get(name) if isinstance(value, dict) else None
    return value


@dataclass
class EspnPlay:
    """One play as ESPN's summary lists it: only what the timing check uses. Anything missing or odd is None."""

    id: str = ""
    sequence: int | None = None
    text: str = ""
    type: str = ""
    period: int | None = None
    clock: str | None = None               # "3:42", the game clock at the snap
    wallclock: float | None = None         # when ESPN says it happened (epoch seconds); sometimes missing or wrong
    down: int | None = None
    distance: str | None = None            # yards to go, as the key writes them
    down_distance: str | None = None       # "2nd & 6 at BUF 35"
    goal: bool = False
    yards: int | None = None
    scoring: bool = False

    @classmethod
    def from_json(cls, raw: Any) -> EspnPlay | None:
        """Read one entry of a drive's ``plays``. Never raises; None when it is not an object at all."""
        if not isinstance(raw, dict):
            return None
        start = raw.get("start") if isinstance(raw.get("start"), dict) else {}
        dd_text = _text(start.get("downDistanceText"), 80) or _text(start.get("shortDownDistanceText"), 80) or None
        parsed = parse_down_and_distance(dd_text)
        down = _int(start.get("down"))
        if down is None or not 1 <= down <= 4:   # ESPN writes 0 or -1 for kickoffs and tries
            down = parsed["down"] if parsed else None
        distance = _to_go(start.get("distance"))
        if parsed and (distance is None or parsed["to_go"] == "goal"):
            distance = _to_go(parsed["to_go"], parsed["spot"]) or distance
        clock = norm_clock(_dig(raw, "clock", "displayValue"))
        if clock is None:   # only the seconds left in the period
            secs = _dig(raw, "clock", "value")
            if isinstance(secs, (int, float)) and not isinstance(secs, bool) and 0 <= secs <= 900:
                clock = f"{int(secs) // 60}:{int(secs) % 60:02d}"
        return cls(
            id=_text(str(raw.get("id")) if isinstance(raw.get("id"), (str, int)) else "", 40),
            sequence=_int(raw.get("sequenceNumber")),
            text=_text(raw.get("text")),
            type=_text(_dig(raw, "type", "text"), 80),
            period=period_number(_dig(raw, "period", "number")),
            clock=clock,
            wallclock=parse_wallclock(raw.get("wallclock")),
            down=down,
            distance=distance,
            down_distance=dd_text,
            goal=bool(dd_text and "goal" in dd_text.lower()),
            yards=_int(raw.get("statYardage")),
            scoring=raw.get("scoringPlay") is True,
        )

    @property
    def raw_key(self) -> str | None:
        """The key from the numbers alone (cheap: no reading of the text)."""
        return play_key(self.period, self.clock, self.down, self.distance)

    @property
    def scrimmage(self) -> bool:
        """A snap the host could have opened a play for: it has a down, and it is not a kick, a timeout or a marker."""
        if self.down is None or _SKIP_TYPES.search(self.type):
            return False
        dd = self.down_distance or f"{ORDINAL.get(self.down, f'{self.down}th')} & {self.distance or ''}"
        return classify({"play": self.text, "downAndDistance": dd}).kind != "skip"

    @property
    def key(self) -> str | None:
        """The cross-feed key of a scrimmage play (None for anything else)."""
        return self.raw_key if self.scrimmage else None

    @property
    def label(self) -> str:
        key = self.raw_key
        return key_label(key, self.goal) if key else ""


def summary_plays(summary: Any) -> list[EspnPlay]:
    """Every play in a game summary, oldest first and each once: ``drives.previous`` then ``drives.current`` (the
    drive in progress can also be listed among the previous ones). Never raises."""
    out: list[EspnPlay] = []
    try:
        drives = summary.get("drives") if isinstance(summary, dict) else None
        groups: list[Any] = []
        if isinstance(drives, dict):
            if isinstance(drives.get("previous"), list):
                groups.extend(drives["previous"])
            groups.append(drives.get("current"))
        raw_plays: list[Any] = []
        for drive in groups:
            if isinstance(drive, dict) and isinstance(drive.get("plays"), list):
                raw_plays.extend(drive["plays"])
        if not raw_plays and isinstance(summary, dict) and isinstance(summary.get("plays"), list):
            raw_plays = summary["plays"]   # some summaries list the plays without drives
        seen: set[str] = set()
        for raw in raw_plays:
            play = EspnPlay.from_json(raw)
            if play is None:
                continue
            ident = play.id or f"{play.sequence}|{play.text}"
            if ident in seen:
                continue
            seen.add(ident)
            out.append(play)
    except Exception:  # noqa: BLE001 - an odd answer must never stop anything
        return out
    return out


# --------------------------------------------------------------------------- #
# Finding the game
# --------------------------------------------------------------------------- #


def tank01_matchup(feed_game_id: str | None) -> tuple[str, str, str] | None:
    """``"20261012_BUF@LAR"`` -> ``("20261012", "BUF", "LAR")`` (date, away, home); None for anything else."""
    m = TANK01_ID_RE.match(str(feed_game_id or "").strip())
    return (m.group(1), m.group(2), m.group(3)) if m else None


def nearby_dates(date: str) -> list[str]:
    """The game's date first, then the day after and the day before (a late game, or a date read differently)."""
    try:
        day = datetime.strptime(date, "%Y%m%d")
    except ValueError:
        return []
    return [date, *((day + timedelta(days=d)).strftime("%Y%m%d") for d in (1, -1))]


def _abbr(value: Any) -> str:
    text = str(value or "").strip().upper()
    return ALIASES.get(text, text)


def _city(abbr: str) -> str | None:
    team = preset_for_abbr(abbr)
    return team["name"].lower() if team else None


def _sides(event: Any) -> tuple[str, dict[str, dict[str, Any]]] | None:
    """``(event id, {"home": {"abbr", "names"}, "away": {...}})`` for one scoreboard event, or None."""
    if not isinstance(event, dict):
        return None
    event_id = event.get("id")
    event_id = str(event_id).strip() if isinstance(event_id, (str, int)) and not isinstance(event_id, bool) else ""
    comps = event.get("competitions")
    comp = comps[0] if isinstance(comps, list) and comps and isinstance(comps[0], dict) else {}
    sides: dict[str, dict[str, Any]] = {}
    for c in comp.get("competitors") or []:
        if not isinstance(c, dict):
            continue
        side = str(c.get("homeAway") or "").lower()
        team = c.get("team") if isinstance(c.get("team"), dict) else {}
        names = [str(team.get(k)).strip().lower() for k in ("location", "displayName", "shortDisplayName", "name")
                 if isinstance(team.get(k), str) and team.get(k).strip()]
        if side in ("home", "away"):
            sides[side] = {"abbr": _abbr(team.get("abbreviation")), "names": names}
    if not EVENT_ID_RE.match(event_id) or len(sides) != 2:
        return None
    return event_id, sides


def _same_team(side: dict[str, Any], abbr: str, by_city: bool) -> bool:
    if not by_city:
        return bool(side["abbr"]) and side["abbr"] == _abbr(abbr)
    city = _city(abbr)
    return bool(city) and any(n == city or n.startswith(f"{city} ") for n in side["names"])


def find_event(scoreboard: Any, away: str, home: str) -> str | None:
    """The ESPN event id of ``away`` at ``home`` (Tank01 abbreviations) on a day's scoreboard, or None.

    Teams match by abbreviation (through ``ALIASES``), else by city ("Washington" against ESPN's location or display
    name, only when exactly one game fits). Home and away must agree; a game listed the other way round (a neutral
    site) is the fallback. Never raises."""
    try:
        events = scoreboard.get("events") if isinstance(scoreboard, dict) else None
        listed = [s for s in (_sides(e) for e in events) if s] if isinstance(events, list) else []
        for by_city in (False, True):
            for swapped in (False, True):
                want_home, want_away = (away, home) if swapped else (home, away)
                hits = [eid for eid, sides in listed if _same_team(sides["home"], want_home, by_city)
                        and _same_team(sides["away"], want_away, by_city)]
                if hits and (not by_city or len(hits) == 1):
                    return hits[0]
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


@dataclass
class EspnResult:
    """What one request to ESPN produced. ``error_kind`` is one of network, http, too_big, bad_json, shape (None
    when ``ok``); ``error_text`` is one plain sentence."""

    ok: bool = False
    http_status: int | None = None
    body: Any = None
    elapsed_ms: int = 0
    error_kind: str | None = None
    error_text: str = ""
    cache: dict[str, Any] | None = None   # how old ESPN's copy was (``cache_info``); None when no answer came


def _header_time(value: Any) -> float | None:
    """An HTTP date ("Tue, 13 Oct 2026 12:00:00 GMT") as epoch seconds, or None."""
    try:
        when = email.utils.parsedate_to_datetime(str(value)) if value else None
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def cache_info(headers: Any) -> dict[str, Any]:
    """How old ESPN's answer was when it came, from its headers: ``age`` (seconds a cache had held this copy, from the
    ``Age`` header), ``stale`` (seconds between ``Last-Modified`` and ``Date``) and ``cache_control`` as sent. Missing
    or odd headers give None (``cache_control``: ""). A copy held for many seconds would make ESPN look slower than it
    is, so the feed log keeps this beside each play ESPN first showed. Never raises."""
    try:
        get = headers.get if headers is not None else (lambda _name: None)
        age = _int(get("age"))
        date, modified = _header_time(get("date")), _header_time(get("last-modified"))
        stale = round(date - modified) if date is not None and modified is not None and date >= modified else None
        return {"age": age if age is not None and age >= 0 else None, "stale": stale,
                "cache_control": _text(get("cache-control"), 80)}
    except Exception:  # noqa: BLE001
        return {"age": None, "stale": None, "cache_control": ""}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class EspnClient:
    """ESPN's site API over plain ``urllib`` in a worker thread: no key, a polite User-Agent, a timeout and a size cap.
    Never raises: an answer that can't be used comes back with ``ok=False`` and a plain ``error_text``."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = TIMEOUT) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.requests = 0
        self._built: urllib.request.OpenerDirector | None = None

    def __repr__(self) -> str:
        return f"EspnClient(base_url={self.base_url!r})"

    async def scoreboard(self, date: str) -> EspnResult:
        """One day's games (``date`` is YYYYMMDD): ``{"events": [...]}``. One request."""
        self.requests += 1
        result = await asyncio.to_thread(self._fetch, SCOREBOARD_PATH, {"dates": date})
        if result.ok and not (isinstance(result.body, dict) and isinstance(result.body.get("events", []), list)):
            result.ok, result.error_kind, result.error_text = False, "shape", "ESPN's scoreboard was not what we expected."
        return result

    async def summary(self, event_id: str) -> EspnResult:
        """One game's summary, with its drives and their plays. One request."""
        self.requests += 1
        result = await asyncio.to_thread(self._fetch, SUMMARY_PATH, {"event": event_id})
        if result.ok and not isinstance(result.body, dict):
            result.ok, result.error_kind, result.error_text = False, "shape", "ESPN's game summary was not what we expected."
        return result

    # -- internals -------------------------------------------------------- #

    def _opener(self) -> urllib.request.OpenerDirector:
        """Built once and reused: building one loads the system's certificates (tens of milliseconds of CPU)."""
        if self._built is None:
            host = urllib.parse.urlsplit(self.base_url).hostname or ""
            local = host in ("localhost", "127.0.0.1", "::1")   # a local test server is never behind a proxy
            self._built = urllib.request.build_opener(*([urllib.request.ProxyHandler({})] if local else []))
        return self._built

    def _fetch(self, path: str, params: dict[str, str]) -> EspnResult:
        """One blocking request. Never raises."""
        started = time.monotonic()
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        headers = {"accept": "application/json", "accept-encoding": "gzip", "user-agent": USER_AGENT}
        try:
            with self._opener().open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as resp:
                status, encoding = resp.status, str(resp.headers.get("content-encoding") or "").lower()
                cache = cache_info(resp.headers)
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            return EspnResult(http_status=exc.code, elapsed_ms=_ms(started), error_kind="http",
                              error_text=f"ESPN answered with an error (HTTP {exc.code}).")
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            kind = "timed out" if isinstance(reason, TimeoutError) or "timed out" in str(reason) else "could not connect"
            return EspnResult(elapsed_ms=_ms(started), error_kind="network", error_text=f"ESPN {kind}.")
        except Exception as exc:  # noqa: BLE001 - nothing may escape the worker thread
            return EspnResult(elapsed_ms=_ms(started), error_kind="network",
                              error_text=f"ESPN request failed: {type(exc).__name__}.")
        result = EspnResult(http_status=status, elapsed_ms=_ms(started), cache=cache)
        if "gzip" in encoding and len(raw) <= MAX_RESPONSE_BYTES:
            try:
                raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, MAX_RESPONSE_BYTES + 1)
            except zlib.error:
                result.error_kind, result.error_text = "bad_json", "ESPN sent something that was not JSON."
                return result
        if len(raw) > MAX_RESPONSE_BYTES:
            result.error_kind, result.error_text = "too_big", "ESPN's answer was far bigger than a game summary."
        elif not 200 <= status < 300:
            result.error_kind, result.error_text = "http", f"ESPN answered with an error (HTTP {status})."
        else:
            try:
                result.body, result.ok = json.loads(raw.decode("utf-8", "replace")), True
            except (ValueError, RecursionError):
                result.error_kind, result.error_text = "bad_json", "ESPN sent something that was not JSON."
        return result
