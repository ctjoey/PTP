"""Fix result: re-scoring a play that was scored wrongly."""

import pytest

import models
from models import GameError, score_prediction
from tests.conftest import ADMIN_KEY, GAME
from tests.test_app import player_socket, recv_until, register, state_event
from tests.test_yardage import auth

HEADERS = {"X-Admin-Key": ADMIN_KEY}


def scored_game(store, picks, result=("PASS", "LEFT", "MEDIUM", 8), down=1, distance="10"):
    """A game with one resolved play. ``picks`` is ``{username: (type, direction, yardage)}``. Returns (users, play)."""
    store.create_game(**GAME)
    users = {name: store.create_user(name) for name in picks}
    _, play = store.open_next_play(down, distance, 15)
    for name, pick in picks.items():
        store.submit_prediction(users[name]["id"], play["id"], *pick)
    store.lock_play()
    store.resolve_play(result[0], result[1], result[2], result[3])
    return users, play


def total(store, user):
    return store.get_user(user["id"])["total_score"]


def points(store, play, user):
    return store.predictions_for_play(play["id"])[user["id"]]["points_earned"]


PICKS = {"alice": ("PASS", "LEFT", "MEDIUM"), "bob": ("RUN", "LEFT", "SHORT"), "carol": ("RUN", "RIGHT", "LONG"),
         "dave": ("PASS", "RIGHT", "MEDIUM")}


def test_correcting_moves_every_total_by_the_difference(store):
    users, play = scored_game(store, PICKS)
    before = {n: (points(store, play, u), total(store, u)) for n, u in users.items()}
    assert [b[0] for b in before.values()] == [40, 10, 0, 20]
    fixed = store.correct_play(play["id"], "RUN", "LEFT", yards=3)         # it was a run left for 3 after all
    assert (fixed["correct_play_type"], fixed["correct_direction"], fixed["correct_yardage"], fixed["yards_gained"]) == \
        ("RUN", "LEFT", "SHORT", 3)
    assert fixed["resolved_by"] == "host-fix" and fixed["state"] == "RESOLVED" and not fixed["voided"]
    after = {n: (points(store, play, u), total(store, u)) for n, u in users.items()}
    assert [a[0] for a in after.values()] == [10, 40, 10, 0]
    for name in users:                                                      # total moved by exactly the change in points
        assert after[name][1] - before[name][1] == after[name][0] - before[name][0]
    for name in users:                                                      # and every pick equals score_prediction
        pick = PICKS[name]
        assert after[name][0] == score_prediction(*pick, "RUN", "LEFT", "SHORT").points


def test_totals_stay_consistent_across_plays_and_leaderboards(store):
    users, play1 = scored_game(store, PICKS)
    _, play2 = store.open_next_play(2, "2", 15)
    for name, pick in PICKS.items():
        store.submit_prediction(users[name]["id"], play2["id"], *pick)
    store.lock_play()
    store.resolve_play("PASS", "RIGHT", "MEDIUM", 7)
    game = store.current_game()["id"]
    store.correct_play(play1["id"], "PASS", "RIGHT", "LONG", 14)
    for name, user in users.items():
        stored = store.predictions_for_play(play1["id"])[user["id"]]["points_earned"] + \
            store.predictions_for_play(play2["id"])[user["id"]]["points_earned"]
        assert total(store, user) == stored
    board = {r["username"]: r for r in store.game_leaderboard(game)}
    assert {n: board[n]["score"] for n in users} == {n: total(store, u) for n, u in users.items()}
    assert board["dave"]["exact_hits"] == 1 and board["alice"]["exact_hits"] == 0   # dave: exact on play 2 only
    assert [r["rank"] for r in store.game_leaderboard(game)] == sorted(r["rank"] for r in store.game_leaderboard(game))
    history = {h["play_number"]: h for h in store.play_history(game)}
    assert history[1]["resolved_by"] == "host-fix" and history[2]["resolved_by"] == "host"
    assert (history[1]["correct_yardage"], history[1]["yards_gained"]) == ("LONG", 14)


def test_correcting_can_lower_a_total_and_leave_unchanged_picks_alone(store):
    users, play = scored_game(store, {"alice": ("PASS", "LEFT", "MEDIUM")})
    assert total(store, users["alice"]) == 40
    store.correct_play(play["id"], "RUN", "RIGHT", "LONG")                  # alice was wrong after all
    assert total(store, users["alice"]) == 0 and points(store, play, users["alice"]) == 0
    store.correct_play(play["id"], "PASS", "LEFT", "MEDIUM")                # and fixed back
    assert total(store, users["alice"]) == 40
    store.correct_play(play["id"], "PASS", "LEFT", "MEDIUM", 8)             # same result again: nothing changes
    assert total(store, users["alice"]) == 40


