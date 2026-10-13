# Pick the Play: notes for the next Claude session

Read this first. It is the hand-over from the previous session (Oct 6 to Oct 13, 2026). README.md is the long
reference; TESTFLIGHT.md covers the iPhone build pipeline; IPHONE_GUIDE.md is an older guide for the owner.

## The owner and how to work with them

- The owner (GitHub `ctjoey`) is **not technical**. Answers must be short, plain and fast. No jargon, no long
  option lists. Give a recommendation, not a survey.
- Develop on branch **`claude/epic-planck-3jvrsr`** of `ctjoey/PTP` (private repo). Push only there. No pull requests
  unless asked.
- **A push to that branch redeploys the live server on Render (a restart of 1 to 2 minutes).** Never push during a
  live game unless the owner says so. NFL games: Thursday ~8:15 PM ET, Sunday 1 PM / 4:25 PM / 8:20 PM ET, Monday
  ~8:15 PM ET. Check `date -u` first. The session's auto-mode guard may block a push as a "production deploy" until
  the owner explicitly says "push".
- **TestFlight builds only when the owner asks** (they did for build 41).
- Never put the admin key, the Tank01 key or any secret in chat, code, docs or commits. They live in Render's
  environment and in GitHub Actions secrets.
- Commit messages end with the attribution lines the session's system reminder gives (Co-Authored-By plus the
  Claude-Session URL).
- Post-game requests the owner calls "for the list" go into the Claude Docs doc **"Pick the Play: After the game"**
  (doc id `6806daf9-e5c4-4b1f-ad48-a8fa8c4d9fb1`, node `787eb925-52b1`), not into code, unless they say to build.
  A helper checklist doc also exists (id `0c5e3f69-1ada-44b6-8471-e450363dfd30`).

## What the project is

**Pick the Play: Live Pro Football Game.** Friends predict each NFL play while they watch the game live: before
the snap they pick **Run or Pass**, **direction** (Left / Middle / Right, as the QB looks downfield) and **distance**
(Short 0-5 / Medium 6-10 / Long 11+ yards; a loss matches nothing). Points per play: 10 for each part right, +10
bonus for all three ("Perfect call", 40, "You picked the play"). A sack, a QB scramble or a penalty "No Play" is
**no play, no points**. A **host** runs the game: opens each play (a 5 to 60 second pick window, default 15),
the play locks, and the result is scored either by the host or automatically from a live NFL play-by-play feed.
Players use the website or the native iPhone app. There are season totals, Head to Head lounges (4-digit codes),
a practice mode and host tools.

**Goal right now:** make the live game feel live. The app was late and got stuck in the first two real games
because the paid feed (Tank01) publishes each play 30 to 70 seconds late and the app waited for it. The owner is
comparing Tank01 with ESPN's free feed to pick the faster one.

## Stack and layout

