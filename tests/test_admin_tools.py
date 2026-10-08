"""Host tools: remove a player (and block their name) and send a message to the players."""

import pytest
from starlette.websockets import WebSocketDisconnect

from models import GameError
from tests.conftest import ADMIN_KEY, GAME
from tests.test_app import recv_until, register, state_event
from tests.test_app_store import auth, play_one


def hello(ws, token=None):
    """Open a player socket: wait for the state, then for the banner every connection is told about."""
    ws.send_json({"type": "hello", "token": token} if token else {"type": "hello"})
    recv_until(ws, state_event("sync"))
    return recv_until(ws, lambda m: m["type"] == "announcement")


def admin_post(client, admin_headers, path, body=None):
    return client.post(f"/api/admin/{path}", json=body or {}, headers=admin_headers)


def players(client, admin_headers):
    res = client.get("/api/admin/players", headers=admin_headers)
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def test_blocked_names_cannot_be_registered_again(store):
    store.create_user("Troll One")
    assert not store.is_name_blocked("troll one")
    store.block_name("Troll One")
    store.block_name("TROLL  one")                        # the same name again: no error, still one entry
    assert store.blocked_names() == ["Troll One"]
    assert store.is_name_blocked("TROLL ONE") and store.is_name_blocked("troll  one")
    assert not store.is_name_blocked("Troll Two")
    with pytest.raises(GameError) as blocked:
        store.create_user("troll one")
    assert blocked.value.message == "Please choose a different name." and blocked.value.status_code == 400
    assert store.unblock_name("TROLL ONE") is True and store.unblock_name("TROLL ONE") is False
    with pytest.raises(GameError) as taken:               # unblocked: now it is simply taken by the existing account
        store.create_user("troll one")
    assert taken.value.status_code == 409


def test_list_players_newest_first_with_game_points(store):
    a, b = store.create_user("alice"), store.create_user("bob")
    assert [p["username"] for p in store.list_players(None)] == ["bob", "alice"]
    assert all(p["picks"] == 0 and p["game_score"] == 0 for p in store.list_players(None))
    assert store.count_users() == 2
    assert len(store.list_players(None, limit=1)) == 1
    assert {p["id"] for p in store.list_players(123)} == {a["id"], b["id"]}


# --------------------------------------------------------------------------- #
# Remove a player
# --------------------------------------------------------------------------- #


