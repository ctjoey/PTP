import itertools

import pytest

from models import (
    DIRECTION_POINTS,
    EXACT_POINTS,
    TYPE_POINTS,
    YARDAGE_POINTS,
    GameError,
    resolve_yardage,
    score_prediction,
    yardage_for_yards,
)

TYPES = ("RUN", "PASS")
DIRS = ("LEFT", "CENTER", "RIGHT")
PICK_YARDAGE = ("SHORT", "MEDIUM", "LONG")
ACTUAL_YARDAGE = ("SHORT", "MEDIUM", "LONG", "LOSS")


@pytest.mark.parametrize(
    "pick, actual, expected",
    [
        (("RUN", "LEFT", "SHORT"), ("RUN", "LEFT", "SHORT"), 30),      # perfect call
        (("PASS", "CENTER", "LONG"), ("PASS", "CENTER", "LONG"), 30),
        (("RUN", "LEFT", "SHORT"), ("RUN", "LEFT", "MEDIUM"), 20),     # the owner's example
        (("RUN", "LEFT", "MEDIUM"), ("PASS", "LEFT", "MEDIUM"), 20),   # direction + distance
        (("PASS", "LEFT", "LONG"), ("PASS", "RIGHT", "LONG"), 20),     # type + distance
        (("RUN", "LEFT", "LONG"), ("RUN", "RIGHT", "SHORT"), 10),      # type only
        (("PASS", "RIGHT", "SHORT"), ("RUN", "RIGHT", "LONG"), 10),    # direction only
        (("PASS", "LEFT", "MEDIUM"), ("RUN", "RIGHT", "MEDIUM"), 10),  # distance only
        (("PASS", "LEFT", "LONG"), ("RUN", "RIGHT", "SHORT"), 0),      # nothing
        (("PASS", "RIGHT", "SHORT"), ("PASS", "RIGHT", "LOSS"), 20),   # a sack: no distance points
        (("RUN", "CENTER", "MEDIUM"), ("PASS", "LEFT", "LOSS"), 0),
    ],
)
def test_score_prediction(pick, actual, expected):
    assert score_prediction(*pick, *actual).points == expected


def test_point_values_match_spec():
    assert (TYPE_POINTS, DIRECTION_POINTS, YARDAGE_POINTS, EXACT_POINTS) == (10, 10, 10, 30)
    assert EXACT_POINTS == TYPE_POINTS + DIRECTION_POINTS + YARDAGE_POINTS  # no extra bonus


def test_every_combination_is_consistent():
    """All 18 picks against all 24 outcomes: 10 points per correct part, LOSS never matches."""
    seen = set()
    for pt, pd, py, at, ad, ay in itertools.product(TYPES, DIRS, PICK_YARDAGE, TYPES, DIRS, ACTUAL_YARDAGE):
        result = score_prediction(pt, pd, py, at, ad, ay)
        assert result.type_correct == (pt == at)
        assert result.direction_correct == (pd == ad)
        assert result.yardage_correct == (py == ay)
        assert not (ay == "LOSS" and result.yardage_correct)
        assert result.exact == (pt == at and pd == ad and py == ay)
        assert result.points == 10 * (pt == at) + 10 * (pd == ad) + 10 * (py == ay)
        assert (result.points == EXACT_POINTS) == result.exact
        seen.add(result.points)
    assert seen == {0, 10, 20, 30}


def test_missing_distance_scores_as_wrong():
    # A pick made before distance picks existed (NULL yardage) ...
    legacy = score_prediction("PASS", "LEFT", None, "PASS", "LEFT", "MEDIUM")
    assert (legacy.points, legacy.yardage_correct, legacy.exact) == (20, False, False)
    # ... and a play resolved before then (NULL correct_yardage).
    old_play = score_prediction("PASS", "LEFT", "MEDIUM", "PASS", "LEFT", None)
    assert (old_play.points, old_play.yardage_correct) == (20, False)


def test_rejects_unknown_values():
    with pytest.raises(ValueError):
        score_prediction("PUNT", "LEFT", "SHORT", "RUN", "LEFT", "SHORT")
    with pytest.raises(ValueError):
        score_prediction("RUN", "LEFT", "LOSS", "RUN", "LEFT", "LOSS")  # LOSS is not a pick
    with pytest.raises(ValueError):
        score_prediction("RUN", "LEFT", "SHORT", "RUN", "LEFT", "HUGE")


@pytest.mark.parametrize(
    "yards, bucket",
    [(-99, "LOSS"), (-1, "LOSS"), (0, "SHORT"), (5, "SHORT"), (6, "MEDIUM"), (10, "MEDIUM"),
     (11, "LONG"), (99, "LONG")],
)
def test_yards_to_bucket_boundaries(yards, bucket):
    assert yardage_for_yards(yards) == bucket


@pytest.mark.parametrize("yards", [-100, 100, 7.5, "7", True, None])
def test_yards_out_of_range_or_not_whole(yards):
    with pytest.raises(GameError) as exc:
        yardage_for_yards(yards)
    assert exc.value.status_code == 422


def test_resolve_yardage_rules():
    assert resolve_yardage("MEDIUM", None) == ("MEDIUM", None)
    assert resolve_yardage(None, 7) == ("MEDIUM", 7)
    assert resolve_yardage("MEDIUM", 7) == ("MEDIUM", 7)
    assert resolve_yardage("LOSS", -4) == ("LOSS", -4)
    assert resolve_yardage(None, 0) == ("SHORT", 0)  # incomplete pass / no gain
    with pytest.raises(GameError, match="7 yards is MEDIUM, not SHORT"):
        resolve_yardage("SHORT", 7)
    with pytest.raises(GameError, match="or enter the yards"):
        resolve_yardage(None, None)
    with pytest.raises(GameError, match="SHORT, MEDIUM, LONG or LOSS"):
        resolve_yardage("FAR", None)
