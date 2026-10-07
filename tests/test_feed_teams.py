"""Tank01 team abbreviations and the names two teams from one city get."""

import itertools

import pytest

from models import Store, validate_team_name
from teams import TEAM_PRESETS, feed_matchup, feed_team, preset_for_abbr

TANK01_ABBREVIATIONS = ("ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LAC LAR LV MIA MIN NE NO NYG NYJ PHI PIT "
                        "SF SEA TB TEN WSH").split()


def test_every_team_has_exactly_one_tank01_abbreviation():
    assert sorted(t["feed_abbr"] for t in TEAM_PRESETS) == sorted(TANK01_ABBREVIATIONS)
    assert len(TANK01_ABBREVIATIONS) == 32


@pytest.mark.parametrize("abbr, label", [
    ("LAC", "Los Angeles (powder blue & gold)"), ("LAR", "Los Angeles (royal blue & gold)"),
    ("NYG", "New York (blue & red)"), ("NYJ", "New York (green & white)"),
    ("WSH", "Washington"), ("CAR", "Carolina"), ("TB", "Tampa Bay"), ("DAL", "Dallas"),
])
def test_abbreviations_map_to_their_preset(abbr, label):
    assert preset_for_abbr(abbr)["label"] == label
    assert preset_for_abbr(abbr.lower())["label"] == label


def test_alternate_spellings_and_unknown_abbreviations():
    assert preset_for_abbr("WAS")["name"] == "Washington" and preset_for_abbr("JAC")["name"] == "Jacksonville"
    assert preset_for_abbr("XYZ") is None and preset_for_abbr("") is None and preset_for_abbr(None) is None
    unknown = feed_team("XYZ")
    assert unknown["name"] == "XYZ" and unknown["primary"] != unknown["secondary"]
    assert validate_team_name(unknown["name"]) == "XYZ"
    assert feed_team(None)["name"] == "TBD"


def test_two_teams_from_one_city_get_names_that_pass_the_rules():
    for away, home in (("NYJ", "NYG"), ("NYG", "NYJ"), ("LAC", "LAR"), ("LAR", "LAC")):
        a, h = feed_matchup(away, home)
        assert a["name"] != h["name"]
        assert validate_team_name(a["name"]) == a["name"] and validate_team_name(h["name"]) == h["name"]
    assert {feed_matchup("NYJ", "NYG")[0]["name"], feed_matchup("NYJ", "NYG")[1]["name"]} == {"New York Green", "New York Blue"}
    assert {feed_matchup("LAC", "LAR")[0]["name"], feed_matchup("LAC", "LAR")[1]["name"]} == {"Los Angeles Powder", "Los Angeles Blue"}
    # different cities keep the plain city names; colours are always the preset's
    a, h = feed_matchup("TB", "DAL")
    assert (a["name"], h["name"]) == ("Tampa Bay", "Dallas") and a["primary"] == "#D50A0A"
    a, h = feed_matchup("XYZ", "XYZ")                      # two unknowns can never collide
    assert a["name"] != h["name"] and validate_team_name(a["name"]) and validate_team_name(h["name"])


def test_every_possible_matchup_creates_a_game(tmp_path):
    """Any two clubs the feed can pair up produce a game the app accepts (names, colours, protected marks)."""
    store = Store(str(tmp_path / "t.db"))
    for away_abbr, home_abbr in itertools.permutations(TANK01_ABBREVIATIONS, 2):
        away, home = feed_matchup(away_abbr, home_abbr)
        game = store.create_game(home["name"], home["primary"], home["secondary"], away["name"], away["primary"],
                                 away["secondary"], feed_game_id="demo")
        assert game["home_name"] == home["name"] and game["away_name"] == away["name"]
        if len({away["name"], home["name"]}) == 1:
            raise AssertionError((away_abbr, home_abbr))
        store.set_game_status("FINAL")
    store.close()
