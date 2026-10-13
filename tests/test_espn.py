"""The ESPN reader for the feed timing check: the play key both feeds share, reading ESPN's (unofficial, odd)
answers, finding the game on the scoreboard, and the client against a fake ESPN (never the real one)."""

import asyncio
import gzip
import json

import pytest

import espn
from espn import (EspnClient, EspnPlay, find_event, key_label, nearby_dates, norm_clock, parse_wallclock, period_number,
                  play_key, summary_plays, tank01_matchup, tank01_play)
from playparse import classify
from tests.fakefeed import ENTRIES, ESPN_EVENT_ID, OFFLINE_ESPN, FakeEspn, espn_event, espn_play
from tests.test_feed import FG, FIRST_PASS, RUN_LEFT_END, VOID


@pytest.fixture
def fake():
    server = FakeEspn()
    yield server
    server.close()


# --------------------------------------------------------------------------- #
# The play key
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value, expected", [
    ("Q1", 1), ("Q4", 4), (" q2 ", 2), ("OT", 5), ("OT2", 6), ("2OT", 6), (2, 2), (2.0, 2), ("3", 3), ("1st", 1),
    ("4th", 4), (None, None), ("", None), ("Final", None), ("Half", None), (True, None), (0, None), (10, None),
    (2.5, None), ("Q", None), ([2], None),
])
def test_period_numbers_from_both_feeds(value, expected):
    assert period_number(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("3:42", "3:42"), ("03:42", "3:42"), ("0:05", "0:05"), ("00:05", "0:05"), (":42", "0:42"), ("15:00", "15:00"),
    (" 9:50 ", "9:50"), ("3:42.5", "3:42"), ("16:00", None), ("3:60", None), ("342", None), ("", None), (None, None),
    (222, None), ("3:4", None), ("a:bc", None),
])
def test_game_clocks_are_written_one_way(value, expected):
    assert norm_clock(value) == expected


def test_play_key_and_its_label():
    assert play_key("Q2", "03:42", 2, "6") == "2|3:42|2|6"
    assert play_key(2, "3:42", "2", 6) == "2|3:42|2|6"           # ESPN's numbers and Tank01's text agree
    assert play_key("OT", "10:00", 1, 10) == "5|10:00|1|10"
    assert play_key(2, "3:42", 2, 6.0) == "2|3:42|2|6"
    for bad in [(None, "3:42", 2, 6), (2, None, 2, 6), (2, "3:42", 0, 6), (2, "3:42", 5, 6), (2, "3:42", None, 6),
                (2, "3:42", 2, None), (2, "3:42", 2, 0), (2, "3:42", 2, "goal"), (2, "3:42", 2, "inches"),
                (2, "3:42", True, 6), (2, "3:42", "two", 6)]:
        assert play_key(*bad) is None, bad
    assert key_label("2|3:42|2|6") == "Q2 3:42 · 2nd & 6"
    assert key_label("5|10:00|1|10") == "OT 10:00 · 1st & 10" and key_label("6|2:00|3|1") == "OT2 2:00 · 3rd & 1"
    assert key_label("4|0:47|1|8", goal=True) == "Q4 0:47 · 1st & Goal"


def test_tank01_entries_are_keyed_and_non_plays_are_not():
    assert tank01_play(ENTRIES[FIRST_PASS]) == ("1|14:55|1|10", "Q1 14:55 · 1st & 10")
    assert tank01_play(ENTRIES[RUN_LEFT_END]) == ("1|14:24|2|3", "Q1 14:24 · 2nd & 3")
    assert tank01_play(ENTRIES[0]) is None                      # the kickoff
    assert tank01_play(ENTRIES[FG]) is None                     # a field goal
    assert tank01_play(ENTRIES[VOID]) is not None               # a penalty's "No Play" is a snap: it is compared too
    goal = next(i for i, e in enumerate(ENTRIES) if "Goal" in str(e.get("downAndDistance"))
                and classify(e).kind != "skip")
    key, label = tank01_play(ENTRIES[goal])
    spot = ENTRIES[goal]["downAndDistance"].rsplit(" ", 1)[1]
    assert key.endswith(f"|{spot}") and label.endswith("& Goal")   # "1st & Goal at CAR 8": 8 to go, as ESPN writes it
    assert tank01_play({**ENTRIES[FIRST_PASS], "playPeriod": "OT", "playClock": "08:05"})[0] == "5|8:05|1|10"
    for odd in [None, "text", 7, {}, {"play": "A.Dalton pass short right for 7 yards"},
                {**ENTRIES[FIRST_PASS], "downAndDistance": None}, {**ENTRIES[FIRST_PASS], "playClock": "soon"},
                {**ENTRIES[FIRST_PASS], "playPeriod": "Halftime"}, {**ENTRIES[FIRST_PASS], "downAndDistance": "5th & 2"}]:
        assert tank01_play(odd) is None, odd


