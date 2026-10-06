"""App Review requirements: account deletion, privacy/support pages, name filter."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import Settings, create_app
from models import GameError, Store, is_offensive_name
from tests.conftest import ADMIN_KEY, GAME
from tests.test_app import recv_until, register, state_event


def auth(user):
    return {"Authorization": f"Bearer {user['token']}"}


def play_one(client, admin_headers, picks, actual=("PASS", "LEFT", "MEDIUM")):
    """Open a play, submit ``[(user, type, dir, yardage), ...]`` picks, lock and resolve it."""
    play = client.post("/api/admin/play/open", json={}, headers=admin_headers).json()
    for user, ptype, pdir, pyard in picks:
        res = client.post("/api/predictions", headers=auth(user),
                          json={"play_id": play["id"], "play_type": ptype, "direction": pdir, "yardage": pyard})
        assert res.status_code == 200, res.text
    client.post("/api/admin/play/lock", headers=admin_headers)
    res = client.post("/api/admin/play/resolve", headers=admin_headers,
                      json={"play_type": actual[0], "direction": actual[1], "yardage": actual[2]})
    assert res.status_code == 200, res.text
    return play


# --------------------------------------------------------------------------- #
# Account deletion (Guideline 5.1.1(v))
# --------------------------------------------------------------------------- #


def test_store_delete_user_cascades(store):
    assert store._one("PRAGMA foreign_keys")["foreign_keys"] == 1
    alice, bob = store.create_user("alice"), store.create_user("bob")
    hosted = store.create_lounge(alice["id"], "Alice Crew")
    store.join_lounge(hosted["id"], bob["id"])
    joined = store.create_lounge(bob["id"], "Bob Den")
    store.join_lounge(joined["id"], alice["id"])
    store.create_game(**GAME)
    _, play = store.open_next_play(1, "10", 15)
    store.submit_prediction(alice["id"], play["id"], "PASS", "LEFT", "MEDIUM")
    store.submit_prediction(bob["id"], play["id"], "PASS", "RIGHT", "LONG")
    store.lock_play()
    store.resolve_play("PASS", "LEFT", "MEDIUM")

    assert store.delete_user(alice["id"]) is True
    assert store.delete_user(alice["id"]) is False  # already gone

    count = lambda sql, *p: store._one(sql, p)["n"]  # noqa: E731
    assert store.get_user(alice["id"]) is None
    assert count("SELECT COUNT(*) AS n FROM predictions WHERE user_id = ?", alice["id"]) == 0
    assert count("SELECT COUNT(*) AS n FROM lounge_members WHERE user_id = ?", alice["id"]) == 0
    # The lounge she hosted is gone, along with bob's membership in it.
    assert store.get_lounge(hosted["id"]) is None
    assert count("SELECT COUNT(*) AS n FROM lounge_members WHERE lounge_id = ?", hosted["id"]) == 0
    # Bob's own lounge, pick and points are untouched.
    assert store.get_lounge(joined["id"])["member_count"] == 1
    assert count("SELECT COUNT(*) AS n FROM predictions WHERE user_id = ?", bob["id"]) == 1
    assert store.get_user(bob["id"])["total_score"] == 10
    assert [r["username"] for r in store.game_leaderboard(store.current_game()["id"])] == ["bob"]
    # The name can be registered again.
    assert store.create_user("Alice")["username"] == "Alice"


def test_prediction_from_deleted_user_is_rejected(store):
    user = store.create_user("ghost")
    store.create_game(**GAME)
    _, play = store.open_next_play(None, None, 15)
    store.delete_user(user["id"])
    with pytest.raises(GameError) as exc:
        store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", "SHORT")
    assert exc.value.status_code == 401


def test_delete_account_over_rest(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    alice_lounge = client.post("/api/lounges", json={"name": "Alice Crew"}, headers=auth(alice)).json()
    bob_lounge = client.post("/api/lounges", json={"name": "Bob Den"}, headers=auth(bob)).json()
    assert client.post(f"/api/lounges/{alice_lounge['id']}/join", headers=auth(bob)).status_code == 200
    assert client.post(f"/api/lounges/{bob_lounge['id']}/join", headers=auth(alice)).status_code == 200
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play_one(client, admin_headers, [(alice, "PASS", "LEFT", "MEDIUM"), (bob, "RUN", "RIGHT", "MEDIUM")])

    assert client.delete("/api/me").status_code == 401
    assert client.delete("/api/me", headers={"Authorization": "Bearer bogus"}).status_code == 401

    res = client.delete("/api/me", headers=auth(alice))
    assert res.status_code == 204 and res.content == b""

    # The token no longer works anywhere.
    assert client.get("/api/me", headers=auth(alice)).status_code == 401
    assert client.delete("/api/me", headers=auth(alice)).status_code == 401
    assert client.post("/api/lounges", json={"name": "Again"}, headers=auth(alice)).status_code == 401

    # Her hosted lounge is gone; bob's survives without her.
    assert client.get(f"/api/lounges/{alice_lounge['id']}").status_code == 404
    assert client.get(f"/api/lounges/{bob_lounge['id']}").json()["member_count"] == 1

    # Bob's points and lounges are untouched; alice is off the leaderboard.
    me = client.get("/api/me", headers=auth(bob)).json()
    assert me["user"]["total_score"] == 10
    assert [lg["id"] for lg in me["lounges"]] == [bob_lounge["id"]]
    board = client.get("/api/state").json()["leaderboard"]
    assert [(r["username"], r["score"]) for r in board] == [("bob", 10)]

    # The username is free again.
    assert client.post("/api/users", json={"username": "alice"}).status_code == 201


def test_delete_account_signs_out_open_sockets(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play_one(client, admin_headers, [(alice, "PASS", "LEFT", "MEDIUM"), (bob, "RUN", "RIGHT", "MEDIUM")])
    ctrl = client.app.state.ctrl

    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.send_json({"type": "hello", "token": alice["token"]})
        recv_until(a, state_event("sync"))
        b.send_json({"type": "hello", "token": bob["token"]})
        first = recv_until(b, state_event("sync"))
        assert {r["username"] for r in first["leaderboard"]} == {"alice", "bob"}

        assert client.delete("/api/me", headers=auth(alice)).status_code == 204

        err = recv_until(a, lambda m: m["type"] == "error")
        assert err == {"type": "error", "code": "account_deleted", "message": "Your account was deleted."}
        with pytest.raises(WebSocketDisconnect) as closed:
            a.receive_json()
        assert closed.value.code == 4401
        assert not any(c.user and c.user["id"] == alice["id"] for c in ctrl.hub.players)

        # Everyone else's boards refresh without her.
        update = recv_until(b, state_event("leaderboard_updated"))
        assert [r["username"] for r in update["leaderboard"]] == ["bob"]
        assert update["me"]["total_score"] == 10

        # Reconnecting with the old token gets a spectator session.
        with client.websocket_connect("/ws") as again:
            again.send_json({"type": "hello", "token": alice["token"]})
            assert again.receive_json()["code"] == "bad_token"


# --------------------------------------------------------------------------- #
# Privacy policy and support pages
# --------------------------------------------------------------------------- #

PRIVACY_PHRASES = (
    "Privacy Policy",
    "Effective October 6, 2026",
    "username",
    "sign-in token",
    "does not collect your email address, phone number, location, contacts",
    "advertising identifier",
    "no analytics",
    "do not sell your data",
    "IP address",
    "Settings &gt; Delete account",
    "operator resets the game",
    "not directed at children under 13",
    "not affiliated with",
)


def test_privacy_page(client):
    res = client.get("/privacy")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    for phrase in PRIVACY_PHRASES:
        assert phrase in res.text, phrase
    assert "App Store" in res.text and "mailto:" not in res.text  # no PTP_CONTACT_EMAIL set
    assert 'href="/support"' in res.text


def test_support_page(client):
    res = client.get("/support")
    assert res.status_code == 200
    for phrase in ("How to play", "15 seconds", "+10", "Correct distance", "Bonus: all three right",
                   "Perfect call</td><td>40</td>", "Left, middle or right?", 'href="/rules"',
                   "as the quarterback looks downfield", "Short</b> (5 yards or less)", "A loss of yards scores no",
                   "4-digit code", "Delete account", 'href="/privacy"', "App Store", "not affiliated with"):
        assert phrase in res.text, phrase


def test_pages_show_contact_email_when_configured(tmp_path):
    settings = Settings(db_path=str(tmp_path / "c.db"), admin_key=ADMIN_KEY, contact_email="help@example.com")
    with TestClient(create_app(settings)) as client:
        for path in ("/privacy", "/support"):
            assert 'href="mailto:help@example.com"' in client.get(path).text


def test_player_page_links_privacy_and_support(client):
    html = client.get("/").text
    assert 'href="/privacy"' in html and 'href="/support"' in html


# --------------------------------------------------------------------------- #
# Offensive-name filter (Guideline 1.2)
# --------------------------------------------------------------------------- #

BLOCKED = [
    "fuck", "FuckFace", "F u c k", "f.u.c.k", "Fuuuuck", "Mother Fucker", "fucking", "sh1t", "5hit",
    "Shitty", "Shitting", "Bullsh1t", "B1tch", "Big Bitch Energy", "Dick Head", "dickhead99",
    "asshole", "Ass Hole", "cunt", "CUNTS", "Twat", "Wankers", "Pi55ed", "Whores", "Retarded",
    "N1gg3r", "xXniggerXx", "n i g g a", "faggot", "fag", "Spic", "Hitler88", "Nazis", "Rapist",
]
ALLOWED = [
    # Real names and places that contain a rude string.
    "Scunthorpe", "Cassidy", "Hancock", "Essex", "Sussex", "Dick Butkus", "Dickens", "Arsenal",
    "Cumming", "Cummins", "Matsushita", "Hitchcock", "Peacock", "Cockburn", "Penistone", "Clitheroe",
    "Titus", "Fagan", "Van Dyke", "Pakistan", "Mississippi", "Sexton", "Kike Garcia",
    # Ordinary words.
    "Shiitake", "Therapist", "Grapes", "Spicy Wings", "Spicer", "Snigger", "Niggardly", "Analytics",
    "Assassin", "Bass Head", "Cocktail Crew", "Cunning Plan", "Rapper", "Passing Game", "Summa Cum Laude",
    # Typical usernames and lounge names.
    "Joey", "alice", "Sunday Crew", "Team 2024", "Agent 007", "Fan4Life", "Shi Tzu", "Blitz_99",
]


@pytest.mark.parametrize("name", BLOCKED)
def test_offensive_names_blocked(name):
    assert is_offensive_name(name)


@pytest.mark.parametrize("name", ALLOWED)
def test_ordinary_names_allowed(name):
    assert not is_offensive_name(name)


def test_store_rejects_offensive_usernames_and_lounge_names(store):
    for bad in ("Sh1t Head", "f.u.c.k"):
        with pytest.raises(GameError, match="Please choose a different name."):
            store.create_user(bad)
    host = store.create_user("Dick Butkus")
    with pytest.raises(GameError, match="Please choose a different name."):
        store.create_lounge(host["id"], "B1tches Club")
    assert store.create_lounge(host["id"], "Scunthorpe Fans")["name"] == "Scunthorpe Fans"


def test_offensive_names_rejected_over_http(client):
    res = client.post("/api/users", json={"username": "Fuuuck You"})
    assert res.status_code == 400 and res.json()["detail"] == "Please choose a different name."
    user = register(client, "Cassidy")
    res = client.post("/api/lounges", json={"name": "Wankers United"}, headers=auth(user))
    assert res.status_code == 400 and res.json()["detail"] == "Please choose a different name."
