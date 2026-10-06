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
  *Tests.swift
```

## Keeping the app and server in step

The fixtures are generated from the running backend. If you change the server's JSON, regenerate
them (see the commit that added them) and the contract tests will tell you what the app must change.
