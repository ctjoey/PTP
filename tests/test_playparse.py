"""playparse: classifying Tank01 play-by-play text, and working out the next down and distance."""

import json
import random
from collections import Counter
from pathlib import Path

import pytest

from models import yardage_for_yards
from playparse import classify, next_down_and_distance, parse_down_and_distance, same_down_and_distance

FIXTURE = Path(__file__).resolve().parents[1] / "demo" / "box_CAR_WSH_20241020.json"
BODY = json.loads(FIXTURE.read_text())["body"]
ENTRIES = BODY["allPlayByPlay"]
DD = "1st & 10 at CAR 25"  # any scrimmage down and distance


def entry(text, dd=DD, **extra):
    e = {"play": text, "playPeriod": "Q2", "playClock": "4:03", **extra}
    if dd is not None:
        e["downAndDistance"] = dd
    return e


def result(text, dd=DD):
    p = classify(entry(text, dd))
    return p.kind, p.play_type, p.direction, p.yards, p.yardage


# --------------------------------------------------------------------------- #
# The recorded game
# --------------------------------------------------------------------------- #


def test_fixture_totals():
    """159 real entries. The five touchdown entries that carry their extra point are plays or reviews, not skips:
    skipping them would shift every later play onto the wrong feed entry."""
    kinds = Counter(classify(e).kind for e in ENTRIES)
    assert len(ENTRIES) == 159
    assert kinds == {"skip": 44, "void": 12, "play": 97, "review": 6}
    # The same data in the spec's terms: add the five touchdown entries to skip and take them off play/review.
    td_with_xp = [i for i, e in enumerate(ENTRIES) if "extra point" in e["play"] and "TOUCHDOWN" in e["play"]]
    assert len(td_with_xp) == 5
    assert [classify(ENTRIES[i]).kind for i in td_with_xp] == ["review", "play", "play", "play", "play"]
    assert kinds["skip"] + 5 == 49 and kinds["play"] - 4 == 93 and kinds["review"] - 1 == 5


def test_fixture_reviews_are_exactly_the_odd_plays():
    reviews = {i: classify(e) for i, e in enumerate(ENTRIES) if classify(e).kind == "review"}
    assert set(reviews) == {8, 31, 34, 41, 89, 103}
    for i in (34, 41, 103):  # sacks: a pass, no direction, the (negative or zero) yards prefilled
        p = reviews[i]
        assert (p.play_type, p.direction, p.reason) == ("PASS", None, "sack") and "Sack" in p.flags
    assert (reviews[34].yards, reviews[34].yardage) == (-4, "LOSS")
    assert (reviews[103].yards, reviews[103].yardage) == (0, "SHORT")
    # Interceptions: the "for 20 yards" / "for 67 yards" are returns, so the prefill is a pass for 0.
    for i in (8, 31):
        p = reviews[i]
        assert (p.play_type, p.direction, p.yards, p.yardage) == ("PASS", "RIGHT", 0, "SHORT")
        assert "Interception" in p.flags
    assert any("Penalty" in f for f in reviews[31].flags)
    assert reviews[89].reason == "aborted_snap" and "Aborted snap" in reviews[89].flags


def test_fixture_voids_are_all_the_no_play_entries():
    voids = [i for i, e in enumerate(ENTRIES) if classify(e).kind == "void"]
    assert voids == [18, 35, 46, 59, 61, 76, 77, 91, 106, 112, 118, 126]
    assert all("no play" in ENTRIES[i]["play"].lower() for i in voids)
    assert all(classify(ENTRIES[i]).flags for i in voids)


def test_fixture_skips_are_the_non_plays():
    reasons = Counter(classify(e).reason for e in ENTRIES if classify(e).kind == "skip")
    assert reasons == {"kickoff": 11, "punt": 6, "field_goal": 4, "timeout": 16, "warning": 2,
                       "end_period": 2, "kneel": 3}