def test_a_loss_never_matches_a_pick_and_yards_set_the_bucket(store):
    users, play = scored_game(store, {"alice": ("RUN", "LEFT", "SHORT")}, result=("RUN", "LEFT", "SHORT", 2))
    assert total(store, users["alice"]) == 40
    fixed = store.correct_play(play["id"], "RUN", "LEFT", yards=-4)
    assert (fixed["correct_yardage"], fixed["yards_gained"]) == ("LOSS", -4) and total(store, users["alice"]) == 20
    fixed = store.correct_play(play["id"], "RUN", "LEFT", yardage="MEDIUM")  # bucket only: yards unknown
    assert (fixed["correct_yardage"], fixed["yards_gained"]) == ("MEDIUM", None) and total(store, users["alice"]) == 20


def test_correct_play_validates_like_resolve(store):
    users, play = scored_game(store, PICKS)
    for kwargs in ({}, {"yardage": "MEDIUM", "yards": 3}, {"yardage": "HUGE"}, {"yards": 500}, {"yards": True}):
        with pytest.raises(GameError):
            store.correct_play(play["id"], "RUN", "LEFT", **kwargs)
    with pytest.raises(ValueError):
        store.correct_play(play["id"], "BOMB", "LEFT", yards=1)
    with pytest.raises(ValueError):
        store.correct_play(play["id"], "RUN", "UP", yards=1)
    assert store.get_play(play["id"])["correct_play_type"] == "PASS"       # nothing changed
    assert store.correct_play(play["id"], "RUN", "CENTER", yards=1)["correct_direction"] == "MIDDLE"   # the old alias


@pytest.mark.parametrize("state", ["open", "locked", "voided"])
def test_only_scored_plays_can_be_corrected(store, state):
    store.create_game(**GAME)
    user = store.create_user("alice")
    _, play = store.open_next_play(1, "10", 15)
    store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", "SHORT")
    if state in ("locked", "voided"):
        store.lock_play()
    if state == "voided":
        store.void_play()
    with pytest.raises(GameError) as exc:
        store.correct_play(play["id"], "RUN", "LEFT", yards=2)
    assert exc.value.status_code == 409
    assert ("not been scored" if state != "voided" else "voided") in exc.value.message
    assert total(store, user) == 0


def test_unknown_and_other_games_plays_are_refused(store):
    users, play = scored_game(store, PICKS)
    with pytest.raises(GameError) as exc:
        store.correct_play(9999, "RUN", "LEFT", yards=1)
    assert exc.value.status_code == 404
    store.create_game("Dallas", "#003594", "#869397", "Tampa Bay", "#D50A0A", "#FF7900")    # a newer game
    with pytest.raises(GameError, match="current game") as exc:
        store.correct_play(play["id"], "RUN", "LEFT", yards=1)
    assert exc.value.status_code == 409
    assert store.get_play(play["id"])["correct_play_type"] == "PASS"
    assert total(store, users["alice"]) == 40


def test_a_failure_halfway_changes_nothing(store, monkeypatch):
    users, play = scored_game(store, PICKS)
    snapshot = ([store.get_play(play["id"])], {n: (points(store, play, u), total(store, u)) for n, u in users.items()})
    calls = []
    real = models.score_prediction

    def flaky(*args):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disk on fire")
        return real(*args)

    monkeypatch.setattr(models, "score_prediction", flaky)
    with pytest.raises(RuntimeError):
        store.correct_play(play["id"], "RUN", "LEFT", yards=3)
    monkeypatch.setattr(models, "score_prediction", real)
    assert len(calls) == 3
    assert snapshot == ([store.get_play(play["id"])], {n: (points(store, play, u), total(store, u)) for n, u in users.items()})
    assert store.get_play(play["id"])["resolved_by"] == "host"
    store.correct_play(play["id"], "RUN", "LEFT", yards=3)                  # and the store still works afterwards
    assert store.get_play(play["id"])["resolved_by"] == "host-fix"


def test_a_pick_with_no_distance_is_scored_as_wrong_on_a_correction(store):
    users, play = scored_game(store, {"alice": ("PASS", "LEFT", "MEDIUM")})
    store._conn.execute("UPDATE predictions SET yardage = NULL, points_earned = 20 WHERE play_id = ?", (play["id"],))
    store._conn.execute("UPDATE users SET total_score = 20")
    store.correct_play(play["id"], "PASS", "LEFT", yards=8)
    assert points(store, play, users["alice"]) == 20 and total(store, users["alice"]) == 20      # no distance pick: 20
    store.correct_play(play["id"], "RUN", "LEFT", yards=8)
    assert points(store, play, users["alice"]) == 10 and total(store, users["alice"]) == 10


# --------------------------------------------------------------------------- #
# Over the wire
# --------------------------------------------------------------------------- #