def test_every_recorded_play_gets_the_same_key_from_both_feeds():
    """The whole recorded game, written ESPN's way (some clocks with a leading zero): the scrimmage plays line up."""
    tank01 = {}
    for i, entry in enumerate(ENTRIES):
        if (picked := tank01_play(entry)) and picked[0] not in tank01:
            tank01[picked[0]] = i
    plays = [EspnPlay.from_json(espn_play(i, e, pad_clock=i % 2 == 0)) for i, e in enumerate(ENTRIES)]
    from_espn = {}
    for i, play in enumerate(plays):
        if play.key and play.key not in from_espn:
            from_espn[play.key] = i
    assert len(tank01) > 100 and from_espn == tank01
    assert all(plays[i].label == tank01_play(ENTRIES[i])[1] for i in tank01.values())


# --------------------------------------------------------------------------- #
# Reading ESPN's plays
# --------------------------------------------------------------------------- #

RUN = {"id": "4016717450012", "sequenceNumber": "1234", "text": "J.Allen right end to BUF 42 for 7 yards (T.Defender).",
       "type": {"text": "Rush"}, "period": {"number": 2}, "clock": {"displayValue": "3:42"},
       "wallclock": "2026-10-13T02:31:05Z", "start": {"down": 2, "distance": 6, "yardLine": 35,
                                                     "downDistanceText": "2nd & 6 at BUF 35", "shortDownDistanceText": "2nd & 6"},
       "statYardage": 7, "scoringPlay": False}


def test_an_espn_play_with_every_field():
    play = EspnPlay.from_json(RUN)
    assert (play.id, play.sequence, play.type, play.period, play.clock) == ("4016717450012", 1234, "Rush", 2, "3:42")
    assert (play.down, play.distance, play.down_distance, play.yards, play.scoring) == (2, "6", "2nd & 6 at BUF 35", 7, False)
    assert play.wallclock == 1791858665.0 and play.scrimmage and play.key == "2|3:42|2|6"
    assert play.label == "Q2 3:42 · 2nd & 6"


@pytest.mark.parametrize("change, key", [
    ({"start": {"downDistanceText": "2nd & 6 at BUF 35"}}, "2|3:42|2|6"),          # numbers missing: read the text
    ({"start": {"shortDownDistanceText": "2nd & 6"}}, "2|3:42|2|6"),
    ({"start": {"down": "2", "distance": "6"}}, "2|3:42|2|6"),                    # numbers as strings
    ({"start": {"down": 1, "distance": 4, "downDistanceText": "1st & Goal at BUF 4"}}, "2|3:42|1|4"),
    ({"start": {"down": 1, "downDistanceText": "1st & Goal at BUF 4"}}, "2|3:42|1|4"),
    ({"clock": {"displayValue": "03:42"}}, "2|3:42|2|6"),
    ({"clock": {"value": 222.0}}, "2|3:42|2|6"),                                  # only the seconds left
    ({"period": {"number": "2"}}, "2|3:42|2|6"),
    ({"period": {"number": 5}}, "5|3:42|2|6"),
    ({"start": {}}, None), ({"start": None}, None), ({"start": {"down": 0, "distance": 0}}, None),
    ({"start": {"down": -1, "distance": 6}}, None),
    ({"clock": None}, None), ({"clock": {"displayValue": "soon"}}, None), ({"period": {}}, None), ({"period": 2}, None),
    ({"text": None}, None),                                                        # nothing to read: not a play
])
def test_espn_plays_with_missing_or_odd_fields(change, key):
    raw = {**RUN, **change}
    assert EspnPlay.from_json(raw).key == key


