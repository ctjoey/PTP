"""Pick the Play: Live Pro Football Game — backend web server.

FastAPI app serving three surfaces:

* ``/``                  live player app
* ``/lounge/{code}``     private head-to-head lounge (same app, lounge leaderboard)
* ``/admin``             admin console that drives the play state machine
* ``/rules``             Rules of the Game: how it works, points, what Left / Middle / Right mean
* ``/privacy``, ``/support``  privacy policy and support pages (App Store listing URLs)

Real-time sync uses two WebSocket endpoints (``/ws`` for players, ``/ws/admin``
for the console). Every state change is pushed to each player as a personalised
snapshot, so clients simply re-render whatever the server sends.

Run locally:  ``python app.py``  (add ``--phone`` to open it from phones on your Wi-Fi)
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # models.py needs enum.StrEnum
    sys.exit(f"Pick the Play needs Python 3.11 or newer (3.14 recommended); this is Python "
             f"{sys.version.split()[0]}. Get it from https://www.python.org/downloads/")

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, model_validator
from starlette.websockets import WebSocketDisconnect, WebSocketState

from models import (
    BONUS_POINTS,
    DIRECTION_POINTS,
    EXACT_POINTS,
    LOUNGE_CODE_RE,
    MAX_LOUNGE_MEMBERS,
    MAX_YARDS,
    MIN_YARDS,
    TYPE_POINTS,
    YARDAGE_POINTS,
    Direction,
    GameError,
    GameStatus,
    PlayState,
    PlayType,
    Store,
    Yardage,
    YardageOutcome,
    resolve_yardage,
    score_prediction,
)
from teams import DEFAULT_AWAY, DEFAULT_HOME, TEAM_PRESETS

BASE_DIR = Path(__file__).resolve().parent
ASSET_VERSION = str(int(time.time()))
LEADERBOARD_SIZE = 25
HELLO_TIMEOUT = 10.0
SEND_TIMEOUT = 5.0
ADMIN_PUSH_THROTTLE = 0.3
DEFAULT_ADMIN_KEY = "admin"

# Point values sent with every snapshot (and shown on /rules and /support): 10 for each correct part,
# 10 more for all three, so a perfect call ("exact") is 40.
SCORING = {"type": TYPE_POINTS, "direction": DIRECTION_POINTS, "yardage": YARDAGE_POINTS,
           "bonus": BONUS_POINTS, "exact": EXACT_POINTS}

log = logging.getLogger("pick_the_play")


@dataclass(frozen=True)
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get("PTP_DB_PATH", str(BASE_DIR / "game.db")))
    admin_key: str = field(default_factory=lambda: os.environ.get("PTP_ADMIN_KEY", DEFAULT_ADMIN_KEY))
    window_seconds: float = field(default_factory=lambda: float(os.environ.get("PTP_PREDICTION_WINDOW", "15")))
    # Shown on /privacy and /support. Empty: those pages point to the App Store listing instead.
    contact_email: str = field(default_factory=lambda: os.environ.get("PTP_CONTACT_EMAIL", "").strip())
    # Picks sent in the final instant still count if they arrive this late.
    grace_seconds: float = 0.5


# --------------------------------------------------------------------------- #
# Request payloads
# --------------------------------------------------------------------------- #


class UserIn(BaseModel):
    username: str = Field(max_length=40)


class LoungeIn(BaseModel):
    name: str = Field(max_length=64)


class PredictionIn(BaseModel):
    play_id: int
    play_type: PlayType
    direction: Direction  # LEFT / MIDDLE / RIGHT ("CENTER" from older apps is read as MIDDLE)
    yardage: Yardage  # SHORT 0-5 yds, MEDIUM 6-10, LONG 11+


class CreateGameIn(BaseModel):
    home_name: str = Field(max_length=40)
    home_primary: str = Field(max_length=7)
    home_secondary: str = Field(max_length=7)
    away_name: str = Field(max_length=40)
    away_primary: str = Field(max_length=7)
    away_secondary: str = Field(max_length=7)


class StatusIn(BaseModel):
    status: GameStatus


class OpenPlayIn(BaseModel):
    down: int | None = Field(default=None, ge=1, le=4)
    distance: str | None = Field(default=None, max_length=8)
    window_seconds: float | None = Field(default=None, ge=5, le=60)


class ResolveIn(BaseModel):
    """The actual play. Send ``yardage`` (SHORT/MEDIUM/LONG/LOSS), ``yards`` (total yards gained,
    from which the bucket is derived) or both (they must agree)."""

    play_type: PlayType
    direction: Direction  # LEFT / MIDDLE / RIGHT ("CENTER" is read as MIDDLE)
    yardage: YardageOutcome | None = None
    yards: int | None = Field(default=None, ge=MIN_YARDS, le=MAX_YARDS, strict=True)  # a JSON integer

    @model_validator(mode="after")
    def _distance_given_and_consistent(self) -> "ResolveIn":
        try:
            resolve_yardage(self.yardage, self.yards)
        except GameError as exc:
            raise ValueError(exc.message) from None
        return self


class EmptyIn(BaseModel):
    pass


def _validation_message(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err.get("loc", ()))
    msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{where}: {msg}" if where else msg


# --------------------------------------------------------------------------- #
# Connection hub
# --------------------------------------------------------------------------- #


@dataclass(eq=False)
class PlayerConn:
    ws: WebSocket
    user: dict[str, Any] | None
    lounge_id: str | None


class Hub:
    """Tracks open sockets and fans messages out to them."""

    def __init__(self) -> None:
        self.players: set[PlayerConn] = set()
        self.admins: set[WebSocket] = set()

    @staticmethod
    async def send(ws: WebSocket, payload: dict[str, Any]) -> bool:
        if ws.client_state != WebSocketState.CONNECTED:
            return False
        try:
            await asyncio.wait_for(ws.send_text(json.dumps(payload, separators=(",", ":"))), SEND_TIMEOUT)
            return True
        except Exception:  # client vanished or is too slow; drop it
            return False

    async def send_many(self, items: list[tuple[WebSocket, dict[str, Any]]]) -> list[WebSocket]:
        """Send concurrently; return the sockets that failed."""
        results = await asyncio.gather(*(self.send(ws, p) for ws, p in items))
        return [ws for (ws, _), ok in zip(items, results) if not ok]

    def prune(self, dead: list[WebSocket]) -> None:
        if not dead:
            return
        dead_set = set(dead)
        self.players = {c for c in self.players if c.ws not in dead_set}
        self.admins -= dead_set


# --------------------------------------------------------------------------- #
# Game controller
# --------------------------------------------------------------------------- #


@dataclass
class _Snapshot:
    """Shared data computed once per broadcast and reused for every client."""

    game: dict[str, Any] | None
    play: dict[str, Any] | None
    leaderboard: list[dict[str, Any]]
    by_user: dict[int, dict[str, Any]]
    predictions: dict[int, dict[str, Any]]
    crowd: dict[str, int] | None
    totals: dict[int, int]
    lounges: dict[str, dict[str, Any] | None] = field(default_factory=dict)


def public_play(play: dict[str, Any] | None) -> dict[str, Any] | None:
    if not play:
        return None
    resolved = play["state"] == PlayState.RESOLVED
    return {
        "id": play["id"],
        "game_id": play["game_id"],
        "play_number": play["play_number"],
        "down": play["down"],
        "distance": play["distance"],
        "state": play["state"],
        "voided": bool(play["voided"]),
        "opened_at": play["opened_at"],
        "locks_at": play["locks_at"],
        "correct_play_type": play["correct_play_type"] if resolved else None,
        "correct_direction": play["correct_direction"] if resolved else None,
        "correct_yardage": play["correct_yardage"] if resolved else None,
        "yards_gained": play["yards_gained"] if resolved else None,
    }


class GameController:
    """Applies admin/player actions to the store and broadcasts the results."""

    def __init__(self, store: Store, hub: Hub, settings: Settings) -> None:
        self.store = store
        self.hub = hub
        self.settings = settings
        self._lock_task: asyncio.Task | None = None
        self._admin_push_pending = False
        self._background: set[asyncio.Task] = set()

    # -- lifecycle -------------------------------------------------------- #

    async def recover(self) -> None:
        """Re-arm the auto-lock timer for a play left OPEN by a restart."""
        for play in self.store.open_plays():
            self._arm_lock_timer(play)

    async def shutdown(self) -> None:
        self._cancel_lock_timer()
        for task in list(self._background):
            task.cancel()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- timers ----------------------------------------------------------- #

    def _arm_lock_timer(self, play: dict[str, Any]) -> None:
        self._cancel_lock_timer()
        self._lock_task = asyncio.create_task(self._auto_lock(play["id"], play["locks_at"]))

    def _cancel_lock_timer(self) -> None:
        task = self._lock_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._lock_task = None

    async def _auto_lock(self, play_id: int, locks_at: float) -> None:
        await asyncio.sleep(max(0.0, locks_at + self.settings.grace_seconds - time.time()))
        try:
            await self.lock_play(play_id=play_id)
            log.info("Play %s auto-locked when its timer expired", play_id)
        except GameError:
            pass  # already locked or voided by the admin

    # -- admin actions ---------------------------------------------------- #

    async def create_game(self, data: CreateGameIn) -> dict[str, Any]:
        game = self.store.create_game(**data.model_dump())
        self._cancel_lock_timer()
        await self.broadcast("game_created")
        return game

    async def set_status(self, data: StatusIn) -> dict[str, Any]:
        game = self.store.set_game_status(data.status)
        await self.broadcast("game_status")
        return game

    async def open_play(self, data: OpenPlayIn) -> dict[str, Any]:
        window = data.window_seconds or self.settings.window_seconds
        _, play = self.store.open_next_play(data.down, data.distance, window)
        self._arm_lock_timer(play)
        await self.broadcast("play_opened")
        return public_play(play)  # type: ignore[return-value]

    async def lock_play(self, data: EmptyIn | None = None, play_id: int | None = None) -> dict[str, Any]:
        play = self.store.lock_play(play_id)
        self._cancel_lock_timer()
        await self.broadcast("play_locked")
        return public_play(play)  # type: ignore[return-value]

    async def resolve_play(self, data: ResolveIn) -> dict[str, Any]:
        play = self.store.resolve_play(data.play_type, data.direction, data.yardage, data.yards)
        await self.broadcast("play_resolved")
        return public_play(play)  # type: ignore[return-value]

    async def void_play(self, data: EmptyIn | None = None) -> dict[str, Any]:
        play = self.store.void_play()
        self._cancel_lock_timer()
        await self.broadcast("play_voided")
        return public_play(play)  # type: ignore[return-value]

    # -- player actions --------------------------------------------------- #

    async def submit_prediction(self, user: dict[str, Any], data: PredictionIn) -> dict[str, Any]:
        game = self.store.current_game()
        play = self.store.get_play(data.play_id)
        if not game or not play or play["game_id"] != game["id"]:
            raise GameError("That play is not part of the current game.", 409)
        pred = self.store.submit_prediction(
            user["id"], data.play_id, data.play_type, data.direction, data.yardage,
            self.settings.grace_seconds,
        )
        self.request_admin_push()
        return pred

    async def delete_account(self, user: dict[str, Any]) -> None:
        """Delete the account, sign out its open sockets and refresh everyone's boards."""
        self.store.delete_user(user["id"])
        doomed = [c for c in self.hub.players if c.user and c.user["id"] == user["id"]]
        self.hub.players.difference_update(doomed)
        notice = {"type": "error", "code": "account_deleted", "message": "Your account was deleted."}
        await self.hub.send_many([(c.ws, notice) for c in doomed])
        await asyncio.gather(*(self._close(c.ws, 4401) for c in doomed))
        await self.broadcast("leaderboard_updated")

    @staticmethod
    async def _close(ws: WebSocket, code: int) -> None:
        if ws.client_state != WebSocketState.CONNECTED:
            return
        try:
            await asyncio.wait_for(ws.close(code=code), SEND_TIMEOUT)
        except Exception:  # already gone
            pass

    # -- snapshots -------------------------------------------------------- #

    def _snapshot(self, user_ids: list[int]) -> _Snapshot:
        game = self.store.current_game()
        play = self.store.latest_play(game["id"]) if game else None
        leaderboard = self.store.game_leaderboard(game["id"]) if game else []
        crowd = None
        if play and play["state"] != PlayState.OPEN:  # never reveal the split while picking
            crowd = self.store.pick_stats(play["id"])
        return _Snapshot(
            game=game,
            play=play,
            leaderboard=leaderboard,
            by_user={r["user_id"]: r for r in leaderboard},
            predictions=self.store.predictions_for_play(play["id"]) if play else {},
            crowd=crowd,
            totals=self.store.total_scores(user_ids),
        )

    def _lounge_view(self, snap: _Snapshot, code: str) -> dict[str, Any] | None:
        if code not in snap.lounges:
            lounge = self.store.get_lounge(code)
            if lounge:
                lounge["leaderboard"] = self.store.lounge_leaderboard(
                    code, snap.game["id"] if snap.game else None
                )
            snap.lounges[code] = lounge
        return snap.lounges[code]

    def player_message(
        self, snap: _Snapshot, user: dict[str, Any] | None, lounge_id: str | None, event: str
    ) -> dict[str, Any]:
        me = my_pick = None
        play = snap.play
        if user:
            uid = user["id"]
            row = snap.by_user.get(uid, {})
            me = {
                "id": uid,
                "username": user["username"],
                "game_score": row.get("score", 0),
                "rank": row.get("rank"),
                "exact_hits": row.get("exact_hits", 0),
                "total_score": snap.totals.get(uid, user.get("total_score", 0)),
            }
            if pick := snap.predictions.get(uid):
                my_pick = dict(pick)
                if play and play["state"] == PlayState.RESOLVED and not play["voided"]:
                    result = score_prediction(
                        pick["play_type"], pick["direction"], pick["yardage"],
                        play["correct_play_type"], play["correct_direction"], play["correct_yardage"],
                    )
                    my_pick |= {"type_correct": result.type_correct,
                                "direction_correct": result.direction_correct,
                                "yardage_correct": result.yardage_correct}
        return {
            "type": "state",
            "event": event,
            "server_time": time.time(),
            "game": snap.game,
            "play": public_play(play),
            "my_prediction": my_pick,
            "me": me,
            "leaderboard": snap.leaderboard[:LEADERBOARD_SIZE],
            "ranked_players": len(snap.leaderboard),
            "crowd": snap.crowd,
            "lounge": self._lounge_view(snap, lounge_id) if lounge_id else None,
            "scoring": SCORING,
        }

    def admin_message(self, event: str) -> dict[str, Any]:
        game = self.store.current_game()
        play = self.store.latest_play(game["id"]) if game else None
        leaderboard = self.store.game_leaderboard(game["id"]) if game else []
        signed_in = sum(1 for c in self.hub.players if c.user)
        return {
            "type": "admin_state",
            "event": event,
            "server_time": time.time(),
            "game": game,
            "play": public_play(play),
            "pick_stats": self.store.pick_stats(play["id"]) if play else None,
            "players_online": signed_in,
            "spectators_online": len(self.hub.players) - signed_in,
            "admins_online": len(self.hub.admins),
            "leaderboard": leaderboard[:LEADERBOARD_SIZE],
            "ranked_players": len(leaderboard),
            "history": self.store.play_history(game["id"]) if game else [],
            "window_seconds": self.settings.window_seconds,
        }

    # -- broadcasting ----------------------------------------------------- #

    async def send_state(self, conn: PlayerConn, event: str = "sync") -> None:
        snap = self._snapshot([conn.user["id"]] if conn.user else [])
        if not await self.hub.send(conn.ws, self.player_message(snap, conn.user, conn.lounge_id, event)):
            self.hub.prune([conn.ws])

    async def broadcast(self, event: str, lounge_id: str | None = None) -> None:
        """Push a fresh personalised snapshot to every player (or one lounge)."""
        conns = [c for c in self.hub.players if lounge_id is None or c.lounge_id == lounge_id]
        snap = self._snapshot([c.user["id"] for c in conns if c.user])
        dead = await self.hub.send_many(
            [(c.ws, self.player_message(snap, c.user, c.lounge_id, event)) for c in conns]
        )
        self.hub.prune(dead)
        await self.push_admin(event)

    async def push_admin(self, event: str) -> None:
        if not self.hub.admins:
            return
        msg = self.admin_message(event)
        self.hub.prune(await self.hub.send_many([(ws, msg) for ws in self.hub.admins]))

    def request_admin_push(self) -> None:
        """Throttled admin refresh for high-frequency events (picks, joins)."""
        if self._admin_push_pending or not self.hub.admins:
            return
        self._admin_push_pending = True

        async def flush() -> None:
            await asyncio.sleep(ADMIN_PUSH_THROTTLE)
            self._admin_push_pending = False
            await self.push_admin("stats")

        self._spawn(flush())


