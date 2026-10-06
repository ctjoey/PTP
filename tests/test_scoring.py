import itertools

import pytest

from models import DIRECTION_POINTS, EXACT_POINTS, TYPE_POINTS, score_prediction


@pytest.mark.parametrize(
    "pred_type, pred_dir, actual_type, actual_dir, expected",
    [
        ("RUN", "LEFT", "RUN", "LEFT", 30),     # exact match
        ("PASS", "CENTER", "PASS", "CENTER", 30),
        ("RUN", "LEFT", "RUN", "RIGHT", 10),    # type only
        ("PASS", "RIGHT", "RUN", "RIGHT", 10),  # direction only
        ("PASS", "LEFT", "RUN", "RIGHT", 0),    # nothing
    ],
)
def test_score_prediction(pred_type, pred_dir, actual_type, actual_dir, expected):
    assert score_prediction(pred_type, pred_dir, actual_type, actual_dir).points == expected


def test_point_values_match_spec():
    assert (TYPE_POINTS, DIRECTION_POINTS, EXACT_POINTS) == (10, 10, 30)


def test_every_combination_is_consistent():
    types, dirs = ("RUN", "PASS"), ("LEFT", "CENTER", "RIGHT")
    for pt, pd, at, ad in itertools.product(types, dirs, types, dirs):
        result = score_prediction(pt, pd, at, ad)
        assert result.type_correct == (pt == at)
        assert result.direction_correct == (pd == ad)
        assert result.exact == (pt == at and pd == ad)
        expected = 30 if result.exact else 10 if (result.type_correct or result.direction_correct) else 0
        assert result.points == expected


def test_rejects_unknown_values():
    with pytest.raises(ValueError):
        score_prediction("PUNT", "LEFT", "RUN", "LEFT")
