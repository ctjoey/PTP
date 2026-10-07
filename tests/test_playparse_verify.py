"""Adversarial checks of the play-by-play reader: invented, realistic NFL strings that must never be auto-scored
wrongly, and the false-skip cases that would shift every later play onto the wrong feed entry."""

import random
import re

import pytest

from models import yardage_for_yards
from playparse import classify, next_down_and_distance

DD = "2nd & 8 at CAR 30"


def entry(text, dd=DD, **extra):
    e = {"play": text, "playPeriod": "Q3", "playClock": "7:41", "teamID": "1", **extra}
    if dd is not None:
        e["downAndDistance"] = dd
    return e


def read(text, dd=DD):
    p = classify(entry(text, dd))
    return p.kind, p.play_type, p.direction, p.yards


# (text, (kind, type, direction, yards)); None parts are not checked.
INVENTED = [
    # -- clean plays: these may be scored without the host --
    ("J.Burrow scrambles up the middle to CIN 40 for 8 yards (R.Smith).", ("play", "RUN", "MIDDLE", 8)),
    ("J.Burrow scrambles right end ran ob at CIN 40 for 8 yards (R.Smith).", ("play", "RUN", "RIGHT", 8)),
    ("L.Jackson pass incomplete short middle to M.Andrews (K.Hamilton).", ("play", "PASS", "MIDDLE", 0)),
    ("L.Jackson pass incomplete short middle intended for M.Andrews.", ("play", "PASS", "MIDDLE", 0)),
    ("J.Fields pass incomplete short left to N.Mooney [A.Jones] deflected.", ("play", "PASS", "LEFT", 0)),
    ("A.Rodgers pass short right to A.Lazard for 12 yards, TOUCHDOWN.", ("play", "PASS", "RIGHT", 12)),
    ("A.Rodgers pass deep middle to G.Wilson for 55 yards, TOUCHDOWN.J.Doe extra point is GOOD.", ("play", "PASS", "MIDDLE", 55)),
    ("D.Henry left end for 99 yards, TOUCHDOWN.N.Folk extra point is GOOD, Center-M.Cox, Holder-R.Brown.", ("play", "RUN", "LEFT", 99)),
    ("D.Henry right guard to TEN 1 for a loss of 2 yards (L.Jones).", ("play", "RUN", "RIGHT", -2)),
    ("D.Henry right guard to TEN 1 for 0 yards (L.Jones).", ("play", "RUN", "RIGHT", 0)),
    ("J.Goff pass short middle to A.St. Brown to DET 40 for 12 yards (D.Wade); first down.", ("play", "PASS", "MIDDLE", 12)),
    ("J.Goff pass short left to A.St. Brown to DET 40 for 12 yards. Penalty on DET-A.St. Brown, Offensive Holding, 10 yards, "
     "enforced at DET 20, declined.", ("play", "PASS", "LEFT", 12)),
    ("M.Harrison Jr. right end to ARI 40 for 3 yards (A.Hooker).", ("play", "RUN", "RIGHT", 3)),
    ("J.Hurts QB sneak up the middle to PHI 3 for 1 yard.", ("play", "RUN", "MIDDLE", 1)),
    ("B.Spikes left end to NE 20 for 3 yards (A.Hooker).", ("play", "RUN", "LEFT", 3)),  # a surname is not a spike
    ("A.Rodgers pass short left to B.Spikes to NYJ 30 for 7 yards (T.Smith).", ("play", "PASS", "LEFT", 7)),
    # -- touchdowns that carry a two-point try or an odd extra point: still the touchdown play (never a skip) --
    ("R.Wilson pass short left to D.Metcalf for 22 yards, TOUCHDOWN. TWO-POINT CONVERSION ATTEMPT. "
     "R.Wilson pass to X is complete. ATTEMPT SUCCEEDS.", ("play", "PASS", "LEFT", 22)),
    ("R.Wilson pass short left to D.Metcalf for 22 yards, TOUCHDOWN.Two-Point Pass formation. ATTEMPT FAILS.",
     ("play", "PASS", "LEFT", 22)),
    ("D.Henry left end for 9 yards, TOUCHDOWN.EXTRA POINT is GOOD.", ("play", "RUN", "LEFT", 9)),
    ("D.Henry left end for 9 yards, TOUCHDOWN.J.Smith Extra Point is NO GOOD.", ("play", "RUN", "LEFT", 9)),
    # -- sacks and turnovers: the host decides --
    ("P.Mahomes sacked at KC 21 for -9 yards (C.Jones). FUMBLES (C.Jones), RECOVERED by KC-P.Mahomes at KC 20.", ("review", "PASS", None, -9)),
    ("J.Allen sacked at BUF 20 for a loss of 7 yards (M.Crosby).", ("review", "PASS", None, -7)),
    ("J.Allen was sacked for a safety at BUF 0. SAFETY.", ("review", "PASS", None, None)),
    ("J.Allen pass short left is incomplete. thrown away to avoid sack.", ("review", "PASS", None, None)),
    ("J.Love pass short left intended for A.Dillon INTERCEPTED by J.Gardner at GB 40. J.Gardner to GB 45 for 5 yards (A.Jones).",
     ("review", "PASS", "LEFT", 0)),
    ("J.Love pass incomplete deep right INTERCEPTED by K.Fuller.", ("review", "PASS", "RIGHT", 0)),
    ("J.Love pass short left to X.Doe is intercepted by Y.Roe at 40.", ("review", "PASS", "LEFT", 0)),
    ("B.Mayfield pass short middle to M.Evans to TB 30 for 3 yards. M.Evans FUMBLES (J.Ward), RECOVERED by TB-M.Evans at TB 31.",
     ("review", "PASS", "MIDDLE", 3)),
    ("S.Barkley right end to NYG 30 for 5 yards (T.Edwards). FUMBLES (T.Edwards), and recovers.", ("review", "RUN", "RIGHT", 5)),
    ("J.Hurts pass short left to D.Smith for 8 yards. SAFETY.", ("review", "PASS", "LEFT", 8)),
    # -- penalties: accepted or offsetting means the host decides --
    ("D.Prescott pass short left to C.Lamb to DAL 30 for 10 yards (S.Griffin). PENALTY on DAL-T.Smith, Unnecessary Roughness, "
     "15 yards, enforced at DAL 40.", ("review", "PASS", "LEFT", 10)),
    ("D.Prescott pass incomplete deep right to C.Lamb. PENALTY on NYG-K.Thibodeaux, Defensive Pass Interference, 32 yards, "
     "enforced at NYG 40.", ("review", "PASS", "RIGHT", 0)),
    ("D.Prescott pass short left to C.Lamb to DAL 30 for 10 yards (S.Griffin). Penalty on NYG-K.Thibodeaux, Defensive Offside, "
     "offsetting.", ("review", "PASS", "LEFT", 10)),
    ("A.Rodgers pass short right to A.Lazard to NYJ 42 for 6 yards. Penalty on NYJ, Ineligible Receiver, 5 yards, ACCEPTED.",
     ("review", "PASS", "RIGHT", 6)),
    ("PENALTY on DAL-T.Smith, False Start, 5 yards, enforced at DAL 20.", ("review", None, None, None)),
    ("R.Mostert up the middle to MIA 13 for 6 yards (D.Wade) 5-yard penalty", ("review", None, None, None)),
    # -- voids --
    ("D.Prescott pass short left to C.Lamb to DAL 30 for 10 yards. PENALTY on DAL-T.Smith, Offensive Holding, 10 yards, "
     "enforced at DAL 20 - No Play.", ("void", None, None, None)),
    ("PENALTY on DAL-T.Smith, Offensive Holding, 10 yards, enforced at DAL 20 - NO PLAY.", ("void", None, None, None)),
    ("R.Wilson pass short left to D.Metcalf for 22 yards, TOUCHDOWN. Touchdown nullified by penalty - No Play.", ("void", None, None, None)),
    # -- tricks, replays, conflicts, missing parts: the host decides --
    ("J.Jones left end to NE 30 for 2 yards. J.Jones pass deep right to K.Bourne for 40 yards (M.Hooker). Flea flicker.", ("review", None, None, None)),
    ("Reverse to W.Robinson. W.Robinson right end to NYG 33 for 8 yards (X.McKinney).", ("review", "RUN", "RIGHT", 8)),
    ("R.Cobb pass short right to J.Jones for 14 yards. Lateral to X for 3 more yards.", ("review", "PASS", "RIGHT", 14)),
    ("J.Hurts pitches to K.Gainwell. K.Gainwell right end to PHI 40 for 6 yards.", ("review", "RUN", "RIGHT", 6)),
    ("D.Carr pass deep left to D.Adams for 42 yards. Touchdown reversed - challenge.", ("review", "PASS", "LEFT", 42)),
    ("M.Ryan pass short right to K.Pitts to ATL 48 for 12 yards (D.Wade). Challenge by BAL: pass complete ruling UPHELD.",
     ("review", "PASS", "RIGHT", 12)),
    ("K.Cousins pass short right to J.Jefferson for 11 yards; Right end ran ob.", ("review", None, None, 11)),
    ("J.Allen pass short right to S.Diggs for 4 yards. J.Allen pass short left to G.Davis for 5 yards.", ("review", "PASS", None, None)),
    ("J.Allen left end to BUF 20 for 4 yards. J.Allen right end to BUF 20 for 4 yards.", ("review", "RUN", None, 4)),
    ("J.Allen pass incomplete short right to S.Diggs for 4 yards.", ("review", "PASS", "RIGHT", None)),
    ("J.Allen pass incomplete.", ("review", "PASS", None, 0)),
    ("J.Allen pass to S.Diggs.", ("review", "PASS", None, None)),
    ("J.Allen up the middle.", ("review", "RUN", "MIDDLE", None)),
    ("J.Allen pass short left to S.Diggs is complete.", ("review", "PASS", "LEFT", None)),
    ("J.Allen pass short left to S.Diggs, no gain.", ("review", "PASS", "LEFT", None)),
    ("J.Burrow scrambles to CIN 40 for 8 yards (R.Smith).", ("review", "RUN", None, 8)),
    ("J.Fields pass deflected at the line.", ("review", "PASS", None, None)),
    ("J.Allen pass short left to S.Diggs for 105 yards, TOUCHDOWN.", ("review", None, None, None)),   # not a legal distance
    ("J.Allen left end for 1000 yards", ("review", None, None, None)),
    # -- not scrimmage plays: skipped --
    ("J.Fields spikes the ball to stop the clock.", ("skip", None, None, None)),
    ("J.Fields kneels to CHI 20 for -1 yards.", ("skip", None, None, None)),
    ("TWO-POINT CONVERSION ATTEMPT. D.Prescott pass to C.Lamb is complete. ATTEMPT SUCCEEDS.", ("skip", None, None, None)),
    ("Two Point Attempt: J.Hurts rushes up the middle. ATTEMPT FAILS.", ("skip", None, None, None)),
    ("Extra Point is GOOD", ("skip", None, None, None)),
    ("J.Elliott 33 yard field goal is GOOD", ("skip", None, None, None)),
    ("M.Dixon kicks 65 yards from MIN 35 to end zone, Touchback.", ("skip", None, None, None)),
    ("T.Way punts 40 yards to CAR 11, Center-T.Ott, fair catch by D.Moore. PENALTY on CAR, Holding, 10 yards, enforced at CAR 20.", ("skip", None, None, None)),
    ("Timeout #1 by DAL at 03:12.", ("skip", None, None, None)),
    ("END QUARTER 1", ("skip", None, None, None)),
    ("Two-Minute Warning", ("skip", None, None, None)),
    ("INJURY TIMEOUT", ("skip", None, None, None)),
    ("The play was challenged by the replay official and upheld.", ("skip", None, None, None)),
]