- **Server:** Python 3.11+ (3.14 recommended; the cloud container's venv is 3.12), FastAPI + Uvicorn, WebSockets,
  SQLite (stdlib `sqlite3`), Jinja2. No ORM, no JS build step.
- **Website:** server-rendered templates plus plain JavaScript modules.
- **iPhone app:** SwiftUI, iOS 17+, Swift 5 mode, generated with XcodeGen (`ios/project.yml`). Built, tested and
  uploaded to TestFlight only by GitHub Actions on macOS runners (the owner has no Mac).
- **Hosting:** Render (paid plan, 1 GB persistent disk) at **https://pick-the-play.onrender.com**.

```
app.py            FastAPI app: Settings (env vars), GameController (all game actions, broadcasts), REST + WebSocket
                  routes, ADMIN_ACTIONS (admin socket actions), public_game/public_play, create_app(), CLI (`python app.py`)
models.py         Store (SQLite schema, migrations, plays/predictions/lounges/feed log/usage), scoring
                  (score_prediction, resolve_yardage), GameError, PlayState, GameStatus
feed.py           Live data. Tank01Client (HTTP), DemoFeed (recorded game for practice), LiveFeed (poller, matcher,
                  suggestions, auto-score, auto-open, caps, recorder, state()), FeedTiming + TimingRow (Tank01 vs ESPN timing)
espn.py           ESPN unofficial API: EspnClient (scoreboard, summary), summary_plays/EspnPlay, find_event,
                  play_key/tank01_play (cross-feed play keys), parse_wallclock. Written to be reusable as a real source.
playparse.py      Reads one play-by-play entry: classify() -> Parsed(kind skip|void|play|review, type, direction, yards...),
                  parse_down_and_distance, next_down_and_distance, next_down_after_void (sacks, scrambles, penalties)
teams.py          Team presets (city names and colours only, no NFL marks), feed_team/feed_matchup
templates/        base.html, player.html (player app + welcome + lounges), admin.html (host console), rules.html,
                  support.html, privacy.html, _theme_toggle.html
static/js/        common.js (PTP helpers incl. renderScorebug), player.js, practice.js, admin.js (host console core),
                  admin-feed.js (Live data card, suggestion card, game clock bar, Feed timing), admin-players.js
static/css/style.css
demo/box_CAR_WSH_20241020.json   recorded Tank01 game used by the practice game and the tests
tests/            pytest suite (~964 tests). fakefeed.py = fake Tank01 + fake ESPN HTTP servers, manual Clock, Rig/run_rig
                  helpers. capture_ios_fixtures.py / capture_ios_admin_fixtures.py regenerate iOS JSON fixtures.
ios/PickThePlay/  App/ (PickThePlayApp, AppState, HostState, HostDrafts, ScreenshotMode), Models/ (Models, AdminModels,
                  HostLogic, TeamPresets), Services/ (APIClient, LiveConnection, AdminConnection, Keychain, Practice),
                  Views/ (LiveView, LeaderboardView, LoungesView, PracticeView, RulesView, SettingsView, OnboardingView,
                  Components incl. ScorebugView, Theme) and Views/Host/ (HostView, HostRunView, HostLiveDataView, HostLogView,
                  HostPlayersView, HostMessageView, HostKit)
ios/PickThePlayTests/   XCTest: ContractTests (decode real server JSON in Fixtures/), GameLogicTests, HostGuidanceTests,
                  FeedTimingTests, GameClockTests, PlayerScreenTests
ios/scripts/      make_icons.py, make_team_presets.py, normalize_icon.py
.github/workflows/ios.yml                builds + tests on every push touching ios/**; TestFlight on manual dispatch
.github/workflows/publish-screenshots.yml   publishes App Store screenshots to their own branch
```

## Run, build, test

```bash
# Server (from the repo root)
python3.14 -m venv venv && venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python app.py                 # http://127.0.0.1:8000/ (player) and /admin (host, default key "admin")
venv/bin/python app.py --phone         # binds 0.0.0.0 so a phone on the same Wi-Fi can join

# Tests (all must pass before any push; ~1 to 1.5 minutes)
venv/bin/python -m pytest tests -q -p no:cacheprovider
node --check static/js/*.js            # after any JS change
```

- The tests never touch Tank01 or ESPN: `tests/fakefeed.py` serves the recorded game and fixtures, and a manual
  clock (`rig.step(seconds)`, `rig.until(predicate, step=...)`) drives the poller. Use `step=0.5` when checking
  2.5 s schedules.
- **iPhone:** there is no Mac. Compile and test happen only in GitHub Actions (`ios.yml`, job "Build + test
  (Simulator)" on `macos-26` with the newest Xcode). A push touching `ios/**` starts it. To ship to TestFlight,
  dispatch `ios.yml` on the branch with input `testflight: true` (GitHub MCP tool `actions_run_trigger`,
  method `run_workflow`). The build number is the run number. A new push cancels an in-progress run on the same branch.
- The previous session also checked Swift on Linux with a hand-made SwiftUI/UIKit stub and a downloaded Swift
  toolchain in its scratch folder. **That harness is not in the repo and is gone.** Rebuild it only if needed;
  otherwise rely on CI. Linux Swift has no Objective-C runtime (no `#selector`).
- Browser checks in the cloud container: Playwright from `/opt/node22/lib/node_modules/playwright`, Chromium in
  `/opt/pw-browsers` (never run `playwright install`). Run the real app against the fakes with a small script that
  builds `app.create_app(Settings(..., tank01_base_url=<fake>))`.
- The cloud container cannot reach `pick-the-play.onrender.com` or ESPN (the proxy answers 403), and WebFetch
  cannot resolve ESPN either. Real-world checks happen only on Render during a game.

## What is finished and working (all pushed; server live on Render)

- Core game: create game, open / lock (timer or host) / resolve / void / fix result, scoring, season totals,
  leaderboard, Head to Head lounges, practice mode (web + iPhone), Rules page, account deletion
  ("Change name or delete account"), name filter, privacy/support pages, light/dark theme.
- Host tools: remove a player (optionally block the name), message banner to players, players list.
- iPhone app: player screens plus a full **host console** (Settings, Open the host console; admin key in the
  Keychain; only ever sent to the built-in server: `ServerConfig.allowsAdminKey`, `KeepAdminKeyOnTheServer`).
- Live data (Tank01): polls only while a play is locked; matches feed entries to plays in order with a
  down-and-distance check; clean plays auto-score after 4 s (`TANK01_AUTO_SCORE_GRACE`); optional auto-open of
  the next play (`feed_auto_open` per game; the host turns it on in Settings and details); caps and budget meter;
  pause/resume; Check now; Hold / Change / Skip; feed log download.
- Fixes from the first two live games (Oct 8 and Oct 12):
  - First play opened before kickoff is matched to the first snap when Tank01 goes live late
    (`LiveFeed._baseline`, `Pending.waited_for_start`, `_skip_to_horizon`, `DISTRUST_LATE_START`).
  - Check now catches up to the newest play (`_consume(catch_up=True)`, `DISTRUST_CAUGHT_UP`) and fills Down /
    To go from where the feed ended.
  - After a no-play the game moves on: sacks/scrambles advance by their yards, penalties replay the down with the
    yardage (automatic first down where the rules give one) (`playparse.next_down_after_void`, `_advance`).
  - After a touchdown the next play is 1st & 10 and auto-open waits for the kickoff (`AFTER_A_SCORE`).
  - Clear guidance when live data can't score a locked play: amber "SCORE BY HAND" chip and a hint line, on the
    website and the iPhone (`lockedPlayIsLeftToHost` in HostLogic.swift).
  - Game clock bar under the scorebug (website and iPhone): feed's newest play, how fresh it is, the play the
    app is on, catch-up/waiting line, live clock (`LiveFeed._note_clock`, `_clock_view`, `feed.state()["clock"]`).
  - Live score beside the team names on every screen (`GameController._with_score`, `game.home_score/away_score/
    score_at`); phones are pushed a refresh when the score changes.
- Wording requested by the owner: points rows "Pick correct play / direction / distance", "Pick all 3 correctly",
  "You picked the play" under the 40 and on a perfect result; "Head to Head" naming everywhere.
- **Feed timing: Tank01 vs ESPN** (commit `c135f1a`): while a game is linked to Tank01, the server also reads ESPN
  every 4 s (only while a play waits, plus 90 s after) and records when each feed first showed each play. Shown
  under Settings and details on both consoles (`feed.state()["timing"]`), every sighting written to the feed
  log (`timing_seen`, `timing`, `espn` rows). It never changes the game and adds no Tank01 requests.
  `ESPN_COMPARE=0` turns it off.
- TestFlight: build 39 (Oct 9) has the after-the-game list. **Build 41** was dispatched Oct 13 for commit
  `fde1898` (feed timing, iPhone game clock, 1st & 10 after a touchdown). Its build step passed on the real SDK.
  Check that the upload finished (`actions_list` with `resource_id ios.yml`, `perPage 2`).

## In progress right now

- No half-done code. The working tree is clean apart from this file.
- TestFlight build 41 (run id 38392188569) was still running when this was written.
- The Tank01 vs ESPN comparison has **never seen real ESPN data**. It was built from ESPN's community-documented
  JSON shape (`drives.previous[].plays[]`, `start.downDistanceText`, `clock.displayValue`, `period.number`,
  `wallclock`). Its first real run is **Thursday Oct 15, 2026**.

## Next steps, in priority order

1. **After Thursday's game (Oct 15):** read Feed timing on the console and have the owner download the feed log
   (website console, Settings and details, Download feed log; or `GET /api/admin/feed/log?game_id=N&download=1`
   with the `X-Admin-Key` header). If the section only ever said "Looking for this game on ESPN..." or listed plays
   with ESPN "-" all the way down, the play keys don't match: compare `timing_seen` rows from both sources and
   fix `espn.play_key` / `tank01_play` / `EspnPlay`.
2. **Decide ESPN or Tank01** with the owner. If ESPN is faster and steady, make it a real live source: add a
   source `"espn"` to `LiveFeed` beside `"tank01"` and `"demo"` (reuse `EspnClient`, `summary_plays`), map ESPN plays
   into what `playparse.classify` expects (or teach it ESPN's wording, using `type.text`), pick games from ESPN's
   scoreboard, and keep Tank01 as a backup if ESPN is unsteady. ESPN is unofficial with no terms or guarantees,
   which is a risk for a public App Store app.
3. **Stop waiting for the feed before opening the next play** ("pipeline mode"): the app opens one play at a time
   and each waits for its result, so with a 30 to 70 s feed delay the app falls behind every play. Let the next
   play open while earlier ones still wait for their results; points arrive late, but picks stay live.
   This is the real fix for lateness and the biggest change on the list.
4. Owner tasks to remind them of: change `PTP_ADMIN_KEY` on Render if a helper had it; submit the App Store update
   (build 41 or later; review notes text in TESTFLIGHT.md mentions the host console); decide whether to reword
   "not affiliated with the NFL or any club" (left as is).
5. Optional, parked: season-points reset button, live chat/reactions, a domain name.
6. Low-priority hardening noted by reviews: give `EspnClient` its own thread pool or a total deadline (urllib's
   timeout is per socket step); light theme "unfair" Feed timing rows use `var(--muted)`.

## Key decisions and why

- **The host drives the game; live data only suggests.** Nothing blocks the host: any play can be scored or voided
  by hand at any time.
- **Tank01 is polled only while a play is locked** (zero requests between plays) because the Basic plan is 1,000
  requests a month. On Oct 13 the plan had **297** left. Polling defaults were halved at the owner's request:
  first check 5 s, then every 2.5 s for the first minute, 5 s to 3 min, 10 s to 10 min, 15 s after
  (`INTERVALS`, `SLOW_INTERVAL`, `TANK01_FIRST_DELAY`, `TANK01_FAST_INTERVAL`), "not started" retried every 60 s.
  More checks do not make Tank01 faster.
- **Owner preferences for speed:** clean plays auto-score after 4 s, auto-open opens the next play at once
  (`TANK01_OPEN_DELAY=0`). The owner declined a gentler polling schedule ("extra checks are from me pressing
  Check now").
- Matching is in order with a down-and-distance check; a mismatch goes to the host as "Needs your check" (review),
  never auto-scored. Auto-open never runs on 4th down (punts and field goals are skipped by the feed), after a
  touchdown, or after a review the host changed.
- Sacks and QB scrambles are **no play** (no points) because nobody could call the throw; the game still moves on by
  their yards. "Thrown away to avoid a sack" stays a throw for the host to check.
- The iPhone host console mirrors the website console's wording exactly (shared strings are listed in the code
  comments of `HostLogic.swift` / `admin-feed.js`).
- The admin key is only ever sent to the app's built-in server (testers can change the server address in Settings).
- ESPN is compared in the shadow first (no effect on the game) because it is unofficial and its play text differs
  from Tank01's; the switch is the owner's call after real numbers.

## Known problems, gotchas, things that didn't work

- **Tank01 is slow:** 30 to 70 s per play in both live games, and on Oct 8 it answered "Game hasn't started yet"
  for minutes after kickoff.
- **The app falls behind** when every play waits for the feed: the cycle (pick window + feed delay + grace) is longer
  than the time between plays. Check now catches up; the real fix is next step 3.
- A locked **4th-down play never gets a result** if it is a punt or field goal (the feed's entry is a "skip"): the
  host must Void it. Auto-open does not open 4th downs for this reason.
- **A server restart loses in-memory live-data state:** the joined-late notice, the first-play horizon, the
  feed-timing table and the last live score. The cursor and locked plays survive (`LiveFeed.attach`). Render
  restarts on every push.
- The host's Down / To go must match the TV for matching to auto-score; after hand-scored plays the form is not
  prefilled (the feed has not published the play yet).
- The iPhone lounge keyboard fix (Done key, tap/scroll to dismiss, Back to the game) and the Timer box's Done key
  have not been checked on a real device.
- Private repo: macOS Actions minutes cost money (~10 to 30 min per run). Each push to `ios/` starts a build.
- GitHub MCP `actions_list` ignores `per_page`: use `perPage`, and filter by `resource_id ios.yml`.
- In the container, `pkill -f serve.py` kills the calling shell too: use a pattern like `pkill -f "[s]erve.py"`, or
  find the PID with `lsof -iTCP:<port> -sTCP:LISTEN`.
- Push and production actions can be refused by the session's auto-mode guard until the owner says "push".

## Services and environment (names only, no values)

- **Render:** web service running `app.py`, auto-deploys from `claude/epic-planck-3jvrsr`. Persistent disk mounted for
  the database. Environment: `PTP_DB_PATH`, `PTP_ADMIN_KEY`, `TANK01_API_KEY`, `TANK01_MAX_REQUESTS_PER_GAME` (set to
  600), optional `PTP_PREDICTION_WINDOW`, `PTP_CONTACT_EMAIL`. Live-data tuning env vars override the code defaults
  (`TANK01_FIRST_DELAY`, `TANK01_FAST_INTERVAL`, `TANK01_AUTO_SCORE_GRACE`, `TANK01_OPEN_DELAY`, `TANK01_RESERVE`,
  `TANK01_MAX_REQUESTS_PER_DAY`, `TANK01_ALLOW_OVERAGE`, `TANK01_TIMEOUT`), and `ESPN_COMPARE`, `ESPN_BASE_URL`,
  `ESPN_INTERVAL` control the ESPN comparison. See README "Settings" and "Configuration".
- **Tank01 NFL API** via RapidAPI (Basic plan, 1,000 requests a month). Endpoints used: `getNFLBoxScore`
  (play-by-play) and `getNFLGamesForDate` (the "Pick today's game" schedule).
- **ESPN** public site API (no key): `site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD`
  and `/summary?event=<id>`.
- **GitHub Actions** (repo `ctjoey/PTP`): variables `BUNDLE_ID_PREFIX`, `PTP_SERVER_URL`; secrets `APPLE_TEAM_ID`,
  `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (App Store Connect API key for signing and TestFlight upload).
- **App Store Connect / TestFlight:** app "Pick the Play"; build number = Actions run number.