def test_correct_play_over_rest_updates_players_and_the_admin_history(client, admin_headers):
    alice, bob = register(client, "alice"), register(client, "bob")
    assert client.post("/api/admin/game", json=GAME, headers=admin_headers).status_code == 201
    play = client.post("/api/admin/play/open", json={"down": 1, "distance": "10"}, headers=admin_headers).json()
    for user, pick in ((alice, ("PASS", "LEFT", "MEDIUM")), (bob, ("RUN", "LEFT", "SHORT"))):
        client.post("/api/predictions", headers=auth(user),
                    json={"play_id": play["id"], "play_type": pick[0], "direction": pick[1], "yardage": pick[2]})
    client.post("/api/admin/play/lock", headers=admin_headers)
    client.post("/api/admin/play/resolve", json={"play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM"}, headers=admin_headers)
    ws_a, a, _ = player_socket(client, alice["token"])
    ws_b, b, _ = player_socket(client, bob["token"])
    try:
        body = {"play_id": play["id"], "play_type": "RUN", "direction": "LEFT", "yards": 4}
        assert client.post("/api/admin/play/correct", json=body).status_code == 401
        res = client.post("/api/admin/play/correct", json=body, headers=admin_headers)
        assert res.status_code == 200 and res.json()["correct_play_type"] == "RUN" and res.json()["yards_gained"] == 4
        msg = recv_until(a, state_event("play_corrected"))
        assert msg["play"]["correct_play_type"] == "RUN" and msg["me"]["game_score"] == 10 and msg["me"]["total_score"] == 10
        assert msg["my_prediction"]["points_earned"] == 10 and msg["my_prediction"]["type_correct"] is False
        mb = recv_until(b, state_event("play_corrected"))
        assert mb["me"]["game_score"] == 40 and mb["me"]["rank"] == 1
        assert [r["username"] for r in mb["leaderboard"]] == ["bob", "alice"] and mb["leaderboard"][0]["exact_hits"] == 1
        assert "resolved_by" not in str(msg) and "host-fix" not in str(msg)         # admin only
        history = client.get("/api/admin/state", headers=admin_headers).json()["history"][0]
        assert history["resolved_by"] == "host-fix" and history["correct_play_type"] == "RUN" and history["exact_hits"] == 1
        # the same through the live-data URL
        res = client.post("/api/admin/feed/correct_play", headers=admin_headers,
                          json={"play_id": play["id"], "play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM"})
        assert res.status_code == 200
        assert client.get("/api/me", headers=auth(alice)).json()["user"]["total_score"] == 40
    finally:
        ws_a.__exit__(None, None, None)
        ws_b.__exit__(None, None, None)


def test_correct_play_errors_over_rest(client, admin_headers):
    client.post("/api/admin/game", json=GAME, headers=admin_headers)
    play = client.post("/api/admin/play/open", json={}, headers=admin_headers).json()
    body = {"play_id": play["id"], "play_type": "RUN", "direction": "LEFT", "yards": 4}
    res = client.post("/api/admin/play/correct", json=body, headers=admin_headers)
    assert res.status_code == 409 and "not been scored" in res.json()["detail"]
    assert client.post("/api/admin/play/correct", json={**body, "play_id": 777}, headers=admin_headers).status_code == 404
    assert client.post("/api/admin/play/correct", json={**body, "yardage": "LONG"}, headers=admin_headers).status_code == 422
    assert client.post("/api/admin/play/correct", json={"play_id": play["id"]}, headers=admin_headers).status_code == 422


def test_correct_play_over_the_admin_socket(client):
    alice = register(client, "alice")
    with client.websocket_connect("/ws/admin") as admin:
        admin.send_json({"type": "auth", "key": ADMIN_KEY})
        recv_until(admin, lambda m: m["type"] == "admin_state")

        seen = []   # broadcasts arrive before the ack, so keep what we skip past

        def act(action, **payload):
            admin.send_json({"action": action, "request_id": action, **payload})

            def is_ack(m):
                seen.append(m)
                return m["type"] == "admin_ack" and m["request_id"] == action
            return recv_until(admin, is_ack)

        act("create_game", **GAME)
        play = act("open_play", down=1, distance="10")["result"]
        client.post("/api/predictions", headers=auth(alice),
                    json={"play_id": play["id"], "play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM"})
        act("lock_play")
        act("resolve_play", play_type="PASS", direction="LEFT", yardage="MEDIUM")
        ack = act("correct_play", play_id=play["id"], play_type="RUN", direction="MIDDLE", yardage="SHORT")
        assert ack["ok"] and ack["result"]["correct_direction"] == "MIDDLE"
        state = next(m for m in seen if m["type"] == "admin_state" and m["event"] == "play_corrected")
        assert state["history"][0]["resolved_by"] == "host-fix" and state["leaderboard"][0]["score"] == 0
        bad = act("correct_play", play_id=play["id"], play_type="RUN", direction="MIDDLE")
        assert bad["ok"] is False and "distance" in bad["error"]