@pytest.mark.parametrize("text, expected", INVENTED, ids=[t[:60] for t, _ in INVENTED])
def test_invented_strings(text, expected):
    kind, ptype, direction, yards = read(text)
    assert kind == expected[0]
    for got, want in zip((ptype, direction, yards), expected[1:]):
        if want is not None:
            assert got == want


def test_a_play_with_a_timeout_note_is_not_skipped():
    """Only an entry that BEGINS with Timeout is a timeout: a play that mentions one is still the play."""
    assert read("A.Dalton pass short right to J.Sanders for 7 yards (B.St-Juste). Timeout #1 by CAR at 03:12.")[0] == "play"


# Words that must never appear in a text the reader is willing to score without the host (outside a declined penalty).
RISKY = re.compile(r"sack|intercept|fumbl|no play|lateral|reverse|flea|challeng|overturn|upheld|safety|aborted|offsetting|"
                   r"\bpitch|wildcat|replay|nullif", re.I)
TOKENS = [
    "J.Allen", "pass", "short", "deep", "left", "middle", "right", "end", "tackle", "guard", "up the middle", "to", "for", "yards",
    "5", "12", "-3", "0", "1", "99", "a loss of 4 yards", "no gain", "incomplete", "(T.Edmunds).", ".", ",", "TOUCHDOWN",
    "Penalty on BUF-X, Holding, 10 yards, enforced at BUF 40", "declined", "PENALTY", "ACCEPTED", "No Play", "sacked", "INTERCEPTED",
    "FUMBLES", "scrambles", "Reverse", "lateral", "SAFETY", "challenged", "kneels", "spikes", "punts", "extra point is GOOD",
    "TWO-POINT", "Timeout", "offsetting", "A.St.", "Brown", "S.Diggs", "BUF 40", "intended for", "pushed ob", "ran ob",
]


