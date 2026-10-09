"""The website side of the "After the game" list: one wording for the points table and the live result, the live score
beside each team, "Head to Head" and "Change name or delete account" named the same on the web and in the iPhone app,
a lounge window that can always be left, and the host console's no-play card.

The scripts that run in the browser are exercised under node with a tiny stand-in for the page (skipped when node is
not installed); the look of it all in a real browser is checked separately."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

ROWS = (
    "Pick correct play (Run / Pass)",
    "Pick correct direction (Left / Middle / Right)",
    "Pick correct distance (Short / Medium / Long)",
    "Pick all 3 correctly",
    "Perfect call",
    "You picked the play",
)
OLD_WORDING = re.compile(
    r"play type right|direction right|distance right|bonus: all three right|all three right"
    r"|(?<!pick )correct (?:play type|direction|distance)",
    re.IGNORECASE,
)


def words(html):
    """Visible words of a page: tags vanish, whitespace collapsed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# --------------------------------------------------------------------------- #
# A. The points table and the live result read the same everywhere
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["/", "/rules", "/support"])
def test_points_table_rows_in_order(client, path):
    text = words(client.get(path).text)
    at = -1
    for row in ROWS:
        found = text.find(row, at + 1)
        assert found > at, f"{row!r} missing or out of order on {path}"
        at = found


def test_perfect_call_row_carries_the_plain_words_line(client):
    for path in ("/", "/rules", "/support"):
        assert re.search(r'Perfect call(?:</b>)? <span class="perfect-sub">You picked the play</span></td>',
                         client.get(path).text), path


def test_old_points_wording_is_gone_from_the_site_and_the_docs():
    files = [*ROOT.glob("templates/*.html"), *ROOT.glob("static/js/*.js"), ROOT / "README.md", ROOT / "TESTFLIGHT.md",
             ROOT / "IPHONE_GUIDE.md", ROOT / "ios/README.md"]
    for path in files:
        hit = OLD_WORDING.search(path.read_text())
        assert hit is None, f"{path.relative_to(ROOT)}: {hit.group(0)!r}"


def test_live_and_practice_result_say_you_picked_the_play(client):
    html = client.get("/").text
    for hook in ('id="result-sub" hidden>You picked the play<', 'id="p-sub" hidden>You picked the play<'):
        assert hook in html, hook
    player = (ROOT / "static/js/player.js").read_text()
    practice = (ROOT / "static/js/practice.js").read_text()
    assert "sub.hidden = right !== 3" in player and "sub.hidden = true" in player, "only for all three, never on a void"
    assert '$("#p-sub").hidden = !(scored && scored.parts === 3)' in practice
    assert '"Perfect call!"' in player and '"Perfect call!"' in practice, "the headline stays"


def test_iphone_app_uses_the_same_words():
    models = (ROOT / "ios/PickThePlay/Models/Models.swift").read_text()
    for title in ("Pick correct play", "Pick correct direction", "Pick correct distance", "Pick all 3 correctly"):
        assert f'title: "{title}"' in models, title
    assert 'perfectNote = "You picked the play"' in (ROOT / "ios/PickThePlay/Services/Practice.swift").read_text()
    assert "Change name or delete account" in (ROOT / "ios/PickThePlay/Views/SettingsView.swift").read_text()


# --------------------------------------------------------------------------- #
# C. Naming parity
# --------------------------------------------------------------------------- #


def test_header_says_head_to_head_like_the_iphone_tab(client):
    html = client.get("/").text
    assert '<button class="btn btn-sm" id="lounge-btn" type="button">Head to Head</button>' in html
    assert "Lounges</button>" not in html and "h2h-prefix" not in html
    assert 'Label("Head to Head"' in (ROOT / "ios/PickThePlay/App/PickThePlayApp.swift").read_text()
    assert "h2h-prefix" not in (ROOT / "static/css/style.css").read_text()


