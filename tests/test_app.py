"""End-to-end tests over HTTP and the real WebSocket endpoints."""

from fastapi.testclient import TestClient

from app import Settings, create_app
from tests.conftest import ADMIN_KEY, GAME


def recv_until(ws, predicate, limit=25):
    """Read socket messages until one matches (skipping throttled stat pushes etc.)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("expected message never arrived")


def state_event(event):
    return lambda m: m.get("type") == "state" and m.get("event") == event


def register(client, name):
    res = client.post("/api/users", json={"username": name})
    assert res.status_code == 201, res.text
    return res.json()


def player_socket(client, token=None, lounge=None):
    ws = client.websocket_connect("/ws")
    sock = ws.__enter__()
    sock.send_json({"type": "hello", "token": token, "lounge": lounge})
    first = recv_until(sock, state_event("sync"))
    return ws, sock, first


def test_pages_render(client):
    for path in ("/", "/admin"):
        res = client.get(path)
        assert res.status_code == 200
        assert "Pick the Play" in res.text
    assert client.get("/static/js/player.js").status_code == 200
    missing = client.get("/lounge/4321", follow_redirects=False)
    assert missing.status_code == 303 and missing.headers["location"] == "/?missing_lounge=4321"
    assert client.get("/healthz").json() == {"ok": True}


def test_admin_api_requires_key(client, admin_headers):
    assert client.post("/api/admin/game", json=GAME).status_code == 401
    assert client.post("/api/admin/game", json=GAME, headers={"X-Admin-Key": "nope"}).status_code == 401
    res = client.post("/api/admin/game", json=GAME, headers=admin_headers)
    assert res.status_code == 201 and res.json()["status"] == "SCHEDULED"


def test_trademark_names_rejected_over_http(client, admin_headers):
    res = client.post("/api/admin/game", json={**GAME, "home_name": "Green Bay Packers"}, headers=admin_headers)
    assert res.status_code == 400 and "protected" in res.json()["detail"]


def test_player_auth_endpoints(client):
    user = register(client, "Joey")
    assert client.post("/api/users", json={"username": "joey"}).status_code == 409
    me = client.get("/api/me", headers={"Authorization": f"Bearer {user['token']}"})
    assert me.status_code == 200 and me.json()["user"]["username"] == "Joey"
    assert client.get("/api/me", headers={"Authorization": "Bearer bogus"}).status_code == 401


def test_full_live_flow_over_websockets(client):
    alice, bob = register(client, "alice"), register(client, "bob")

    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        assert recv_until(admin, lambda m: m["type"] == "admin_state")["game"] is None

        a_cm, a, a_first = player_socket(client, alice["token"])
        b_cm, b, _ = player_socket(client, bob["token"])
        assert a_first["game"] is None and a_first["me"]["username"] == "alice"

        admin_seen = []  # broadcasts arrive before the ack, so keep what we skip past

        def admin_do(action, **payload):
            admin.send_json({"action": action, "request_id": action, **payload})

            def is_ack(m):
                admin_seen.append(m)
                return m["type"] == "admin_ack" and m["request_id"] == action

            ack = recv_until(admin, is_ack)
            assert ack["ok"], ack
            return ack["result"]

        admin_do("create_game", **GAME)
        created = recv_until(a, state_event("game_created"))
        assert created["game"]["home_name"] == "Green Bay" and created["play"] is None

        play = admin_do("open_play", down=3, distance="7")
        opened = recv_until(a, state_event("play_opened"))
        recv_until(b, state_event("play_opened"))
        assert opened["play"]["state"] == "OPEN" and opened["game"]["status"] == "LIVE"
        assert 14 < opened["play"]["locks_at"] - opened["server_time"] <= 15
        assert opened["crowd"] is None  # split stays hidden while picking

        a.send_json({"type": "predict", "play_id": play["id"], "play_type": "PASS", "direction": "LEFT"})
        assert recv_until(a, lambda m: m["type"] == "prediction_saved")["prediction"]["play_type"] == "PASS"
        b.send_json({"type": "predict", "play_id": play["id"], "play_type": "RUN", "direction": "LEFT"})
        recv_until(b, lambda m: m["type"] == "prediction_saved")
        b.send_json({"type": "predict", "play_id": play["id"], "play_type": "BOMB", "direction": "LEFT"})
        assert recv_until(b, lambda m: m["type"] == "error")["message"]

        admin_do("lock_play")
        locked = recv_until(a, state_event("play_locked"))
        recv_until(b, state_event("play_locked"))
        assert locked["play"]["state"] == "LOCKED"
        assert locked["crowd"]["total"] == 2 and locked["crowd"]["LEFT"] == 2
        assert locked["my_prediction"]["direction"] == "LEFT"

        b.send_json({"type": "predict", "play_id": play["id"], "play_type": "PASS", "direction": "LEFT"})
        assert "locked" in recv_until(b, lambda m: m["type"] == "error")["message"]

        admin_do("resolve_play", play_type="PASS", direction="LEFT")
        res_a = recv_until(a, state_event("play_resolved"))
        res_b = recv_until(b, state_event("play_resolved"))
        assert res_a["play"]["correct_play_type"] == "PASS"
        assert res_a["my_prediction"]["points_earned"] == 30 and res_a["my_prediction"]["type_correct"]
        assert res_b["my_prediction"]["points_earned"] == 10
        assert res_a["me"] == {**res_a["me"], "game_score": 30, "rank": 1, "total_score": 30}
        assert res_b["me"]["rank"] == 2
        assert [r["username"] for r in res_a["leaderboard"]] == ["alice", "bob"]

        admin_state = next(m for m in admin_seen if m["type"] == "admin_state" and m["event"] == "play_resolved")
        assert admin_state["history"][0]["exact_hits"] == 1 and admin_state["ranked_players"] == 2

        # Errors come back as a failed ack rather than a dropped socket.
        admin.send_json({"action": "lock_play", "request_id": "again"})
        assert not recv_until(admin, lambda m: m["type"] == "admin_ack")["ok"]

        a_cm.__exit__(None, None, None)
        b_cm.__exit__(None, None, None)


def test_lounge_leaderboard_over_websocket(client, admin_headers):
    host, friend, outsider = register(client, "host"), register(client, "friend"), register(client, "outsider")
    auth = lambda u: {"Authorization": f"Bearer {u['token']}"}  # noqa: E731

    lounge = client.post("/api/lounges", json={"name": "Sunday Crew"}, headers=auth(host)).json()
    code = lounge["id"]
    assert client.get(f"/lounge/{code}").status_code == 200
    assert client.post(f"/api/lounges/{code}/join", headers=auth(friend)).json()["member_count"] == 2

    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    cm, sock, first = player_socket(client, friend["token"], lounge=code)
    assert first["lounge"]["name"] == "Sunday Crew"
    assert {r["username"] for r in first["lounge"]["leaderboard"]} == {"host", "friend"}

    # Non-members asking for the lounge simply don't get it.
    cm2, sock2, other = player_socket(client, outsider["token"], lounge=code)
    assert other["lounge"] is None

    play = client.post("/api/admin/play/open", json={"down": 1, "distance": "10"}, headers=admin_headers).json()
    res = client.post("/api/predictions", headers=auth(friend),
                      json={"play_id": play["id"], "play_type": "RUN", "direction": "RIGHT"})
    assert res.status_code == 200
    client.post("/api/admin/play/lock", headers=admin_headers)
    client.post("/api/admin/play/resolve", json={"play_type": "RUN", "direction": "LEFT"}, headers=admin_headers)

    resolved = recv_until(sock, state_event("play_resolved"))
    rows = resolved["lounge"]["leaderboard"]
    assert [(r["username"], r["score"], r["is_host"]) for r in rows] == [("friend", 10, False), ("host", 0, True)]
    cm.__exit__(None, None, None)
    cm2.__exit__(None, None, None)


def test_admin_socket_rejects_bad_key(client):
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": "wrong"})
        assert admin.receive_json()["type"] == "auth_error"


def test_bad_token_is_reported(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": "not-a-real-token"})
        assert ws.receive_json()["code"] == "bad_token"
        assert ws.receive_json()["me"] is None  # still gets a spectator snapshot


def test_play_auto_locks_when_timer_expires(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), admin_key=ADMIN_KEY, window_seconds=0.3, grace_seconds=0.0)
    with TestClient(create_app(settings)) as client:
        headers = {"X-Admin-Key": ADMIN_KEY}
        client.post("/api/admin/game", json=GAME, headers=headers)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello"})
            recv_until(ws, state_event("sync"))
            client.post("/api/admin/play/open", json={}, headers=headers)
            recv_until(ws, state_event("play_opened"))
            locked = recv_until(ws, state_event("play_locked"))
            assert locked["play"]["state"] == "LOCKED"
