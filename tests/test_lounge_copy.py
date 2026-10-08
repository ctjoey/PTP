"""The Head to Head instructions read the same on the website and in the iPhone app."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOIN = "Got a code from a friend? Type it in and tap Join. Your group's leaderboard opens."
CREATE = "Name your group and tap Create, then send the code to friends. They enter it above to join."


def test_lounge_how_to_lines_match_on_web_and_iphone(client):
    html = client.get("/").text
    swift = (ROOT / "ios/PickThePlay/Views/LoungesView.swift").read_text()
    for line in (JOIN, CREATE):
        assert line in html, line
        assert line.replace("'", "\\'") in swift or line in swift, line
