"""Feed timing: Tank01 vs ESPN, side by side, against a fake Tank01, a fake ESPN and a manual clock.

The comparison must never change the game: the same Tank01 requests at the same moments, the same suggestions and
scores, whatever ESPN does. It reads ESPN only while Tank01 waits for a locked play (and 90 s after a play is matched
or scored), never for the practice game, never after the game is over, and not at all with ESPN_COMPARE=0."""

import time

import pytest

from app import ResolveIn, Settings, StatusIn
from espn import EspnPlay, tank01_play
from feed import ESPN_MESSAGES, FeedTiming, TimingRow
from tests.fakefeed import ENTRIES, ESPN_EVENT_ID, FEED_ID, Rig, run_rig
from tests.test_after_game_web import needs_node, run_node
from tests.test_feed import FIRST_PASS, RUN_LEFT_END, lock_entry

KEY1, KEY2, KEY3 = (tank01_play(ENTRIES[i])[0] for i in (1, 2, 3))   # the game's first three snaps
TIMING_KEYS = {"espn", "rows", "summary"}
ROW_KEYS = {"key", "label", "tank01", "espn", "gap", "lead", "fair", "tank01_shown", "espn_shown"}
SUMMARY_KEYS = {"plays", "espn_first", "tank01_first", "ties", "avg_gap", "avg_tank01", "avg_espn"}


def timing(rig: Rig) -> dict:
    return rig.state()["timing"]


def rows(rig: Rig) -> dict:
    return {r["key"]: r for r in timing(rig)["rows"]}


def kinds(rig: Rig, exclude=()) -> list:
    return [r["kind"] for r in rig.store.feed_log(rig.game["id"]) if r["kind"] not in exclude]


def show(fake, i: int) -> None:
    """Make entry ``i`` (and everything before it) visible on a fake feed; never hides anything already shown."""
    fake.visible = max(fake.visible, i + 1)


async def race(rig: Rig, i: int, *, tank01_at: float, espn_at: float, snap_at: float | None = None,
               steps: int = 300, opened: bool = False) -> float:
    """Lock the play for entry ``i`` (open it first unless ``opened``): ESPN shows it ``espn_at`` s after the lock (its
    wallclock ``snap_at`` s after the lock, when given), Tank01 ``tank01_at`` s after. Steps a second at a time until
    the play is scored and both feeds have shown it. Returns the lock time."""
    rig.feed.lags.clear()   # the first Tank01 check stays 5 s after the lock: a schedule the test can predict
    await (rig.lock() if opened else lock_entry(rig, i))
    locked = rig.clock()
    if snap_at is not None:
        rig.espn.wallclock[i] = locked + snap_at
    key = tank01_play(ENTRIES[i])[0]
    for _ in range(steps):
        t = rig.clock() + 1 - locked
        if t >= espn_at:
            show(rig.espn, i)
        if t >= tank01_at:
            show(rig.server, i)
        await rig.step(1)
        row = rig.feed.timing.rows.get(key)
        if rig.play()["state"] == "RESOLVED" and row is not None and row.both:
            return locked
    raise AssertionError(f"no race result for entry {i}: {timing(rig)}")


# --------------------------------------------------------------------------- #
# Who was first
# --------------------------------------------------------------------------- #