SKELETONS = [
    "J.Allen pass short {d} to S.Diggs to BUF 40 for {n} yards (T.Edmunds).",
    "J.Allen pass incomplete deep {d} to S.Diggs.",
    "D.Singletary {d} end to BUF 30 for {n} yards (A.Hooker).",
    "D.Singletary up the middle to BUF 30 for {n} yards (A.Hooker).",
    "J.Allen scrambles {d} end ran ob at BUF 45 for {n} yards (A.Hooker).",
]


def test_fuzz_nothing_risky_is_ever_clean():
    """A clean play with random extra sentences glued on: whenever the reader still calls the result a clean 'play', the
    result is complete and the text holds nothing that needs a human (sacks, turnovers, accepted penalties, replays ...).
    Never raises."""
    rng = random.Random(20261008)
    clean = 0
    for _ in range(30000):
        text = rng.choice(SKELETONS).format(d=rng.choice(["left", "right"]), n=rng.choice([0, 1, 5, 7, 12, 40]))
        for _ in range(rng.randint(0, 3)):
            text += " " + " ".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 8)))
        p = classify(entry(text))
        if p.kind != "play":
            continue
        clean += 1
        assert p.play_type in ("RUN", "PASS") and p.direction in ("LEFT", "MIDDLE", "RIGHT"), text
        assert isinstance(p.yards, int) and p.yardage == yardage_for_yards(p.yards).value, text
        # Penalty sentences are only harmless when declined; everything else risky is a reason for a human.
        head = text
        if (td := re.search(r"TOUCHDOWN", text, re.I)) and re.search(r"extra point|kick is|kick attempt|two-point|2-point|2pt|"
                                                                     r"conversion|\bPAT\b", text[td.end():], re.I):
            head = text[: td.end()]   # what follows a touchdown plus its extra point / two-point try is not the play
        sentences = re.split(r"(?<=[A-Za-z0-9)\]])(?<!\bSt)\.\s+(?=[A-Z(\[])", head)
        rest = " ".join(s for s in sentences if not re.search(r"penalty", s, re.I))
        assert not RISKY.search(rest), text
        for s in sentences:
            for piece in re.split(r"(?i)(?=\bpenalty\b)", s)[1:]:
                assert re.search(r"declined", piece, re.I) and not re.search(r"offsetting|no play", piece, re.I), text
    assert clean > 1000   # plenty of clean plays survive the decoration, so the assertions above were really exercised