# Admin actions available over the admin WebSocket: name -> (payload model, handler).
ADMIN_ACTIONS: dict[str, tuple[type[BaseModel], Callable[[GameController, Any], Awaitable[Any]]]] = {
    "create_game": (CreateGameIn, GameController.create_game),
    "set_status": (StatusIn, GameController.set_status),
    "open_play": (OpenPlayIn, GameController.open_play),
    "lock_play": (EmptyIn, GameController.lock_play),
    "resolve_play": (ResolveIn, GameController.resolve_play),
    "void_play": (EmptyIn, GameController.void_play),
}


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


def get_ctrl(request: Request) -> GameController:
    return request.app.state.ctrl


def require_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = get_ctrl(request).store.get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Sign in first.")
    return user


def require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    if not secrets.compare_digest((x_admin_key or "").encode(), request.app.state.settings.admin_key.encode()):
        raise HTTPException(401, "Invalid admin key.")


def _page(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"v": ASSET_VERSION, **ctx})


@router.get("/", response_class=HTMLResponse)
async def player_page(request: Request) -> HTMLResponse:
    return _page(request, "player.html", lounge_id=None)


@router.get("/lounge/{lounge_id}", response_class=HTMLResponse, response_model=None)
async def lounge_page(request: Request, lounge_id: str) -> HTMLResponse | RedirectResponse:
    if not get_ctrl(request).store.get_lounge(lounge_id):
        safe = lounge_id if LOUNGE_CODE_RE.match(lounge_id) else ""
        return RedirectResponse(f"/?missing_lounge={safe}", status_code=303)
    return _page(request, "player.html", lounge_id=lounge_id)


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    defaults = {side: next(t for t in TEAM_PRESETS if t["label"] == label)
                for side, label in (("away", DEFAULT_AWAY), ("home", DEFAULT_HOME))}
    return _page(request, "admin.html", teams=TEAM_PRESETS, defaults=defaults)


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request) -> HTMLResponse:
    return _page(request, "rules.html", scoring=SCORING)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    return _page(request, "privacy.html", contact_email=request.app.state.settings.contact_email)


