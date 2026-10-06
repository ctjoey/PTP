import time

import pytest

from models import GameError, GameStatus, PlayState
from tests.conftest import GAME


def _game(store, **overrides):
    return store.create_game(**{**GAME, **overrides})


def test_create_game_normalises_and_validates(store):
    game = _game(store, home_name="  Green   Bay ", home_primary="#203731")
    assert game["home_name"] == "Green Bay"
    assert game["home_primary"] == "#203731"
    assert _game(store, away_primary="#0b162a")["away_primary"] == "#0B162A"
    assert game["status"] == GameStatus.SCHEDULED


@pytest.mark.parametrize("name", ["Green Bay Packers", "Chicago Bears", "NFL All-Stars", "Super Bowl"])
def test_protected_marks_are_blocked(store, name):
    with pytest.raises(GameError, match="protected"):
        _game(store, home_name=name)


def test_bad_colors_and_duplicate_teams_rejected(store):
    with pytest.raises(GameError):
        _game(store, home_primary="green")
    with pytest.raises(GameError, match="must be different"):
        _game(store, away_name="Detroit")
    # Two clubs share New York: the same name is refused, with a hint to add a word.
    with pytest.raises(GameError, match='"New York Blue" / "New York Green"'):
        _game(store, home_name="New York", away_name="new york")
    game = _game(store, home_name="New York Blue", away_name="New York Green")
    assert (game["away_name"], game["home_name"]) == ("New York Green", "New York Blue")


def test_new_game_finalises_previous(store):
    first = _game(store)
    second = _game(store, home_name="Denver", away_name="Boston")
    assert store.get_game(first["id"])["status"] == GameStatus.FINAL
    assert store.current_game()["id"] == second["id"]


def test_play_lifecycle_and_scoring(store):
    _game(store)
    alice = store.create_user("alice")
    bob = store.create_user("bob")
    carol = store.create_user("carol")

    game, play = store.open_next_play(3, "7", window_seconds=15)
    assert game["status"] == GameStatus.LIVE  # first play takes the game live
    assert play["state"] == PlayState.OPEN and play["play_number"] == 1

    with pytest.raises(GameError):
        store.open_next_play(1, "10", 15)  # only one active play at a time
    with pytest.raises(GameError, match="Lock"):
        store.resolve_play("PASS", "LEFT", "MEDIUM")  # must lock first

    store.submit_prediction(alice["id"], play["id"], "RUN", "LEFT", "SHORT")
    saved = store.submit_prediction(alice["id"], play["id"], "PASS", "LEFT", "MEDIUM")  # change of mind
    assert saved == {"play_id": play["id"], "play_type": "PASS", "direction": "LEFT", "yardage": "MEDIUM",
                     "points_earned": None}
    store.submit_prediction(bob["id"], play["id"], "PASS", "RIGHT", "SHORT")
    store.submit_prediction(carol["id"], play["id"], "RUN", "MIDDLE", "LONG")
    dave = store.create_user("dave")
    store.submit_prediction(dave["id"], play["id"], "RUN", "LEFT", "MEDIUM")

    store.lock_play()
    with pytest.raises(GameError, match="locked"):
        store.submit_prediction(carol["id"], play["id"], "PASS", "LEFT", "MEDIUM")

    resolved = store.resolve_play("PASS", "LEFT", yards=7)
    assert resolved["state"] == PlayState.RESOLVED
    assert (resolved["correct_yardage"], resolved["yards_gained"]) == ("MEDIUM", 7)

    preds = store.predictions_for_play(play["id"])
    assert preds[alice["id"]]["points_earned"] == 40  # all three + the bonus
    assert preds[alice["id"]]["yardage"] == "MEDIUM"
    assert preds[dave["id"]]["points_earned"] == 20   # direction + distance
    assert preds[bob["id"]]["points_earned"] == 10    # type only
    assert preds[carol["id"]]["points_earned"] == 0
    assert store.get_user(alice["id"])["total_score"] == 40

    board = store.game_leaderboard(game["id"])
    assert [(r["username"], r["score"], r["rank"], r["exact_hits"]) for r in board] == [
        ("alice", 40, 1, 1), ("dave", 20, 2, 0), ("bob", 10, 3, 0), ("carol", 0, 4, 0),
    ]

    stats = store.pick_stats(play["id"])
    assert stats == {"total": 4, "RUN": 2, "PASS": 2, "LEFT": 2, "MIDDLE": 1, "RIGHT": 1,
                     "SHORT": 1, "MEDIUM": 2, "LONG": 1, "exact": 1, "scored": 3}

    history = store.play_history(game["id"])
    assert history[0]["correct_yardage"] == "MEDIUM" and history[0]["yards_gained"] == 7
    assert history[0]["picks"] == 4 and history[0]["exact_hits"] == 1