def test_account_button_names_both_things_and_the_confirm_is_honest(client):
    html = client.get("/").text
    assert 'id="change-name">Change name or delete account</button>' in html
    js = (ROOT / "static/js/player.js").read_text()
    confirm = js[js.index("confirm(`") : js.index("`)", js.index("confirm(`"))]
    assert "delete your account" in confirm and "picks" in confirm and "can't be undone" in confirm
    for path in ("/support", "/privacy"):
        assert "Settings &gt; Change name or delete account" in client.get(path).text, path


# --------------------------------------------------------------------------- #
# D. The lounge window can always be left
# --------------------------------------------------------------------------- #


def test_lounge_window_has_a_clear_way_back_and_puts_the_keyboard_away(client):
    html = client.get("/").text
    modal = html[html.index('id="lounge-modal"'):]
    assert '<button class="btn btn-sm" type="button" data-close>Back to the game</button>' in modal
    assert '<h2 id="lounge-modal-title">Head to Head</h2>' in modal
    assert 'id="join-code" inputmode="numeric"' in modal and 'enterkeyhint="go"' in modal
    js = (ROOT / "static/js/player.js").read_text()
    for needle in ("dropKeyboard", '"pointerdown"', '"touchmove"', "(pointer: coarse)"):
        assert needle in js, needle
    assert "Escape" in js and "modal.hidden = true" in js


def test_lounge_page_strip_leads_back_to_the_game(client):
    token = client.post("/api/users", json={"username": "alice"}).json()["token"]
    code = client.post("/api/lounges", json={"name": "Sunday Crew"}, headers={"Authorization": f"Bearer {token}"}).json()["id"]
    html = client.get(f"/lounge/{code}").text
    assert '<a class="btn btn-sm" href="/">Back to the game</a>' in html
    assert "Head to Head</button>" in html


# --------------------------------------------------------------------------- #
# B. The live score beside each team
# --------------------------------------------------------------------------- #


def test_scorebug_has_a_score_slot_beside_each_abbreviation(client):
    for path in ("/", "/admin"):
        html = client.get(path).text
        bug = html[html.index('id="scorebug"'):html.index("</section>", html.index('id="scorebug"'))]
        assert bug.count('<div class="team-top"><div class="team-abbr"></div><div class="team-score" hidden></div></div>') == 2, path
        assert ("bug-age" in bug) == (path == "/admin"), "only the host console says how old the score is"


PRELUDE = r"""
const fs = require("fs");
class FakeNode {
  constructor(tag = "div") {
    Object.assign(this, { tagName: tag, hidden: false, disabled: false, checked: false, value: "", textContent: "",
      className: "", dataset: {}, style: { setProperty() {} }, children: [], options: [], attrs: {}, handlers: {},
      firstChild: { nodeValue: "" }, firstElementChild: { style: {} },
      classList: { toggle() {}, add() {}, remove() {}, contains: () => false } });
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  querySelector(sel) { return page(sel); }
  querySelectorAll() { return []; }
  remove() {} focus() {} scrollIntoView() {} click() {}
}
const reg = new Map();
const page = (sel) => { if (!reg.has(sel)) reg.set(sel, new FakeNode()); return reg.get(sel); };
const textOf = (n) => (n.textContent || "") + (n.children || []).map(textOf).join("");
const document = {
  documentElement: new FakeNode(), title: "Admin Console", hidden: false,
  querySelector: page, querySelectorAll: () => [], addEventListener() {},
  createElement: (t) => new FakeNode(t), createTextNode: (t) => Object.assign(new FakeNode("#text"), { textContent: String(t) }),
};
const globals = { document, window: { addEventListener() {} }, Node: FakeNode, setInterval: () => 0, localStorage: {},
  navigator: {}, location: { protocol: "http:", host: "x" } };
const load = (files) => new Function(...Object.keys(globals),
  files.map((f) => fs.readFileSync(f, "utf8")).join("\n;\n") + "\n;return { PTP: PTP, PTPFeed: typeof PTPFeed === 'undefined' ? null : PTPFeed };"
)(...Object.values(globals));
"""


def run_node(body):
    script = PRELUDE + body
    done = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