@router.get("/support", response_class=HTMLResponse)
async def support_page(request: Request) -> HTMLResponse:
    return _page(request, "support.html", contact_email=request.app.state.settings.contact_email,
                 window_seconds=round(request.app.state.settings.window_seconds),
                 max_lounge_members=MAX_LOUNGE_MEMBERS,
                 scoring=SCORING)


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


# -- player API -------------------------------------------------------------- #


@router.post("/api/users", status_code=201)
async def create_user(body: UserIn, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return ctrl.store.create_user(body.username)


@router.get("/api/me")
async def me(user: dict = Depends(require_user), ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return {"user": user, "lounges": ctrl.store.user_lounges(user["id"])}


@router.delete("/api/me", status_code=204, response_class=Response)
async def delete_me(user: dict = Depends(require_user), ctrl: GameController = Depends(get_ctrl)) -> Response:
    """Permanently delete the signed-in account and everything tied to it."""
    await ctrl.delete_account(user)
    return Response(status_code=204)


@router.get("/api/state")
async def public_state(ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return ctrl.player_message(ctrl._snapshot([]), None, None, "sync")


@router.post("/api/predictions")
async def predict(
    body: PredictionIn, user: dict = Depends(require_user), ctrl: GameController = Depends(get_ctrl)
) -> dict[str, Any]:
    return await ctrl.submit_prediction(user, body)


@router.post("/api/lounges", status_code=201)
async def create_lounge(
    body: LoungeIn, user: dict = Depends(require_user), ctrl: GameController = Depends(get_ctrl)
) -> dict[str, Any]:
    return ctrl.store.create_lounge(user["id"], body.name)


@router.get("/api/lounges/{code}")
async def get_lounge(code: str, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    lounge = ctrl.store.get_lounge(code)
    if not lounge:
        raise HTTPException(404, "No lounge found with that code.")
    return lounge


@router.post("/api/lounges/{code}/join")
async def join_lounge(
    code: str, user: dict = Depends(require_user), ctrl: GameController = Depends(get_ctrl)
) -> dict[str, Any]:
    lounge = ctrl.store.join_lounge(code, user["id"])
    await ctrl.broadcast("lounge_updated", lounge_id=code)
    return lounge


# -- admin API (same actions as the admin socket, handy for scripts) ------- #

admin_api = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@admin_api.get("/state")
async def admin_state(ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return ctrl.admin_message("sync")


@admin_api.post("/game", status_code=201)
async def admin_create_game(body: CreateGameIn, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.create_game(body)


@admin_api.post("/game/status")
async def admin_set_status(body: StatusIn, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.set_status(body)


@admin_api.post("/play/open")
async def admin_open_play(body: OpenPlayIn, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.open_play(body)


@admin_api.post("/play/lock")
async def admin_lock_play(ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.lock_play()


@admin_api.post("/play/resolve")
async def admin_resolve_play(body: ResolveIn, ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.resolve_play(body)


@admin_api.post("/play/void")
async def admin_void_play(ctrl: GameController = Depends(get_ctrl)) -> dict[str, Any]:
    return await ctrl.void_play()


router.include_router(admin_api)


# --------------------------------------------------------------------------- #
# WebSockets
# --------------------------------------------------------------------------- #


async def _receive_json(ws: WebSocket, timeout: float | None = None) -> dict[str, Any] | None:
    """Receive one JSON object; ``None`` for malformed input."""
    message = await (asyncio.wait_for(ws.receive(), timeout) if timeout else ws.receive())
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text") or (message.get("bytes") or b"").decode("utf-8", "replace")
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


@router.websocket("/ws")
async def player_socket(ws: WebSocket) -> None:
    """Player channel.

    Client -> server: ``hello {token?, lounge?}`` first, then ``predict``,
    ``sync`` or ``ping``. Server -> client: ``state`` snapshots plus
    ``prediction_saved`` / ``error`` replies.
    """
    ctrl: GameController = ws.app.state.ctrl
    await ws.accept()
    try:
        hello = await _receive_json(ws, HELLO_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=4408)
        return
    if not hello or hello.get("type") != "hello":
        await ws.close(code=4400)
        return

    user = ctrl.store.get_user_by_token(hello.get("token"))
    if hello.get("token") and not user:
        await Hub.send(ws, {"type": "error", "code": "bad_token", "message": "Session expired. Sign in again."})
    lounge_id = hello.get("lounge")
    if lounge_id and not (user and ctrl.store.is_lounge_member(str(lounge_id), user["id"])):
        lounge_id = None
    conn = PlayerConn(ws=ws, user=user, lounge_id=lounge_id)
    ctrl.hub.players.add(conn)
    ctrl.request_admin_push()
    try:
        await ctrl.send_state(conn, "sync")
        while True:
            msg = await _receive_json(ws)
            kind = msg.get("type") if msg else None
            if kind == "predict":
                if not user:
                    await Hub.send(ws, {"type": "error", "message": "Sign in to make picks."})
                    continue
                try:
                    pred = await ctrl.submit_prediction(user, PredictionIn.model_validate(msg))
                    await Hub.send(ws, {"type": "prediction_saved", "prediction": pred})
                except ValidationError as exc:
                    await Hub.send(ws, {"type": "error", "message": _validation_message(exc)})
                except GameError as exc:
                    await Hub.send(ws, {"type": "error", "message": exc.message})
            elif kind == "sync":
                await ctrl.send_state(conn, "sync")
            elif kind == "ping":
                await Hub.send(ws, {"type": "pong", "server_time": time.time()})
            else:
                await Hub.send(ws, {"type": "error", "message": "Unknown message."})
    except WebSocketDisconnect:
        pass
    finally:
        ctrl.hub.players.discard(conn)
        ctrl.request_admin_push()


@router.websocket("/ws/admin")
async def admin_socket(ws: WebSocket) -> None:
    """Admin channel: ``auth {key}`` first, then ``{action, request_id, ...payload}``."""
    ctrl: GameController = ws.app.state.ctrl
    settings: Settings = ws.app.state.settings
    await ws.accept()
    try:
        hello = await _receive_json(ws, HELLO_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=4408)
        return
    key = str((hello or {}).get("key", ""))
    if not hello or hello.get("type") != "auth" or not secrets.compare_digest(
        key.encode(), settings.admin_key.encode()
    ):
        await Hub.send(ws, {"type": "auth_error", "message": "Invalid admin key."})
        await ws.close(code=4401)
        return

    ctrl.hub.admins.add(ws)
    try:
        await Hub.send(ws, ctrl.admin_message("sync"))
        while True:
            msg = await _receive_json(ws) or {}
            if msg.get("type") == "ping":  # client keep-alive (also keeps sleepy hosts awake)
                await Hub.send(ws, {"type": "pong", "server_time": time.time()})
                continue
            action, request_id = msg.pop("action", None), msg.pop("request_id", None)
            if action == "sync":
                await Hub.send(ws, ctrl.admin_message("sync"))
                continue
            reply: dict[str, Any] = {"type": "admin_ack", "action": action, "request_id": request_id}
            if action not in ADMIN_ACTIONS:
                reply |= {"ok": False, "error": f"Unknown action: {action!r}"}
            else:
                model, handler = ADMIN_ACTIONS[action]
                try:
                    reply |= {"ok": True, "result": await handler(ctrl, model.model_validate(msg))}
                except ValidationError as exc:
                    reply |= {"ok": False, "error": _validation_message(exc)}
                except GameError as exc:
                    reply |= {"ok": False, "error": exc.message}
            await Hub.send(ws, reply)
    except WebSocketDisconnect:
        pass
    finally:
        ctrl.hub.admins.discard(ws)
        ctrl.request_admin_push()


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.admin_key == DEFAULT_ADMIN_KEY:
            log.warning("Using the default admin key %r. Set PTP_ADMIN_KEY before going live.",
                        DEFAULT_ADMIN_KEY)
        store = Store(settings.db_path)
        ctrl = GameController(store, Hub(), settings)
        app.state.ctrl = ctrl
        await ctrl.recover()
        try:
            yield
        finally:
            await ctrl.shutdown()
            store.close()

    app = FastAPI(title="Pick the Play: Live Pro Football Game", lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(router)

    @app.exception_handler(GameError)
    async def game_error_handler(request: Request, exc: GameError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return app


app = create_app()


def lan_ip() -> str | None:
    """Best guess at this computer's Wi-Fi/LAN address.

    Connecting a UDP socket only picks a route; no packets are sent.
    """
    import socket

    for probe in ("8.8.8.8", "192.168.0.1", "10.0.0.1"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect((probe, 80))
                ip = s.getsockname()[0]
            except OSError:
                continue
        if not ip.startswith(("127.", "169.254.", "0.")):
            return ip
    return None


def port_is_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":  # like uvicorn: don't trip over sockets lingering in TIME_WAIT
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Pick the Play server.")
    parser.add_argument("--phone", action="store_true",
                        help="listen on your Wi-Fi network so phones on it can connect")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    cli = parser.parse_args()
    host = "0.0.0.0" if cli.phone else os.environ.get("HOST", "127.0.0.1")

    if not port_is_free(host, cli.port):
        sys.exit(f"\n  Port {cli.port} is already in use: Pick the Play is probably already running in another window."
                 "\n  Use that window, or click in it, press Ctrl+C to stop it, and start again here.\n")

    logging.basicConfig(level=logging.INFO)
    if host == "0.0.0.0":
        ip = lan_ip()
        lines = (
            [f"On your iPhone (same Wi-Fi), open:  http://{ip}:{cli.port}/",
             f"Admin console:                      http://{ip}:{cli.port}/admin"]
            if ip else
            ["Could not detect this computer's Wi-Fi address. Look it up in your network",
             f"settings and open http://<that-address>:{cli.port}/ on your iPhone."]
        )
        lines += ["",
                  "Keep this window open while you play. Press Ctrl+C to stop.",
                  "iPhone can't connect? Use the same Wi-Fi (not a guest network), turn off",
                  "VPNs, and allow Python through this computer's firewall."]
        print("\n" + "\n".join(f"  {line}" if line else "" for line in lines) + "\n", flush=True)
    # The hub lives in process memory, so run a single worker.
    uvicorn.run("app:app", host=host, port=cli.port, reload=bool(os.environ.get("PTP_RELOAD")))
