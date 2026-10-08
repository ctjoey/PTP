"""Capture the iOS contract fixtures (ios/PickThePlayTests/Fixtures) from a real running server.

Usage (from the repo root):  venv/bin/python tests/capture_ios_fixtures.py [out_dir]

Starts the server with uvicorn on a random free port with a throwaway database, plays a short
scripted game as JoeyC and Sam (Chicago at Detroit, lounge "Sunday Crew"), and writes each message
JoeyC's app receives as pretty JSON. ContractTests.swift decodes these files, so regenerate them
whenever the protocol changes, then update the Swift tests to match.

  Play 1, 3rd & 7:  JoeyC Pass/Middle/Medium, Sam Run/Middle/Medium (sent as the old "CENTER", saved as
                    MIDDLE) -> Pass over the middle for 7 yards (Medium):
                    JoeyC 40 (all three + the bonus), Sam 20 (direction + distance)
  Play 2, 2nd & 10: JoeyC Pass/Right/Short, Sam Run/Right/Short -> a pass for a loss of 4 (Loss):
                    JoeyC 20 (no distance points for a loss), Sam 10
  Play 3, 1st & 10: voided; then the game goes FINAL.
  Host tools: a banner is sent and cleared, and a removed player is signed out (msg_announcement*, msg_account_removed).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from websockets.sync.client import connect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "ios", "PickThePlayTests", "Fixtures")
ADMIN_KEY = "fixture-admin"

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}"

tmp = tempfile.mkdtemp(prefix="ptp-fixtures-")  # never touches your game.db
env = {**os.environ, "PTP_DB_PATH": os.path.join(tmp, "game.db"), "PTP_ADMIN_KEY": ADMIN_KEY}
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT),
     "--log-level", "warning"],
    cwd=REPO, env=env,
)
print("server pid", server.pid, "port", PORT, flush=True)


def save(name, obj):
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        f.write(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    print("wrote", name)


def http(method, path, body=None, token=None, admin=False, expect=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if admin:
        headers["X-Admin-Key"] = ADMIN_KEY
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req) as res:
            status, data = res.status, res.read()
    except urllib.error.HTTPError as err:
        status, data = err.code, err.read()
    if expect is not None:
        assert status == expect, (path, status, data)
    return json.loads(data) if data else None


def recv(ws, pred, limit=50):
    for _ in range(limit):
        msg = json.loads(ws.recv(timeout=10))
        if pred(msg):
            return msg
    raise AssertionError("message never arrived")


state = lambda event: (lambda m: m.get("type") == "state" and m.get("event") == event)  # noqa: E731

try:
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/healthz")
            break
        except Exception:
            time.sleep(0.1)

    joey = http("POST", "/api/users", {"username": "JoeyC"}, expect=201)
    save("rest_create_user", joey)
    sam = http("POST", "/api/users", {"username": "Sam"}, expect=201)
    save("rest_error", http("POST", "/api/users", {"username": "JoeyC"}, expect=409))
    lounge = http("POST", "/api/lounges", {"name": "Sunday Crew"}, token=joey["token"], expect=201)
    save("rest_lounge", lounge)
    http("POST", f"/api/lounges/{lounge['id']}/join", token=sam["token"], expect=200)
    save("rest_me", http("GET", "/api/me", token=joey["token"], expect=200))

    with connect(WS + "/ws") as bad:
        bad.send(json.dumps({"type": "hello", "token": "expired-token"}))
        save("msg_bad_token", recv(bad, lambda m: m.get("type") == "error"))

    with connect(WS + "/ws") as j, connect(WS + "/ws/admin") as admin:
        j.send(json.dumps({"type": "hello", "token": joey["token"], "lounge": lounge["id"]}))
        save("state_sync_nogame", recv(j, state("sync")))
        save("msg_announcement_none", recv(j, lambda m: m["type"] == "announcement"))   # told on connect: nothing showing
        admin.send(json.dumps({"type": "auth", "key": ADMIN_KEY}))
        recv(admin, lambda m: m["type"] == "admin_state")

        def act(action, **payload):
            admin.send(json.dumps({"action": action, "request_id": action, **payload}))
            ack = recv(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == action)
            assert ack["ok"], ack
            return ack["result"]

        act("create_game", away_name="Chicago", away_primary="#0B162A", away_secondary="#C83803",
            home_name="Detroit", home_primary="#0076B6", home_secondary="#B0B7BC")
        save("state_game_created", recv(j, state("game_created")))

        # The host's banner: every connection is told what it is on connect (msg_announcement_none, above), then the
        # host sends one, then clears it. A removed player's socket gets the reason and closes.
        act("announce", text="Halftime! Back in about 15 minutes.")
        save("msg_announcement", recv(j, lambda m: m["type"] == "announcement" and m["text"]))
        act("announce", text="")
        save("msg_announcement_cleared", recv(j, lambda m: m["type"] == "announcement" and not m["text"]))
        troll = http("POST", "/api/users", {"username": "Troll"}, expect=201)
        with connect(WS + "/ws") as t:
            t.send(json.dumps({"type": "hello", "token": troll["token"]}))
            recv(t, state("sync"))
            act("remove_player", user_id=troll["id"], block=True)
            save("msg_account_removed", recv(t, lambda m: m.get("type") == "error"))
        recv(j, state("leaderboard_updated"))

        # Play 1, 3rd & 7: JoeyC Pass/Middle/Medium, Sam Run/Middle/Medium (an older app's "CENTER").
        # Pass over the middle for 7 yards: JoeyC's perfect call is 40.
        play = act("open_play", down=3, distance="7")
        save("state_play_opened", recv(j, state("play_opened")))
        j.send(json.dumps({"type": "predict", "play_id": play["id"], "play_type": "PASS", "direction": "MIDDLE",
                           "yardage": "MEDIUM"}))
        save("msg_prediction_saved", recv(j, lambda m: m["type"] == "prediction_saved"))
        sam_pick = http("POST", "/api/predictions", {"play_id": play["id"], "play_type": "RUN", "direction": "CENTER",
                                                     "yardage": "MEDIUM"}, token=sam["token"], expect=200)
        assert sam_pick["direction"] == "MIDDLE", sam_pick
        j.send(json.dumps({"type": "predict", "play_id": play["id"], "play_type": "BOMB", "direction": "MIDDLE",
                           "yardage": "MEDIUM"}))
        save("msg_error", recv(j, lambda m: m["type"] == "error"))
        j.send(json.dumps({"type": "ping"}))
        save("msg_pong", recv(j, lambda m: m["type"] == "pong"))
        act("lock_play")
        save("state_play_locked", recv(j, state("play_locked")))
        act("resolve_play", play_type="PASS", direction="MIDDLE", yardage="MEDIUM", yards=7)
        resolved = recv(j, state("play_resolved"))
        assert resolved["my_prediction"]["points_earned"] == 40, resolved["my_prediction"]
        save("state_play_resolved", resolved)

        # Play 2, 2nd & 10: JoeyC Pass/Right/Short, Sam Run/Right/Short. A pass for a loss of 4.
        play = act("open_play", down=2, distance="10")
        recv(j, state("play_opened"))
        j.send(json.dumps({"type": "predict", "play_id": play["id"], "play_type": "PASS", "direction": "RIGHT",
                           "yardage": "SHORT"}))
        recv(j, lambda m: m["type"] == "prediction_saved")
        http("POST", "/api/predictions", {"play_id": play["id"], "play_type": "RUN", "direction": "RIGHT",
                                          "yardage": "SHORT"}, token=sam["token"], expect=200)
        act("lock_play")
        recv(j, state("play_locked"))
        act("resolve_play", play_type="PASS", direction="RIGHT", yards=-4)
        save("state_play_resolved_loss", recv(j, state("play_resolved")))

        # Play 3, 1st & 10: flag on the play, voided.
        act("open_play", down=1, distance="10")
        recv(j, state("play_opened"))
        act("void_play")
        save("state_play_voided", recv(j, state("play_voided")))
        act("set_status", status="FINAL")
        save("state_final", recv(j, state("game_status")))
finally:
    server.terminate()
    server.wait(timeout=10)
    print("server stopped")
