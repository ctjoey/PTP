"""Reading Tank01 play-by-play text: is this entry a play, and what happened?

Pure functions, no I/O. ``classify`` turns one feed entry into a ``Parsed`` result the live-data feature
uses to suggest a score:

* ``skip``    not a scrimmage play (kickoff, punt, field goal, extra point, kneel, timeout, quarter marker, ...)
* ``void``    a scrimmage entry nullified by a penalty ("No Play")
* ``play``    a run or pass whose type, direction and yards are all unambiguous: safe to score automatically
* ``review``  a scrimmage play the host must look at (sack, interception, fumble, accepted penalty, no charted
              direction, anything unusual). It carries a best-effort prefill and plain-English ``flags``.

The host's rules: direction is the offense's left/right as the QB looks downfield (runs by run location or gap,
passes by pass location); distance is total yards gained (incomplete or no gain is 0 = SHORT, negative = LOSS).

Safe defaults beat cleverness: when the text is unclear the answer is ``review``, and ``classify`` never raises.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from models import yardage_for_yards

SKIP, VOID, PLAY, REVIEW = "skip", "void", "play", "review"


@dataclass
class Parsed:
    """The result of reading one feed entry."""

    kind: str = SKIP                       # skip | void | play | review
    play_type: str | None = None           # RUN | PASS
    direction: str | None = None           # LEFT | MIDDLE | RIGHT
    yards: int | None = None
    yardage: str | None = None             # SHORT | MEDIUM | LONG | LOSS (derived from yards)
    flags: list[str] = field(default_factory=list)  # plain-English reasons shown to the host
    reason: str = ""                       # short machine code: "clean", "kickoff", "sack", ...
    text: str = ""
    clock: str = ""                        # "Q2 4:03"
    down_and_distance: str | None = None   # as the feed wrote it: "2nd & 3 at CAR 21"
    touchdown: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

_I = re.IGNORECASE
_RUN_LOCATION = re.compile(r"\b(left|right)\s+(?:end|tackle|guard)\b|\bup the (middle)\b", _I)
_RUN_WORDS = re.compile(r"\b(?:scrambles|scrambled|rushes|runs|sneaks?)\b", _I)
_PASS_WORD = re.compile(r"\bpass(?:es)?\b", _I)
_PASS_LOCATION = re.compile(r"\bpass(?:es)?\s+(?:incomplete\s+)?(?:(?:short|deep)\s+)?(left|middle|right)\b", _I)
_INCOMPLETE = re.compile(r"\bpass(?:es)?\s+incomplete\b|\bincomplete\b", _I)
_YARDS = re.compile(
    r"\bfor\s+(?:(?:a\s+)?loss\s+of\s+(\d+)\s+(?:yards?|yds?)|(-?\d+)\s+(?:yards?|yds?)\b|(no\s+gain))", _I
)
_SACK = re.compile(r"\bsacked\b|\bsack\b", _I)
_INTERCEPTION = re.compile(r"\bintercept(?:ed|ion|s)?\b", _I)
_FUMBLE = re.compile(r"\bfumbl(?:es|ed|e)\b", _I)
_ABORTED = re.compile(r"\baborted\b", _I)
_TRICK = re.compile(r"\blateral(?:ed|s)?\b|\breverse\b|\bflea[- ]?flicker\b|\btrick\b|\bdouble pass\b|\bhalfback pass\b"
                    r"|\bstatue of liberty\b|\bwildcat\b|\bpitch(?:es|ed)?\b|\boption\b", _I)
_SAFETY = re.compile(r"\bsafety\b", _I)
_REPLAY = re.compile(r"\bchalleng\w*|\breplay\b|\bupheld\b|\breversed\b|\boverturned\b|\bruling\b", _I)
_MARKER_WORDS = re.compile(r"\btime ?out\b|\binjur\w*|\bchalleng\w*|\breplay\b|\bhalftime\b|\bintermission\b|\bofficial\b"
                           r"|\bdelay\b|\bwarning\b|\bcoin\b", _I)
_TOUCHDOWN = re.compile(r"\btouchdown\b", _I)
_NO_PLAY = re.compile(r"\bno play\b", _I)
_PENALTY = re.compile(r"\bpenalt(?:y|ies)\b", _I)
_PENALTY_START = re.compile(r"(?=\bpenalt(?:y|ies)\b)", _I)   # splits a sentence in front of each penalty it names
_PENALTY_DECLINED = re.compile(r"\bdeclined\b", _I)
_PENALTY_OFFSETTING = re.compile(r"\boffsetting\b|\boffset\b", _I)

# Things that are never scrimmage plays. Checked on the whole text, in order.
_SKIP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("timeout", re.compile(r"^\W*timeout\b", _I)),
    ("warning", re.compile(r"\btwo[- ]minute warning\b|\b2[- ]minute warning\b", _I)),
    ("end_period", re.compile(r"^\W*end (?:of )?(?:the )?(?:quarter|game|half|period|regulation)\b|\bend of (?:the )?(?:half|quarter|game)\b", _I)),
    ("coin_toss", re.compile(r"\bcoin toss\b|\bwon the toss\b|\bwins the toss\b", _I)),
    ("kickoff", re.compile(r"\bkicks\b|\bkick ?off\b|\bonside\b|\bfree kick\b", _I)),
    ("punt", re.compile(r"\bpunts\b|\bpunted\b|\bpunt (?:blocked|return)", _I)),
    ("field_goal", re.compile(r"\bfield goal\b|\bfg attempt\b", _I)),
    ("two_point", re.compile(r"\btwo[- ]point\b|\b2[- ]point\b|\b2pt\b", _I)),
    ("kneel", re.compile(r"\bkneel(?:s|ed)?\b", _I)),
    # Not after a "." or "-": a player can be called Spikes ("B.Spikes"), and a real play must never be skipped.
    ("spike", re.compile(r"(?<![.\w-])spikes?\b|(?<![.\w-])spiked\b", _I)),
)
# An extra point written on its own (or after a touchdown, handled by cutting the tail first).
_EXTRA_POINT = re.compile(r"(?i:\bextra point\b|\bkick is (?:good|no good)\b|\bkick attempt\b)|\bPAT\b")
# What can follow "TOUCHDOWN" in the same entry: the extra point, or a two-point try.
_AFTER_TOUCHDOWN = re.compile(r"(?i:\bextra point\b|\bkick is\b|\bkick attempt\b|\btwo[- ]point\b|\b2[- ]point\b|\b2pt\b"
                              r"|\bconversion\b)|\bPAT\b")
_D_AND_D = re.compile(r"^\s*(\d)(?:st|nd|rd|th)\s*&\s*(goal|inches|\d+)\b(?:\s+at\s+(?:([A-Za-z]{2,4})\s+)?(\d{1,2}))?", _I)
_SENTENCE_BREAK = re.compile(r"(?<=[A-Za-z0-9\)\]])(?<!\bSt)(?<!\bJr)(?<!\bSr)\.\s+(?=[A-Z(\[])")  # not "A.St. Brown"
_LEADING_PAREN = re.compile(r"^\s*(?:\([^)]*\)\s*)+")


# --------------------------------------------------------------------------- #
# Down and distance
# --------------------------------------------------------------------------- #


def parse_down_and_distance(value: Any) -> dict[str, Any] | None:
    """``"2nd & 3 at CAR 21"`` -> ``{"down": 2, "to_go": "3", "spot_team": "CAR", "spot": 21}``.

    ``to_go`` is a string ("3", "goal", "inches"); ``spot_team`` is None for the 50 and when the feed leaves
    the spot out. Returns None when the text is not a down and distance.
    """
    if not isinstance(value, str):
        return None
    m = _D_AND_D.match(value)
    if not m:
        return None
    down = int(m.group(1))
    if not 1 <= down <= 4:
        return None
    team = m.group(3).upper() if m.group(3) else None
    spot = int(m.group(4)) if m.group(4) else None
    return {"down": down, "to_go": m.group(2).lower(), "spot_team": team, "spot": spot}


def same_down_and_distance(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool | None:
    """True/False when two parsed down and distances agree on down and yards to go (the spot is ignored);
    None when either is missing so there is nothing to compare."""
    if not a or not b:
        return None
    return a["down"] == b["down"] and a["to_go"] == b["to_go"]


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #


def classify(entry: Any) -> Parsed:
    """Classify one feed entry. Never raises: anything odd that still looks like a play is ``review``."""
    try:
        return _classify(entry)
    except Exception:  # noqa: BLE001 - the contract is "never raise"
        text = entry.get("play") if isinstance(entry, dict) else entry
        return Parsed(kind=REVIEW, flags=["Could not read this feed entry"], reason="parse_error",
                      text=_squash(text)[:300])


def _squash(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _classify(entry: Any) -> Parsed:
    raw = entry if isinstance(entry, dict) else {"play": entry}
    text = _squash(raw.get("play"))
    period, clock = _squash(raw.get("playPeriod")), _squash(raw.get("playClock"))
    dd_raw = raw.get("downAndDistance")
    dd_text = _squash(dd_raw) or None
    out = Parsed(text=text, clock=f"{period} {clock}".strip(), down_and_distance=dd_text)
    if not text:
        out.reason = "empty"
        return out

    low = text.lower()
    # A touchdown play carries its extra point (or a two-point try) in the same entry ("... for 8 yards,
    # TOUCHDOWN.A.Seibert extra point is GOOD"): read only the scrimmage play in front of that tail, so the
    # tail can never make the touchdown itself look like a skipped entry.
    core = text
    if (td := _TOUCHDOWN.search(text)) and _AFTER_TOUCHDOWN.search(text, td.end()):
        core = text[: td.end()]
    for reason, pattern in _SKIP_PATTERNS:
        if pattern.search(core):
            out.reason = reason
            return out

    if cut := _EXTRA_POINT.search(core):
        # An extra point with no touchdown in front of it: on its own it is a skip; glued to a run or pass it is odd.
        if not (_PASS_WORD.search(core[: cut.start()]) or _RUN_LOCATION.search(core[: cut.start()])
                or _RUN_WORDS.search(core[: cut.start()]) or _SACK.search(core[: cut.start()])):
            out.reason = "extra_point"
            return out
        return _review(out, ["An extra point is mentioned without a touchdown: check this play"], "extra_point_odd")

    has_dd = parse_down_and_distance(dd_text) is not None
    if _NO_PLAY.search(low):
        if not has_dd:  # a penalty on a kickoff or other special-teams snap: not one of the host's plays
            out.reason = "no_play_special_teams"
            return out
        out.kind, out.reason = VOID, "no_play"
        out.flags = [_penalty_flag(core) or "Penalty: no play"]
        return out

    return _scrimmage(out, core, has_dd)


def _penalty_flag(text: str) -> str | None:
    m = re.search(r"penalty on\s+[A-Za-z]{2,4}(?:-[^,]+)?,\s*([^,.]+)", text, _I)
    return f"Penalty: {m.group(1).strip()}, no play" if m else None


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_BREAK.split(text) if s.strip()]


def _scrimmage(out: Parsed, core: str, has_dd: bool) -> Parsed:
    """Read a (presumed) run or pass. ``core`` has any extra-point tail removed."""
    flags: list[str] = []
    accepted_penalty = offsetting = False
    kept: list[str] = []
    offsetting = bool(_PENALTY_OFFSETTING.search(core))   # the down is normally replayed: the play does not count
    for sentence in _sentences(core):
        if _PENALTY.search(sentence):
            # Each penalty named in the sentence must itself be declined (or offsetting, flagged separately).
            pieces = _PENALTY_START.split(sentence)[1:]
            if any(not (_PENALTY_DECLINED.search(x) or _PENALTY_OFFSETTING.search(x)) for x in pieces):
                accepted_penalty = True
            continue  # a declined penalty does not change the play; an accepted one is flagged below
        kept.append(sentence)
    body = _LEADING_PAREN.sub("", " ".join(kept))
    out.touchdown = bool(_TOUCHDOWN.search(core))

    sack = bool(_SACK.search(body))
    interception = bool(_INTERCEPTION.search(body))
    fumble = bool(_FUMBLE.search(body))
    aborted = bool(_ABORTED.search(body))

    pass_dirs = {m.group(1).upper() for m in _PASS_LOCATION.finditer(body)}
    run_dirs = {("MIDDLE" if m.group(2) else m.group(1).upper()) for m in _RUN_LOCATION.finditer(body)}
    passes = bool(_PASS_WORD.search(body)) or sack
    runs = bool(run_dirs) or bool(_RUN_WORDS.search(body))

    if not (passes or runs or sack or aborted):
        if accepted_penalty or offsetting:
            return _review(out, ["A penalty with no play described: probably no play, check it"], "penalty_only")
        if not has_dd or _MARKER_WORDS.search(body):
            out.reason = "non_play"          # an unrecognised marker (official timeout, injury, ...)
            return out
        return _review(out, flags=["Could not tell what kind of play this was"], reason="unrecognised")

    play_type: str | None
    reason = ""
    if passes and runs and not sack:
        play_type = None
        flags.append("The text mentions both a run and a pass")
        reason = "conflicting"
    else:
        play_type = "PASS" if passes else "RUN" if runs else None

    # Direction.
    directions = pass_dirs if play_type == "PASS" else run_dirs if play_type == "RUN" else set()
    direction = next(iter(directions)) if len(directions) == 1 else None
    if len(directions) > 1:
        flags.append("The text names more than one direction")
        reason = reason or "conflicting"

    # Yards.
    numbers = [-int(m.group(1)) if m.group(1) else 0 if m.group(3) else int(m.group(2)) for m in _YARDS.finditer(body)]
    incomplete = play_type == "PASS" and bool(_INCOMPLETE.search(body)) and not _INTERCEPTION.search(body)
    yards: int | None
    if incomplete:
        yards = 0
        if any(n != 0 for n in numbers):
            yards = None
            flags.append("Incomplete pass, but the text also gives yards")
            reason = reason or "conflicting_numbers"
    elif len(set(numbers)) == 1:
        yards = numbers[0]
    else:
        yards = None
        if len(set(numbers)) > 1:
            flags.append("The text gives different yardages")
            reason = reason or "conflicting_numbers"

    # Things that always need the host.
    if sack:
        play_type = "PASS"
        direction = None
        flags[:0] = ["Sack", "No direction is charted for a sack: pick Left, Middle or Right"]
        reason = "sack"
    if interception:
        play_type = "PASS"
        yards = 0  # any yardage in the text is the return, not the play
        flags.append("Interception")
        reason = "interception"
    if aborted:
        flags.append("Aborted snap")
        reason = "aborted_snap"
        direction, yards = None, None
    if fumble:
        flags.append("Fumble")
        reason = reason or "fumble"
    if _TRICK.search(body):
        flags.append("Trick play (lateral, reverse or option)")
        reason = reason or "trick_play"
    if _SAFETY.search(body):
        flags.append("Safety")
        reason = reason or "safety"
    if _REPLAY.search(core):
        flags.append("Replay challenge: the call may have changed")
        reason = reason or "replay"
    if accepted_penalty:
        flags.append("Penalty accepted: the yardage may be different")
        reason = reason or "penalty_accepted"
    if offsetting:
        flags.append("Offsetting penalties: the down is probably replayed (no play)")
        reason = reason or "penalty_offsetting"
    if play_type is None and not flags:
        flags.append("Could not tell if this was a run or a pass")
        reason = "no_type"
    if play_type is not None and direction is None and not sack and not aborted:
        flags.append("No direction in the feed text: pick Left, Middle or Right")
        reason = reason or "no_direction"
    if play_type is not None and yards is None and not aborted and not flags_mention_yards(flags):
        flags.append("No yards in the feed text")
        reason = reason or "no_yards"

    out.play_type, out.direction, out.yards = play_type, direction, yards
    if yards is not None:
        out.yardage = yardage_for_yards(yards).value
    if flags:
        out.kind, out.flags, out.reason = REVIEW, flags, reason or "review"
    else:
        out.kind, out.reason = PLAY, "clean"
    return out


def flags_mention_yards(flags: list[str]) -> bool:
    return any("yard" in f.lower() for f in flags)


def _review(out: Parsed, flags: list[str], reason: str) -> Parsed:
    out.kind, out.flags, out.reason = REVIEW, flags, reason
    return out


# --------------------------------------------------------------------------- #
# What comes next
# --------------------------------------------------------------------------- #


def next_down_and_distance(
    entry: Any, parsed: Parsed, home_abbr: str | None, away_abbr: str | None,
    home_id: Any, away_id: Any,
) -> dict[str, Any] | None:
    """The down and distance of the NEXT play, worked out from this one: ``{"down": 2, "distance": "3"}``.

    From the entry's own situation ("2nd & 3 at CAR 21"), the clean yards gained and which team has the ball
    (``entry["teamID"]`` against the two team ids, to know whether the spot is on the offense's own side).
    A first down gives "1st & 10", or "1st & Goal" inside the opponent's 10; otherwise the down goes up and the
    yards to go go down ("Goal" stays "Goal"). Returns None for anything uncertain: not a clean play, a
    touchdown, a would-be 5th down, an unreadable situation.
    """
    try:
        return _next_down(entry, parsed, home_abbr, away_abbr, home_id, away_id)
    except Exception:  # noqa: BLE001
        return None


def _next_down(entry: Any, parsed: Parsed, home_abbr: Any, away_abbr: Any, home_id: Any, away_id: Any):
    if parsed.kind != PLAY or parsed.yards is None or parsed.touchdown or not isinstance(entry, dict):
        return None
    situation = parse_down_and_distance(entry.get("downAndDistance"))
    if not situation or situation["to_go"] == "inches":
        return None
    team_id = str(entry.get("teamID", "")).strip()
    if not team_id:
        return None
    if team_id == str(home_id):
        offense, defense = home_abbr, away_abbr
    elif team_id == str(away_id):
        offense, defense = away_abbr, home_abbr
    else:
        return None
    if not offense or not defense:
        return None
    offense, defense = str(offense).upper(), str(defense).upper()

    spot, spot_team = situation["spot"], situation["spot_team"]
    if spot is None or not 1 <= spot <= 50:
        return None
    if spot_team is None:
        if spot != 50:
            return None
        to_goal = 50
    elif spot_team == offense:
        to_goal = 100 - spot
    elif spot_team == defense:
        to_goal = spot
    else:
        return None

    goal_to_go = situation["to_go"] == "goal"
    to_go = to_goal if goal_to_go else int(situation["to_go"])
    if to_go > to_goal or to_go < 1:
        return None
    gain = parsed.yards
    new_to_goal = to_goal - gain
    if new_to_goal <= 0 or new_to_goal > 99:  # touchdown or a safety
        return None
    if gain >= to_go:  # first down
        return {"down": 1, "distance": "Goal" if new_to_goal <= 10 else "10"}
    down = situation["down"] + 1
    if down > 4:
        return None
    new_to_go = to_go - gain
    if goal_to_go or new_to_go >= new_to_goal:
        return {"down": down, "distance": "Goal"}
    return {"down": down, "distance": str(new_to_go)}