# --------------------------------------------------------------------------- #
# next down and distance
# --------------------------------------------------------------------------- #

HOME, AWAY, HID, AID = "WSH", "CAR", "32", "5"


def nd(dd, yards, team=AID, text=None, td=False):
    sentence = text or ("A.Dalton pass short right to X for %d yards (Y)." % yards)
    e = {"play": sentence + (", TOUCHDOWN" if td else ""), "downAndDistance": dd, "teamID": team}
    p = classify(e)
    return next_down_and_distance(e, p, HOME, AWAY, HID, AID)


@pytest.mark.parametrize("dd, yards, team, expected", [
    ("1st & 10 at CAR 25", 4, AID, (2, "6")),                 # own half
    ("2nd & 6 at CAR 29", 6, AID, (1, "10")),                 # first down by exactly the yards needed
    ("2nd & 6 at CAR 29", 5, AID, (3, "1")),
    ("3rd & 1 at CAR 49", 1, AID, (1, "10")),
    ("1st & 10 at 50", 3, AID, (2, "7")),                      # midfield, no team in the spot
    ("1st & 10 at 50", 40, AID, (1, "Goal")),                  # a long gain to inside the opponent's 10
    ("2nd & 7 at WSH 45", 7, AID, (1, "10")),                  # opponent's half, first down short of the 10
    ("1st & 10 at WSH 20", 12, AID, (1, "Goal")),              # first down inside the 10 is goal to go
    ("1st & 10 at WSH 20", 9, AID, (2, "1")),
    ("1st & 10 at WSH 20", 10, AID, (1, "Goal")),
    ("1st & Goal at WSH 8", 3, AID, (2, "Goal")),              # goal to go stays goal to go
    ("2nd & Goal at WSH 5", 0, AID, (3, "Goal")),
    ("2nd & Goal at WSH 5", -4, AID, (3, "Goal")),
    ("1st & 10 at WSH 11", 3, AID, (2, "7")),
    ("1st & 10 at WSH 10", 3, AID, (2, "Goal")),               # the line to gain is the goal line: Goal
    ("1st & 10 at CAR 25", -3, AID, (2, "13")),                # a loss
    ("3rd & 4 at CAR 30", 0, AID, (4, "4")),
    ("4th & 2 at WSH 30", 2, AID, (1, "10")),                  # converted on fourth down
    ("1st & 10 at WSH 25", 4, HID, (2, "6")),                  # WSH on offense, own half is WSH
    ("1st & 10 at CAR 40", 4, HID, (2, "6")),                  # WSH on offense, opponent's half is CAR
    ("1st & 10 at CAR 14", 4, HID, (2, "6")),
    ("1st & Goal at CAR 4", 1, HID, (2, "Goal")),
])
def test_next_down_cases(dd, yards, team, expected):
    got = nd(dd, yards, team)
    assert got == {"down": expected[0], "distance": expected[1]}