def test_every_fixture_play_is_complete_and_consistent():
    plays = [classify(e) for e in ENTRIES if classify(e).kind == "play"]
    assert len(plays) == 97
    for p in plays:
        assert p.play_type in ("RUN", "PASS") and p.direction in ("LEFT", "MIDDLE", "RIGHT")
        assert isinstance(p.yards, int) and p.yardage == yardage_for_yards(p.yards).value
        assert p.flags == [] and p.reason == "clean" and p.text and p.clock
    # Incomplete passes and no gain are 0 yards = SHORT; negative yards are LOSS.
    assert all(p.yards == 0 and p.yardage == "SHORT" for p in plays if "incomplete" in p.text or "no gain" in p.text)
    assert sum(1 for p in plays if p.yardage == "LOSS") == 6


def test_touchdown_plays_keep_their_play_and_ignore_the_extra_point_tail():
    for i, (kind, ptype, direction, yards) in {48: ("play", "RUN", "MIDDLE", 8), 84: ("play", "PASS", "RIGHT", 12),
                                               99: ("play", "PASS", "RIGHT", 3), 132: ("play", "RUN", "LEFT", 4)}.items():
        p = classify(ENTRIES[i])
        assert (p.kind, p.play_type, p.direction, p.yards) == (kind, ptype, direction, yards)
        assert p.touchdown


SPOT_CHECKS = [
    ("A.Dalton pass short right to J.Sanders to CAR 21 for 7 yards (B.St-Juste).", ("play", "PASS", "RIGHT", 7, "MEDIUM")),
    ("C.Hubbard left end to CAR 25 for 4 yards (M.Sainristil, Q.Martin).", ("play", "RUN", "LEFT", 4, "SHORT")),
    ("A.Dalton pass incomplete short left to Di.Johnson (M.Sainristil).", ("play", "PASS", "LEFT", 0, "SHORT")),
    ("M.Mariota scrambles left end ran ob at CAR 47 for 11 yards (C.Smith-Wade).", ("play", "RUN", "LEFT", 11, "LONG")),
    ("C.Hubbard left end pushed ob at CAR 46 for no gain (M.Sainristil).", ("play", "RUN", "LEFT", 0, "SHORT")),
    ("A.Ekeler up the middle to CAR 38 for 6 yards (X.Woods; C.Smith-Wade).", ("play", "RUN", "MIDDLE", 6, "MEDIUM")),
    ("M.Mariota sacked at CAR 46 for -4 yards (C.Harris).", ("review", "PASS", None, -4, "LOSS")),
]


@pytest.mark.parametrize("text, expected", SPOT_CHECKS)
def test_spot_checks(text, expected):
    assert result(text) == expected


