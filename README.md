# Pick the Play: Live Pro Football Game

A real-time, second-screen prediction game for live pro football. Before every snap, players have
**15 seconds** to call the play — **Run or Pass** and **Left, Center or Right** — then watch points
and leaderboards update the instant the admin scores the play.

The MVP is a single Python FastAPI server with three web surfaces, synchronised over WebSockets:

| Surface | URL | Who |
| --- | --- | --- |
| **Live Player App** | `/` | Fans. Mobile-first, dark mode, installable to the iPhone home screen. |
| **Head-to-Head Lounges** | `/lounge/<4-digit code>` | Friends playing each other with a private leaderboard. |
| **Admin Console** | `/admin` | The operator watching the game and driving each play. |

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

1. **Create Game.** Enter generic team identifiers (e.g. *Chicago* at *Green Bay*) and pick
   primary/accent colors. Official league marks and club nicknames are rejected.
2. **Open Next Play.** Set down and distance, then open. Every player gets the pick grid and a
   synchronised 15-second countdown. (The game goes LIVE automatically on the first play.)
3. **Lock Predictions.** Lock early, or let the timer lock it automatically. Late picks are rejected
   by the server.
4. **Resolve & Score Play.** Select the actual type and direction. Points are calculated, totals and
   leaderboards update, and every player sees their result animation.
5. Repeat. Use **Void play** for penalties or no-plays (nobody scores). **End Game** marks it FINAL.

Keyboard shortcuts in the console: `O` open · `L` lock · `R`/`P` run/pass ·
`←` `↑` `→` left/center/right · `Enter` resolve.

### Play state machine

```
            Open Next Play             Lock (admin or 15 s timer)        Resolve & Score
  (idle) ─────────────────▶  OPEN  ─────────────────────────────▶ LOCKED ──────────────────▶ RESOLVED
                               │                                     │
                               └──────────── Void play ──────────────┴──▶ RESOLVED (voided, 0 pts)
```

Only one play per game can be OPEN or LOCKED at a time (enforced by a partial unique index).

## Scoring

| Prediction vs. actual | Points |
| --- | --- |
| Exact match (type **and** direction) | **+30** |
| Correct play type only | +10 |
| Correct direction only | +10 |
| Neither | 0 |

Ties share a rank (1, 2, 2, 4). Within a tie, more exact hits sort first.
The live leaderboard ranks points in the current game; *Season pts* is the user's all-time total.

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
├── models.py           # SQLite schema, Store (data access), validation, scoring engine
├── static/
│   ├── css/style.css   # Dark, mobile-first styles shared by all pages
│   ├── js/common.js    # DOM helpers, reconnecting WebSocket, server-clock sync
│   ├── js/player.js    # Player app + lounges
│   ├── js/admin.js     # Admin console
│   ├── img/icon.svg
│   └── manifest.webmanifest
├── templates/          # Jinja2: base.html, player.html, admin.html
├── tests/              # pytest: scoring, data layer, HTTP + WebSocket end-to-end
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
| → | `{"type":"predict","play_id":7,"play_type":"PASS","direction":"LEFT"}` (repeat to change the pick while OPEN) |
| → | `{"type":"sync"}` · `{"type":"ping"}` |
| ← | `{"type":"state","event":"play_opened"\|"play_locked"\|"play_resolved"\|"play_voided"\|"game_created"\|"game_status"\|"lounge_updated"\|"sync", …snapshot}` |
| ← | `{"type":"prediction_saved","prediction":{…}}` · `{"type":"error","message":"…"}` |

**Admin — `/ws/admin`**

| Direction | Message |
| --- | --- |
| → | `{"type":"auth","key":"…"}` first |
| → | `{"action":"create_game"\|"set_status"\|"open_play"\|"lock_play"\|"resolve_play"\|"void_play","request_id":1, …payload}` |
| ← | `{"type":"admin_state", …}` with live pick stats, players online, leaderboard and play log |
| ← | `{"type":"admin_ack","request_id":1,"ok":true\|false,"error":"…"}` |

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
| `POST /api/predictions` | Submit a pick (HTTP fallback when the socket is down) |
| `POST /api/lounges` `{name}` | Create a lounge (returns its 4-digit code) |
| `GET /api/lounges/{code}` · `POST /api/lounges/{code}/join` | Look up or join a lounge |
| `GET /api/admin/state` | Admin snapshot |
| `POST /api/admin/game` | Create a game |
| `POST /api/admin/game/status` `{status}` | `LIVE` / `FINAL` |
| `POST /api/admin/play/open` `{down, distance, window_seconds}` | Open the next play |
| `POST /api/admin/play/lock` · `/resolve` `{play_type, direction}` · `/void` | Drive the play |
| `GET /privacy` · `GET /support` | Privacy policy and support pages (HTML; use as the App Store privacy policy and support URLs) |

Interactive docs: <http://127.0.0.1:8000/docs>.

**App Store.** App Review needs a privacy policy URL, a support URL, in-app account deletion and
filtering of user-visible names. Point App Store Connect at `https://<your server>/privacy` and
`/support` (set `PTP_CONTACT_EMAIL` first); the iOS app deletes accounts with `DELETE /api/me`; and
offensive usernames and lounge names are rejected with "Please choose a different name."
(`models.is_offensive_name`).

### Data model

| Table | Key columns |
| --- | --- |
| `games` | id, home/away name, home/away primary + secondary hex colors, status (`SCHEDULED`/`LIVE`/`FINAL`) |
| `plays` | id, game_id, play_number, down, distance, state (`OPEN`/`LOCKED`/`RESOLVED`), correct_play_type, correct_direction, voided, locks_at |
| `users` | id, username (unique, case-insensitive), token, total_score |
| `predictions` | user_id, play_id (unique together), play_type, direction, points_earned |
| `lounges` / `lounge_members` | id (= 4-digit code), name, host_user_id; membership join table |

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PTP_ADMIN_KEY` | `admin` | Admin console key. **Change it** before inviting real players. |
| `PTP_DB_PATH` | `./game.db` | SQLite file location |
| `PTP_PREDICTION_WINDOW` | `15` | Default seconds a play stays open |
| `PTP_CONTACT_EMAIL` | unset | Contact address shown on `/privacy` and `/support` (unset: they point to the App Store listing) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address when using `python app.py` (`--phone` binds `0.0.0.0`; `--port` overrides `PORT`) |
| `PTP_RELOAD` | unset | Set to `1` for auto-reload during development |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite covers every scoring combination, the play state machine, late-pick rejection, voids,
ties, lounges, the trademark filter, and full end-to-end flows over the real HTTP and WebSocket
endpoints (including the auto-lock timer).

## Legal & branding safeguards

- No official league names, club nicknames, logos or wordmarks ship with the app.
- The server rejects team names containing league marks or club nicknames (`models.PROTECTED_MARKS`).
  Use city or region identifiers instead.
- Teams are shown as generated initials on custom hex colors (defaults are generic green/yellow vs.
  navy/orange shades), with a "not affiliated" notice on the player app.
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
