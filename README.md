# Pick the Play: Live Pro Football Game

A real-time, second-screen prediction game for live pro football. Before every snap, players have
**15 seconds** to call the play — **Run or Pass**, **Left, Middle or Right** (as the QB looks
downfield) and **how far: Short, Medium or Long** — then watch points and leaderboards update the
instant the admin scores the play. For example: *Run, Left, Short*. Each right call is worth 10
points, and picking all 3 correctly adds a 10-point bonus: a perfect call is 40 ("You picked the play").

The MVP is a single Python FastAPI server with three web surfaces, synchronised over WebSockets:

| Surface | URL | Who |
| --- | --- | --- |
| **Live Player App** | `/` | Fans. Mobile-first, dark by default with a light/dark toggle (the sun/moon button in every page header), installable to the iPhone home screen. **Practice mode** (the button under the scores, on the "no game" card and on the welcome screen) plays simulated plays with the live timer and scoring, so anyone can try it with no game on and without signing in. Nothing is sent to the server. |
| **Head to Head** (lounges) | `/lounge/<4-digit code>` | Friends playing each other with a private leaderboard. |
| **Admin Console** | `/admin` | The operator watching the game and driving each play, by hand or with [live data](#live-data-tank01). |
| **Rules of the Game** | `/rules` | Everyone: how it works, the points, and what Left / Middle / Right mean. |

> Pick the Play is an independent fan game. It is not affiliated with, endorsed by, or sponsored by
> any professional football league or club, and it uses no official names, marks or logos.

---

> **Want to play on your iPhone?** Follow the step-by-step **[iPhone guide](IPHONE_GUIDE.md)**.

## Quick start

Requires **Python 3.11 or newer**. Install **3.14** from python.org (also tested on 3.12 and 3.13;
python.org no longer ships 3.12 installers). Avoid brand-new releases such as 3.15 until the
dependencies publish builds for them.

macOS / Linux:

```bash
python3.14 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python app.py
```

Windows (PowerShell), with no activation step:

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Then open:

- Admin console: <http://127.0.0.1:8000/admin> (default key `admin`; set `PTP_ADMIN_KEY` for anything real)
- Player app: <http://127.0.0.1:8000/> (open it in a second browser on this computer; for a phone, see [Play on your iPhone](#play-on-your-iphone))

The SQLite database `game.db` is created automatically next to `app.py`. Delete it to start fresh.

### Play on your iPhone

1. Start the server with `--phone`: `venv/bin/python app.py --phone` (macOS/Linux) or
   `.\venv\Scripts\python.exe app.py --phone` (Windows).
   It listens on your Wi-Fi and prints the exact address to open on the phone:
   ```
     On your iPhone (same Wi-Fi), open:  http://192.168.1.23:8000/
     Admin console:                      http://192.168.1.23:8000/admin
   ```
2. On the iPhone (same Wi-Fi, not a guest network), type that address into Safari.
3. Add it to the Home Screen **before** signing up. The Home Screen app keeps its own storage, so a
   player created in a Safari tab doesn't carry over.

To play away from your Wi-Fi, use a temporary HTTPS tunnel or free cloud hosting. Both are covered
in the [iPhone guide](IPHONE_GUIDE.md).

---

## Running a game (admin)

1. **Create Game.** Pick each team from the preset list (32 cities with their real team colors, e.g.
   *Chicago* at *Detroit*, the default), or choose **Custom** and type a city or region name and pick
   colors. Teams are city names only: official league marks and club nicknames are rejected. Two
   clubs share New York and two share Los Angeles; their presets fill the plain city name, so for a
   game between them add a word to each (e.g. *New York Blue* at *New York Green*).
   With [live data](#live-data-tank01) you can instead tap **Pick today's game** (or **Practice with a
   recorded game**) and **Create Game** creates *and* connects it in one tap.
2. **Open Next Play.** Set the down and the yards to go (e.g. 3rd & 7), then open. Every player gets the pick
   grid and a synchronised 15-second countdown. (The game goes LIVE automatically on the first play.)
3. **Lock Predictions.** Lock early, or let the timer lock it automatically. Late picks are rejected
   by the server.
4. **Resolve & Score Play.** Select what actually happened: the play type, the direction **as the QB
   looks downfield** (the offense's left and right, not the TV picture's; see
   [Left, Middle and Right](#left-middle-and-right)), and the distance:
   **Short** (0-5 yards), **Medium** (6-10), **Long** (11+) or **Loss** (negative yards). Or type the
   **yards gained** (e.g. `7`, or `-4` for a run stopped behind the line) and the distance is picked for you; an incomplete
   pass or no gain is 0 yards = Short. Points are calculated, totals and leaderboards update, and every
   player sees their result animation.
5. Repeat. Use **Void play** for penalties or no-plays (nobody scores). **End Game** marks it FINAL.
6. Scored it wrongly? **Fix result** on the Play log row re-scores every pick and moves each player's
   total by the difference (players see the change at once).

With live data on, steps 4 and 5 mostly do themselves: see [Live data (Tank01)](#live-data-tank01). Scoring by
hand always works exactly as above, whatever the feed is doing.

Keyboard shortcuts in the console:

| Key | Action |
| --- | --- |
| `O` / `L` | Open the next play / lock predictions |
| `R` / `P` | Run / pass |
| `←` `↑` `→` | Left / middle / right (as the QB looks downfield) |
| `S` / `M` / `G` / `X` | Short / medium / long / loss |
| `Y` | Type the yards gained (then `Enter` resolves) |
| `Enter` | Resolve & score the play (with a live-data suggestion ready: score the suggestion now) |
| `H` | Hold the live-data suggestion (stop its countdown) |
| `Shift` + `P` | Pause / resume live data |

### Play state machine

```
            Open Next Play             Lock (admin or 15 s timer)        Resolve & Score
  (idle) ─────────────────▶  OPEN  ─────────────────────────────▶ LOCKED ──────────────────▶ RESOLVED
                               │                                     │
                               └──────────── Void play ──────────────┴──▶ RESOLVED (voided, 0 pts)
```

Only one play per game can be OPEN or LOCKED at a time (enforced by a partial unique index).

### Message to players, and removing a player

Two cards in the right-hand column of the admin console:

- **Message to Players.** Type a short message (200 characters at most) or tap a quick one (*Delayed*,
  *Halftime*, *Paused*, *Game over*) and **Send to everyone**: it appears as a banner at the top of every player's
  screen on the website, including lounge pages and people who haven't signed in. It floats over the top of the page,
  so it never shifts the pick buttons. Anyone can dismiss it with the ✕ (it stays dismissed until you send a different
  message). **Clear banner** takes it down for everyone. Every connection is told what the banner is when it opens (a
  phone that reconnects after you cleared it drops the old one). It lives in memory, so a server restart clears it. Pages
  that were already open before this feature was deployed need a reload to show banners. The iPhone app ignores it until
  its next update.
- **Players.** Open the list to see everyone who signed up (newest first, with their picks and points in the current
  game, and a dot for who is online); type a name to narrow it. **Remove** asks first: **Remove and block name** deletes
  the account (picks, points and any lounges they host) and stops anyone signing up with that name again;
  **Remove only** deletes it but leaves the name free. The removed player is signed out where they are (the website says
  "You were removed by the host."; the iPhone app returns to its welcome screen) and every leaderboard refreshes.
  Blocked names are listed under the players, each with an **Unblock** button (for a mistaken block); they are stored in
  the `blocked_names` table. Typing in the search box also searches the server, so players beyond the first 500 can
  still be found. The iPhone app shows "Your account was deleted." (not the host's wording) until its next update.

### Running the game from the iPhone app (host console)

Everything the website's console does can be done from the iPhone app too, for a helper who is away from a computer.
**Settings → Open the host console** (also on the welcome screen as *Running the game? Host sign-in*) asks for the same
admin key, then shows four tabs:

- **Run**: Pick today's game (or the recorded practice game) and **Create Game**; **Open Next Play** (down and yards to go
  are filled in from the live feed between plays), **Lock Predictions**, the live-data card (status, **Pause / Resume /
  Check now**, the feed's suggestion with **Score now / Hold / Change / Skip**, the usage meter, the two switches), **Score
  it yourself** (type, direction, distance, optional yards, **Score Play**, **Void play**), **End game**.
- **Log**: this game's leaderboard and Play log, with **Fix result** on every scored play.
- **Players**: the signed-up list with search, **Remove** (and block the name), **Unblock**.
- **Message**: the banner to every player's screen, with the four quick messages.

It talks to the same `/ws/admin` socket and `/api/admin/*` routes as the website (no server change); several consoles can
be open at once, and the console warns when a second host is connected. The key is typed once and kept in the iPhone's
Keychain (device-only, not in backups); **Sign out** in the console's menu forgets it. The screen is kept awake while the
console is open. The app code is `Models/AdminModels.swift` (wire models, lenient on purpose: only the game and the play must
decode), `Models/HostLogic.swift` (the decisions: result entry, suggestion choices, next down, fix), `App/HostState.swift`
(socket, state, one method per action), `App/HostDrafts.swift` and `Views/Host/`. Fixtures for the contract tests come
from `venv/bin/python tests/capture_ios_admin_fixtures.py` (a real server on a random port, the recorded game as the live
source). `ios/PickThePlay/Models/TeamPresets.swift` is generated from `teams.py` by `ios/scripts/make_team_presets.py`
(a test fails when they drift apart).

## Live data (Tank01)

Optional. Connect a game to [Tank01's](https://rapidapi.com/tank01/api/tank01-nfl-live-in-game-real-time-statistics-nfl)
NFL play-by-play and the server reads each play's result for you. **The less the host has to do, the
better**: clean plays score themselves, the next play's down and distance is filled in, and you only step in
for odd plays. Players notice nothing: they get the same snapshots as always.

If the feed is wrong, slow, capped or down, you score by hand exactly as before. Manual scoring never stops
working, and nothing waits for the feed.

### How it works (the "smart method")

1. You open a play and it locks (you, or the 15 s timer), as always.
2. **Only while a locked play is waiting for its result** the server asks Tank01 for the box score: first
   after a short delay, then every few seconds. Between plays it makes **zero requests**.
3. The first scrimmage entry that shows up is that play. It is read (`playparse.py`) and becomes a
   **suggestion** in the console: what the feed says, as the same chips you already know (Run/Pass,
   Left/Middle/Right, Short/Medium/Long and the yards).
4. A clean suggestion is **scored automatically after 4 seconds** (a visible countdown), unless you
   **Hold** it, **Change** it, **Score now**, or **Skip** the feed play. Odd plays wait for you.
5. When it is scored, the **next play's down and distance is prefilled** in the Open Next Play form.

| What the feed says | What happens |
| --- | --- |
| A clean run or pass: type, direction **and** yards all unambiguous (touchdowns too) | Ready: scored automatically after the grace period. Incomplete pass or no gain = 0 yards = Short; a loss of yards = Loss |
| "No Play" (a penalty nullified the play), a **sack**, or a **quarterback scramble** | Void: voided automatically after the grace period. A sack or a scramble is no play and scores nothing for anyone: there was no throw to call |
| An interception, fumble or aborted snap, lateral or reverse, an accepted penalty, no charted direction or yards, anything unrecognised | **Review**: an amber card with a prefill and the reasons. Never automatic: you finish it (e.g. pick the direction) and press Score |
| The feed's down and distance differs from the play you opened ("Feed shows 2nd & 3 but this play is 2nd & 8") | Review with that warning, and a **Skip this feed play** button that throws the entry away and keeps waiting |
| "No Play" (or a sack / scramble) when the down and distance can't prove it is this play's | Review, but still a **no-play card**: it offers **Void play** (not the Run / Pass, direction and distance pickers) and keeps "Check the down and distance before voiding this play" in view. It never voids by itself |
| Kickoffs, punts, field goals, extra points, two-point tries, kneel-downs, spikes, timeouts, quarter markers | Not plays: skipped silently |

When in doubt the parser says "review", never a guess. An interception's return yards, a penalty's yards and
a field goal's distance are never mistaken for the play's yards.

**If you miss a play** (never open it), its feed entry is still waiting when you open the next one. The down-and-distance
check catches it ("Feed shows 2nd & 3 but this play is 1st & 10": tap **Skip this feed play**), but it cannot when both
plays have the same down and distance, so glance at the shown play text during the 4 s countdown; **Hold** stops it.

**If you score first** (you are faster than the feed) your play stays in line as "verification only": when its
entry arrives it is checked, and if the feed disagrees you get a **Disagreement** banner with **Fix result** or
**Dismiss**. **Fix result** (also on any row of the Play log) re-scores every pick and moves each player's
total by the difference.

**After a restart** (or when live data is connected in the middle of a game) the server has forgotten which hand-scored
plays still wait for their entries, so the **first** suggestion is shown as a review ("Score now") instead of counting
down by itself; after that it is automatic again.

### The live-data panel

- A status line with a coloured dot and one plain sentence: *Waiting for the result of play #7*, *Paused*,
  *Capped*, *Error*, *Waiting for kickoff*, *The game is over*, or *Feed is quiet: long delay (injury or review?)*.
- **Joined late.** If you lock a play after the feed is already several plays ahead (live data connected mid-game),
  it cannot tell which play yours was: the chip turns amber (**SCORE BY HAND**) and a line under the play says to tap
  what happened and press **Resolve & Score Play**, or press **Void play**. After that, open the next play and live
  data follows along again.
- **Pause / Resume / Check now.** Pause stops every request and the auto-score countdown (use it for an injury,
  a long replay review, or any time you want hands-off); Resume checks right away if a play is waiting;
  **Check now** makes one request on demand, even while paused. With nothing locked, it throws away feed entries for plays
  nobody opened (a way to catch up after missing a few), but keeps the newest one for a play you have open and not yet locked,
  and fills Down and To go from where the feed ended. With a play locked while the app is behind the game, it skips the
  older plays and offers the newest one for you to confirm ("Caught up to the newest play").
- Toggles: **Auto-score clean plays** (on by default) and **Open next play automatically** (**off** by default).
- **Typical delay: N s**, the median of how long Tank01 took to show the last plays. Auto-open is only safe once
  you have seen it: lag + the 4 s grace (the next play opens at once) should be well under about 25 s.
- A usage meter ("Requests this game 214 / 900, plan has 786 left") with an amber warning at 80%, and
  **Allow 100 more** when the per-game cap stops it.
- **Download feed log**: everything the recorder noted (see below).

**Auto-open** opens the next play by itself, `TANK01_OPEN_DELAY` seconds after a clean score, with the usual
15 s window and the computed down and distance. It only does so for a 1st to 3rd down after a clean play (never
into a likely punt or field goal, a touchdown or a turnover), only while the game is LIVE with nothing waiting,
and any host action (open, void, pause, end game) cancels it.

### Plan limits and the budget

Tank01's **Basic** plan allows **1,000 requests per month** (the responses say how many are left). The smart
method spends roughly **2 to 4 requests per play** when the feed's delay is steady: about 250 to 450 for a whole
game. (Measured on the recorded game with a steady delay: 1.5 per play at 12 s, 2 at 20 s, 3 at 45 s, 5 at 60 s,
8 at 90 s; the first check learns from the typical delay, up to 45 s.) A later **Pro** plan is 1,000 per day, then $0.01 per extra request.

Every real request goes through one gate that counts it (for the game and for the UTC day, saved in the database,
so a restart cannot reset a count) and enforces the caps. Live data stops polling, shows **Capped** with a plain
message, and manual scoring carries on, when:

- the game has used its `TANK01_MAX_REQUESTS_PER_GAME` (900; **Allow 100 more** raises it by 100),
- the day has used `TANK01_MAX_REQUESTS_PER_DAY` (1,000),
- the plan's own `x-ratelimit-requests-remaining` is at or below `TANK01_RESERVE` (15), unless `TANK01_ALLOW_OVERAGE=1`
  (then also raise the daily cap), or
- Tank01 itself says the quota is used up.

Schedule:
the first check comes `0.8 x typical delay` after the lock (kept between 3 and 45 s, so a slow feed is not polled from second 25 on; `TANK01_FIRST_DELAY`, 5 s,
until it has samples), then every `TANK01_FAST_INTERVAL` (2.5 s) for the first minute, every 5 s until 3 minutes,
every 10 s until 10 minutes, then every 15 s. A game that has not started is checked every minute. Errors back
off (10, 20, then 30 s); after 5 failures in a row the panel shows **Error** (it keeps trying slowly), after 10 it
**pauses** itself. A rejected key (HTTP 401/403) stops at once and says so. A play scored from entries the server
already holds costs nothing.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `TANK01_API_KEY` | unset | Your RapidAPI key. Without it live data is off (the practice game still works). A secret: never shown, logged or sent anywhere but Tank01 |
| `TANK01_BASE_URL` | Tank01's RapidAPI host | Only changed in tests |
| `TANK01_MAX_REQUESTS_PER_GAME` | `900` | Requests one game may use |
| `TANK01_MAX_REQUESTS_PER_DAY` | `1000` | Requests per UTC day, over all games |
| `TANK01_RESERVE` | `15` | Stop when the plan has this many left |
| `TANK01_ALLOW_OVERAGE` | `0` | `1` ignores the reserve (paid overage on a Pro plan) |
| `TANK01_FIRST_DELAY` | `5` | Seconds to the first check, until there are lag samples |
| `TANK01_FAST_INTERVAL` | `2.5` | Seconds between checks in the first minute |
| `TANK01_AUTO_SCORE_GRACE` | `4` | Seconds a clean suggestion shows before it is scored |
| `TANK01_OPEN_DELAY` | `0` | Seconds after a clean score until auto-open opens the next play |
| `TANK01_TIMEOUT` | `15` | Seconds before a request to Tank01 is given up on |
| `TANK01_DEMO_LAG` | `12` | Seconds the practice game takes to "show" a locked play |

#### Setting the key on Render

Dashboard, your service, **Environment**, **Add Environment Variable**: name `TANK01_API_KEY`, value your
RapidAPI key, then **Save** and let it redeploy. Never put the key in the code, a file in the repository, or a
URL. The console only ever learns whether a key exists (`available: true`).

### The practice game (no requests)

**Practice with a recorded game** replays a real finished game (Carolina at Washington, from `demo/`). When you lock
a play, its recorded entry "appears" after `TANK01_DEMO_LAG` seconds, so you can rehearse the whole flow: auto-score,
Hold, Change, Skip, Pause, review plays (it has an interception and an aborted snap), voids (penalties, sacks and scrambles), Fix result.
It costs zero requests, needs no key and is never capped. The host's team presets are filled in for you.

### The feed log

Every poll (status, milliseconds, entries, the game's status fields, what the plan has left), every new entry with how
it was read and how long after the lock it appeared, every match, suggestion, score, skip, orphan, pause and error
are recorded in the `feed_log` table (the last 5,000 rows per game). **Download feed log** (or
`GET /api/admin/feed/log?game_id=`) returns it as JSON. After the first live game it shows the real status and clock
values Tank01 sends at halftime and in overtime, how late entries appear, and whether earlier entries are ever
revised: what to tune next. One line per poll also goes to the normal log.

### First live game checklist

1. Set `TANK01_API_KEY` (and a strong `PTP_ADMIN_KEY`) on the server; redeploy. On the Basic plan (1,000 requests a
   month) consider `TANK01_MAX_REQUESTS_PER_GAME=500` too: a game normally needs 250 to 450, and the cap keeps a
   stuck play from eating the rest of the month.
2. Rehearse once with **Practice with a recorded game**: let a few plays auto-score, try Hold, Change, Skip, Pause and Fix result.
3. On game day tap **Pick today's game** (one request), pick it and **Create Game**. Leave **Open next play automatically** off.
4. Open the first play shortly before the snap. Before kickoff the feed answers "not started" and live data waits
   (it looks again every minute). If Tank01 is slow to go live, the first play simply waits: when the feed
   catches up, the game's first play is offered to you as a suggestion to confirm with one tap (**Score now**), and the
   plays that happened while it waited are skipped. You can also score it by hand at any time.
5. Watch **Typical delay** and the requests meter for the first few plays. Keep **Check now** for a stuck play.
6. Any play that surprises you: score it by hand (**Resolve & Score**, **Void play**); nothing else is needed.
7. Afterwards download the feed log and keep it for tuning.

### Live data protocol

`admin_state` (the admin socket message, also `GET /api/admin/state`) has a `feed` object. Timestamps are server
epoch seconds, like `locks_at` (use `server_time` for the clock offset).

```json
"feed": {
  "available": true,                  // TANK01_API_KEY is set (the practice game is always possible)
  "linked": true, "source": "tank01",  // "tank01" | "demo" | null
  "game_id": "20261008_TB@DAL",
  "state": "waiting",                 // off | idle | waiting | paused | capped | error | not_started | done
  "message": "Waiting for the result of play #7 (check 2).",
  "paused": false, "auto_score": true, "auto_open": false,
  "requests": {"game": 214, "today": 214, "game_cap": 900, "day_cap": 1000, "plan_remaining": 786, "plan_limit": 1000},
  "lag": {"median": 18.0, "last": 17.0, "samples": 6},
  "waiting": {"play_id": 7, "checks": 1, "since": 1791504900.1, "next_check_at": 1791504910.1},
  "suggestion": {"play_id": 7, "status": "ready", "text": "A.Dalton pass short right ...", "clock": "Q1 14:55",
                 "down_and_distance": "1st & 10 at CAR 14", "play_type": "PASS", "direction": "RIGHT",
                 "yards": 7, "yardage": "MEDIUM", "flags": [], "warning": null, "auto_at": 1791504921.4, "kind": "play"},
  "disagreement": {"play_id": 6, "feed": "PASS - RIGHT - MEDIUM", "scored": "RUN - LEFT - MEDIUM", "text": "...",
                   "feed_result": {"play_type": "PASS", "direction": "RIGHT", "yards": 7, "yardage": "MEDIUM"}},
  "next_down": {"down": 2, "distance": "3"},
  "auto_open_at": null,
  "last_scored": {"play_id": 6, "by": "feed", "auto": true, "summary": "PASS - RIGHT - MEDIUM (7 yds)", "at": 1791504912.0, "voided": false}
}
```

`suggestion.status` is `ready` (clean, counting down while `auto_at` is set), `review`, `void` or `held`. Each play
object in `history` also has `resolved_by` (`host` · `feed` · `void` · `host-fix`, `null` on old rows) and `feed_text`;
players never see either, nor any live-data field. A correction broadcasts a normal snapshot with `event`
`"play_corrected"`.

**Admin actions** (socket: `{"action": ..., "request_id": ..., ...fields}` answered by `admin_ack`; REST: `POST
/api/admin/feed/<name>` with the same fields, `<name>` without or with the `feed_` prefix). Every feed action
answers with `{"feed": <the feed object>}`.

| Action | Fields | Does |
| --- | --- | --- |
| `feed_link` | `feed_game_id` (`"20261008_TB@DAL"`, `"demo"` or `null`) | Connect (or disconnect) the current game. `create_game` / `POST /api/admin/game` also take `feed_game_id` |
| `feed_pause` · `feed_resume` | | Pause (no requests, no countdown) · resume (checks at once if a play waits) |
| `feed_check_now` | | One request now |
| `feed_set` | `auto_score?`, `auto_open?` | The two switches |
| `feed_hold` | | Stop the suggestion's countdown until you act |
| `feed_accept` | `play_id`, `play_type?`, `direction?`, `yardage?` or `yards?`, `void?` | Apply the suggestion now; any field given overrides it; `void: true` voids. Also answers with `play` |
| `feed_skip` | `play_id` | Throw away the matched feed entry and keep waiting |
| `feed_dismiss` | | Clear the disagreement banner |
| `feed_allow_more` | `n` (100) | Raise this game's request cap |
| `correct_play` | `play_id`, `play_type`, `direction`, `yardage?` or `yards?` | Fix a scored play (REST: `POST /api/admin/play/correct` or `/api/admin/feed/correct_play`) |

`GET /api/admin/feed/games?date=YYYYMMDD` lists the day's games for "Pick today's game": `{"date", "games":
[{"feed_game_id", "away": {"abbr", "name", "label", "primary", "secondary"}, "home": {...}, "time", "status",
"status_code"}], "demo": {...}, "available", "error", "cached"}`. One real request, then cached for five minutes.
Teams map to the presets by `feed_abbr`; two clubs from one city become *New York Blue* / *New York Green* and
*Los Angeles Blue* / *Los Angeles Powder*; an unknown abbreviation gets the abbreviation as its name and neutral
colors. `GET /api/admin/feed/log` returns the recorder.

## Scoring

Each play has three picks. Each one you get right is worth **10 points**, and picking all 3 correctly
adds a **10-point bonus**: +10 pick correct play, +10 pick correct direction, +10 pick correct distance, +10 pick all 3 correctly = 40.
Every screen (website, iPhone app, Rules, Support, Practice) words the points table the same way:

| Row | Choices | Points |
| --- | --- | --- |
| Pick correct play (Run / Pass) | Run · Pass | +10 |
| Pick correct direction (Left / Middle / Right), as the QB looks downfield | Left · Middle · Right | +10 |
| Pick correct distance (Short / Medium / Long), total yards gained | Short 0-5 yds · Medium 6-10 · Long 11+ | +10 |
| Pick all 3 correctly | | +10 |
| **Perfect call** ("You picked the play") | | **40** |

When a player picks all 3 correctly, the live result (and Practice) shows **Perfect call!** with the line
"You picked the play" under it.

So a play scores 0, 10, 20 or 40 (30 can't happen). A **loss of yards** (a run stopped behind
the line) scores no distance points, and so no bonus: a loss earns at most 20. An incomplete pass or
no gain is 0 yards, which is Short. Example: you pick *Run, Left, Short* and it's a run to the left for
7 yards (Medium): **20 points**. Pick *Run, Left, Medium*: **40**. Points already scored before the
bonus existed stay as they were (an old 30 stays 30).

Ties share a rank (1, 2, 2, 4). Within a tie, more perfect calls (all 3 picked correctly) sort first.
The live leaderboard ranks points in the current game; *Season pts* is the user's all-time total.

### Left, Middle and Right

Directions follow the official NFL play-by-play (the full text, with diagrams, is on the
**Rules of the Game** page at `/rules`, linked from the player app, the welcome screen, the admin
console and the support page). Always from the offense's point of view, as the QB looks downfield:

- **Runs** go by run location (and the gap the run goes through). **Middle** is any run between the left and right guards
  (the A-gaps on either side of the center); **Left** / **Right** is at or outside a guard: guard,
  tackle or end on that side.
- **Passes** go by pass location, using the hash marks. **Middle** is between the hashes;
  **Left** / **Right** is outside a hash, out to that sideline.
- A sack or a quarterback scramble is no play and scores no points for anyone: there was no throw to call. When
  no direction is charted, the host makes the call. All official calls are derived from the official game
  statistics, and all final calls are at the host's discretion.

## Head-to-Head Lounges

From the player app, tap **Head to Head** (in the website's header, and a tab in the iPhone app) to:

- **Create** a lounge. You become the host (👑) and get a 4-digit code plus a share link
  (uses the iOS share sheet where available).
- **Join** with a friend's 4-digit code, or open `/lounge/<code>` directly.

Inside a lounge you play the same live game, with a private leaderboard tab of every member's points.

---

## Project structure

```
├── app.py              # FastAPI server: routes, WebSocket hub, game controller, auto-lock timer
├── models.py           # SQLite schema + migrations, Store (data access), validation, scoring engine
├── teams.py            # Team presets for the admin: 32 city names with team colors and Tank01 abbreviations
├── playparse.py        # Live data: reads one play-by-play entry (skip / void / play / review) and the next down
├── feed.py             # Live data: Tank01 client, recorded demo feed, the poller/matcher, budget caps, recorder
├── demo/               # The recorded game the practice mode replays (a real, finished game)
├── static/
│   ├── css/style.css   # Dark (default) and light, mobile-first styles shared by all pages
│   ├── js/common.js    # DOM helpers, reconnecting WebSocket, server-clock sync, light/dark toggle
│   ├── js/player.js    # Player app + lounges
│   ├── js/admin.js     # Admin console
│   ├── js/admin-feed.js # Admin console: the live-data panel, suggestion card, schedule picker, Fix result
│   ├── img/            # app icons (generated by ios/scripts/make_icons.py)
│   └── manifest.webmanifest
├── templates/          # Jinja2: base.html, player.html, admin.html
├── tests/              # pytest: scoring, data layer, HTTP + WebSocket end-to-end, live data (fake Tank01 in
│                       # tests/fakefeed.py, recorded fixtures in tests/fixtures/); iOS fixture capture
├── requirements.txt
├── requirements-dev.txt
├── .python-version    # Python version for cloud hosts (3.14)
├── IPHONE_GUIDE.md     # Step-by-step: play it on an iPhone
└── README.md
```

## Architecture

- **Server-authoritative state.** Everything lives in SQLite (`game.db`, WAL mode). Each admin action
  is one transaction in `Store`, after which `GameController` broadcasts.
- **Personalised snapshots.** On every change each connected player receives a full `state` snapshot:
  game, current play, *their* pick and points, *their* rank, the top-25 leaderboard and (in a lounge)
  the lounge leaderboard. Clients just re-render, so a reconnect is always consistent. Shared data is
  computed once per broadcast, not once per client.
- **Synchronised timers.** Snapshots carry `server_time` and the play's `locks_at`, so each device
  counts down against server time rather than its own clock. The server auto-locks at `locks_at`
  plus a 0.5 s grace for in-flight picks, and re-arms the timer after a restart.
- **The crowd split stays hidden** while a play is OPEN, so it can't influence picks. It appears once
  the play is locked.

### WebSocket protocol

**Player — `/ws`**

| Direction | Message |
| --- | --- |
| → | `{"type":"hello","token":"…","lounge":"1234"}` first (token optional = spectator) |
| → | `{"type":"predict","play_id":7,"play_type":"PASS","direction":"LEFT","yardage":"SHORT"}` (all four fields required; repeat to change the pick while OPEN) |
| → | `{"type":"sync"}` · `{"type":"ping"}` |
| ← | `{"type":"state","event":"play_opened"\|"play_locked"\|"play_resolved"\|"play_voided"\|"play_corrected"\|"game_created"\|"game_status"\|"lounge_updated"\|"sync", …snapshot}` |
| ← | `{"type":"prediction_saved","prediction":{"play_id":7,"play_type":"PASS","direction":"LEFT","yardage":"SHORT","points_earned":null}}` · `{"type":"error","message":"…"}` |
| ← | `{"type":"announcement","id":3,"text":"Halftime! Back in about 15 minutes.","sent_at":1760000000.0}`: the host's banner (`text` is empty when it has been cleared); also sent once to every new connection (with an empty `text` when nothing is showing). Ids keep growing across server restarts. Clients that don't know the type ignore it |
| ← | `{"type":"error","code":"account_deleted","message":"Your account was deleted."\|"You were removed by the host."}` then the socket closes with `4401` |

**Admin — `/ws/admin`**

| Direction | Message |
| --- | --- |
| → | `{"type":"auth","key":"…"}` first |
| → | `{"action":"create_game"\|"set_status"\|"open_play"\|"lock_play"\|"resolve_play"\|"void_play"\|"correct_play","request_id":1, …payload}` and the `feed_*` live-data actions ([protocol](#live-data-protocol)) |
| → | e.g. `{"action":"resolve_play","request_id":2,"play_type":"RUN","direction":"LEFT","yardage":"MEDIUM","yards":7}` |
| → | Host tools: `{"action":"announce","text":"…"}` (empty text clears the banner) · `{"action":"remove_player","user_id":12,"block":true}` · `{"action":"unblock_name","name":"…"}` |
| ← | `{"type":"admin_state", …}` with live pick stats, players online, leaderboard, play log, the `feed` object, `announcement` (what is showing, or `null`) and `registered_players` |
| ← | `{"type":"admin_ack","request_id":1,"ok":true\|false,"error":"…"}` |

**Picks, results and the fields that carry them**

| Field | Values |
| --- | --- |
| `play_type` | `RUN` · `PASS` |
| `direction` | `LEFT` · `MIDDLE` · `RIGHT`, as the QB looks downfield. `CENTER` (what older apps send) is accepted as an alias for `MIDDLE` on picks and resolves; the server only ever sends `MIDDLE` |
| `yardage` (a pick) | `SHORT` (0-5 yds) · `MEDIUM` (6-10) · `LONG` (11+). Required on every new pick. Never called "distance" in code or JSON: `distance` is the down-and-distance ("3rd & **7**") |
| `correct_yardage` (a result) | `SHORT` · `MEDIUM` · `LONG` · `LOSS` (negative yards), `null` until resolved |
| `yards_gained` (a result) | Whole number -99..99 when the admin or a data feed entered it, otherwise `null` |

- **Resolving** (`resolve_play` / `POST /api/admin/play/resolve`) takes `play_type`, `direction` and
  `yardage` (`SHORT`/`MEDIUM`/`LONG`/`LOSS`) and/or `yards`. With `yards` alone the server derives the
  bucket (below 0 = `LOSS`, 0-5 = `SHORT`, 6-10 = `MEDIUM`, 11+ = `LONG`); with both they must agree.
  Missing both, a mismatch, or yards outside -99..99 is a validation error (HTTP `422`, or an
  `admin_ack` with `"ok": false`).
- **Every play object** carries `correct_play_type`, `correct_direction`, `correct_yardage` and
  `yards_gained` (all `null` until the play is resolved), plus `down`, `distance`, `state`, `voided`,
  `opened_at` and `locks_at`.
- **Every prediction object** (`my_prediction`, `prediction_saved`) carries `play_type`, `direction`,
  `yardage` (`null` for picks made before distance picks existed) and `points_earned`. Once the play is
  resolved, `my_prediction` adds `type_correct`, `direction_correct` and `yardage_correct`.
- **Snapshots** carry `"scoring": {"type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40}`
  (`bonus` = extra for picking all 3 correctly, `exact` = a perfect call), and `crowd` (hidden while OPEN)
  counts `RUN`, `PASS`, `LEFT`, `MIDDLE`, `RIGHT`, `SHORT`, `MEDIUM`, `LONG`, `total`, `exact` (picks
  with all 3 correct) and `scored` (picks worth more than 0). Leaderboard `exact_hits` counts plays
  with all 3 correct.
- **The game object** (`state.game`, and `admin_state.game` for the host) also carries `home_score`, `away_score`
  (whole numbers) and `score_at` (server epoch seconds): the live score as of the last check of the live data, so it can
  trail the TV by about a minute. All three are `null` until a check has happened, and always `null` for the
  recorded practice game. Every screen shows the number beside the team name when it has one and hides it otherwise
  (the host console adds "Score as of N s ago").

### REST API

Player endpoints use `Authorization: Bearer <token>` (issued by `POST /api/users`).
Admin endpoints use the `X-Admin-Key` header and mirror the admin socket actions, which makes them
handy for scripting.

| Method & path | Purpose |
| --- | --- |
| `POST /api/users` `{username}` | Register and get a token |
| `GET /api/me` | Current user and their lounges |
| `DELETE /api/me` | Permanently delete the account → `204 No Content`. Removes the user's picks, lounge memberships and hosted lounges; their open sockets get `{"type":"error","code":"account_deleted"}` and close with code `4401`; everyone else gets a `leaderboard_updated` snapshot |
| `GET /api/state` | Public snapshot |
| `POST /api/predictions` `{play_id, play_type, direction, yardage}` | Submit a pick (HTTP fallback when the socket is down) |
| `POST /api/lounges` `{name}` | Create a lounge (returns its 4-digit code) |
| `GET /api/lounges/{code}` · `POST /api/lounges/{code}/join` | Look up or join a lounge |
| `GET /api/admin/state` | Admin snapshot |
| `POST /api/admin/game` | Create a game |
| `POST /api/admin/game/status` `{status}` | `LIVE` / `FINAL` |
| `POST /api/admin/play/open` `{down, distance, window_seconds}` | Open the next play |
| `POST /api/admin/play/lock` · `/resolve` `{play_type, direction, yardage?, yards?}` · `/void` | Drive the play (resolve needs `yardage`, `yards` or both) |
| `POST /api/admin/play/correct` `{play_id, play_type, direction, yardage?, yards?}` | Fix a scored play: re-scores every pick, moves each total by the difference, broadcasts `play_corrected` |
| `POST /api/admin/feed/<action>` · `GET /api/admin/feed/games?date=` · `GET /api/admin/feed/log` | Live data (see [Live data protocol](#live-data-protocol)) |
| `GET /api/admin/players[?q=]` | Everyone signed up (or whose name contains `q`), newest first: `{count, players:[{id, username, total_score, game_score, picks, online, created_at}], blocked_names}` |
| `POST /api/admin/player/remove` `{user_id, block?}` | Remove a player (and with `block` their name) → `{removed, blocked}`; `404` if they are already gone |
| `POST /api/admin/name/unblock` `{name}` | Let a blocked name be used again → `{unblocked}`; `404` if it isn't blocked |
| `POST /api/admin/announce` `{text}` | Show a banner to every player (≤ 200 chars; empty clears it) → `{id, text, sent_to}` |
| `GET /rules` | Rules of the Game (HTML) |
| `GET /privacy` · `GET /support` | Privacy policy and support pages (HTML; use as the App Store privacy policy and support URLs) |

Interactive docs: <http://127.0.0.1:8000/docs>.

**Data feeds.** The built-in [live data](#live-data-tank01) does exactly this for Tank01. A different feed can
still drive the game through the admin API: open a play before the snap, lock it, then resolve it with the
feed's play type, direction and `yards` (the server picks the distance bucket). Feeds report direction in
different ways (some by field side, some by the offense's left/right), so convert to the QB's view looking
downfield before sending. NFL play-by-play `run_location` / `pass_location` values `left` / `middle` / `right`
map straight to `LEFT` / `MIDDLE` / `RIGHT`.

**App Store.** App Review needs a privacy policy URL, a support URL, in-app account deletion and
filtering of user-visible names. Point App Store Connect at `https://<your server>/privacy` and
`/support` (set `PTP_CONTACT_EMAIL` first); the iOS app deletes accounts with `DELETE /api/me` (Settings → **Change name or
delete account**; on the website it is the same button under the scores, because deleting the account is how a name is
changed); and offensive usernames and lounge names are rejected with "Please choose a different name."
(`models.is_offensive_name`).

### Data model

| Table | Key columns |
| --- | --- |
| `games` | id, home/away name, home/away primary + secondary hex colors, status (`SCHEDULED`/`LIVE`/`FINAL`); live data: `feed_game_id`, `feed_auto_score`, `feed_auto_open`, `feed_paused`, `feed_cursor`, `feed_requests`, `feed_cap_extra` |
| `plays` | id, game_id, play_number, down, distance (down-and-distance, e.g. `7`), state (`OPEN`/`LOCKED`/`RESOLVED`), correct_play_type, correct_direction, correct_yardage (`SHORT`/`MEDIUM`/`LONG`/`LOSS`), yards_gained, voided, locks_at, `resolved_by` (`host`/`feed`/`void`/`host-fix`; NULL on old rows), `feed_text` |
| `users` | id, username (unique, case-insensitive), token, total_score |
| `predictions` | user_id, play_id (unique together), play_type, direction, yardage (`SHORT`/`MEDIUM`/`LONG`; NULL for older picks), points_earned |
| `lounges` / `lounge_members` | id (= 4-digit code), name, host_user_id; membership join table |
| `blocked_names` | name (case-insensitive), created_at: names the host removed and blocked; `create_user` refuses them |
| `feed_log` | Live-data recorder: id, game_id, ts, kind, play_id, feed_index, data (JSON); the last 5,000 rows per game |
| `feed_usage` | Live-data requests per UTC day and the plan's last-seen allowance, so caps survive restarts |

Upgrading keeps your data: on start-up `Store` adds any columns an older `game.db` is missing
(`models.MIGRATIONS`), for example the distance columns on a database kept on a host's disk. Picks made
before the upgrade have no distance and score it as wrong; scores already earned are unchanged. A
database from before Left/Middle/Right has `CENTER` in the `plays` and `predictions` CHECK constraints;
SQLite can't change those in place, so `Store` rebuilds the two tables once (in one transaction, keeping
every row, id and index) and turns stored `CENTER` values into `MIDDLE`. The live-data release adds its
columns and tables the same way (`games` and `plays` gain columns, `feed_log` and `feed_usage` are new); old
rows keep working and read `NULL` / the defaults (auto-score on, auto-open off).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PTP_ADMIN_KEY` | `admin` | Admin console key. **Change it** before inviting real players. |
| `PTP_DB_PATH` | `game.db` next to `app.py` | SQLite file location |
| `PTP_PREDICTION_WINDOW` | `15` | Default seconds a play stays open |
| `PTP_CONTACT_EMAIL` | unset | Contact address shown on `/privacy` and `/support` (unset: they point to the App Store listing) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address when using `python app.py` (`--phone` binds `0.0.0.0`; `--port` overrides `PORT`) |
| `PTP_RELOAD` | unset | Set to `1` for auto-reload during development |
| `TANK01_API_KEY` and friends | unset / see [Settings](#settings) | Live data: the key, the request caps, the delays. The key is a secret |

## Tests

```bash
venv/bin/python -m pip install -r requirements-dev.txt   # Windows: .\venv\Scripts\python.exe -m pip ...
venv/bin/python -m pytest -q                             # Windows: .\venv\Scripts\python.exe -m pytest -q
```

The suite covers every scoring combination over all three picks and the all-three bonus (including
losses and picks with no distance), yards-to-distance boundaries, resolve validation, the `CENTER`
alias, migrating databases from every earlier version, the Rules page and its links, the team presets, the play state machine, late-pick rejection, voids, ties, lounges, the
trademark filter, and full end-to-end flows over the real HTTP and WebSocket endpoints (including the
auto-lock timer).

Live data is tested without ever touching Tank01: a fake Tank01 (a local HTTP server, `tests/fakefeed.py`) serves a
real recorded game and real response fixtures, and a manual clock makes the polling schedule run in
milliseconds. The suite covers the play-by-play parser (on the whole recorded game, many other phrasings, and fuzzed input
that must never raise), the poller (zero requests while idle, the schedule, matching in order, orphans, voids, restarts,
caps, errors and backoff, pause/resume/check now, auto-score, hold, auto-open), a full-game replay through the real
controller with picks scored against `score_prediction`, the admin socket and REST protocol, migrations, Fix result, and the
API key never appearing in any output.

The iOS app's contract tests decode real server messages saved in `ios/PickThePlayTests/Fixtures/`.
After changing what the server sends, regenerate them from a real running server (temporary database,
random port): `venv/bin/python tests/capture_ios_fixtures.py` (player messages) and
`venv/bin/python tests/capture_ios_admin_fixtures.py` (host console messages and REST payloads).

## Legal & branding safeguards

- No official league names, club nicknames, logos or wordmarks ship with the app.
- The server rejects team names containing league marks or club nicknames (`models.PROTECTED_MARKS`).
  Use city or region identifiers instead.
- Teams are shown as a city or region name and generated initials (CHI, DET, NY, ...) on team colors,
  with no nicknames or logos, and a "not affiliated" notice on the player app. The presets live in
  `teams.py`.
- Free to play, with no wagering or prizes. If you add prizes, check sweepstakes and contest rules
  where you operate.

## Production notes and next steps

- **Single process.** Connected sockets are tracked in memory, so run one worker
  (`uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1`). To scale out, move broadcasts to Redis pub/sub and SQLite to Postgres.
- Serve behind HTTPS so sockets use `wss://`. The client picks `ws`/`wss` automatically.
- Accounts are device tokens (localStorage). Add Sign in with Apple or email login for cross-device play.
- Lounge codes are 4 digits by design (easy to share, but guessable). Add host approval or longer
  codes if lounges need to be private.
- **Native iOS.** The web app is already mobile- and PWA-ready. Wrap it with Capacitor or a SwiftUI
  `WKWebView` shell for the App Store, adding push notifications ("Play is open!") and haptics.