# --------------------------------------------------------------------------- #
# Other phrasings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text, expected", [
    # runs
    ("J.Taylor right guard to IND 33 for 5 yards (X.Smith).", ("play", "RUN", "RIGHT", 5, "SHORT")),
    ("D.Henry left tackle to TEN 22 for 12 yards (A.Smith; B.Jones).", ("play", "RUN", "LEFT", 12, "LONG")),
    ("N.Chubb up the middle for 2 yards, TOUCHDOWN.", ("play", "RUN", "MIDDLE", 2, "SHORT")),
    ("J.Allen scrambles up the middle to BUF 30 for 4 yards (D.Wise).", ("play", "RUN", "MIDDLE", 4, "SHORT")),
    ("J.Allen scrambles right end ran ob at BUF 30 for 14 yards.", ("play", "RUN", "RIGHT", 14, "LONG")),
    ("A.Ekeler left end for a loss of 3 yards (X.Woods).", ("play", "RUN", "LEFT", -3, "LOSS")),
    ("A.Ekeler right end to CAR 38 for no gain (X.Woods).", ("play", "RUN", "RIGHT", 0, "SHORT")),
    ("D.Henry right tackle to TEN 25 for 1 yard (A.Smith).", ("play", "RUN", "RIGHT", 1, "SHORT")),
    ("(Shotgun) J.Taylor left guard to IND 33 for 10 yards (X.Smith).", ("play", "RUN", "LEFT", 10, "MEDIUM")),
    ("(No Huddle, Shotgun) J.Taylor up the middle to IND 33 for 6 yards (X.Smith).", ("play", "RUN", "MIDDLE", 6, "MEDIUM")),
    ("Direct snap to B.Robinson.  B.Robinson left guard to CAR 8 for 6 yards (D.Jackson).", ("play", "RUN", "LEFT", 6, "MEDIUM")),
    ("T.Scott reported in as eligible.  J.McNichols right tackle to WAS 31 for 7 yards (L.Ray).", ("play", "RUN", "RIGHT", 7, "MEDIUM")),
    ("J.Taylor left end to IND 33 for 5 yards (X.Smith). Penalty on IND-Y.Zed, Offensive Holding, declined.", ("play", "RUN", "LEFT", 5, "SHORT")),
    # passes
    ("P.Mahomes pass deep middle to T.Kelce to KC 45 for 25 yards (A.Jones).", ("play", "PASS", "MIDDLE", 25, "LONG")),
    ("P.Mahomes pass incomplete deep right to M.Hardman.", ("play", "PASS", "RIGHT", 0, "SHORT")),
    ("P.Mahomes pass incomplete short middle to T.Kelce (A.Jones).", ("play", "PASS", "MIDDLE", 0, "SHORT")),
    ("P.Mahomes pass incomplete deep left intended for T.Hill, broken up by X.Y.", ("play", "PASS", "LEFT", 0, "SHORT")),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe).", ("play", "PASS", "LEFT", 8, "MEDIUM")),
    ("J.Allen pass short right to S.Diggs for 15 yards, TOUCHDOWN.", ("play", "PASS", "RIGHT", 15, "LONG")),
    ("J.Allen pass short left to S.Diggs to BUF 18 for a loss of 2 yards (J.Doe).", ("play", "PASS", "LEFT", -2, "LOSS")),
    ("J.Allen pass short middle to D.Knox to BUF 20 for no gain (J.Doe).", ("play", "PASS", "MIDDLE", 0, "SHORT")),
    ("(Shotgun) J.Allen pass deep right to S.Diggs to BUF 40 for 30 yards (J.Doe). Penalty on BUF-Y.Zed, Holding, declined.",
     ("play", "PASS", "RIGHT", 30, "LONG")),
    ("J.Allen pass short left to S.Diggs to BUF 11 for 1 yard (J.Doe).", ("play", "PASS", "LEFT", 1, "SHORT")),
    ("J.Allen pass short right to S.Diggs to BUF 16 for 6 yards (J.Doe).", ("play", "PASS", "RIGHT", 6, "MEDIUM")),
    ("J.Allen pass short right to S.Diggs to BUF 21 for 11 yards (J.Doe).", ("play", "PASS", "RIGHT", 11, "LONG")),
])
def test_clean_plays(text, expected):
    assert result(text) == expected
    assert classify(entry(text)).flags == []


