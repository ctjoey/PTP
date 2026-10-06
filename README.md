# Pick the Play: Live Pro Football Game

A real-time, second-screen prediction game for live pro football. Before every snap, players have
**15 seconds** to call the play — **Run or Pass**, **Left, Middle or Right** (as the QB looks
downfield) and **how far: Short, Medium or Long** — then watch points and leaderboards update the
instant the admin scores the play. For example: *Run, Left, Short*. Each right call is worth 10
points, and getting all three right adds a 10-point bonus: a perfect call is 40.

The MVP is a single Python FastAPI server with three web surfaces, synchronised over WebSockets:

| Surface | URL | Who |
| --- | --- | --- |
| **Live Player App** | `/` | Fans. Mobile-first, dark mode, installable to the iPhone home screen. |
| **Head-to-Head Lounges** | `/lounge/<4-digit code>` | Friends playing each other with a private leaderboard. |
| **Admin Console** | `/admin` | The operator watching the game and driving each play. |
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
2. **Open Next Play.** Set down and distance (e.g. 3rd & 7), then open. Every player gets the pick
   grid and a synchronised 15-second countdown. (The game goes LIVE automatically on the first play.)
3. **Lock Predictions.** Lock early, or let the timer lock it automatically. Late picks are rejected
   by the server.
