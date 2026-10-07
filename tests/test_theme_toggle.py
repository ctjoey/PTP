"""The light/dark toggle: markup on every page and what the stylesheet and script promise. (Behaviour in a real
browser is checked separately; these guard the contract: dark by default, a labelled button everywhere, the saved
choice applied before first paint.)"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["/", "/rules", "/support", "/privacy", "/admin"]


@pytest.fixture
def lounge_code(client):
    token = client.post("/api/users", json={"username": "alice"}).json()["token"]
    return client.post("/api/lounges", json={"name": "Sunday Crew"}, headers={"Authorization": f"Bearer {token}"}).json()["id"]


def page_for(client, path):
    res = client.get(path)
    assert res.status_code == 200, path
    return res.text


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_a_labelled_toggle_and_starts_dark(client, path):
    html = page_for(client, path)
    assert re.search(r'<html[^>]*data-theme="dark"', html), "dark is the default"
    buttons = re.findall(r"<button[^>]*data-theme-toggle[^>]*>", html)
    assert buttons, "a toggle button on the page"
    for button in buttons:
        assert 'type="button"' in button
        assert re.search(r'aria-label="Switch to light mode"', button), "the label states the action"
    assert html.count("<svg") >= 2 * len(buttons) and "aria-hidden" in html     # inline sun and moon, decorative


def test_the_lounge_page_has_the_toggle_too(client, lounge_code):
    html = page_for(client, f"/lounge/{lounge_code}")
    assert "data-theme-toggle" in html and re.search(r'<html[^>]*data-theme="dark"', html)


@pytest.mark.parametrize("path", PAGES)
def test_the_saved_choice_is_applied_before_the_stylesheet_loads(client, path):
    html = page_for(client, path)
    head = html[: html.index("</head>")]
    script = head.index("<script>")
    assert script < head.index('rel="stylesheet"'), "applied before first paint: no flash of the wrong theme"
    inline = head[script: head.index("</script>", script)]
    assert 'localStorage.getItem("ptp-theme")' in inline and "try" in inline and "catch" in inline   # storage may be blocked
    assert '"light"' in inline and "data-theme" in inline
    assert re.search(r'<meta name="theme-color"', head), "the browser bar colour follows the theme"


def test_the_stylesheet_defines_a_light_palette_and_native_control_colours():
    css = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
    assert '[data-theme="light"]' in css
    assert "color-scheme" in css
    light = css[css.index('[data-theme="light"]'):]
    for token in ("--bg", "--text"):
        assert token in light, token


def test_the_script_remembers_the_choice_safely():
    js = (ROOT / "static/js/common.js").read_text(encoding="utf-8")
    assert "ptp-theme" in js and "data-theme-toggle" in js and "theme-color" in js
    assert re.search(r"try\s*\{[^}]*localStorage", js), "every storage access is guarded"