SCOREBUG = r"""
const { PTP } = load(["static/js/common.js"]);
const game = (extra) => ({ home_name: "Washington", away_name: "Carolina", home_primary: "#5A1414", away_primary: "#0085CA",
  home_secondary: "#FFB612", away_secondary: "#101820", status: "LIVE", ...extra });
const bug = (withSlots, withAge) => {
  const nodes = {};
  const root = { hidden: true, dataset: {}, querySelector: (sel) => {
    const slot = /team-score|bug-age/.test(sel);
    if (slot && !withSlots) return null;
    if (sel === ".bug-age" && !withAge) return null;
    return (nodes[sel] = nodes[sel] || new FakeNode()); } };
  return { root, nodes };
};
const read = ({ root, nodes }) => ({
  away: nodes[".team.away .team-score"] ? { hidden: nodes[".team.away .team-score"].hidden, text: nodes[".team.away .team-score"].textContent } : null,
  home: nodes[".team.home .team-score"] ? { hidden: nodes[".team.home .team-score"].hidden, text: nodes[".team.home .team-score"].textContent } : null,
  age: nodes[".bug-age"] ? { hidden: nodes[".bug-age"].hidden, text: nodes[".bug-age"].textContent } : null,
});
const run = (extra, withSlots = true, withAge = true) => { const b = bug(withSlots, withAge); PTP.renderScorebug(b.root, game(extra), null); return read(b); };
const at = Date.now() / 1000;
const out = {
  good: run({ home_score: 21, away_score: 17, score_at: at - 12 }),
  zero: run({ home_score: 0, away_score: 0, score_at: at - 1 }),
  nulls: run({ home_score: null, away_score: null, score_at: null }),
  missing: run({}),
  odd: ["21", NaN, Infinity, -3, 2.5, {}, [], true, 1000].map((v) => run({ home_score: v, away_score: 7, score_at: at })),
  oneSided: run({ home_score: 21, away_score: null, score_at: at }),
  oddAt: run({ home_score: 3, away_score: 0, score_at: "soon" }),
  longAgo: run({ home_score: 3, away_score: 0, score_at: at - 400 }),
  noSlots: run({ home_score: 21, away_score: 17, score_at: at }, false, false),
  noAgeLine: run({ home_score: 21, away_score: 17, score_at: at }, true, false),
};
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_scorebug_shows_the_score_only_when_it_is_good():
    out = run_node(SCOREBUG)
    good = out["good"]
    assert (good["away"], good["home"]) == ({"hidden": False, "text": "17"}, {"hidden": False, "text": "21"})
    assert re.fullmatch(r"Score as of 1[2-4] s ago", good["age"]["text"]) and good["age"]["hidden"] is False
    assert out["zero"]["away"] == {"hidden": False, "text": "0"} and out["zero"]["home"]["text"] == "0", "0-0 is a score"
    quiet = {"hidden": True, "text": ""}
    for name in ("nulls", "missing"):
        assert (out[name]["away"], out[name]["home"]) == (quiet, quiet), name
        assert out[name]["age"]["hidden"] is True, name
    for i, shown in enumerate(out["odd"]):
        assert (shown["away"], shown["home"]) == (quiet, quiet), f"odd value #{i}"
    assert (out["oneSided"]["away"], out["oneSided"]["home"]) == (quiet, quiet), "both or neither"
    assert out["oddAt"]["home"] == {"hidden": False, "text": "3"} and out["oddAt"]["age"]["hidden"] is True, "no time, no age"
    assert out["longAgo"]["age"]["text"] == "Score as of 7 min ago"
    assert out["noSlots"] == {"away": None, "home": None, "age": None}, "an old page without the slots does not break"
    assert out["noAgeLine"]["away"]["text"] == "17" and out["noAgeLine"]["age"] is None, "player pages have no age line"


# --------------------------------------------------------------------------- #
# W7. A "No play" the server downgraded to review is still a no-play card
# --------------------------------------------------------------------------- #

CARD = r"""
const { PTPFeed } = load(["static/js/common.js", "static/js/admin-feed.js"]);
const feedState = (sg) => ({ available: true, linked: true, source: "tank01", game_id: "x", state: "idle", message: "", paused: false,
  auto_score: true, auto_open: false, requests: { game: 3, game_cap: 600 }, lag: {}, waiting: null, disagreement: null,
  next_down: null, auto_open_at: null, last_scored: undefined, suggestion: sg });
