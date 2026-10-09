"""'Change name or delete account' on the web player: markup on the player and lounge pages, what the script
promises, and the account round trip it relies on (delete the account, then take a new name or even the same one
again)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lounge_code(client):
    token = client.post("/api/users", json={"username": "alice"}).json()["token"]
    return client.post("/api/lounges", json={"name": "Sunday Crew"}, headers={"Authorization": f"Bearer {token}"}).json()["id"]


def test_player_page_has_a_hidden_change_name_button(client):
    html = client.get("/").text
    assert re.search(r'<p class="whoami" id="whoami" hidden>', html), "hidden until signed in"
    assert re.search(r'<button[^>]*id="change-name"[^>]*type="button"|<button[^>]*type="button"[^>]*id="change-name"', html)
    assert "Change name or delete account" in html and 'id="whoami-name"' in html


def test_lounge_page_has_it_too(client, lounge_code):
    assert 'id="change-name"' in client.get(f"/lounge/{lounge_code}").text


def test_script_deletes_the_account_then_signs_in_again():
    js = (ROOT / "static/js/player.js").read_text()
    body = js[js.index("async function changeName"):js.index("function wireUI")]
    assert 'method: "DELETE"' in body and '"/api/me"' in body
    assert "confirm(" in body, "asks first"
    assert "delete your account" in body, "the question says what really happens"
    assert "forgetToken()" in body and "location.reload()" in body
    assert "err.status !== 401" in body, "an account that is already gone still signs out"
    assert '$("#change-name").addEventListener("click", changeName)' in js
    assert '"account_deleted"' in js, "a deleted account signs out here too"


def test_privacy_and_support_point_web_players_at_it(client):
    for path in ("/privacy", "/support"):
        assert "Change name or delete account" in client.get(path).text, path


def test_the_name_can_be_taken_again_after_changing_it(client):
    first = client.post("/api/users", json={"username": "tester"}).json()
    headers = {"Authorization": f"Bearer {first['token']}"}
    assert client.post("/api/users", json={"username": "tester"}).status_code == 409
    assert client.delete("/api/me", headers=headers).status_code == 204
    assert client.get("/api/me", headers=headers).status_code == 401, "the old sign-in is dead"
    again = client.post("/api/users", json={"username": "tester"})
    assert again.status_code == 201 and again.json()["total_score"] == 0
