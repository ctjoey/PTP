# Pick the Play — iOS (SwiftUI)

Native iPhone client for the Pick the Play game server (the FastAPI app at the repo root). It plays
the same live game as the web app over the same `/ws` WebSocket and REST API, plus an offline
Practice mode. Built and shipped to TestFlight by `.github/workflows/ios.yml` on GitHub's macOS
runners, exactly like GameDial. See [TESTFLIGHT.md](../TESTFLIGHT.md) for the one-time setup.

## Open in Xcode (optional, macOS)

```bash
brew install xcodegen
cd ios
xcodegen generate
open PickThePlay.xcodeproj
```

Set the game server for local runs in the scheme's build settings (`PTP_SERVER_URL`), or type it
in the app's Settings tab (press and hold the version number under About to reveal the Game server box; e.g. `192.168.1.20:8000` for `python app.py --phone` on your Wi-Fi).

## The game in the app

A play opens for 15 seconds and the player makes a three-part call, sent only once all three are
chosen (changing any part re-sends it while the play is OPEN):

| Part | Choices | Notes |
| --- | --- | --- |
| Play type | Run / Pass | |
| Direction | Left / Middle / Right | As the QB looks downfield (the offense's point of view) |
| Distance (`yardage`) | Short 0–5 yds / Medium 6–10 / Long 11+ | Total yards gained; an incomplete pass is 0 yds = Short |

Scoring (the server is the authority; `ScoreRules` mirrors it for Practice mode and the result view):
+10 pick correct play, +10 pick correct direction, +10 pick correct distance, +10 pick all 3 correctly = 40 (a
"perfect call", `exact` on the wire). The points table reads the same on every screen: *Pick correct play (Run /
Pass)*, *Pick correct direction (Left / Middle / Right)*, *Pick correct distance (Short / Medium / Long)*, *Pick all 3
correctly*, then *Perfect call* with the line "You picked the play" under it. Possible totals are 0, 10, 20 and 40; 30
can't happen. A loss of yards (`LOSS`) never matches a distance pick, so it scores no distance points and so no
bonus. Result labels follow the per-part right/wrong flags (falling back to points): all three "Perfect call!" (with
"You picked the play" under it), two "Two of three", one "One of three", none "No points this time". Plays scored
before the bonus keep their 30; they still read as a perfect call. The four rows are `Scoring.rows` and the
words under the pick panel are `Scoring.bonusLine`, so the welcome screen, the Rules tab, Settings and the pick
screen can't drift apart; `ScoreRules.perfectNote` is "You picked the play".

The live score sits beside each team in the scorebug (`CHI 17 @ 24 DET`). The server adds `home_score`,
`away_score` and `score_at` to the game (null until its live-data checks have read a score, and always for the
practice game), and the score can trail the TV by about a minute, so the player screens show just the number:
no spinner, no clock. `Game` reads all three leniently (a missing, null, text or out-of-range value means "no
score", never a decode error); `Game.liveScore` gives both numbers or nil (one team's number alone is never
drawn), and `Game.scoreAge(now:)` is the "as of 40 s ago" the host console prints.

Directions are `LEFT` / `MIDDLE` / `RIGHT` (the NFL's run and pass location words). The app always
sends `MIDDLE`; it still decodes `CENTER` (and a crowd count under `"CENTER"`) from a server or game.db
from before the rename, as `.middle`.

The **Rules** tab (`RulesView`, also opened from onboarding as a sheet) is static: the same "Rules of
the Game" text as the web's `/rules` page, the points table, the bonus math and two diagrams drawn
from behind the quarterback: the offensive line (Middle = between the guards, through the A-gaps;
Left / Right = guard, tackle, end) and the field split by the hash marks (Left | Middle | Right).
It needs no server and no account.

The distance pick is always `yardage` in code and JSON, never "distance", because `Play.distance` is
already down-and-distance ("3rd & 7"). What the app sends and reads:

```
-> {"type":"predict","play_id":7,"play_type":"PASS","direction":"MIDDLE","yardage":"SHORT"}
<- prediction:  {..., "yardage": "SHORT" | null, "yardage_correct": true | false (once resolved)}
<- play:        {..., "correct_yardage": "SHORT" | "MEDIUM" | "LONG" | "LOSS" | null, "yards_gained": 7 | null}
<- scoring:     {"type": 10, "direction": 10, "yardage": 10, "bonus": 10, "exact": 40}
<- crowd:       {..., "LEFT": n, "MIDDLE": n, "RIGHT": n, "SHORT": n, "MEDIUM": n, "LONG": n}
```

`yardage` fields are optional in the models so a server or game.db from before the distance pick
still decodes (a pick with no distance scores no distance points; a missing `scoring.yardage` reads
as 10, and a missing `scoring.bonus` is whatever `exact` pays beyond the three parts: 0 on an old
server). Teams show whatever city name and colors the game carries; the store screenshots
(`ScreenshotMode`) use Chicago at Detroit in their real colors.

The pick screen measures its height and switches to a tighter layout below 620 pt of usable height,
sized so the whole three-part pick fits an iPhone SE (375×667 pt) without scrolling; larger phones
get the roomier default.

## Layout

```
project.yml              XcodeGen spec (app + unit tests). iPhone only, iOS 17+.
PickThePlay/
  App/       PickThePlayApp (entry, tabs, reconnect on foreground), AppState (source of truth),
             HostState + HostDrafts (the host console's socket/state and half-finished entries),
             ScreenshotMode (sample data for CI store screenshots, incl. the host console)
  Models/    Codable wire models matching the server's JSON; AdminModels (host console, decoded
             leniently), HostLogic (result entry, suggestion choices, next down, fix: pure and
             tested), TeamPresets (generated from teams.py)
  Services/  APIClient + ServerConfig (REST, server address), LiveConnection (WebSocket with
             hello/ping/backoff), AdminConnection (the host console's /ws/admin), Keychain (the
             admin key), Practice (scoring rules + offline practice game)
  Views/     Live (scorebug with the live score + open/locked/result/final stages), Leaderboard,
             Lounges (the Head to Head tab: "Back to the game" on top, a Done key on the keyboards,
             tap or scroll to put them away), Rules (rules of the game, points table, run/pass
             direction diagrams), Settings (server, points, privacy, "Change name or delete
             account"), Onboarding, Practice, shared Components, Theme,
             Host/ (the host console: key screen, Run, Log, Players, Message)
PickThePlayTests/
  Fixtures/  Real messages captured from the Python server; ContractTests decode every one
             (incl. state_play_resolved_loss: a pass for a loss that scores type + direction only;
             state_game_score is state_play_opened with the game's three score fields filled in, as
             the server sends them once its live-data checks have read a score)
  ContractTests.swift   fixtures decode; the server's points equal ScoreRules on the same pick
  GameLogicTests.swift  every 3-part scoring combination incl. the bonus, LOSS, labels, MIDDLE and
                        the CENTER alias, practice odds, pick restore
  PlayerScreenTests.swift  the live score on the game (missing, null and odd values), the points
                        table's words, the bonus line
```

## Keeping the app and server in step

The fixtures are generated from the running backend. If you change the server's JSON, regenerate
them with `venv/bin/python tests/capture_ios_fixtures.py` (from the repo root; it starts a throwaway
server and plays a short scripted game) and the contract tests will tell you what the app must change.
Tokens, lounge codes and timestamps change on every capture, so the tests never pin them.

## Host console

Whoever runs the game can do it from the app: **Settings → Open the host console** (or *Running the game? Host sign-in*
on the welcome screen), then the admin key. It is the website's console on a phone: same `/ws/admin` socket and
`/api/admin/*` routes, nothing extra on the server. See "Running the game from the iPhone app" in the main README. The
key is kept in the Keychain (`Services/Keychain.swift`, device-only) and **Sign out** forgets it. Four tabs: Run (start a
game, open/lock/score plays, the live-data card), Log (leaderboard, Play log, Fix result), Players (search, remove and
block), Message (banner to every player).

`AdminState` decodes only the game and the play strictly; every other part is read leniently (a part that can't be read is
left out, one bad Play-log row is skipped), so a small server change can't blank the console mid-game. Contract tests decode
real messages from `tests/capture_ios_admin_fixtures.py`.