const base = { play_id: 1, status: "review", text: "PENALTY on WAS, False Start, 5 yards - No Play.", clock: "Q1 8:20",
  down_and_distance: "2nd & 8", play_type: null, direction: null, yards: null, yardage: null, flags: [], warning: null,
  auto_at: null, kind: "void" };
const card = async (sg) => {
  reg.clear();
  const st = { game: { id: 1 }, play: { id: 1, state: "LOCKED", voided: false, play_number: 1 }, history: [], feed: feedState(sg) };
  const acts = [];
  const feed = PTPFeed.create({ act: async (a, p) => { acts.push([a, p]); return { ok: true }; }, getState: () => st, getKey: () => "k",
    isBusy: () => false, loadResult() {}, applyNextDown() {}, refreshHistory() {} });
  feed.render(st);
  const chips = page("#sg-chips").children;
  const out = { badge: page("#sg-badge").textContent, chips: chips.map((c) => `${c.className}:${textOf(c)}`),
    askGroups: chips.filter((c) => c.className === "ask").length,
    score: { label: page("#sg-score").firstChild.nodeValue.trim(), disabled: page("#sg-score").disabled },
    flags: page("#sg-flags").children.map(textOf), flagsHidden: page("#sg-flags").hidden,
    warning: page("#sg-warning").hidden ? null : page("#sg-warning").textContent,
    note: page("#sg-note").hidden ? null : page("#sg-note").textContent, skipHidden: page("#sg-skip").hidden };
  await page("#sg-score").handlers.click();
  out.acts = acts;
  return out;
};
(async () => {
  const flag = "Check the down and distance before voiding this play";
  const out = {
    downgraded: await card({ ...base, flags: ["Penalty: False Start, no play", flag] }),
    withWarning: await card({ ...base, flags: [flag], warning: "Feed shows 2nd & 3 but this play is 2nd & 8" }),
    readyVoid: await card({ ...base, status: "void", auto_at: Date.now() / 1000 + 5 }),
    oldServer: await card({ ...base, kind: undefined }),
    odd: await card({ ...base, kind: "review", status: "review", play_type: "PASS", direction: null, yards: 0, yardage: "SHORT",
      text: "A.Dalton pass INTERCEPTED", flags: ["Interception"] }),
  };
  process.stdout.write(JSON.stringify(out));
})();
"""


@needs_node
def test_downgraded_no_play_still_offers_void_with_its_warning():
    out = run_node(CARD)
    for name in ("downgraded", "withWarning"):
        card = out[name]
        assert card["badge"] == "NEEDS YOUR CHECK", name
        assert card["chips"] == ["rchip:NO PLAY"] and card["askGroups"] == 0, f"{name}: no Run/Pass/direction/distance pickers"
        assert card["score"] == {"label": "Void play", "disabled": False}, name
        assert "Check the down and distance before voiding this play" in card["flags"] and card["flagsHidden"] is False, name
        assert card["skipHidden"] is False and "Void play" in card["note"], name
        assert card["acts"] == [["feed_accept", {"play_id": 1, "void": True}]], f"{name}: the button voids"
    assert out["withWarning"]["warning"] == "Feed shows 2nd & 3 but this play is 2nd & 8", "the warning stays in view"
    # Unchanged: a clean void, and a server that does not send `kind` (read from the text when on hold).
    assert out["readyVoid"]["chips"] == ["rchip:NO PLAY"] and out["readyVoid"]["score"]["label"] == "Void play"
    assert out["oldServer"]["askGroups"] == 3, "without a kind, a review stays a review"
    # An unusual play that is not a void still asks for what is missing and cannot be scored until it has it.
    odd = out["odd"]
    assert odd["askGroups"] == 1 and odd["score"] == {"label": "Score now", "disabled": True}