@pytest.mark.parametrize("kind, text, dd", [
    ("Kickoff", "T.Kicker kicks 65 yards from BUF 35 to end zone, Touchback.", None),
    ("Timeout", "Timeout #1 by BUF at 03:42.", "2nd & 6 at BUF 35"),
    ("End Period", "END QUARTER 2", "2nd & 6 at BUF 35"),
    ("Two-minute warning", "Two-Minute Warning", "2nd & 6 at BUF 35"),
    ("Punt", "S.Punter punts 45 yards to LAR 20, fair catch.", "4th & 6 at BUF 35"),
    ("Field Goal Good", "T.Kicker 44 yard field goal is GOOD.", "4th & 6 at LAR 26"),
    ("Extra Point Good", "T.Kicker extra point is GOOD.", "1st & 3 at LAR 3"),
    ("Rush", "(Kneel) J.Allen kneels to BUF 34 for -1 yards.", "1st & 10 at BUF 35"),
])
def test_kicks_timeouts_and_markers_are_never_compared(kind, text, dd):
    start = {"down": 4 if dd and dd.startswith("4th") else 1 if dd else 0, "distance": 6}
    if dd:
        start["downDistanceText"] = dd
    play = EspnPlay.from_json({**RUN, "type": {"text": kind}, "text": text, "start": start})
    assert not play.scrimmage and play.key is None


def test_odd_play_objects_never_raise():
    assert EspnPlay.from_json(None) is None and EspnPlay.from_json("play") is None and EspnPlay.from_json([RUN]) is None
    empty = EspnPlay.from_json({})
    assert empty.key is None and empty.label == "" and empty.wallclock is None and not empty.scrimmage
    weird = EspnPlay.from_json({"id": {"x": 1}, "sequenceNumber": "12a", "text": 5, "type": "Rush", "period": "two",
                                "clock": [], "wallclock": 1791858665, "start": "2nd & 6", "statYardage": "lots",
                                "scoringPlay": "yes"})
    assert (weird.id, weird.sequence, weird.text, weird.type, weird.yards, weird.scoring) == ("", None, "", "", None, False)
    assert weird.key is None


@pytest.mark.parametrize("value, expected", [
    ("2026-10-13T02:31:05Z", 1791858665.0), ("2026-10-13T02:31:05.000Z", 1791858665.0),
    ("2026-10-13T02:31:05+00:00", 1791858665.0), ("2026-10-12T22:31:05-04:00", 1791858665.0),
    ("2026-10-13T02:31:05", 1791858665.0),      # no zone: read as UTC
    ("yesterday", None), ("", None), (None, None), (1791858665, None), ("2026-13-45T99:00:00Z", None),
])
def test_wallclock_parsing(value, expected):
    assert parse_wallclock(value) == expected


def test_summary_plays_reads_every_drive_once():
    body = json.loads(json.dumps({"drives": {"previous": [{"plays": [espn_play(1, ENTRIES[1]), espn_play(2, ENTRIES[2])]},
                                                         {"plays": [espn_play(3, ENTRIES[3])]}],
                                             "current": {"plays": [espn_play(3, ENTRIES[3]), espn_play(4, ENTRIES[4])]}}}))
    plays = summary_plays(body)
    assert [p.id for p in plays] == [f"{ESPN_EVENT_ID}{i:04d}" for i in (1, 2, 3, 4)]   # the current drive repeats play 3
    assert summary_plays({"plays": [espn_play(1, ENTRIES[1])]})[0].key == "1|14:55|1|10"   # plays without drives
    assert summary_plays({"drives": {"previous": [], "current": {"plays": []}}}) == []
    no_id = {k: v for k, v in espn_play(1, ENTRIES[1]).items() if k != "id"}
    assert len(summary_plays({"drives": {"previous": [{"plays": [no_id, no_id]}]}})) == 1


