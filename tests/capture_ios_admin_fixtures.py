"""Capture the host-console fixtures (ios/PickThePlayTests/Fixtures/admin_*.json) from a real running server.

Usage (from the repo root):  venv/bin/python tests/capture_ios_admin_fixtures.py [out_dir]

Starts the server on a random port with a throwaway database and the recorded practice game ("demo") as the
live-data source, then plays a few plays like a host would and writes each message the admin socket receives.
AdminModels.swift is decoded from these files by ContractTests.swift, so regenerate them when the admin protocol
changes.
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

tmp = tempfile.mkdtemp(prefix="ptp-admin-fixtures-")  # never touches your game.db
env = {**os.environ, "PTP_DB_PATH": os.path.join(tmp, "game.db"), "PTP_ADMIN_KEY": ADMIN_KEY,
       "TANK01_API_KEY": "", "TANK01_DEMO_LAG": "1", "TANK01_FIRST_DELAY": "1", "TANK01_FAST_INTERVAL": "1",
       "TANK01_AUTO_SCORE_GRACE": "30"}
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
    cwd=REPO, env=env)
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


def recv(ws, pred, limit=200, timeout=15):
    for _ in range(limit):
        msg = json.loads(ws.recv(timeout=timeout))
        if pred(msg):
            return msg
    raise AssertionError("message never arrived")


def is_state(pred=lambda m: True):
    return lambda m: m.get("type") == "admin_state" and pred(m)


def poll(admin, pred, timeout=40):
    """Ask for the state until it satisfies ``pred`` (pushes may already have been read past by an ack)."""
    end = time.time() + timeout
    while time.time() < end:
        admin.send(json.dumps({"action": "sync"}))
        state = recv(admin, is_state())
        if pred(state):
            return state
        time.sleep(0.6)
    raise AssertionError("state never matched")


try:
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/healthz")
            break
        except Exception:
            time.sleep(0.1)

    with connect(WS + "/ws/admin") as bad:
        bad.send(json.dumps({"type": "auth", "key": "not-the-key"}))
        save("admin_auth_error", recv(bad, lambda m: m.get("type") == "auth_error"))

    joey = http("POST", "/api/users", {"username": "JoeyC"}, expect=201)
    sam = http("POST", "/api/users", {"username": "Sam"}, expect=201)

    with connect(WS + "/ws/admin") as admin, connect(WS + "/ws") as player:
        admin.send(json.dumps({"type": "auth", "key": ADMIN_KEY}))
        save("admin_state_nogame", recv(admin, is_state()))
        save("rest_admin_players", http("GET", "/api/admin/players", admin=True, expect=200))
        save("rest_admin_feed_games", http("GET", "/api/admin/feed/games?date=20261008", admin=True, expect=200))

        def act(action, **payload):
            admin.send(json.dumps({"action": action, "request_id": action, **payload}))
            ack = recv(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == action)
            return ack

        ack = act("create_game", away_name="Carolina", away_primary="#0085ca", away_secondary="#101820",
                  home_name="Washington", home_primary="#5a1414", home_secondary="#ffb612", feed_game_id="demo")
        assert ack["ok"], ack
        save("admin_ack_ok", ack)
        save("admin_state_game_demo", poll(admin, lambda m: m["game"] and m["feed"]["linked"]))

        bad_ack = act("open_play", down=9)
        assert not bad_ack["ok"]
        save("admin_ack_error", bad_ack)

        player.send(json.dumps({"type": "hello", "token": joey["token"]}))
        ack = act("open_play", down=1, distance="10")
        assert ack["ok"], ack
        play = ack["result"]
        player.send(json.dumps({"type": "predict", "play_id": play["id"], "play_type": "RUN", "direction": "LEFT",
                                "yardage": "SHORT"}))
        http("POST", "/api/predictions", {"play_id": play["id"], "play_type": "PASS", "direction": "RIGHT",
                                          "yardage": "MEDIUM"}, token=sam["token"], expect=200)
        time.sleep(0.5)
        save("admin_state_play_open", poll(admin, lambda m: m["play"] and m["play"]["state"] == "OPEN"
                                           and m["pick_stats"] and m["pick_stats"]["total"] == 2))

        assert act("lock_play")["ok"]
        sug = poll(admin, lambda m: m["feed"] and m["feed"]["suggestion"])
        save("admin_state_suggestion", sug)
        assert act("feed_hold")["ok"]
        save("admin_state_suggestion_held",
             poll(admin, lambda m: m["feed"]["suggestion"] and m["feed"]["suggestion"]["status"] == "held"))
        accept = act("feed_accept", play_id=sug["feed"]["suggestion"]["play_id"])
        assert accept["ok"], accept
        save("admin_ack_feed_accept", accept)
        save("admin_state_play_resolved", poll(admin, lambda m: m["play"] and m["play"]["state"] == "RESOLVED"
                                               and m["history"]))

        # A second play, voided by hand, to get a voided row (the database stores voided as 0/1).
        assert act("open_play", down=2, distance="3")["ok"]
        assert act("void_play")["ok"]
        save("admin_state_play_voided", poll(admin, lambda m: m["history"] and m["history"][0]["voided"]))

        sent = act("announce", text="Halftime! Back in about 15 minutes.")
        assert sent["ok"]
        save("admin_ack_announce", sent)
        save("admin_state_announcement", poll(admin, lambda m: m["announcement"]))
        assert act("feed_pause")["ok"]
        save("admin_state_feed_paused", poll(admin, lambda m: m["feed"]["paused"]))
finally:
    server.terminate()
    server.wait(timeout=10)
    print("server stopped")
