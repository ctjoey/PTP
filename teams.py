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

# name | label | primary | secondary
_PRESETS = """
Arizona       | Arizona                          | #97233F | #FFB612
Atlanta       | Atlanta                          | #A71930 | #A5ACAF
Baltimore     | Baltimore                        | #241773 | #9E7C0C
Buffalo       | Buffalo                          | #00338D | #C60C30
Carolina      | Carolina                         | #0085CA | #101820
Chicago       | Chicago                          | #0B162A | #C83803
Cincinnati    | Cincinnati                       | #FB4F14 | #000000
Cleveland     | Cleveland                        | #311D00 | #FF3C00
Dallas        | Dallas                           | #003594 | #869397
Denver        | Denver                           | #FB4F14 | #002244
Detroit       | Detroit                          | #0076B6 | #B0B7BC
Green Bay     | Green Bay                        | #203731 | #FFB612
Houston       | Houston                          | #03202F | #A71930
Indianapolis  | Indianapolis                     | #002C5F | #A2AAAD
Jacksonville  | Jacksonville                     | #006778 | #D7A22A
Kansas City   | Kansas City                      | #E31837 | #FFB81C
Las Vegas     | Las Vegas                        | #A5ACAF | #000000
Los Angeles   | Los Angeles (royal blue & gold)  | #003594 | #FFA300
Los Angeles   | Los Angeles (powder blue & gold) | #0080C6 | #FFC20E
Miami         | Miami                            | #008E97 | #FC4C02
Minnesota     | Minnesota                        | #4F2683 | #FFC62F
New England   | New England                      | #002244 | #C60C30
New Orleans   | New Orleans                      | #D3BC8D | #101820
New York      | New York (blue & red)            | #0B2265 | #A71930
New York      | New York (green & white)         | #125740 | #FFFFFF
Philadelphia  | Philadelphia                     | #004C54 | #A5ACAF
Pittsburgh    | Pittsburgh                       | #FFB612 | #101820
San Francisco | San Francisco                    | #AA0000 | #B3995D
Seattle       | Seattle                          | #002244 | #69BE28
Tampa Bay     | Tampa Bay                        | #D50A0A | #FF7900
Tennessee     | Tennessee                        | #0C2340 | #4B92DB
Washington    | Washington                       | #5A1414 | #FFB612
"""

TEAM_PRESETS: list[dict[str, Any]] = [
    dict(zip(("name", "label", "primary", "secondary"), (cell.strip() for cell in line.split("|"))))
    for line in _PRESETS.strip().splitlines()
]

# The admin form's default matchup (away at home).
DEFAULT_AWAY = "Chicago"
DEFAULT_HOME = "Detroit"


def preset(label: str) -> dict[str, Any]:
    """Look up a preset by its dropdown label (e.g. ``"Chicago"``)."""
    for team in TEAM_PRESETS:
        if team["label"] == label:
            return dict(team)
    raise KeyError(label)