@pytest.mark.parametrize("body", [
    None, [], "drives", 5, {}, {"drives": None}, {"drives": "x"}, {"drives": []}, {"drives": {"previous": "x"}},
    {"drives": {"previous": [None, 5, "x", {"plays": "x"}, {"plays": [None, 3, "x"]}], "current": [1]}},
    {"plays": "x"}, {"plays": [None]},
])
def test_summary_plays_survives_any_shape(body):
    assert summary_plays(body) == []


# --------------------------------------------------------------------------- #
# Finding the game on the scoreboard
# --------------------------------------------------------------------------- #


def board(*events):
    return {"events": list(events)}


def test_the_game_is_found_by_abbreviation():
    sb = board(espn_event("401", "NYJ", "NYG", "New York", "New York"), espn_event("402", "CAR", "WSH"),
               espn_event("403", "BUF", "LAR", "Buffalo", "Los Angeles"))
    assert find_event(sb, "CAR", "WSH") == "402"
    assert find_event(sb, "BUF", "LAR") == "403" and find_event(sb, "NYJ", "NYG") == "401"
    assert find_event(sb, "LAR", "BUF") == "403"                  # listed the other way round (a neutral site)
    assert find_event(sb, "KC", "DEN") is None


@pytest.mark.parametrize("tank01, espn_abbr", [("WAS", "WSH"), ("WSH", "WAS"), ("JAC", "JAX"), ("JAX", "JAC"),
                                               ("LA", "LAR"), ("OAK", "LV"), ("SD", "LAC"), ("STL", "LAR")])
def test_old_and_odd_abbreviations_are_aliases(tank01, espn_abbr):
    sb = board(espn_event("500", "MIA", espn_abbr, "Miami", "Somewhere"))
    assert find_event(sb, "MIA", tank01) == "500"


def test_the_game_is_found_by_city_when_the_abbreviations_differ():
    sb = board(espn_event("600", "CARX", "WSHX", "Carolina", "Washington"))
    assert find_event(sb, "CAR", "WSH") == "600"
    named = espn_event("601", "C1", "W1")
    for c in named["competitions"][0]["competitors"]:
        c["team"] = {"abbreviation": "??", "displayName": f"{c['team']['location']} Club"}   # only a display name
    assert find_event(board(named), "CAR", "WSH") == "601"
    # Two games that both fit by city: no guess.
    ny = [espn_event(str(700 + i), "AA", "BB", "New York", "New York") for i in range(2)]
    assert find_event(board(*ny), "NYJ", "NYG") is None
    assert find_event(board(ny[0]), "NYJ", "NYG") == "700"


@pytest.mark.parametrize("scoreboard", [
    None, [], {}, {"events": None}, {"events": "x"}, {"events": [None, 5, "x", {}]},
    {"events": [{"id": "1", "competitions": "x"}]}, {"events": [{"id": "1", "competitions": [{"competitors": 5}]}]},
    {"events": [{"id": "1", "competitions": [{"competitors": [None, {"homeAway": "home"}]}]}]},
    {"events": [{**espn_event(), "id": "../summary?event=1"}]}, {"events": [{**espn_event(), "id": None}]},
    {"events": [{**espn_event(), "id": True}]},
])
def test_odd_scoreboards_find_nothing_and_never_raise(scoreboard):
    assert find_event(scoreboard, "CAR", "WSH") is None


def test_tank01_game_ids_and_nearby_dates():
    assert tank01_matchup("20261012_BUF@LAR") == ("20261012", "BUF", "LAR")
    for bad in (None, "", "demo", "20261012_BUF-LAR", "2026101_BUF@LAR", "20261012_buf@lar"):
        assert tank01_matchup(bad) is None
    assert nearby_dates("20261231") == ["20261231", "20270101", "20261230"]
    assert nearby_dates("2026-12-31") == [] and nearby_dates("20261340") == []