def test_espn_sooner_with_its_wallclock(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        assert timing(rig) == {"espn": {"state": "looking", "message": "Looking for this game on ESPN..."}, "rows": [],
                               "summary": {"plays": 0, "espn_first": 0, "tank01_first": 0, "ties": 0, "avg_gap": None,
                                           "avg_tank01": None, "avg_espn": None}}
        locked = await race(rig, FIRST_PASS, tank01_at=40, espn_at=19, snap_at=6)
        # ESPN reads 1, 5, ... 17, 21 s after the lock; Tank01 checks 5, 8, 11, ... 38, 41 s after it.
        row = rig.feed.timing.rows[KEY1]
        assert (row.espn_seen - locked, row.espn_prev - locked) == (21, 17)
        assert (row.tank01_seen - locked, row.tank01_prev - locked) == (41, 38)
        t = timing(rig)
        assert t["espn"] == {"state": "on", "message": ESPN_MESSAGES["on"]}
        assert t["espn"]["message"] == "Comparing with ESPN. It is free and uses none of your Tank01 requests."
        assert t["rows"] == [{"key": "1|14:55|1|10", "label": "Q1 14:55 · 1st & 10", "tank01": 35.0, "espn": 15.0,
                              "gap": 20.0, "lead": "espn", "fair": True, "tank01_shown": True, "espn_shown": True}]
        assert t["summary"] == {"plays": 1, "espn_first": 1, "tank01_first": 0, "ties": 0, "avg_gap": 20.0,
                                "avg_tank01": 35.0, "avg_espn": 15.0}
        # The game went on exactly as Tank01 said: auto-scored from Tank01's entry.
        assert rig.play()["resolved_by"] == "feed" and rig.play()["yards_gained"] == 7

    run_rig(tmp_path, scenario)


def test_tank01_sooner_without_a_wallclock_and_a_tie(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await race(rig, FIRST_PASS, tank01_at=10, espn_at=30)      # no wallclock: no delays, only the gap
        r = rows(rig)[KEY1]
        assert (r["tank01"], r["espn"], r["lead"], r["fair"]) == (None, None, "tank01", True)
        row = rig.feed.timing.rows[KEY1]
        assert r["gap"] == round(row.tank01_seen - row.espn_seen, 1) and -23 <= r["gap"] <= -19
        await race(rig, RUN_LEFT_END, tank01_at=19, espn_at=19)
        r = rows(rig)[KEY2]
        assert r["lead"] == "tie" and abs(r["gap"]) <= 3 and r["fair"]
        s = timing(rig)["summary"]
        assert (s["plays"], s["espn_first"], s["tank01_first"], s["ties"]) == (2, 0, 1, 1)
        assert s["avg_gap"] == round((rows(rig)[KEY1]["gap"] + r["gap"]) / 2, 1)
        assert s["avg_tank01"] is None and s["avg_espn"] is None
        assert [x["key"] for x in timing(rig)["rows"]] == [KEY2, KEY1]          # newest game time first

    run_rig(tmp_path, scenario)


def test_summary_over_three_plays_and_the_feed_log(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await race(rig, 1, tank01_at=40, espn_at=19, snap_at=6)             # ESPN by 20 s
        await race(rig, 2, tank01_at=10, espn_at=30, snap_at=4)             # Tank01 by about 20 s
        await race(rig, 3, tank01_at=19, espn_at=19)                        # about the same
        r = rows(rig)
        assert [r[k]["lead"] for k in (KEY1, KEY2, KEY3)] == ["espn", "tank01", "tie"] and all(x["fair"] for x in r.values())
        s = timing(rig)["summary"]
        gaps = [r[k]["gap"] for k in (KEY1, KEY2, KEY3)]
        assert (s["plays"], s["espn_first"], s["tank01_first"], s["ties"]) == (3, 1, 1, 1)
        assert s["avg_gap"] == round(sum(gaps) / 3, 1)
        assert s["avg_tank01"] == round((r[KEY1]["tank01"] + r[KEY2]["tank01"]) / 2, 1)
        assert s["avg_espn"] == round((r[KEY1]["espn"] + r[KEY2]["espn"]) / 2, 1)
        # One "timing" row per play in the downloadable feed log, written once both feeds have shown it.
        log = [x for x in rig.store.feed_log(rig.game["id"]) if x["kind"] == "timing"]
        assert [x["data"]["key"] for x in log] == [KEY1, KEY2, KEY3]
        first = log[0]
        assert first["feed_index"] == 1 and first["data"]["text"].startswith("A.Dalton pass short right")
        assert {k: first["data"][k] for k in ("tank01", "espn", "gap", "lead", "fair", "tank01_since_check",
                                              "espn_since_check")} == {
            "tank01": 35.0, "espn": 15.0, "gap": 20.0, "lead": "espn", "fair": True, "tank01_since_check": 3.0,
            "espn_since_check": 4.0}
        assert first["data"]["wallclock"] == first["data"]["espn_seen"] - 15
        espn_rows = [x["data"] for x in rig.store.feed_log(rig.game["id"]) if x["kind"] == "espn"]
        assert espn_rows[0]["found"] == ESPN_EVENT_ID and espn_rows[0]["date"] == "20241020"
        assert espn_rows[1] == {"state": "on", "error": None, "event_id": ESPN_EVENT_ID}
        assert len(espn_rows) == 2                                          # only changes are logged

    run_rig(tmp_path, scenario)


def test_unfair_rows_are_shown_but_not_counted(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await race(rig, 1, tank01_at=40, espn_at=19, snap_at=6)             # fair
        for _ in range(30):                                                 # the host takes half a minute
            await rig.step(1)
        await race(rig, 2, tank01_at=0, espn_at=0)    # Tank01 already had it on its first check: when did it arrive?
        row = rig.feed.timing.rows[KEY2]
        assert row.tank01_seen - row.tank01_prev > 12 and rows(rig)[KEY2]["fair"] is False
        assert timing(rig)["summary"]["plays"] == 1                         # only the fair play counts
        # ESPN unreachable for a while: its sighting comes long after its previous answer, so that race is unfair too.
        rig.espn.queue(status=503, times=3)
        await race(rig, 3, tank01_at=8, espn_at=6)
        row = rig.feed.timing.rows[KEY3]
        assert row.espn_seen - row.espn_prev > 12 and rows(rig)[KEY3]["fair"] is False
        assert [r["key"] for r in timing(rig)["rows"]] == [KEY3, KEY2, KEY1] and timing(rig)["summary"]["plays"] == 1

    run_rig(tmp_path, scenario)


def test_espn_clocks_with_a_leading_zero_and_a_repeated_drive_still_match(tmp_path):
    async def scenario(rig: Rig):
        rig.espn.pad_clock, rig.espn.repeat_current = True, True
        await rig.new_game()
        await race(rig, 1, tank01_at=20, espn_at=10)
        await race(rig, 2, tank01_at=20, espn_at=10)
        assert set(rows(rig)) == {KEY1, KEY2} and all(r["lead"] == "espn" for r in rows(rig).values())

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# The maths
# --------------------------------------------------------------------------- #


def row(key="1|14:55|1|10", t01=None, t01_prev=None, espn=None, espn_prev=None, wall=None, order=1) -> TimingRow:
    return TimingRow(key, "label", order, tank01_seen=t01, tank01_prev=t01_prev, espn_seen=espn, espn_prev=espn_prev,
                     wallclock=wall)


@pytest.mark.parametrize("gap, lead", [(3.0, "tie"), (3.1, "espn"), (-3.0, "tie"), (-3.1, "tank01"), (0.0, "tie"),
                                       (36.0, "espn"), (-0.04, "tie")])
def test_gap_and_lead(gap, lead):
    r = row(t01=1000.0 + gap, t01_prev=990.0, espn=1000.0, espn_prev=996.0)
    assert r.gap == round(gap, 1) and r.lead == lead


def test_fairness_needs_both_feeds_checked_within_12_seconds():
    assert row(t01=100.0, t01_prev=88.0, espn=90.0, espn_prev=78.0).fair is True          # 12 s each: still fair
    assert row(t01=100.0, t01_prev=87.9, espn=90.0, espn_prev=86.0).fair is False
    assert row(t01=100.0, t01_prev=98.0, espn=90.0, espn_prev=77.9).fair is False
    assert row(t01=100.0, t01_prev=None, espn=90.0, espn_prev=86.0).fair is False         # Tank01's first answer
    assert row(t01=100.0, t01_prev=98.0, espn=90.0, espn_prev=None).fair is False
    assert row(t01=100.0, t01_prev=98.0).fair is False and row(espn=90.0, espn_prev=86.0).fair is False
    one = row(espn=90.0, espn_prev=86.0, wall=80.0)
    assert one.view() == {"key": "1|14:55|1|10", "label": "label", "tank01": None, "espn": 10.0, "gap": None,
                          "lead": None, "fair": False, "tank01_shown": False, "espn_shown": True}


def test_delays_need_a_wallclock_and_never_go_below_zero():
    r = row(t01=160.0, t01_prev=158.0, espn=130.0, espn_prev=126.0, wall=100.0)
    assert (r.delay(r.tank01_seen), r.delay(r.espn_seen), r.gap) == (60.0, 30.0, 30.0)
    assert row(t01=99.0, wall=100.0).delay(99.0) == 0.0
    assert row(t01=160.0, espn=130.0).delay(160.0) is None
    assert set(r.view()) == ROW_KEYS


def test_summary_counts_and_averages():
    rows_ = [row(t01=150.0, t01_prev=148.0, espn=120.0, espn_prev=116.0, wall=100.0),   # ESPN by 30
             row(t01=110.0, t01_prev=108.0, espn=121.0, espn_prev=117.0, wall=100.0),   # Tank01 by 11
             row(t01=130.0, t01_prev=128.0, espn=128.5, espn_prev=125.0),               # tie, no wallclock
             row(t01=140.0, t01_prev=138.0, espn=115.0, espn_prev=111.0, wall=None)]    # ESPN by 25
    assert FeedTiming.summary(rows_) == {"plays": 4, "espn_first": 2, "tank01_first": 1, "ties": 1,
                                         "avg_gap": round((30 - 11 + 1.5 + 25) / 4, 1), "avg_tank01": 30.0,
                                         "avg_espn": 20.5}
    assert FeedTiming.summary([]) == {"plays": 0, "espn_first": 0, "tank01_first": 0, "ties": 0, "avg_gap": None,
                                      "avg_tank01": None, "avg_espn": None}


def test_rows_are_newest_first_at_most_twelve_and_one_sided_plays_only_while_in_flight(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        t = rig.feed.timing
        now = rig.clock()
        for n in range(14):                       # 14 compared plays, noticed out of game order
            minute = (n * 5) % 14
            r = t._add(f"{1 + n // 7}|{minute}:00|1|10", f"play {n}")
            r.tank01_seen, r.tank01_prev, r.espn_seen, r.espn_prev = now, now - 2, now - 5, now - 9
        lone_new = t._add("4|1:00|2|5", "only ESPN, newest")              # ESPN has it, Tank01 not yet: in flight
        lone_new.espn_seen, lone_new.espn_prev, lone_new.wallclock = now, now - 4, now - 20
        lone_old = t._add("1|14:00|3|2", "only ESPN, old")               # a play the feeds wrote differently
        lone_old.espn_seen, lone_old.espn_prev, lone_old.wallclock = now, now - 4, now - 20
        no_delay = t._add("4|0:30|1|10", "only ESPN, no wallclock")
        no_delay.espn_seen = now
        only_t01 = t._add("4|0:20|1|10", "only Tank01")
        only_t01.tank01_seen = now
        shown = timing(rig)["rows"]
        labels = [r["label"] for r in shown]
        # one-sided plays newer than every compared one are still in the race: listed (newest game time first),
        # whichever feed has them and with or without ESPN's time of the play
        assert len(shown) == 12 and labels[:3] == ["only Tank01", "only ESPN, no wallclock", "only ESPN, newest"]
        assert shown[0]["tank01_shown"] is True and shown[0]["espn_shown"] is False and shown[2]["espn"] == 20.0
        assert "only ESPN, old" not in labels                            # older than compared plays: written differently
        keys = [r["key"] for r in shown[3:]]
        order = [(int(k.split("|")[0]), -int(k.split("|")[1].split(":")[0])) for k in keys]
        assert order == sorted(order, reverse=True)                      # quarter, then less time left, first
        assert timing(rig)["summary"]["plays"] == 14                     # the summary counts every compared play

    run_rig(tmp_path, scenario)


def test_rows_are_capped_per_game(tmp_path):
    async def scenario(rig: Rig):
        import feed

        await rig.new_game()
        t = rig.feed.timing
        for n in range(feed.TIMING_KEEP + 5):
            t._add(f"1|{n // 60}:{n % 60:02d}|1|{1 + n % 9}", str(n))
        assert len(t.rows) == feed.TIMING_KEEP and "0" not in {r.label for r in t.rows.values()}

    run_rig(tmp_path, scenario)


@pytest.mark.parametrize("wall_offset, believed", [
    (-15.0, True), (0.0, True), (-900.0, True),      # when ESPN showed it, or up to 15 minutes before
    (2.0, False), (-901.0, False), (None, False),     # in the future, too old, or missing
])
def test_wallclock_is_believed_only_when_plausible(tmp_path, wall_offset, believed):
    async def scenario(rig: Rig):
        await rig.new_game()
        now = rig.clock()
        play = EspnPlay.from_json({"id": "1", "text": "A.Dalton pass short right to J.Sanders for 7 yards.",
                                   "type": {"text": "Pass Reception"}, "period": {"number": 1},
                                   "clock": {"displayValue": "14:55"}, "start": {"down": 1, "distance": 10}})
        play.wallclock = None if wall_offset is None else now + wall_offset
        assert rig.feed.timing._saw_espn([play], now) is True
        r = rig.feed.timing.rows[KEY1]
        assert (r.wallclock is not None) is believed
        assert r.delay(r.espn_seen) == (round(-wall_offset, 1) if believed else None)
        assert rig.feed.timing._saw_espn([play], now + 4) is False         # first sighting wins

    run_rig(tmp_path, scenario)


def test_garbage_wallclock_from_espn_is_ignored(tmp_path):
    async def scenario(rig: Rig):
        rig.espn.wallclock[1] = "not a time"
        await rig.new_game()
        await race(rig, 1, tank01_at=20, espn_at=10)
        r = rows(rig)[KEY1]
        assert r["tank01"] is None and r["espn"] is None and r["lead"] == "espn"

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# It never changes the game
# --------------------------------------------------------------------------- #


async def standard_play(rig: Rig) -> dict:
    """One play the same way every time: Tank01 shows it 20 s after the lock (ESPN at 12 s, when it is up)."""
    await rig.new_game()
    await rig.open(1, "10")
    for _ in range(5):
        await rig.step(10)                       # an open play is not locked: nothing is read
    await rig.lock()
    locked = rig.clock()
    suggested = None
    for t in range(1, 160):
        if t >= 12:
            show(rig.espn, FIRST_PASS)
        if t >= 20:
            show(rig.server, FIRST_PASS)
        await rig.step(1)
        if suggested is None and rig.state()["suggestion"]:
            suggested = rig.clock() - locked
    play = rig.play()
    return {"tank01_hits": [(h["path"], h["query"]) for h in rig.server.hits], "polls": rig.poll_offsets(locked),
            "suggested": suggested,
            "play": (play["state"], play["correct_play_type"], play["correct_direction"], play["yards_gained"],
                     play["resolved_by"], play["feed_text"]),
            "log": kinds(rig, exclude=("espn", "timing", "timing_seen")), "requests": rig.state()["requests"]["game"]}


def _baseline(tmp_path) -> dict:
    return run_rig(tmp_path / "off", standard_play, espn_compare=False)


ESPN_TROUBLE = {
    "healthy": lambda rig: None,
    "http_500": lambda rig: rig.espn.queue(status=500, raw=b"oops", times=500),
    "garbage_json": lambda rig: rig.espn.queue(raw=b"{not json", times=500),
    "timeout": lambda rig: (setattr(rig.feed.espn, "timeout", 0.2), rig.espn.queue(delay=0.5, times=500)),
    "no_event": lambda rig: setattr(rig.espn, "events", []),
    "summary_404": lambda rig: setattr(rig.espn, "event_id", "999"),
    "unreachable": lambda rig: setattr(rig.feed.espn, "base_url", "http://127.0.0.1:9"),
}


@pytest.mark.parametrize("trouble", list(ESPN_TROUBLE))
def test_whatever_espn_does_the_game_and_tank01_requests_are_unchanged(tmp_path, trouble):
    (tmp_path / "off").mkdir()
    baseline = _baseline(tmp_path)

    async def scenario(rig: Rig):
        ESPN_TROUBLE[trouble](rig)
        out = await standard_play(rig)
        out["timing"] = timing(rig)
        out["espn_reads"] = rig.feed.espn.requests
        return out

    got = run_rig(tmp_path, scenario)
    t = got.pop("timing")
    assert got.pop("espn_reads") > 0
    assert got == baseline                                    # same Tank01 requests, same moments, same result
    assert baseline["play"][:5] == ("RESOLVED", "PASS", "RIGHT", 7, "feed") and baseline["suggested"] == 20
    if trouble == "healthy":
        assert t["espn"]["state"] == "on" and rows_of(t)[KEY1]["lead"] == "espn"
    else:
        state = "looking" if trouble == "no_event" else "error"   # ESPN answered without the game: still looking
        assert t["espn"]["state"] == state and t["espn"]["message"] == ESPN_MESSAGES[state]
        # Only Tank01 showed the play: it is listed with ESPN "-", so a silent ESPN is plain to see; nothing compared.
        assert [(r["tank01_shown"], r["espn_shown"]) for r in t["rows"]] == [(True, False)] and t["summary"]["plays"] == 0


def rows_of(t: dict) -> dict:
    return {r["key"]: r for r in t["rows"]}


def test_espn_errors_back_off_and_recover(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)                                         # found, first read
        assert len(rig.espn.summary_hits) == 1 and timing(rig)["espn"]["state"] == "on"
        rig.espn.queue(status=500, times=4)
        reads = []
        for _ in range(70):
            n = len(rig.espn.hits)
            await rig.step(1)
            if len(rig.espn.hits) > n:
                reads.append(rig.clock())
        gaps = [round(b - a) for a, b in zip(reads, reads[1:])]
        assert gaps[:5] == [4, 8, 16, 30, 4]                      # 4, 8, 16, 30 s after failures, then every 4 s
        assert timing(rig)["espn"]["state"] == "on"
        errors = [x["data"] for x in rig.store.feed_log(rig.game["id"]) if x["kind"] == "espn"]
        assert [e.get("state") for e in errors[1:]] == ["on", "error", "on"]
        assert errors[2]["error"] == "ESPN answered with an error (HTTP 500)."

    run_rig(tmp_path, scenario)


def test_game_not_on_espn_keeps_looking_every_30_s_and_logs_why_once(tmp_path):
    async def scenario(rig: Rig):
        rig.espn.events = []
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)
        assert [h["query"]["dates"] for h in rig.espn.scoreboard_hits] == ["20241020", "20241021", "20241019"]
        assert timing(rig)["espn"]["state"] == "looking" and rig.espn.summary_hits == []   # ESPN answered: still looking
        for _ in range(29):
            await rig.step(1)
        assert len(rig.espn.scoreboard_hits) == 3
        await rig.step(1)
        assert len(rig.espn.scoreboard_hits) == 6
        notes = [x["data"] for x in rig.store.feed_log(rig.game["id"]) if x["kind"] == "espn"]
        assert notes == [{"not_found": "20241020_CAR@WSH", "dates": ["20241020", "20241021", "20241019"]}]   # said once
        assert timing(rig)["espn"]["state"] == "looking"

    run_rig(tmp_path, scenario)


def test_the_game_is_found_on_the_next_day_and_by_an_alias(tmp_path):
    async def scenario(rig: Rig):
        rig.espn.date = "20241021"                                # ESPN files the game a day later
        await rig.new_game("20241020_CAR@WAS")                    # Tank01 writing Washington the old way
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)
        assert [h["query"]["dates"] for h in rig.espn.scoreboard_hits] == ["20241020", "20241021"]
        assert rig.feed.timing.event_id == ESPN_EVENT_ID and timing(rig)["espn"]["state"] == "on"

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# When ESPN is read (and when it is not)
# --------------------------------------------------------------------------- #


def test_espn_is_read_only_while_tank01_waits_and_90_s_after_the_play(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        for _ in range(10):
            await rig.step(30)
        await rig.open(1, "10")
        for _ in range(10):
            await rig.step(30)
        assert rig.espn.hits == []                                # between plays (and while a play is open): nothing
        locked = await race(rig, FIRST_PASS, tank01_at=10, espn_at=6, opened=True)
        assert rig.espn.hits and rig.state()["suggestion"] is None
        matched = rig.feed.timing.rows[KEY1].tank01_seen
        reads = []
        for _ in range(200):
            n = len(rig.espn.hits)
            await rig.step(1)
            if len(rig.espn.hits) > n:
                reads.append(rig.clock())
        assert reads and max(reads) - matched <= 90 + 4 + 4 and max(reads) - matched > 80   # the 90 s after the play
        assert all(b - a == 4 for a, b in zip(reads, reads[1:]))
        n = len(rig.espn.hits)
        for _ in range(20):
            await rig.step(30)
        assert len(rig.espn.hits) == n                            # then nothing until the next lock
        await lock_entry(rig, RUN_LEFT_END)
        await rig.step(1)
        assert len(rig.espn.hits) == n + 1 and rig.clock() - locked > 400

    run_rig(tmp_path, scenario)


def test_espn_is_also_read_after_the_host_scores_by_hand(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))   # faster than both feeds
        n = len(rig.espn.hits)
        for _ in range(85):
            await rig.step(1)
        assert len(rig.espn.hits) > n + 15                        # still reading for a slower ESPN
        n = len(rig.espn.hits)
        for _ in range(60):
            await rig.step(1)
        assert len(rig.espn.hits) <= n + 2

    run_rig(tmp_path, scenario)


def test_the_interval_is_a_setting(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        reads = []
        for _ in range(40):
            n = len(rig.espn.summary_hits)
            await rig.step(1)
            if len(rig.espn.summary_hits) > n:
                reads.append(rig.clock())
        assert [round(b - a) for a, b in zip(reads, reads[1:])] == [10, 10, 10]

    run_rig(tmp_path, scenario, espn_interval=10)


def test_no_espn_for_the_practice_game(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game("demo")
        assert rig.feed.timing is None and timing(rig) is None
        await rig.open(1, "10")
        await rig.lock()
        await rig.until(lambda: rig.play()["state"] == "RESOLVED", limit=120)
        for _ in range(10):
            await rig.step(10)
        assert rig.espn.hits == [] and timing(rig) is None

    run_rig(tmp_path, scenario)


def test_no_espn_with_espn_compare_off(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        assert rig.feed.timing is None and "timing" in rig.state() and timing(rig) is None
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.play()["state"] == "RESOLVED")
        for _ in range(10):
            await rig.step(10)
        assert rig.espn.hits == [] and rig.server.hits

    run_rig(tmp_path, scenario, espn_compare=False)


def test_no_espn_after_the_game_is_over(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        for _ in range(5):
            await rig.step(1)
        assert rig.espn.hits
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))   # (it would read 90 s more)
        await rig.ctrl.set_status(StatusIn(status="FINAL"))
        n = len(rig.espn.hits)
        for _ in range(30):
            await rig.step(10)
        assert len(rig.espn.hits) == n and timing(rig)["espn"]["state"] == "on"   # the table stays to look at

    run_rig(tmp_path, scenario)


def test_a_game_over_before_espn_was_found_says_the_comparison_is_off(tmp_path):
    async def scenario(rig: Rig):
        rig.espn.events = []
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)
        assert timing(rig)["espn"]["state"] == "looking"
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        await rig.ctrl.set_status(StatusIn(status="FINAL"))
        await rig.step(1)
        assert timing(rig)["espn"] == {"state": "off", "message": "The ESPN comparison is off."}

    run_rig(tmp_path, scenario)


def test_turning_live_data_off_stops_espn(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)
        await rig.feed.link(None)
        assert rig.feed.timing is None and rig.state()["timing"] is None
        n = len(rig.espn.hits)
        for _ in range(30):
            await rig.step(5)
        assert len(rig.espn.hits) == n

    run_rig(tmp_path, scenario)


def test_without_a_tank01_key_there_is_nothing_to_compare(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()
        rig.feed.tank01._key = ""                                 # the key was removed: Tank01 checks nothing
        rig.feed.attach(rig.game)
        assert timing(rig)["espn"]["state"] == "off"
        await lock_entry(rig, FIRST_PASS)
        await rig.ctrl.resolve_play(ResolveIn(play_type="PASS", direction="RIGHT", yards=7))
        for _ in range(20):
            await rig.step(5)
        assert rig.espn.hits == [] and rig.server.hits == []

    run_rig(tmp_path, scenario)


def test_espn_news_reaches_the_consoles_but_never_wakes_the_tank01_poller(tmp_path):
    async def scenario(rig: Rig):
        pushes = []
        rig.ctrl.feed_changed = lambda: pushes.append(rig.clock())
        await rig.new_game()
        await lock_entry(rig, FIRST_PASS)
        await rig.step(1)                                         # found and read: the status line turned "on"
        rig.espn.visible = FIRST_PASS + 1
        rig.clock.advance(4)
        rig.feed._wake.clear()
        pushes.clear()
        await rig.feed.espn_tick()                                # ESPN shows the play: one push, Tank01 left alone
        assert pushes == [rig.clock()] and not rig.feed._wake.is_set()
        assert rig.feed.timing.rows[KEY1].espn_seen == rig.clock()
        rig.clock.advance(4)
        await rig.feed.espn_tick()                                # nothing new: no push
        assert len(pushes) == 1

    run_rig(tmp_path, scenario)


def test_a_bug_in_the_comparison_never_reaches_the_game(tmp_path):
    async def scenario(rig: Rig):
        await rig.new_game()

        def boom(*_args, **_kwargs):
            raise RuntimeError("timing bug")

        rig.feed.timing.saw_tank01 = boom
        rig.feed.timing.tick = boom
        rig.feed.timing.view = boom
        rig.feed.timing.linger = boom
        await lock_entry(rig, FIRST_PASS)
        rig.server.reveal(upto=FIRST_PASS)
        await rig.until(lambda: rig.play()["state"] == "RESOLVED")
        assert rig.play()["resolved_by"] == "feed" and rig.state()["timing"] is None
        assert await rig.feed.espn_tick() == 30.0

    run_rig(tmp_path, scenario)


# --------------------------------------------------------------------------- #
# The feed object always has "timing"
# --------------------------------------------------------------------------- #


def test_state_always_has_timing(tmp_path):
    async def scenario(rig: Rig):
        assert "timing" in rig.state() and rig.state()["timing"] is None          # no game
        await rig.new_game(None)
        assert rig.state()["timing"] is None                                       # live data off
        await rig.feed.link(FEED_ID)
        t = timing(rig)
        assert set(t) == TIMING_KEYS and set(t["espn"]) == {"state", "message"} and set(t["summary"]) == SUMMARY_KEYS
        await race(rig, FIRST_PASS, tank01_at=10, espn_at=5)
        assert all(set(r) == ROW_KEYS for r in timing(rig)["rows"])
        await rig.feed.link("demo")
        assert rig.state()["timing"] is None                                       # the practice game

        def broken():
            raise RuntimeError("x")

        rig.feed.state = broken
        assert rig.ctrl._feed_state()["timing"] is None                            # the console's fallback too

    run_rig(tmp_path, scenario)


def test_settings_read_the_environment(monkeypatch):
    for name in ("ESPN_COMPARE", "ESPN_BASE_URL", "ESPN_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    d = Settings()
    assert (d.espn_compare, d.espn_base_url, d.espn_interval) == (True, "https://site.api.espn.com", 4.0)
    for value in ("0", "false", "No", " off "):
        monkeypatch.setenv("ESPN_COMPARE", value)
        assert Settings().espn_compare is False, value
    for value in ("1", "yes", "", "on"):
        monkeypatch.setenv("ESPN_COMPARE", value)
        assert Settings().espn_compare is True, value
    monkeypatch.setenv("ESPN_BASE_URL", " http://espn.test ")
    monkeypatch.setenv("ESPN_INTERVAL", "oops")
    s = Settings()
    assert (s.espn_base_url, s.espn_interval) == ("http://espn.test", 4.0)
    monkeypatch.setenv("ESPN_INTERVAL", "6")
    assert Settings().espn_interval == 6.0


# --------------------------------------------------------------------------- #
# In the real app, in real time (background tasks)
# --------------------------------------------------------------------------- #


def test_the_real_app_compares_in_the_background(tmp_path):
    from fastapi.testclient import TestClient

    from app import create_app
    from tests.conftest import ADMIN_KEY, GAME
    from tests.fakefeed import SENTINEL_KEY, FakeEspn, FakeTank01

    tank01, espn = FakeTank01(), FakeEspn()
    headers = {"X-Admin-Key": ADMIN_KEY}
    settings = Settings(db_path=str(tmp_path / "app.db"), admin_key=ADMIN_KEY, window_seconds=15,
                        tank01_api_key=SENTINEL_KEY, tank01_base_url=tank01.url, tank01_first_delay=0.05,
                        tank01_fast_interval=0.05, tank01_auto_score_grace=0.15, espn_base_url=espn.url, espn_interval=1)

    def wait(client, predicate, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            state = client.get("/api/admin/state", headers=headers).json()
            if predicate(state):
                return state
            time.sleep(0.02)
        raise AssertionError(state["feed"])

    try:
        with TestClient(create_app(settings)) as client:
            client.post("/api/admin/game", json={**GAME, "feed_game_id": FEED_ID}, headers=headers)
            assert wait(client, lambda s: s["feed"]["timing"] is not None)["feed"]["timing"]["espn"]["state"] == "looking"
            assert espn.hits == []                                    # nothing locked yet: ESPN is not read
            client.post("/api/admin/play/open", json={"down": 1, "distance": "10"}, headers=headers)
            client.post("/api/admin/play/lock", headers=headers)
            wait(client, lambda s: len(espn.summary_hits) >= 1 and s["feed"]["timing"]["espn"]["state"] == "on")
            espn.wallclock[FIRST_PASS] = time.time() - 5
            espn.reveal(upto=FIRST_PASS)                              # ESPN has it first ...
            state = wait(client, lambda s: bool(s["feed"]["timing"]["rows"]))
            assert state["feed"]["timing"]["rows"][0]["tank01"] is None and state["feed"]["timing"]["rows"][0]["espn"] >= 5
            time.sleep(0.3)
            tank01.reveal(upto=FIRST_PASS)                            # ... then Tank01
            state = wait(client, lambda s: any(r["gap"] is not None for r in s["feed"]["timing"]["rows"]))
            r = state["feed"]["timing"]["rows"][0]
            assert r["key"] == KEY1 and r["gap"] > 0 and r["lead"] in ("espn", "tie")
            wait(client, lambda s: s["play"]["state"] == "RESOLVED")
            history = client.get("/api/admin/state", headers=headers).json()["history"][0]
            assert history["resolved_by"] == "feed" and history["yards_gained"] == 7
            log = client.get("/api/admin/feed/log", headers=headers).json()["rows"]
            assert [x["data"]["key"] for x in log if x["kind"] == "timing"] == [KEY1]
            assert all(h["path"] in ("/getNFLBoxScore",) for h in tank01.hits)   # ESPN never went to Tank01
    finally:
        tank01.close()
        espn.close()


# --------------------------------------------------------------------------- #
# The website's Feed timing section (run under node with the page stand-in)
# --------------------------------------------------------------------------- #


TIMING_WEB = r"""
const { PTPFeed } = load(["static/js/common.js", "static/js/admin-feed.js"]);
const show = (timing) => {
  reg.clear();
  const feed = { available: true, linked: true, source: "tank01", game_id: "x", state: "idle", message: "", paused: false,
    auto_score: true, auto_open: false, requests: { game: 3, game_cap: 900 }, lag: {}, waiting: null, suggestion: null,
    disagreement: null, next_down: null, auto_open_at: null, last_scored: null, clock: null, timing };
  const st = { game: { id: 1 }, play: null, history: [], feed };
  PTPFeed.create({ act: async () => ({ ok: true }), getState: () => st, getKey: () => "k", isBusy: () => false,
    loadResult() {}, applyNextDown() {}, refreshHistory() {} }).render(st);
  const cell = (td) => ({ text: td.children[0].textContent, sooner: td.className === "sooner" });
  return {
    hidden: page("#feed-timing").hidden, status: page("#ft-status").textContent, tone: page("#ft-dot").dataset.tone,
    verdict: page("#ft-verdict").hidden ? null : page("#ft-verdict").textContent, tableHidden: page("#ft-table").hidden,
    rows: page("#ft-rows").children.map((tr) => ({ label: tr.children[0].children[0].textContent, cls: tr.className,
      tank01: cell(tr.children[1]), espn: cell(tr.children[2]) })),
  };
};
const summary = (s) => ({ plays: 0, espn_first: 0, tank01_first: 0, ties: 0, avg_gap: null, avg_tank01: null, avg_espn: null, ...s });
const row = (r) => ({ key: "k", label: "Q2 3:42 · 2nd & 6", tank01: null, espn: null, gap: null, lead: null, fair: true, ...r });
const on = { state: "on", message: "Comparing with ESPN. It is free and uses none of your Tank01 requests." };
const out = {
  none: show(null),
  looking: show({ espn: { state: "looking", message: "Looking for this game on ESPN..." }, rows: [], summary: summary({}) }),
  espn: show({ espn: on, summary: summary({ plays: 9, espn_first: 7, tank01_first: 1, ties: 1, avg_gap: 31.4 }), rows: [
    row({ label: "Q2 3:42 · 2nd & 6", tank01: 58.0, espn: 22.0, gap: 36.0, lead: "espn" }),
    row({ label: "Q2 4:10 · 1st & 10", gap: 36.0, lead: "espn" }),
    row({ label: "Q2 4:50 · 3rd & 2", gap: -12.4, lead: "tank01" }),
    row({ label: "Q2 5:20 · 2nd & 1", gap: 0.0, lead: "tie" }),
    row({ label: "Q2 5:40 · 1st & Goal", gap: 2.0, lead: "tie" }),
    row({ label: "Q2 6:00 · 1st & 10", espn: 15.0 }),
    row({ label: "Q2 6:30 · 2nd & 4", gap: 40.0, lead: "espn", fair: false }),
    ...Array.from({ length: 5 }, (_, i) => row({ label: `Q1 ${i}:00 · 1st & 10`, gap: 5, lead: "espn" })),
  ] }),
  tank01: show({ espn: on, rows: [], summary: summary({ plays: 4, espn_first: 1, tank01_first: 3, avg_gap: -18.6 }) }),
  same: show({ espn: on, rows: [], summary: summary({ plays: 9, espn_first: 3, tank01_first: 3, ties: 3, avg_gap: 2 }) }),
  one: show({ espn: on, rows: [], summary: summary({ plays: 1, espn_first: 1, avg_gap: 0.4 }) }),
  mixed: show({ espn: on, rows: [], summary: summary({ plays: 5, espn_first: 3, tank01_first: 2, avg_gap: -6 }) }),
  error: show({ espn: { state: "error", message: "ESPN isn't answering right now. Tank01 still runs the game." }, rows: [],
    summary: summary({}) }),
};
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_website_shows_feed_timing_as_specified():
    out = run_node(TIMING_WEB)
    assert out["none"]["hidden"] is True                                      # no comparison: no section
    looking = out["looking"]
    assert looking["hidden"] is False and looking["status"] == "Looking for this game on ESPN..." and looking["tone"] == "wait"
    assert looking["verdict"] is None and looking["tableHidden"] is True     # nothing compared yet
    e = out["espn"]
    assert e["verdict"] == "ESPN was first on 7 of 9 plays, about 31 s sooner on average." and e["tone"] == "good"
    rows = e["rows"]
    assert len(rows) == 8 and rows[0]["label"] == "Q2 3:42 · 2nd & 6"        # the newest 8
    cells = [(r["tank01"]["text"], r["espn"]["text"]) for r in rows[:7]]
    assert cells == [("58 s", "22 s"), ("+36 s", "first"), ("first", "+12 s"), ("same", "same"), ("+2 s", "first"),
                     ("-", "15 s"), ("+40 s", "first")]
    sooner = [(r["tank01"]["sooner"], r["espn"]["sooner"]) for r in rows[:7]]
    assert sooner == [(False, True), (False, True), (True, False), (False, False), (False, False), (False, False),
                      (False, True)]                                         # a tie or a one-sided play: no highlight
    assert [r["cls"] for r in rows[:7]] == ["", "", "", "", "", "", "unfair"]
    assert out["tank01"]["verdict"] == "Tank01 was first on 3 of 4 plays, about 19 s sooner on average."
    assert out["same"]["verdict"] == "Both feeds were about the same on 9 plays."
    assert out["one"]["verdict"] == "ESPN was first on 1 of 1 play."
    assert out["mixed"]["verdict"] == "ESPN was first on 3 of 5 plays."      # the average points the other way: no claim
    assert out["error"]["tone"] == "bad" and out["error"]["status"].startswith("ESPN isn't answering")