@pytest.mark.parametrize("text, reason, flag", [
    ("J.Allen sacked at BUF 20 for -7 yards (J.Doe).", "sack", "Sack"),
    ("J.Allen sacked at BUF 20 for a loss of 7 yards (J.Doe).", "sack", "Sack"),
    ("J.Allen pass short left intended for S.Diggs INTERCEPTED by X.Y at BUF 40. X.Y to BUF 30 for 10 yards.",
     "interception", "Interception"),
    ("J.Allen pass deep middle to S.Diggs INTERCEPTED by X.Y at END ZONE, touchback.", "interception", "Interception"),
    ("J.Taylor left end to IND 30 for 4 yards (X). FUMBLES (X), recovered by BAL-Y.Zed at IND 31.", "fumble", "Fumble"),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe). FUMBLES (J.Doe), recovered by BUF-S.Diggs.",
     "fumble", "Fumble"),
    ("J.Taylor left end to IND 30 for 4 yards (X). PENALTY on IND-Y.Zed, Offensive Holding, 10 yards, enforced at IND 25.",
     "penalty_accepted", "Penalty accepted: the yardage may be different"),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe). Lateral to X.Y to BUF 45 for 5 yards.",
     "conflicting_numbers", "Trick play (lateral, reverse or option)"),
    ("Reverse. W.Doe right end to IND 33 for 6 yards (X.Smith).", "trick_play", "Trick play (lateral, reverse or option)"),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe). Replay challenged, the play was REVERSED.",
     "replay", "Replay challenge: the call may have changed"),
    ("J.Allen pass to S.Diggs to BUF 40 for 8 yards (J.Doe).", "no_direction", None),
    ("J.Allen pass incomplete to S.Diggs.", "no_direction", None),
    ("J.Taylor rushes to IND 33 for 5 yards (X.Smith).", "no_direction", None),
    ("J.Allen scrambles to BUF 30 for 6 yards (D.Wise).", "no_direction", None),
    ("J.Taylor left end to IND 33.", "no_yards", None),
    ("J.Taylor to IND 33 for 5 yards (X.Smith).", "unrecognised", None),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards, then runs left end for 12 yards.", "conflicting", None),
    ("M.Mariota Aborted. T.Biadasz FUMBLES at WAS 41, recovered by WAS-M.Mariota at WAS 41.", "aborted_snap", "Aborted snap"),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe). Safety.", "safety", "Safety"),
    ("PENALTY on BUF-Y.Zed, Offensive Holding, 10 yards, enforced at BUF 25.", "penalty_only", None),
    ("J.Allen pass short left to S.Diggs to BUF 40 for 8 yards (J.Doe). Then to BUF 45 for 3 yards (Q).", "conflicting_numbers", None),
])
def test_odd_plays_go_to_the_host(text, reason, flag):
    p = classify(entry(text))
    assert p.kind == "review", (p.kind, p.reason)
    assert p.reason == reason
    assert p.flags, "the host is told why"
    if flag:
        assert flag in p.flags


def test_review_prefills_what_it_can():
    p = classify(entry("J.Allen pass deep left intended for S.Diggs INTERCEPTED by X.Y at BUF 40. X.Y to BUF 30 for 10 yards."))
    assert (p.play_type, p.direction, p.yards, p.yardage) == ("PASS", "LEFT", 0, "SHORT")
    p = classify(entry("J.Taylor left end to IND 30 for 4 yards (X). FUMBLES (X), recovered by BAL-Y at IND 31."))
    assert (p.play_type, p.direction, p.yards, p.yardage) == ("RUN", "LEFT", 4, "SHORT")
    p = classify(entry("J.Allen pass to S.Diggs to BUF 40 for 8 yards (J.Doe)."))
    assert (p.play_type, p.direction, p.yards, p.yardage) == ("PASS", None, 8, "MEDIUM")
    p = classify(entry("J.Allen sacked at BUF 20 for -7 yards (J.Doe)."))
    assert (p.play_type, p.direction, p.yards, p.yardage) == ("PASS", None, -7, "LOSS")


