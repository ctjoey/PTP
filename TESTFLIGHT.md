# Pick the Play on TestFlight (no Mac needed)

This works exactly like **GameDial**. GitHub's macOS computers build the iPhone app, test it, and
upload it to TestFlight. You never open Xcode. The recipe is in `.github/workflows/ios.yml`.

## What's already done

- **Native iPhone app** in `ios/PickThePlay` (iPhone only, iOS 17 or later), named **Pick the Play**.
  It includes a **Practice** mode that works without a live game, the **Live** game, leaderboards,
  lounges, and **Delete account** in Settings (Apple requires that).
- **Build + test on every push** to `ios/`. Each run also takes the App Store screenshots.
- **TestFlight upload on demand**, using the same cloud signing as GameDial. Apple creates the
  certificates and profiles for you. The build number goes up by itself each run.
- **App icon**: "Play Call", a chalk-talk X and O with one bold mint route arrow, at App Store size
  (`icon-1024.png`). Its source is `ios/scripts/app-icon.svg`; after editing it, run
  `NODE_PATH=$(npm root -g) node ios/scripts/render_icon.js` to regenerate the PNG. You can also
  drop your own square artwork in as `AppIcon.appiconset/artwork.png` and CI will normalise it.

## Heads-up: build minutes cost something on this repo

`ctjoey/PTP` is a **private** repo, so macOS build time comes out of your monthly GitHub Actions
allowance. GameDial's repo is public, which is why its builds were free. On the free plan you get
2,000 minutes a month, and macOS minutes use it up **10 times faster**, so you have about 200
macOS minutes. Each run takes roughly **10-30 macOS minutes**: a build + test is 10-20, and a
TestFlight run adds about 10 more. Every push to `ios/` starts a build on its own.

- Check usage: GitHub → your profile picture → **Settings → Billing and licensing**.
- To make builds free, make the repo public (repo → **Settings → General → Danger Zone → Change
  visibility**). Anyone could then read the code, but your passwords and keys stay hidden, because
  they live in Secrets and in Render.

## What you do, step by step

### Step 1. Add the secrets and variables to the PTP repo (10 min)

Secrets belong to one repository, so **GameDial's don't carry over**. You enter the same values
again.

1. Go to **github.com/ctjoey/PTP → Settings → Secrets and variables → Actions**.
2. On the **Secrets** tab, click **New repository secret** four times:

   | Name | Value |
   |---|---|
   | `APPLE_TEAM_ID` | Your 10-character Team ID (developer.apple.com/account → Membership details). Same as GameDial. |
   | `ASC_KEY_ID` | The App Store Connect API **Key ID**. Same as GameDial. |
   | `ASC_ISSUER_ID` | The **Issuer ID** shown above the keys list. Same as GameDial. |
   | `ASC_KEY_P8` | The **full text** of the `AuthKey_XXXXXXXXXX.p8` file. Open it in TextEdit or Notepad and paste everything, including the `-----BEGIN PRIVATE KEY-----` and `-----END PRIVATE KEY-----` lines. |

   The key must have the **Admin** role. GameDial's key already does, so you can reuse it.
   **Lost the `.p8` file?** Apple only lets you download it once. Make a new one: App Store Connect →
   **Users and Access → Integrations → App Store Connect API → Team Keys → +**, give it a name, choose
   **Admin**, then **Generate** and **Download**. Use the new Key ID and file text. GameDial keeps
   working with its old key.
3. On the **Variables** tab, click **New repository variable** twice:

   | Name | Value |
   |---|---|
   | `BUNDLE_ID_PREFIX` | `com.nfl-gtr` (what GameDial used). The app's ID then becomes `com.nfl-gtr.PickThePlay`. |
   | `PTP_SERVER_URL` | Your game server's address from Step 2, e.g. `https://pick-the-play.onrender.com`. It must start with `https://` and have **no `/` at the end**. |

