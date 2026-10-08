"""Practice mode on the web player: the markup is on the player and lounge pages, it uses its own element hooks (so
the live pick grid is untouched), it never talks to the server, and its scoring constants are the server's.
(The behaviour in a real browser is checked separately.)"""

import re
from pathlib import Path

import pytest

import models

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/practice.js").read_text()


@pytest.fixture
def lounge_code(client):
    token = client.post("/api/users", json={"username": "alice"}).json()["token"]
    return client.post("/api/lounges", json={"name": "Sunday Crew"}, headers={"Authorization": f"Bearer {token}"}).json()["id"]


def practice_modal(html):
    start = html.index('id="practice-modal"')
    return html[start:html.index("<!-- Head-to-head lounges -->", start)]


def test_player_page_has_three_ways_in_and_the_script(client):
    html = client.get("/").text
    assert html.count("data-practice-open") == 3, "no-game card, under the scores, sign-in"
    assert "Try a practice round" in html and "Practice mode" in html and "Just practice first" in html
    assert 'id="practice-btn"' in html
    assert html.count("/static/js/practice.js") == 1
    assert html.index("/static/js/player.js") < html.index("/static/js/practice.js")
    assert re.search(r'<div class="modal-backdrop" id="practice-modal" hidden>', html), "closed until asked for"


def test_lounge_page_has_it_too(client, lounge_code):
    html = client.get(f"/lounge/{lounge_code}").text
    assert 'id="practice-modal"' in html and "/static/js/practice.js" in html


def test_it_does_not_reuse_the_live_pick_grid_hooks(client):
    """player.js binds [data-type], [data-dir], [data-yard] and [data-view] page-wide; practice must not collide."""
    modal = practice_modal(client.get("/").text)
    for hook in ('data-type="', 'data-dir="', 'data-yard="', 'data-view="'):
        assert hook not in modal, hook
    ids = re.findall(r'\sid="([^"]+)"', client.get("/").text)
    assert len(ids) == len(set(ids)), "duplicate element ids"
    for attr in ("data-p-type", "data-p-dir", "data-p-yard", "data-p-view"):
        assert attr in modal


def test_the_script_never_talks_to_the_server():
    assert "fetch(" not in JS and "api(" not in JS and "WebSocket" not in JS and "LiveSocket" not in JS
    assert "localStorage" not in JS, "nothing is kept between visits"


def test_scoring_constants_match_the_server():
    found = dict(re.findall(r"(\w+):\s*(\d+)", re.search(r"const POINTS = \{([^}]*)\}", JS).group(1)))
    assert found == {
        "type": str(models.TYPE_POINTS), "direction": str(models.DIRECTION_POINTS),
        "distance": str(models.YARDAGE_POINTS), "bonus": str(models.BONUS_POINTS),
    }
    assert models.EXACT_POINTS == 40


def test_the_wording_matches_the_live_screens(client):
    html = client.get("/").text
    for phrase in ("Call the play!", "As the QB looks downfield", "Predictions Locked — Play in Progress",
                   "Practice plays are simulated and don't count toward the live leaderboard."):
        assert phrase in html, phrase
    for phrase in ("Perfect call!", "Two of three", "One of three", "No points this time",
                   "You didn't pick this play", "Change it until the clock hits 0."):
        assert phrase in JS, phrase