def test_players_list_needs_the_admin_key(client):
    assert client.get("/api/admin/players").status_code == 401
    assert client.get("/api/admin/players", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.post("/api/admin/player/remove", json={"user_id": 1}).status_code == 401
    assert client.post("/api/admin/announce", json={"text": "hi"}).status_code == 401


def test_players_list_shows_points_and_who_is_online(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play_one(client, admin_headers, [(alice, "PASS", "LEFT", "MEDIUM"), (bob, "RUN", "RIGHT", "MEDIUM")])
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": alice["token"]})
        recv_until(ws, state_event("sync"))
        data = players(client, admin_headers)
    assert data["count"] == 2 and data["blocked_names"] == []
    by_name = {p["username"]: p for p in data["players"]}
    assert [p["username"] for p in data["players"]] == ["bob", "alice"]
    assert by_name["alice"]["game_score"] == 40 and by_name["alice"]["picks"] == 1 and by_name["alice"]["online"] is True
    assert by_name["bob"]["game_score"] == 10 and by_name["bob"]["online"] is False


def test_remove_player_over_rest(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play_one(client, admin_headers, [(alice, "PASS", "LEFT", "MEDIUM"), (bob, "RUN", "RIGHT", "MEDIUM")])
    lounge = client.post("/api/lounges", json={"name": "Alice Crew"}, headers=auth(alice)).json()

    res = admin_post(client, admin_headers, "player/remove", {"user_id": alice["id"]})
    assert res.status_code == 200 and res.json() == {"removed": "alice", "blocked": False}

    assert client.get("/api/me", headers=auth(alice)).status_code == 401          # signed out everywhere
    assert client.get(f"/api/lounges/{lounge['id']}").status_code == 404           # her lounge went with her
    assert [(r["username"], r["score"]) for r in client.get("/api/state").json()["leaderboard"]] == [("bob", 10)]
    assert client.get("/api/me", headers=auth(bob)).json()["user"]["total_score"] == 10
    assert client.post("/api/users", json={"username": "alice"}).status_code == 201   # not blocked: the name is free again

    gone = admin_post(client, admin_headers, "player/remove", {"user_id": alice["id"]})
    assert gone.status_code == 404 and gone.json()["detail"] == "That player is already gone."
    assert admin_post(client, admin_headers, "player/remove", {}).status_code == 422


def test_remove_and_block_keeps_the_name_out(client, admin_headers):
    troll = register(client, "Bad Actor")
    res = admin_post(client, admin_headers, "player/remove", {"user_id": troll["id"], "block": True})
    assert res.json() == {"removed": "Bad Actor", "blocked": True}
    again = client.post("/api/users", json={"username": "bad actor"})
    assert again.status_code == 400 and again.json()["detail"] == "Please choose a different name."
    assert players(client, admin_headers)["blocked_names"] == ["Bad Actor"]
    assert client.post("/api/users", json={"username": "Bad Actor 2"}).status_code == 201   # only that name


def test_remove_player_signs_out_their_sockets_and_refreshes_the_rest(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play_one(client, admin_headers, [(alice, "PASS", "LEFT", "MEDIUM"), (bob, "RUN", "RIGHT", "MEDIUM")])
    ctrl = client.app.state.ctrl
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_json({"type": "hello", "token": alice["token"]})
        recv_until(a, state_event("sync"))
        b.send_json({"type": "hello", "token": bob["token"]})
        recv_until(b, state_event("sync"))

        assert admin_post(client, admin_headers, "player/remove", {"user_id": alice["id"]}).status_code == 200

        err = recv_until(a, lambda m: m["type"] == "error")
        assert err == {"type": "error", "code": "account_deleted", "message": "You were removed by the host."}
        with pytest.raises(WebSocketDisconnect) as closed:
            a.receive_json()
        assert closed.value.code == 4401
        assert not any(c.user and c.user["id"] == alice["id"] for c in ctrl.hub.players)
        update = recv_until(b, state_event("leaderboard_updated"))
        assert [r["username"] for r in update["leaderboard"]] == ["bob"]


def test_remove_player_over_the_admin_socket(client):
    alice = register(client, "alice")
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        first = recv_until(admin, lambda m: m["type"] == "admin_state")
        assert first["registered_players"] == 1
        admin.send_json({"action": "remove_player", "request_id": 7, "user_id": alice["id"], "block": True})
        ack = recv_until(admin, lambda m: m["type"] == "admin_ack")
        assert ack["ok"] is True and ack["request_id"] == 7 and ack["result"] == {"removed": "alice", "blocked": True}
        admin.send_json({"action": "sync"})                # (state pushes can arrive before the ack, so ask again)
        assert recv_until(admin, lambda m: m["type"] == "admin_state")["registered_players"] == 0
        admin.send_json({"action": "remove_player", "request_id": 8, "user_id": 999})
        bad = recv_until(admin, lambda m: m["type"] == "admin_ack" and m["request_id"] == 8)
        assert bad["ok"] is False and bad["error"] == "That player is already gone."


# --------------------------------------------------------------------------- #
# Message to the players
# --------------------------------------------------------------------------- #


def test_announce_reaches_everyone_connected_and_late_joiners(client, admin_headers):
    alice = register(client, "alice")
    with client.websocket_connect("/ws") as signed_in, client.websocket_connect("/ws") as spectator:
        assert hello(signed_in, alice["token"])["text"] == "" and hello(spectator)["text"] == ""   # nothing showing yet

        res = admin_post(client, admin_headers, "announce", {"text": "  Kickoff   is delayed\n10 minutes  "})
        assert res.status_code == 200
        assert res.json()["text"] == "Kickoff is delayed 10 minutes" and res.json()["sent_to"] == 2
        for ws in (signed_in, spectator):
            msg = recv_until(ws, lambda m: m["type"] == "announcement")
            assert msg["text"] == "Kickoff is delayed 10 minutes" and msg["id"] == res.json()["id"]
            assert isinstance(msg["sent_at"], float)

        # Someone who opens the page later still sees it.
        with client.websocket_connect("/ws") as late:
            assert hello(late)["text"] == "Kickoff is delayed 10 minutes"

        # Clearing hides it for everyone and for later arrivals.
        cleared = admin_post(client, admin_headers, "announce", {"text": "   "}).json()
        assert cleared["text"] == ""
        for ws in (signed_in, spectator):
            msg = recv_until(ws, lambda m: m["type"] == "announcement")
            assert msg["text"] == "" and msg["id"] == cleared["id"] and msg["id"] > res.json()["id"]
        # ... and a connection that opens after the clear is told there is nothing to show (so a phone that was asleep
        # when the host cleared the banner drops the stale one when it reconnects).
        with client.websocket_connect("/ws") as late:
            banner = hello(late)
            assert banner["text"] == "" and banner["id"] == cleared["id"]


def test_announce_validation_and_admin_view(client, admin_headers):
    too_long = admin_post(client, admin_headers, "announce", {"text": "x" * 201})
    assert too_long.status_code == 422
    assert admin_post(client, admin_headers, "announce", {"text": "x" * 200}).status_code == 200
    assert client.get("/api/admin/state", headers=admin_headers).json()["announcement"]["text"] == "x" * 200
    admin_post(client, admin_headers, "announce", {})
    assert client.get("/api/admin/state", headers=admin_headers).json()["announcement"] is None


def test_announce_over_the_admin_socket(client):
    with client.websocket_connect("/ws/admin") as admin, client.websocket_connect("/ws") as player:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        recv_until(admin, lambda m: m["type"] == "admin_state")
        hello(player)
        admin.send_json({"action": "announce", "request_id": 1, "text": "Halftime: back in 15"})
        ack = recv_until(admin, lambda m: m["type"] == "admin_ack")
        assert ack["ok"] and ack["result"]["sent_to"] == 1
        assert recv_until(player, lambda m: m["type"] == "announcement")["text"] == "Halftime: back in 15"
        admin.send_json({"action": "sync"})
        state = recv_until(admin, lambda m: m["type"] == "admin_state")
        assert state["announcement"]["text"] == "Halftime: back in 15"


def test_the_message_is_plain_text_on_the_player_page():
    """The banner is filled with textContent, never innerHTML, so a message cannot inject markup."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static/js/player.js").read_text()
    body = js[js.index("function showAnnouncement"):]
    body = body[:body.index("\n  }\n")]
    assert "textContent" in body and "innerHTML" not in body


def test_a_new_connection_is_always_told_what_the_banner_is(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello"})
        recv_until(ws, state_event("sync"))
        banner = recv_until(ws, lambda m: m["type"] == "announcement")
    assert banner["text"] == "" and isinstance(banner["id"], int)


def test_banner_ids_keep_growing_across_server_restarts(tmp_path):
    """A player who dismissed banner N must not have a later banner (after a restart) hidden because its id is also N."""
    import time

    from fastapi.testclient import TestClient

    from app import Settings, create_app

    ids = []
    for _ in range(2):
        settings = Settings(db_path=str(tmp_path / "app.db"), admin_key=ADMIN_KEY, window_seconds=15)
        with TestClient(create_app(settings)) as c:
            ids.append(c.post("/api/admin/announce", json={"text": "hi"}, headers={"X-Admin-Key": ADMIN_KEY}).json()["id"])
        time.sleep(0.01)
    assert ids[1] > ids[0]


# --------------------------------------------------------------------------- #
# More removal cases
# --------------------------------------------------------------------------- #


def test_removing_a_lounge_host_with_connected_members(client, admin_headers):
    host, member = register(client, "host"), register(client, "member")
    lounge = client.post("/api/lounges", json={"name": "Host Crew"}, headers=auth(host)).json()
    assert client.post(f"/api/lounges/{lounge['id']}/join", headers=auth(member)).status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": member["token"], "lounge": lounge["id"]})
        first = recv_until(ws, state_event("sync"))
        assert first["lounge"]["name"] == "Host Crew"
        assert admin_post(client, admin_headers, "player/remove", {"user_id": host["id"]}).status_code == 200
        update = recv_until(ws, state_event("leaderboard_updated"))
        assert update["lounge"] is None                       # the lounge went with its host; the member is fine
        assert client.get("/api/me", headers=auth(member)).json()["lounges"] == []
    assert client.get(f"/api/lounges/{lounge['id']}").status_code == 404


def test_removing_a_player_who_picked_on_an_open_play(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play = admin_post(client, admin_headers, "play/open").json()
    for user in (alice, bob):
        res = client.post("/api/predictions", headers=auth(user),
                          json={"play_id": play["id"], "play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM"})
        assert res.status_code == 200
    assert admin_post(client, admin_headers, "player/remove", {"user_id": alice["id"]}).status_code == 200
    assert admin_post(client, admin_headers, "play/lock").status_code == 200
    assert admin_post(client, admin_headers, "play/resolve",
                      {"play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM"}).status_code == 200
    board = client.get("/api/state").json()["leaderboard"]
    assert [(r["username"], r["score"]) for r in board] == [("bob", 40)]
    late = client.post("/api/predictions", headers=auth(alice),
                       json={"play_id": play["id"], "play_type": "RUN", "direction": "LEFT", "yardage": "SHORT"})
    assert late.status_code == 401


def test_remove_user_is_one_step_and_reports_who(store):
    user = store.create_user("Someone")
    assert store.remove_user(user["id"], block=True) == "Someone"
    assert store.is_name_blocked("someone") and store.get_user(user["id"]) is None
    assert store.remove_user(user["id"], block=True) is None


def test_unblock_over_rest_and_the_socket(client, admin_headers):
    one, two = register(client, "Name One"), register(client, "Name Two")
    admin_post(client, admin_headers, "player/remove", {"user_id": one["id"], "block": True})
    admin_post(client, admin_headers, "player/remove", {"user_id": two["id"], "block": True})
    assert client.post("/api/users", json={"username": "name one"}).status_code == 400
    res = admin_post(client, admin_headers, "name/unblock", {"name": "NAME ONE"})
    assert res.status_code == 200 and res.json() == {"unblocked": "NAME ONE"}
    assert client.post("/api/users", json={"username": "name one"}).status_code == 201
    assert admin_post(client, admin_headers, "name/unblock", {"name": "name one"}).status_code == 404
    assert client.post("/api/admin/name/unblock", json={"name": "Name Two"}).status_code == 401
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        recv_until(admin, lambda m: m["type"] == "admin_state")
        admin.send_json({"action": "unblock_name", "request_id": 3, "name": "Name Two"})
        ack = recv_until(admin, lambda m: m["type"] == "admin_ack")
        assert ack["ok"] and ack["result"] == {"unblocked": "Name Two"}
    assert players(client, admin_headers)["blocked_names"] == []


def test_players_search_is_case_insensitive_and_treats_wildcards_literally(client, admin_headers):
    for name in ("Alice", "alicia", "Bob_1", "Bobby"):
        register(client, name)
    names = lambda q: [p["username"] for p in client.get("/api/admin/players", params={"q": q}, headers=admin_headers).json()["players"]]
    assert names("ALI") == ["alicia", "Alice"]
    assert names("b_") == ["Bob_1"]            # "_" is not "any one character"
    assert names("%") == [] and names("") == ["Bobby", "Bob_1", "alicia", "Alice"]
    assert client.get("/api/admin/players", params={"q": "ali"}, headers=admin_headers).json()["count"] == 4