@pytest.mark.parametrize("text", [
    "A.Seibert kicks 59 yards from WAS 35 to CAR 6. R.Blackshear to CAR 30 for 24 yards (C.Yankoff).",
    "J.Tucker kicks off 65 yards to end zone, Touchback.",
    "J.Tucker kicks onside 15 yards, recovered by BUF-Y.Zed at BAL 45.",
    "A.Seibert kicks 66 yards from WAS 35 to CAR -1. R.Blackshear to CAR 21 for 22 yards. FUMBLES (J.Reaves), recovered by CAR-J.Sanders.",
    "S.Koch punts 45 yards to NE 20, Center-J.Doe, downed by BAL-X.Y.",
    "J.Hekker punts 62 yards to WAS 26, Center-J.Jansen. O.Zaccheaus to WAS 41 for 15 yards (J.Windmon).",
    "J.Tucker 52 yard field goal is No Good, Hit Left Upright, Center-N.Moore, Holder-S.Koch.",
    "J.Tucker 23 yard field goal is GOOD, Center-N.Moore, Holder-S.Koch.",
    "J.Tucker extra point is GOOD, Center-N.Moore, Holder-S.Koch.",
    "J.Tucker extra point is No Good, Blocked.",
    "TWO-POINT CONVERSION ATTEMPT. L.Jackson pass to M.Andrews is complete. ATTEMPT SUCCEEDS.",
    "Two-Point Conversion Attempt. J.Taylor right end ATTEMPT FAILS.",
    "L.Jackson kneels to BLT 20 for -1 yards.",
    "L.Jackson spikes the ball to stop the clock.",
    "L.Jackson spikes the ball.",
    "Timeout #1 by BAL at 04:12.",
    "Timeout at 11:14.",
    "Two-Minute Warning",
    "END QUARTER 1",
    "END QUARTER 3",
    "END GAME",
    "End of Half",
    "Official timeout at 08:44.",
    "Injury timeout, BUF-S.Diggs is being checked.",
    "Washington challenged the runner was down by contact ruling, and the play was Upheld. The ruling on the field stands.",
    "Coin toss won by BUF, elected to receive.",
])
def test_non_plays_are_skipped(text):
    for dd in (None, "1st & 10 at CAR 25"):  # the feed copies the next down and distance onto timeouts and the like
        assert classify(entry(text, dd)).kind == "skip", text


def test_no_play_is_a_void_on_scrimmage_entries_only():
    text = "PENALTY on WAS-T.Biadasz, False Start, 5 yards, enforced at CAR 39 - No Play."
    p = classify(entry(text, "2nd & 5 at CAR 39"))
    assert p.kind == "void" and p.reason == "no_play" and p.flags == ["Penalty: False Start, no play"]
    assert classify(entry(text.lower(), "2nd & 5 at CAR 39")).kind == "void"
    assert classify(entry("J.Allen pass incomplete short left. PENALTY on BAL-X, Defensive Holding, 5 yards - NO PLAY.")).kind == "void"
    assert classify(entry("J.Taylor left end to IND 33 for 5 yards. PENALTY on IND-Z, Holding, 10 yards - No Play.")).kind == "void"
    # No down and distance: a penalty on a kickoff or other special-teams snap, not one of the host's plays.
    assert classify(entry(text, None)).kind == "skip"
    # A punt that is nullified is still special teams.
    assert classify(entry("S.Koch punts 45 yards to NE 20. PENALTY on BAL-X, Offside, 5 yards - No Play.")).kind == "skip"


def test_declined_penalties_do_not_matter_but_offsetting_ones_need_the_host():
    p = classify(entry("J.Taylor left end to IND 33 for 5 yards (X). Penalty on IND-Z, Holding, declined."))
    assert (p.kind, p.yards) == ("play", 5)
    # Offsetting penalties normally mean the down is replayed: never auto-score that.
    p = classify(entry("J.Taylor left end to IND 33 for 5 yards (X). PENALTY on IND-Z, Holding, offsetting. "
                       "PENALTY on BAL-Y, Holding, offsetting."))
    assert (p.kind, p.reason, p.yards) == ("review", "penalty_offsetting", 5)
    assert any("Offsetting" in f for f in p.flags)


def test_a_penalty_yardage_is_never_the_play_yardage():
    p = classify(entry("J.Allen pass incomplete short left to S.Diggs. PENALTY on BAL-X, Holding, 10 yards, enforced at BUF 25."))
    assert p.kind == "review" and p.yards == 0
    p = classify(entry("J.Allen pass deep right to S.Diggs to BUF 40 for 8 yards (J.Doe). Penalty on BUF-Y, Holding, 10 yards, "
                       "declined."))
    assert (p.kind, p.yards) == ("play", 8)