4. **Resolve & Score Play.** Select what actually happened: the play type, the direction **as the QB
   looks downfield** (the offense's left and right, not the TV picture's; see
   [Left, Middle and Right](#left-middle-and-right)), and the distance:
   **Short** (0-5 yards), **Medium** (6-10), **Long** (11+) or **Loss** (negative yards). Or type the
   **yards gained** (e.g. `7`, or `-4` for a sack) and the distance is picked for you; an incomplete
   pass or no gain is 0 yards = Short. Points are calculated, totals and leaderboards update, and every
   player sees their result animation.
5. Repeat. Use **Void play** for penalties or no-plays (nobody scores). **End Game** marks it FINAL.

Keyboard shortcuts in the console:

| Key | Action |
| --- | --- |
| `O` / `L` | Open the next play / lock predictions |
| `R` / `P` | Run / pass |
| `←` `↑` `→` | Left / middle / right (as the QB looks downfield) |
| `S` / `M` / `G` / `X` | Short / medium / long / loss |
| `Y` | Type the yards gained (then `Enter` resolves) |
| `Enter` | Resolve & score the play |

### Play state machine

```
            Open Next Play             Lock (admin or 15 s timer)        Resolve & Score
  (idle) ─────────────────▶  OPEN  ─────────────────────────────▶ LOCKED ──────────────────▶ RESOLVED
                               │                                     │
                               └──────────── Void play ──────────────┴──▶ RESOLVED (voided, 0 pts)
```

Only one play per game can be OPEN or LOCKED at a time (enforced by a partial unique index).

## Scoring

Each play has three picks. Each one you get right is worth **10 points**, and getting all three right
adds a **10-point bonus**: +10 play type, +10 direction, +10 distance, +10 bonus for all three = 40.

| Pick | Choices | Points if right |
| --- | --- | --- |
| Play type | Run · Pass | +10 |
| Direction (as the QB looks downfield) | Left · Middle · Right | +10 |
| Distance (total yards gained) | Short 0-5 yds · Medium 6-10 · Long 11+ | +10 |
| Bonus: all three right | | +10 |
| **Perfect call** | | **40** |

So a play scores 0, 10, 20 or 40 (30 can't happen). A **loss of yards** (a sack, a run stopped behind
the line) scores no distance points, and so no bonus: a loss earns at most 20. An incomplete pass or
no gain is 0 yards, which is Short. Example: you pick *Run, Left, Short* and it's a run to the left for
7 yards (Medium): **20 points**. Pick *Run, Left, Medium*: **40**. Points already scored before the
bonus existed stay as they were (an old 30 stays 30).

Ties share a rank (1, 2, 2, 4). Within a tie, more perfect calls (all three right) sort first.
The live leaderboard ranks points in the current game; *Season pts* is the user's all-time total.

### Left, Middle and Right

Directions follow the official NFL play-by-play (the full text, with diagrams, is on the
**Rules of the Game** page at `/rules`, linked from the player app, the welcome screen, the admin
console and the support page). Always from the offense's point of view, as the QB looks downfield:

- **Runs** go by run location and run gap. **Middle** is any run between the left and right guards
  (the A-gaps on either side of the center); **Left** / **Right** is at or outside a guard: guard,
  tackle or end on that side.
- **Passes** go by pass location, using the hash marks. **Middle** is between the hashes (18 ft 6 in
  apart); **Left** / **Right** is outside a hash, out to that sideline.
- A sack is a pass with a loss; a quarterback scramble is a run. When no direction is charted, the host
  makes the call. All official calls are derived from the official NFL statistics, and all final calls
  are at the host's discretion.

## Head-to-Head Lounges

From the player app, tap **H2H Lounges** to:

- **Create** a lounge. You become the host (👑) and get a 4-digit code plus a share link
  (uses the iOS share sheet where available).
- **Join** with a friend's 4-digit code, or open `/lounge/<code>` directly.

Inside a lounge you play the same live game, with a private leaderboard tab of every member's points.

---

## Project structure

```
├── app.py              # FastAPI server: routes, WebSocket hub, game controller, auto-lock timer
├── models.py           # SQLite schema + migrations, Store (data access), validation, scoring engine
├── teams.py            # Team presets for the admin: 32 city names with team colors
├── static/
│   ├── css/style.css   # Dark, mobile-first styles shared by all pages
│   ├── js/common.js    # DOM helpers, reconnecting WebSocket, server-clock sync
│   ├── js/player.js    # Player app + lounges
│   ├── js/admin.js     # Admin console
│   ├── img/            # app icons (generated by ios/scripts/make_icons.py)
│   └── manifest.webmanifest
├── templates/          # Jinja2: base.html, player.html, admin.html
├── tests/              # pytest: scoring, data layer, HTTP + WebSocket end-to-end; iOS fixture capture
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
| ← | `{"type":"state","event":"play_opened"\|"play_locked"\|"play_resolved"\|"play_voided"\|"game_created"\|"game_status"\|"lounge_updated"\|"sync", …snapshot}` |
| ← | `{"type":"prediction_saved","prediction":{"play_id":7,"play_type":"PASS","direction":"LEFT","yardage":"SHORT","points_earned":null}}` · `{"type":"error","message":"…"}` |

**Admin — `/ws/admin`**

| Direction | Message |
| --- | --- |
| → | `{"type":"auth","key":"…"}` first |
| → | `{"action":"create_game"\|"set_status"\|"open_play"\|"lock_play"\|"resolve_play"\|"void_play","request_id":1, …payload}` |
| → | e.g. `{"action":"resolve_play","request_id":2,"play_type":"RUN","direction":"LEFT","yardage":"MEDIUM","yards":7}` |
| ← | `{"type":"admin_state", …}` with live pick stats, players online, leaderboard and play log |
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
  (`bonus` = extra for all three right, `exact` = a perfect call), and `crowd` (hidden while OPEN)
  counts `RUN`, `PASS`, `LEFT`, `MIDDLE`, `RIGHT`, `SHORT`, `MEDIUM`, `LONG`, `total`, `exact` (picks
  with all three right) and `scored` (picks worth more than 0). Leaderboard `exact_hits` counts plays
  with all three right.

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
| `GET /rules` | Rules of the Game (HTML) |
| `GET /privacy` · `GET /support` | Privacy policy and support pages (HTML; use as the App Store privacy policy and support URLs) |

Interactive docs: <http://127.0.0.1:8000/docs>.

**Data feeds.** A play-by-play feed can drive the game through the admin API: open a play before the
snap, lock it, then resolve it with the feed's play type, direction and `yards` (the server picks the
distance bucket). Feeds report direction in different ways (some by field side, some by the offense's
left/right), so convert to the QB's view looking downfield before sending. NFL play-by-play
`run_location` / `pass_location` values `left` / `middle` / `right` map straight to `LEFT` / `MIDDLE` /
`RIGHT`.

**App Store.** App Review needs a privacy policy URL, a support URL, in-app account deletion and
filtering of user-visible names. Point App Store Connect at `https://<your server>/privacy` and
`/support` (set `PTP_CONTACT_EMAIL` first); the iOS app deletes accounts with `DELETE /api/me`; and
offensive usernames and lounge names are rejected with "Please choose a different name."
(`models.is_offensive_name`).

### Data model

| Table | Key columns |
| --- | --- |
| `games` | id, home/away name, home/away primary + secondary hex colors, status (`SCHEDULED`/`LIVE`/`FINAL`) |
| `plays` | id, game_id, play_number, down, distance (down-and-distance, e.g. `7`), state (`OPEN`/`LOCKED`/`RESOLVED`), correct_play_type, correct_direction, correct_yardage (`SHORT`/`MEDIUM`/`LONG`/`LOSS`), yards_gained, voided, locks_at |
| `users` | id, username (unique, case-insensitive), token, total_score |
| `predictions` | user_id, play_id (unique together), play_type, direction, yardage (`SHORT`/`MEDIUM`/`LONG`; NULL for older picks), points_earned |
| `lounges` / `lounge_members` | id (= 4-digit code), name, host_user_id; membership join table |

Upgrading keeps your data: on start-up `Store` adds any columns an older `game.db` is missing
(`models.MIGRATIONS`), for example the distance columns on a database kept on a host's disk. Picks made
before the upgrade have no distance and score it as wrong; scores already earned are unchanged. A
database from before Left/Middle/Right has `CENTER` in the `plays` and `predictions` CHECK constraints;
SQLite can't change those in place, so `Store` rebuilds the two tables once (in one transaction, keeping
every row, id and index) and turns stored `CENTER` values into `MIDDLE`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PTP_ADMIN_KEY` | `admin` | Admin console key. **Change it** before inviting real players. |
| `PTP_DB_PATH` | `game.db` next to `app.py` | SQLite file location |
| `PTP_PREDICTION_WINDOW` | `15` | Default seconds a play stays open |
| `PTP_CONTACT_EMAIL` | unset | Contact address shown on `/privacy` and `/support` (unset: they point to the App Store listing) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address when using `python app.py` (`--phone` binds `0.0.0.0`; `--port` overrides `PORT`) |
| `PTP_RELOAD` | unset | Set to `1` for auto-reload during development |

## Tests

```bash
venv/bin/python -m pip install -r requirements-dev.txt   # Windows: .\venv\Scripts\python.exe -m pip ...
venv/bin/python -m pytest -q                             # Windows: .\venv\Scripts\python.exe -m pytest -q
```

The suite covers every scoring combination over all three picks and the all-three bonus (including
losses and picks with no distance), yards-to-distance boundaries, resolve validation, the `CENTER`
alias, migrating databases from both earlier versions, the Rules page and its links, the team presets, the play state machine, late-pick rejection, voids, ties, lounges, the
trademark filter, and full end-to-end flows over the real HTTP and WebSocket endpoints (including the
auto-lock timer).

The iOS app's contract tests decode real server messages saved in `ios/PickThePlayTests/Fixtures/`.
After changing what the server sends, regenerate them from a real running server (temporary database,
random port): `venv/bin/python tests/capture_ios_fixtures.py`.

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