The server address is built into the app. If it ever changes, update the variable and run Step 4
again (testers can also type the new address in the app's Settings).

### Step 2. Put the game server online (10 min)

The app talks to your Pick the Play server, so the server has to be on the internet.

1. Follow **[IPHONE_GUIDE.md → Route B: Cloud hosting on Render](IPHONE_GUIDE.md#route-b-cloud-hosting-on-render-no-computer-needed)**.
   Your address looks like `https://pick-the-play.onrender.com`. Copy it into `PTP_SERVER_URL`.
   Players can switch to another server in the app's **Settings**, including a home Wi-Fi one like
   `http://192.168.1.20:8000`, but the built-in address should be the https one.
2. **For real testers, use the paid setup.** The Free server falls asleep and **wipes all accounts
   and scores** whenever it restarts. In Render, open your service:
   - **Settings → Instance Type → Starter** (about $7 a month)
   - **Disks → Add Disk**: Mount Path `/var/data`, Size `1` GB (about $0.25 a month)
   - **Environment → Add Environment Variable**: `PTP_DB_PATH` = `/var/data/game.db`, then Save

   Free is fine for a quick try on your own phone.

### Step 3. Create the app in App Store Connect (5 min)

1. Go to **appstoreconnect.apple.com → Apps → + → New App**.
2. Fill in the form:
   - Platforms: **iOS**
   - Name: **Pick the Play**. If the name is taken, try a variant such as **Pick the Play: Live**.
   - Primary language: English (U.S.)
   - Bundle ID: **com.nfl-gtr.PickThePlay** (or *your prefix*`.PickThePlay`)
   - SKU: `picktheplay`
   - User Access: Full Access
3. **If the bundle ID isn't in the list**, register it first. Go to developer.apple.com/account →
   **Certificates, IDs & Profiles → Identifiers → +**, choose **App IDs → App**, enter Description
   `Pick the Play` and Bundle ID **Explicit** `com.nfl-gtr.PickThePlay`, then click **Continue →
   Register**. Reload the New App form.

### Step 4. Build and upload (about 20-30 min, all automatic)

1. Go to **github.com/ctjoey/PTP → Actions → iOS** (in the left list) → **Run workflow**.
2. Under **Use workflow from**, pick the branch that has the app. Tick **testflight**, then click
   **Run workflow**.
3. Wait for two green checks: **Build + test**, then **Archive + upload to TestFlight**.
4. Apple then "processes" the build for about 5-15 minutes and emails you when it's ready.

GitHub only shows the **Run workflow** button for workflows on the repo's **default branch**. PTP's
default branch is `claude/epic-planck-3jvrsr` (its only branch), which is where the app lives, so the
button is there. If you later make a different branch the default, merge the app into it first.

### Step 5. Install it on your phone, and invite friends

**Internal testing** (instant, up to 100 people, no review):
1. App Store Connect → your app → **TestFlight → Internal Testing → +** to create a group, then add
   yourself. Each tester must be an App Store Connect user. Invite friends first under **Users and
   Access → +**: a limited role is fine, and you can restrict them to this app only.
2. Turn on automatic distribution for the group, or add the build to the group by hand.
3. On the iPhone, install **TestFlight** from the App Store, open the email invite, then tap
   **Install**.

**External testing** (anyone with a link, up to 10,000 people):
1. **TestFlight → External Testing → +** to create a group, then add the build.
2. Fill in **Test Information**: a short description, a feedback email, and Privacy Policy URL
   *your server*`/privacy`. Under sign-in, say none is needed (players just pick a username).
3. Submit for **Beta App Review**. It's short, and usually done within a day. Then turn on **Public
   Link** and text it to friends.

TestFlight builds expire after 90 days. Run Step 4 again for a fresh one.

### Step 6. Playing it

- **Practice** works anytime, even when no live game is running.
- **Live** needs someone running the game from the web admin console (*your server*`/admin`, using
  your `PTP_ADMIN_KEY`): create the game, open each play, lock it, then resolve it. See README →
  "Running a game".

## If something goes wrong

| What you see | Fix |
|---|---|
| "Cloud signing permission error" at Export IPA | The API key isn't **Admin**. Make a new Admin key (Step 1) and replace `ASC_KEY_ID` and `ASC_KEY_P8`. |
| Upload fails with "no suitable application records" | The app isn't created in App Store Connect yet, or its bundle ID doesn't match `BUNDLE_ID_PREFIX` + `.PickThePlay` (Step 3). |
| Build + test is red | Ask Claude. It reads the logs from the run's `ios-build-N` download and fixes the code. Each round trip is about 10 minutes. |
| The app's Live tab can't connect | Open the server address in Safari to wake it. Check the address in the app's Settings and in `PTP_SERVER_URL`: it needs `https://` and no trailing `/`. |
| Accounts or scores disappeared | The Free Render server reset. Switch to Starter + Disk (Step 2). |

## Before the App Store (not needed for TestFlight)

- [ ] **Privacy Policy URL**: *your server*`/privacy`
- [ ] **Support URL**: *your server*`/support`
- [x] **Account deletion**: done (Settings → Delete account)
- [ ] **App Privacy**. Answer "Yes, we collect data". Choose **Identifiers → User ID** (the username)
      and **User Content → Gameplay Content** (the picks). For both: **linked to the user: Yes**,
      **used for tracking: No**, purpose **App Functionality** only.
- [ ] **Age rating** questionnaire: there's no chat, no gambling and no prizes (points only). The
      only thing players create is a username shown on leaderboards.
- [ ] **Review notes**, for example: "Free-to-play football prediction game, no password: pick any
      username. To try it without a live game, use **Practice**. To see a live game, open
      *your server*`/admin`, enter the key `<the key you give them>`, create a game and run a few
      plays while the app is open."
- [ ] **Export compliance**: the app only uses standard HTTPS, so the answer is **No**. The build
      already declares `ITSAppUsesNonExemptEncryption = false`, so TestFlight doesn't ask each time.
- [ ] **Screenshots**: open any green run in **Actions**, scroll to **Artifacts**, and download
      **app-store-screenshots-N**. It has `6.9-inch` (1290x2796) and `6.5-inch` (1284x2778) sets
      that are ready to upload.
- [ ] **No league or club names or logos** in the name, keywords or screenshots (see README →
      "Legal & branding safeguards").