# --------------------------------------------------------------------------- #
# Robustness: never raise, always say something safe
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [
    None, {}, [], "", "   ", 0, 1.5, True, b"bytes", object(), {"play": None}, {"play": ""}, {"play": 123},
    {"play": ["not", "text"]}, {"play": {"a": 1}}, {"downAndDistance": "1st & 10 at CAR 25"},
    {"play": "x", "downAndDistance": None}, {"play": "x", "downAndDistance": 5}, {"play": "x", "playClock": None},
    {"play": "\x00\x01‮ weird \U0001f3c8"}, {"play": "x" * 20000}, {"play": "left end " * 3000},
])
def test_never_raises_on_odd_input(value):
    p = classify(value)
    assert p.kind in ("skip", "void", "play", "review")
    assert isinstance(p.flags, list) and isinstance(p.reason, str)
    if p.kind == "play":
        assert p.play_type and p.direction and p.yards is not None


def test_empty_entries_are_skips_and_unknown_scrimmage_text_is_a_review():
    assert classify(None).kind == "skip" and classify({"play": ""}).kind == "skip" and classify({}).kind == "skip"
    assert classify(entry("Something nobody has ever seen before happened.")).kind == "review"
    assert classify(entry("Something nobody has ever seen before happened.", None)).kind == "skip"


def test_fuzz_never_raises_and_clean_plays_are_complete():
    rng = random.Random(20261008)
    words = ["pass", "left", "right", "middle", "end", "tackle", "guard", "up the", "short", "deep", "incomplete", "for", "7", "-3",
             "yards", "yard", "no gain", "loss of", "a", "sacked", "INTERCEPTED", "FUMBLES", "PENALTY on X-Y,", "declined.",
             "No Play", "TOUCHDOWN", "kicks", "punts", "to", "CAR 21", "(A.B)", ".", ",", "A.Dalton", "scrambles", "Reverse",
             "lateral", "Timeout", "extra point", "is GOOD", "(Shotgun)", "1st & 10", "-", "é", "None", "",
             "Aborted", "Direct snap", "challenged", "REVERSED", "\n", "\t", "  "]
    for _ in range(4000):
        text = " ".join(rng.choice(words) for _ in range(rng.randint(0, 14)))
        dd = rng.choice([None, "1st & 10 at CAR 25", "4th & Goal at WSH 2", "garbage"])
        p = classify(entry(text, dd))
        assert p.kind in ("skip", "void", "play", "review")
        if p.kind == "play":
            assert p.play_type in ("RUN", "PASS") and p.direction in ("LEFT", "MIDDLE", "RIGHT")
            assert p.yardage == yardage_for_yards(p.yards).value and p.flags == []
        if p.kind == "review":
            assert p.flags
        for fn in (next_down_and_distance,):
            assert fn(entry(text, dd, teamID="5"), p, "WSH", "CAR", "32", "5") is None or p.kind == "play"
    for _ in range(500):  # raw junk characters
        text = "".join(chr(rng.randint(0, 0x2FFF)) for _ in range(rng.randint(0, 80)))
        assert classify(entry(text)).kind in ("skip", "void", "play", "review")


# --------------------------------------------------------------------------- #
# down and distance
# --------------------------------------------------------------------------- #


def test_parse_down_and_distance():
    assert parse_down_and_distance("2nd & 3 at CAR 21") == {"down": 2, "to_go": "3", "spot_team": "CAR", "spot": 21}
    assert parse_down_and_distance("1st & Goal at WSH 7") == {"down": 1, "to_go": "goal", "spot_team": "WSH", "spot": 7}
    assert parse_down_and_distance("1st & 10 at 50") == {"down": 1, "to_go": "10", "spot_team": None, "spot": 50}
    assert parse_down_and_distance("4th & 15") == {"down": 4, "to_go": "15", "spot_team": None, "spot": None}
    for bad in (None, "", "garbage", "5th & 1 at CAR 2", 3, "0th & 1"):
        assert parse_down_and_distance(bad) is None
    a, b = parse_down_and_distance("2nd & 3 at CAR 21"), parse_down_and_distance("2nd & 3 at WSH 40")
    assert same_down_and_distance(a, b) is True
    assert same_down_and_distance(a, parse_down_and_distance("2nd & 8 at CAR 21")) is False
    assert same_down_and_distance(a, None) is None