@pytest.mark.parametrize("dd, yards, team", [
    ("4th & 2 at CAR 30", 1, AID),            # would be a fifth down (turnover on downs)
    ("4th & 3 at WSH 20", 2, AID),
    ("1st & Goal at WSH 3", 3, AID),          # touchdown without the word (yards reach the goal line)
    ("1st & 10 at CAR 1", -2, AID),           # a safety
    ("1st & 10 at CAR 2", -2, AID),           # a safety
    ("1st & 10 at CAR 25", 4, "99"),          # a team id we do not know
    ("1st & 10 at XYZ 25", 4, AID),           # a spot team that is neither side
    ("1st & 10 at CAR 25", 4, ""),            # no team
    ("", 4, AID),
    ("1st & inches at CAR 25", 1, AID),
    ("garbage", 4, AID),
    ("1st & 10 at CAR 65", 4, AID),           # an impossible spot
    ("1st & 25 at WSH 20", 4, AID),           # to go farther than the goal line
])
def test_next_down_is_none_when_unsure(dd, yards, team):
    assert nd(dd, yards, team) is None


def test_next_down_none_for_a_touchdown_and_for_reviews():
    assert nd("1st & Goal at WSH 4", 4, AID, td=True) is None
    e = entry("A.Dalton sacked at CAR 12 for -4 yards (F.Luvu).", "2nd & 8 at CAR 16", teamID=AID)
    assert next_down_and_distance(e, classify(e), HOME, AWAY, HID, AID) is None