# --------------------------------------------------------------------------- #
# The client (against the fake ESPN on 127.0.0.1)
# --------------------------------------------------------------------------- #


def run(coro):
    return asyncio.run(coro)


def test_client_reads_the_scoreboard_and_a_summary(fake):
    fake.reveal(upto=4)
    client = EspnClient(fake.url)
    sb = run(client.scoreboard("20241020"))
    assert sb.ok and sb.http_status == 200 and find_event(sb.body, "CAR", "WSH") == ESPN_EVENT_ID
    assert run(client.scoreboard("20241021")).body == {"events": []}
    summary = run(client.summary(ESPN_EVENT_ID))
    assert summary.ok and [p.key for p in summary_plays(summary.body) if p.key] == [
        "1|14:55|1|10", "1|14:24|2|3", "1|13:50|1|10", "1|13:16|1|10"]
    assert client.requests == 3 and repr(client) == f"EspnClient(base_url={fake.url!r})"
    hit = fake.hits[0]
    assert hit["path"] == "/apis/site/v2/sports/football/nfl/scoreboard" and hit["query"] == {"dates": "20241020"}
    assert fake.hits[2]["query"] == {"event": ESPN_EVENT_ID}
    headers = hit["headers"]
    assert headers["user-agent"].startswith("PickThePlay/") and "x-rapidapi-key" not in headers
    assert not any("key" in name or "auth" in name for name in headers)       # nothing secret: ESPN needs no key


@pytest.mark.parametrize("override, kind", [
    (dict(status=500, raw=b"oops"), "http"), (dict(status=404, body={"code": 404}), "http"),
    (dict(status=200, raw=b"<html>gateway</html>"), "bad_json"), (dict(status=200, raw=b""), "bad_json"),
    (dict(status=200, body=["not", "a", "summary"]), "shape"), (dict(status=200, body=None), "shape"),
])
def test_client_errors_come_back_as_plain_results(fake, override, kind):
    fake.queue(**override)
    result = run(EspnClient(fake.url).summary(ESPN_EVENT_ID))
    assert not result.ok and result.error_kind == kind and result.error_text.startswith("ESPN")


def test_client_scoreboard_must_list_events(fake):
    fake.queue(body={"events": "none"})
    result = run(EspnClient(fake.url).scoreboard("20241020"))
    assert not result.ok and result.error_kind == "shape"


def test_client_timeout_and_no_server(fake):
    fake.queue(delay=0.6)
    result = run(EspnClient(fake.url, timeout=0.2).summary(ESPN_EVENT_ID))
    assert not result.ok and result.error_kind == "network" and result.error_text == "ESPN timed out."
    result = run(EspnClient(OFFLINE_ESPN, timeout=2).scoreboard("20241020"))
    assert not result.ok and result.error_kind == "network" and result.error_text == "ESPN could not connect."
    assert EspnClient("").base_url == "https://site.api.espn.com" and EspnClient().timeout == 8.0


def test_client_caps_the_answer_size_and_reads_gzip(fake, monkeypatch):
    monkeypatch.setattr(espn, "MAX_RESPONSE_BYTES", 1000)
    fake.queue(raw=json.dumps({"events": [espn_event()] * 50}).encode())
    result = run(EspnClient(fake.url).scoreboard("20241020"))
    assert not result.ok and result.error_kind == "too_big"
    monkeypatch.setattr(espn, "MAX_RESPONSE_BYTES", 8_000_000)

    class Gzip(FakeEspn):
        def _handle(self, h):
            payload = gzip.compress(json.dumps({"events": [espn_event()]}).encode())
            h.send_response(200)
            h.send_header("content-type", "application/json")
            h.send_header("content-encoding", "gzip")
            h.send_header("content-length", str(len(payload)))
            h.end_headers()
            h.wfile.write(payload)

    zipped = Gzip()
    try:
        result = run(EspnClient(zipped.url).scoreboard("20241020"))
        assert result.ok and find_event(result.body, "CAR", "WSH") == ESPN_EVENT_ID
    finally:
        zipped.close()