HOME, AWAY, HOME_ID, AWAY_ID = "WSH", "CAR", "32", "5"


def nxt(text, dd, team="5"):
    e = entry(text, dd, teamID=team)
    return next_down_and_distance(e, classify(e), HOME, AWAY, HOME_ID, AWAY_ID)


def run(yards, dd, team="5", extra=""):
    text = (f"C.Hubbard left end to CAR 25 for {yards} yards{extra}" if yards >= 0
            else f"C.Hubbard left end to CAR 25 for a loss of {-yards} yards{extra}")
    return nxt(text, dd, team)


@pytest.mark.parametrize("dd, yards, team, expected", [
    # own side (CAR has the ball and the spot is CAR's own)
    ("1st & 10 at CAR 14", 7, "5", {"down": 2, "distance": "3"}),
    ("2nd & 3 at CAR 21", 4, "5", {"down": 1, "distance": "10"}),
    ("2nd & 3 at CAR 21", 3, "5", {"down": 1, "distance": "10"}),   # first down by exactly the yards needed
    ("2nd & 3 at CAR 21", 2, "5", {"down": 3, "distance": "1"}),    # one short
    ("1st & 10 at CAR 25", 26, "5", {"down": 1, "distance": "10"}),
    ("2nd & 6 at CAR 25", -3, "5", {"down": 3, "distance": "9"}),   # loss of yards
    ("1st & 10 at CAR 25", 0, "5", {"down": 2, "distance": "10"}),
    ("3rd & 4 at CAR 41", 0, "5", {"down": 4, "distance": "4"}),    # 4th down is fine to prefill
    # opponent side (the spot names the other team)
    ("1st & 10 at WSH 49", 6, "5", {"down": 2, "distance": "4"}),
    ("1st & 10 at WSH 25", 5, "5", {"down": 2, "distance": "5"}),
    ("2nd & 7 at WSH 15", 8, "5", {"down": 1, "distance": "Goal"}),  # first down inside the 10
    ("3rd & 8 at WSH 20", 12, "5", {"down": 1, "distance": "Goal"}),
    ("1st & 10 at WSH 30", 14, "5", {"down": 1, "distance": "10"}),  # first down at the 16: plain 1st & 10
    ("3rd & 7 at WSH 17", 9, "5", {"down": 1, "distance": "Goal"}),
    ("1st & 10 at 50", 3, "5", {"down": 2, "distance": "7"}),         # midfield has no team
    ("1st & 10 at 50", 12, "5", {"down": 1, "distance": "10"}),
    # the other team has the ball: its own side is the other abbreviation
    ("1st & 10 at CAR 44", 5, "32", {"down": 2, "distance": "5"}),
    ("1st & 10 at WSH 10", 46, "32", {"down": 1, "distance": "10"}),
    ("2nd & 10 at WSH 31", 7, "32", {"down": 3, "distance": "3"}),
    # goal to go stays "Goal" and never becomes a number
    ("1st & Goal at CAR 8", 3, "32", {"down": 2, "distance": "Goal"}),
    ("2nd & Goal at CAR 5", -3, "32", {"down": 3, "distance": "Goal"}),
    ("3rd & Goal at CAR 8", 3, "32", {"down": 4, "distance": "Goal"}),
    ("1st & Goal at WSH 6", 3, "5", {"down": 2, "distance": "Goal"}),
    ("1st & 10 at WSH 10", 3, "5", {"down": 2, "distance": "Goal"}),  # 10 to go from the 10 is goal to go
    # 4th down
    ("4th & 4 at CAR 41", 4, "5", {"down": 1, "distance": "10"}),
    ("4th & 4 at CAR 41", 5, "5", {"down": 1, "distance": "10"}),
    ("4th & 4 at WSH 8", 5, "5", {"down": 1, "distance": "Goal"}),
])
def test_next_down_and_distance(dd, yards, team, expected):
    assert run(yards, dd, team) == expected


