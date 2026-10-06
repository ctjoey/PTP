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
in the app's Settings tab (e.g. `192.168.1.20:8000` for `python app.py --phone` on your Wi-Fi).

## The game in the app

A play opens for 15 seconds and the player makes a three-part call, sent only once all three are
chosen (changing any part re-sends it while the play is OPEN):

| Part | Choices | Notes |
| --- | --- | --- |
| Play type | Run / Pass | |
| Direction | Left / Center / Right | As the QB looks downfield (the offense's point of view) |
| Distance (`yardage`) | Short 0–5 yds / Medium 6–10 / Long 11+ | Total yards gained; an incomplete pass is 0 yds = Short |

Scoring (the server is the authority; `ScoreRules` mirrors it for Practice mode and the result view):
+10 play type, +10 direction, +10 distance. All three = 30 (a "perfect call", `exact` on the wire).
A loss of yards (`LOSS`) never matches a distance pick, so it scores no distance points. Result labels
go by points: 30 "Perfect call!", 20 "Two of three", 10 "One of three", 0 "No points this time".

The distance pick is always `yardage` in code and JSON, never "distance", because `Play.distance` is
already down-and-distance ("3rd & 7"). What the app sends and reads:

```
-> {"type":"predict","play_id":7,"play_type":"PASS","direction":"LEFT","yardage":"SHORT"}
<- prediction:  {..., "yardage": "SHORT" | null, "yardage_correct": true | false (once resolved)}
<- play:        {..., "correct_yardage": "SHORT" | "MEDIUM" | "LONG" | "LOSS" | null, "yards_gained": 7 | null}
<- scoring:     {"type": 10, "direction": 10, "yardage": 10, "exact": 30}
<- crowd:       {..., "SHORT": n, "MEDIUM": n, "LONG": n}
```

`yardage` fields are optional in the models so a server or game.db from before the distance pick
still decodes (a pick with no distance scores no distance points; a missing `scoring.yardage` reads
as 10). Teams show whatever city name and colors the game carries; the store screenshots
(`ScreenshotMode`) use Chicago at Detroit in their real colors.

The pick screen measures its height and switches to a tighter layout below 620 pt of usable height,
sized so the whole three-part pick fits an iPhone SE (375×667 pt) without scrolling; larger phones
get the roomier default.

## Layout

```
project.yml              XcodeGen spec (app + unit tests). iPhone only, iOS 17+.
PickThePlay/
  App/       PickThePlayApp (entry, tabs, reconnect on foreground), AppState (source of truth),
             ScreenshotMode (sample data for CI store screenshots)
  Models/    Codable wire models matching the server's JSON
  Services/  APIClient + ServerConfig (REST, server address), LiveConnection (WebSocket with
             hello/ping/backoff), Practice (scoring rules + offline practice game)
  Views/     Live (scorebug + open/locked/result/final stages), Leaderboard, Lounges, Settings
             (server, privacy, delete account), Onboarding, Practice, shared Components, Theme
PickThePlayTests/
  Fixtures/  Real messages captured from the Python server; ContractTests decode every one
             (incl. state_play_resolved_loss: a sack that scores type + direction only)
  ContractTests.swift   fixtures decode; the server's points equal ScoreRules on the same pick
  GameLogicTests.swift  every 3-part scoring combination, LOSS, labels, practice odds, pick restore
```

## Keeping the app and server in step

The fixtures are generated from the running backend. If you change the server's JSON, regenerate
them with `venv/bin/python tests/capture_ios_fixtures.py` (from the repo root; it starts a throwaway
server and plays a short scripted game) and the contract tests will tell you what the app must change.
Tokens, lounge codes and timestamps change on every capture, so the tests never pin them.
