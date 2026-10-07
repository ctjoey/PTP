"""Team presets for the admin "Create Game" form: city names with real team colors.

Each preset is ``{"name", "label", "primary", "secondary"}``. ``name`` is what goes into the game
(a city or region only: no nicknames, logos or league marks, which ``models.validate_team_name``
rejects anyway). ``label`` is what the admin dropdown shows; it adds a color hint for the two
cities that have two clubs. Both of those presets fill the plain city name, so a game between them
needs a word added to one side (e.g. "New York Blue" / "New York Green").

Abbreviations on the scorebug are derived from the name (CHI, DET, GB, KC, NY, LA, ...).
"""

from __future__ import annotations

from typing import Any

# name | label | primary | secondary | Tank01 abbreviation
_PRESETS = """
Arizona       | Arizona                          | #97233F | #FFB612 | ARI
Atlanta       | Atlanta                          | #A71930 | #A5ACAF | ATL
Baltimore     | Baltimore                        | #241773 | #9E7C0C | BAL
Buffalo       | Buffalo                          | #00338D | #C60C30 | BUF
Carolina      | Carolina                         | #0085CA | #101820 | CAR
Chicago       | Chicago                          | #0B162A | #C83803 | CHI
Cincinnati    | Cincinnati                       | #FB4F14 | #000000 | CIN
Cleveland     | Cleveland                        | #311D00 | #FF3C00 | CLE
Dallas        | Dallas                           | #003594 | #869397 | DAL
Denver        | Denver                           | #FB4F14 | #002244 | DEN
Detroit       | Detroit                          | #0076B6 | #B0B7BC | DET
Green Bay     | Green Bay                        | #203731 | #FFB612 | GB
Houston       | Houston                          | #03202F | #A71930 | HOU
Indianapolis  | Indianapolis                     | #002C5F | #A2AAAD | IND
Jacksonville  | Jacksonville                     | #006778 | #D7A22A | JAX
Kansas City   | Kansas City                      | #E31837 | #FFB81C | KC
Las Vegas     | Las Vegas                        | #A5ACAF | #000000 | LV
Los Angeles   | Los Angeles (royal blue & gold)  | #003594 | #FFA300 | LAR
Los Angeles   | Los Angeles (powder blue & gold) | #0080C6 | #FFC20E | LAC
Miami         | Miami                            | #008E97 | #FC4C02 | MIA
Minnesota     | Minnesota                        | #4F2683 | #FFC62F | MIN
New England   | New England                      | #002244 | #C60C30 | NE
New Orleans   | New Orleans                      | #D3BC8D | #101820 | NO
New York      | New York (blue & red)            | #0B2265 | #A71930 | NYG
New York      | New York (green & white)         | #125740 | #FFFFFF | NYJ
Philadelphia  | Philadelphia                     | #004C54 | #A5ACAF | PHI
Pittsburgh    | Pittsburgh                       | #FFB612 | #101820 | PIT
San Francisco | San Francisco                    | #AA0000 | #B3995D | SF
Seattle       | Seattle                          | #002244 | #69BE28 | SEA
Tampa Bay     | Tampa Bay                        | #D50A0A | #FF7900 | TB
Tennessee     | Tennessee                        | #0C2340 | #4B92DB | TEN
Washington    | Washington                       | #5A1414 | #FFB612 | WSH
"""

TEAM_PRESETS: list[dict[str, Any]] = [
    dict(zip(("name", "label", "primary", "secondary", "feed_abbr"), (cell.strip() for cell in line.split("|"))))
    for line in _PRESETS.strip().splitlines()
]

# When both teams of a game share a city, the game needs two different names (the app rejects equal ones).
_SAME_CITY_NAMES = {"NYG": "New York Blue", "NYJ": "New York Green",
                    "LAR": "Los Angeles Blue", "LAC": "Los Angeles Powder"}

# The admin form's default matchup (away at home).
DEFAULT_AWAY = "Chicago"
DEFAULT_HOME = "Detroit"


# Other spellings the NFL has used for the same club; the feed itself uses the keys of ``feed_abbr``.
_ABBR_ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def preset_for_abbr(abbr: str | None) -> dict[str, Any] | None:
    """The preset for a Tank01 team abbreviation ("WSH", "NYG", ...), or None for an unknown one."""
    wanted = (abbr or "").strip().upper()
    wanted = _ABBR_ALIASES.get(wanted, wanted)
    for team in TEAM_PRESETS:
        if team["feed_abbr"] == wanted:
            return dict(team)
    return None


def feed_team(abbr: str | None) -> dict[str, Any]:
    """A team for the "pick today's game" list: ``{"abbr", "name", "label", "primary", "secondary"}``.

    Known abbreviations map to their preset; an unknown one gets the abbreviation as its name and neutral colours,
    so a new or odd abbreviation never breaks the list.
    """
    abbr = (abbr or "").strip().upper() or "TBD"
    team = preset_for_abbr(abbr)
    if team is None:
        return {"abbr": abbr, "name": abbr, "label": abbr, "primary": "#5B6573", "secondary": "#AEB6C2"}
    return {"abbr": abbr, "name": team["name"], "label": team["label"],
            "primary": team["primary"], "secondary": team["secondary"]}


def feed_matchup(away_abbr: str | None, home_abbr: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(away, home)`` as ``feed_team`` dicts; two teams from one city get distinct names
    ("New York Blue" / "New York Green", "Los Angeles Blue" / "Los Angeles Powder")."""
    away, home = feed_team(away_abbr), feed_team(home_abbr)
    if away["name"].lower() == home["name"].lower():
        for side in (away, home):
            side["name"] = _SAME_CITY_NAMES.get(side["abbr"], side["name"])
        if away["name"].lower() == home["name"].lower():  # unknown abbreviations that collide
            away["name"], home["name"] = f"{away['name']} Away", f"{home['name']} Home"
    return away, home


def preset(label: str) -> dict[str, Any]:
    """Look up a preset by its dropdown label (e.g. ``"Chicago"``)."""
    for team in TEAM_PRESETS:
        if team["label"] == label:
            return dict(team)
    raise KeyError(label)