# A second round of invented strings (a different verifier, different phrasing): (text, (kind, type, direction, yards)).
INVENTED_2 = [
    ("J.Allen pass short left to S.Diggs to BUF 40 for 12 yards (T.Edmunds). FUMBLES (T.Edmunds), touched at BUF 38, RECOVERED by BUF-S.Diggs.",
     ("review", "PASS", "LEFT", 12)),
    ("A.Rodgers pass incomplete short right to A.Lazard. Batted at the line by T.Watt.", ("play", "PASS", "RIGHT", 0)),
    ("A.Rodgers pass deep left to G.Wilson, tipped by M.Peters, INTERCEPTED by M.Peters at NYJ 20. M.Peters to NYJ 30 for 10 yards.",
     ("review", "PASS", "LEFT", 0)),
    ("P.Mahomes pass short middle to T.Kelce to KC 45 for 6 yards (L.David). Penalty on TB-L.David, Defensive Holding, declined.",
     ("play", "PASS", "MIDDLE", 6)),
    ("P.Mahomes pass short middle to T.Kelce to KC 45 for 6 yards (L.David). Penalty on TB-L.David, Defensive Holding, 5 yards, "
     "enforced at KC 39, accepted, 1st down.", ("review", "PASS", "MIDDLE", 6)),
    ("I.Pacheco left tackle to KC 30 for 2 yards (V.Vea). Penalty on KC-C.Humphrey, Holding, 10 yards, enforced at KC 28.",
     ("review", "RUN", "LEFT", 2)),
    ("I.Pacheco left tackle to KC 30 for 2 yards (V.Vea). Penalty on KC-C.Humphrey, Holding, 10 yards, enforced at KC 28 - No Play.",
     ("void", None, None, None)),
    ("Jet sweep: R.Rice left end to KC 35 for 7 yards (V.Vea).", ("play", "RUN", "LEFT", 7)),
    ("P.Mahomes scrambles up the middle to KC 40 for 9 yards (V.Vea). Pass was thrown away earlier.", ("review", None, None, 9)),
    ("P.Mahomes pass to the sideline thrown away.", ("review", "PASS", None, None)),
    ("P.Mahomes sack: no direction charted. -8 yards.", ("review", "PASS", None, None)),
    ("P.Mahomes pass incomplete deep middle to M.Hardman. Intended for X, INTERCEPTION overturned by replay.",
     ("review", "PASS", "MIDDLE", 0)),
    ("Hail Mary: A.Rodgers pass deep middle to A.Lazard to END ZONE, incomplete.", ("play", "PASS", "MIDDLE", 0)),
    ("D.Henry up the middle to TEN 20 for 1 yard (J.Allen). TOUCHDOWN nullified by penalty, no play.", ("void", None, None, None)),
    ("D.Henry right end for 12 yards, TOUCHDOWN. The Replay Official reviewed the runner was down by contact. The ruling on the field "
     "was reversed.", ("review", "RUN", "RIGHT", 12)),
    ("D.Henry right end to TEN 1 for 12 yards (J.Allen). Titans challenged the spot, ruling upheld.", ("review", "RUN", "RIGHT", 12)),
    ("J.Fields pass short left to D.Moore for 8 yards, TOUCHDOWN. Two-Point Conversion Attempt: J.Fields pass to C.Kmet is complete. "
     "ATTEMPT SUCCEEDS.", ("play", "PASS", "LEFT", 8)),
    ("J.Fields right end for 1 yard, TOUCHDOWN. PAT: B.Santos kick is good.", ("play", "RUN", "RIGHT", 1)),
    ("J.Fields up the middle to GB 1 for 2 yards (Q.Walker). TOUCHDOWN. Challenge: down by contact.", ("review", "RUN", "MIDDLE", 2)),
    ("J.Fields pass short right to D.Moore to GB 20 for 15 yards (J.Alexander). Lateral to K.Herbert to GB 5 for 15 yards.",
     ("review", "PASS", "RIGHT", 15)),
    ("Fake punt: T.Gill pass short right to X.Doe for 11 yards.", ("play", "PASS", "RIGHT", 11)),
    ("J.Fields pass short right to D.Moore for 7 yards. Pass Interference on GB.", ("review", "PASS", "RIGHT", 7)),   # named outside "PENALTY on"
    ("J.Fields pass incomplete short right to D.Moore. Penalty on GB-J.Alexander, Defensive Pass Interference, 12 yards, "
     "enforced at CHI 20, accepted.", ("review", "PASS", "RIGHT", 0)),
    ("J.Fields pass incomplete short right to D.Moore. Penalty on GB-J.Alexander, Defensive Pass Interference, declined.",
     ("play", "PASS", "RIGHT", 0)),
    ("J.Fields pass incomplete short right to D.Moore. Penalty on GB-J.Alexander, Defensive Pass Interference, declined. "
     "Penalty on CHI-L.Wallace, Illegal Use of Hands, offsetting.", ("review", "PASS", "RIGHT", 0)),
    ("J.Fields pass short left to D.Moore to CHI 30 for 5 yards (J.Alexander). Penalty on CHI-L.Wallace, Illegal Formation, 5 yards, "
     "enforced at CHI 25 - No Play.", ("void", None, None, None)),
    ("(Run Pass Option) J.Fields up the middle to CHI 33 for 3 yards (J.Alexander).", ("play", "RUN", "MIDDLE", 3)),
    ("Z.Wilson right guard to NYJ 20 for 4 yards, 1st down. Z.Wilson fumbles snap exchange, recovered by Z.Wilson.",
     ("review", "RUN", "RIGHT", 4)),
    ("Z.Wilson scrambles, pass incomplete short left to G.Wilson.", ("review", None, None, None)),
]


@pytest.mark.parametrize("text, expected", INVENTED_2, ids=[t[:60] for t, _ in INVENTED_2])
def test_invented_strings_round_two(text, expected):
    kind, ptype, direction, yards = read(text)
    assert kind == expected[0]
    for got, want in zip((ptype, direction, yards), expected[1:]):
        if want is not None:
            assert got == want