@pytest.mark.parametrize("dd, yards, team", [
    ("4th & 1 at CAR 40", -2, "5"),    # a would-be 5th down (turnover on downs)
    ("4th & 4 at CAR 41", 2, "5"),
    ("4th & Goal at CAR 2", 1, "32"),
    ("1st & Goal at WSH 4", 4, "5"),   # reaches the goal line: a touchdown
    ("1st & 10 at WSH 6", 9, "5"),
    ("1st & 10 at CAR 3", -5, "5"),    # a safety
    ("1st & 10 at CAR 25", 3, "99"),   # unknown team id
    ("1st & 10 at CAR 25", 3, ""),
    ("1st & 10 at XYZ 25", 3, "5"),    # unknown spot team
    ("1st & 10 at CAR", 3, "5"),       # no spot
    ("1st & 10 at CAR 77", 3, "5"),    # impossible spot
    ("2nd & 90 at CAR 25", 3, "5"),    # to-go larger than the field
    ("garbage", 3, "5"),
    ("2nd & inches at CAR 25", 3, "5"),
])
def test_next_down_is_none_when_uncertain(dd, yards, team):
    assert run(yards, dd, team) is None


def test_next_down_is_none_without_a_clean_play():
    td = nxt("C.Hubbard left end for 4 yards, TOUCHDOWN.E.Pineiro extra point is GOOD", "1st & Goal at WSH 4")
    assert td is None
    assert nxt("A.Dalton sacked at CAR 12 for -4 yards (F.Luvu).", "3rd & 5 at CAR 16") is None     # review
    assert nxt("A.Dalton pass short right INTERCEPTED by X.Y at WAS 33.", "3rd & 9 at WSH 25") is None
    assert nxt("PENALTY on WAS-T.Biadasz, False Start, 5 yards, enforced at CAR 39 - No Play.", "2nd & 5 at CAR 39") is None
    assert nxt("J.Hekker punts 49 yards to WAS 10.", "4th & 4 at CAR 41") is None
    assert nxt("C.Hubbard left end to CAR 25 for 4 yards (M.Sainristil).", None) is None
    assert next_down_and_distance(None, classify(None), HOME, AWAY, HOME_ID, AWAY_ID) is None
    assert next_down_and_distance("text", classify("text"), HOME, AWAY, HOME_ID, AWAY_ID) is None
    e = entry("C.Hubbard left end to CAR 25 for 4 yards (M.Sainristil).", "2nd & 3 at CAR 21", teamID="5")
    assert next_down_and_distance(e, classify(e), None, AWAY, HOME_ID, AWAY_ID) is None
    assert next_down_and_distance(e, classify(e), HOME, AWAY, None, None) is None
    e.pop("teamID")
    assert next_down_and_distance(e, classify(e), HOME, AWAY, HOME_ID, AWAY_ID) is None


def test_next_down_agrees_with_the_recorded_game():
    """For every clean play followed by the same team's next snap, the prediction matches the feed's own next
    down and distance. (When the 4th-down play is a punt or field goal the next entry is the other team: those
    predictions are the 4th-down ones, which are still right for a host who opens that play.)"""
    agree = checked = 0
    for i, e in enumerate(ENTRIES):
        p = classify(e)
        guess = next_down_and_distance(e, p, BODY["home"], BODY["away"], BODY["teamIDHome"], BODY["teamIDAway"])
        if p.kind != "play" or guess is None:
            continue
        j = i + 1
        while j < len(ENTRIES) and classify(ENTRIES[j]).kind == "skip":
            j += 1
        if j >= len(ENTRIES) or ENTRIES[j]["teamID"] != e["teamID"]:
            continue
        actual = parse_down_and_distance(ENTRIES[j].get("downAndDistance"))
        checked += 1
        agree += (actual["down"], actual["to_go"]) == (guess["down"], guess["distance"].lower())
    assert checked >= 60 and agree == checked