def test_ties_share_rank(store):
    game = _game(store)
    a, b, c = (store.create_user(n) for n in ("a1", "b1", "c1"))
    _, play = store.open_next_play(1, "10", 15)
    for user in (a, b):
        store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", "SHORT")
    store.submit_prediction(c["id"], play["id"], "PASS", "RIGHT", "LONG")
    store.lock_play()
    store.resolve_play("RUN", "LEFT", "SHORT")
    ranks = [r["rank"] for r in store.game_leaderboard(game["id"])]
    assert ranks == [1, 1, 3]


def test_late_submission_rejected_after_timer(store):
    _game(store)
    user = store.create_user("latecomer")
    _, play = store.open_next_play(1, "10", window_seconds=0.01)
    time.sleep(0.05)
    with pytest.raises(GameError, match="locked"):
        store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", "SHORT")


def test_void_play_awards_nothing(store):
    _game(store)
    user = store.create_user("voidy")
    _, play = store.open_next_play(2, "5", 15)
    store.submit_prediction(user["id"], play["id"], "RUN", "LEFT", "SHORT")
    voided = store.void_play()
    assert voided["voided"] == 1 and voided["state"] == PlayState.RESOLVED
    assert store.predictions_for_play(play["id"])[user["id"]]["points_earned"] == 0
    assert store.get_user(user["id"])["total_score"] == 0
    store.open_next_play(2, "5", 15)  # the next play can open


def test_status_transitions(store):
    with pytest.raises(GameError):
        store.set_game_status(GameStatus.LIVE)  # no game yet
    _game(store)
    assert store.set_game_status(GameStatus.LIVE)["status"] == GameStatus.LIVE
    with pytest.raises(GameError):
        store.set_game_status(GameStatus.SCHEDULED)
    store.open_next_play(1, "10", 15)
    with pytest.raises(GameError, match="Resolve or void"):
        store.set_game_status(GameStatus.FINAL)
    store.void_play()
    assert store.set_game_status(GameStatus.FINAL)["status"] == GameStatus.FINAL
    with pytest.raises(GameError, match="FINAL"):
        store.open_next_play(1, "10", 15)


def test_usernames_unique_case_insensitive(store):
    store.create_user("Joey")
    with pytest.raises(GameError, match="taken"):
        store.create_user("joey")
    with pytest.raises(GameError):
        store.create_user("x")
    with pytest.raises(GameError):
        store.create_user("<script>")


def test_lounges(store):
    game = _game(store)
    host = store.create_user("host")
    friend = store.create_user("friend")
    lounge = store.create_lounge(host["id"], "Sunday Crew")
    code = lounge["id"]
    assert len(code) == 4 and code.isdigit()
    assert lounge["member_count"] == 1 and lounge["host_user_id"] == host["id"]

    store.join_lounge(code, friend["id"])
    assert store.join_lounge(code, friend["id"])["member_count"] == 2  # idempotent
    assert store.is_lounge_member(code, friend["id"])
    assert [l["id"] for l in store.user_lounges(friend["id"])] == [code]

    with pytest.raises(GameError) as missing:
        store.join_lounge("0000" if code != "0000" else "0001", friend["id"])
    assert missing.value.status_code == 404
    with pytest.raises(GameError):
        store.join_lounge("12a4", friend["id"])

    _, play = store.open_next_play(1, "10", 15)
    store.submit_prediction(friend["id"], play["id"], "PASS", "LEFT", "LONG")
    store.lock_play()
    store.resolve_play("PASS", "RIGHT", "LOSS", yards=-6)  # sack: type right, no distance points
    board = store.lounge_leaderboard(code, game["id"])
    assert [(r["username"], r["score"], r["rank"], r["is_host"]) for r in board] == [
        ("friend", 10, 1, False), ("host", 0, 2, True),
    ]
