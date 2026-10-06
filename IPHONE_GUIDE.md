# Launch Pick the Play on your iPhone

There are two ways to have Pick the Play on an iPhone:

- **The native iPhone app**, installed through TestFlight like GameDial. See **[TESTFLIGHT.md](TESTFLIGHT.md)**.
  It needs the game server running on the internet, which is Route B below.
- **The web app** (this guide). A server runs the game, your iPhone opens it in Safari, and you add it to
  your Home Screen. It then opens full-screen from its own icon. No App Store needed.

## Choose your route

| | **Route A: Home Wi-Fi** | **Route B: Cloud (no computer)** |
| --- | --- | --- |
| Where the game runs | Your Mac or Windows PC | A free [Render](https://render.com) server |
| You need | A computer that stays on during the game | A free Render account (you can sign up on the iPhone) |
| Who can join | Phones on your Wi-Fi ([add a tunnel](#route-a-extra-let-friends-join-from-anywhere) so anyone can) | Anyone you send the link to |
| Cost | Free | Free (Render may place a $1 card-verification hold) |
| Scores and players | Kept on your computer | **Reset** whenever the free server sleeps, restarts or updates |
| Setup time | About 15 minutes the first time | About 10 minutes |

If you have a computer, **Route A** is the most reliable. **Route B** works entirely from your phone,
and it's the one the native app uses.

---

## Route A: Home Wi-Fi

### A1. Download the code (one time)

1. On your computer, go to **https://github.com/ctjoey/PTP** and sign in. The repository is private,
   so if you see "Page not found", you aren't signed in with the account that owns it.
2. Click the green **Code** button, then **Download ZIP**.
3. Unzip it. On a Mac, Safari usually unzips it for you and you'll see a folder in Downloads. If you
   see a `.zip` file instead, double-click it. On Windows, right-click the ZIP and choose **Extract All...**.
4. Find the folder that contains **`app.py`**. It's named something like `PTP-claude-epic-planck-3jvrsr`,
   and unzipping sometimes puts it inside a second folder with the same name. Keep it on your computer's
   own drive (Downloads or Documents is fine), not on a network drive.

### A2. Install Python 3.14 (one time)

Install **Python 3.14**. Do **not** pick 3.15 yet: it's brand new and some of the app's building
blocks don't support it yet.

**Mac**
1. Go to **https://www.python.org/downloads/macos/** and download the **Python 3.14.x** macOS installer (`.pkg`).
2. Open it and click through **Continue / Agree / Install**.
3. Open **Terminal** (press `Cmd + Space`, type `Terminal`, press Return) and type:
   ```
   python3.14 --version
   ```
   You should see `Python 3.14.x`. If it says `command not found: python3.14`, Python didn't install:
   run the installer again, then quit Terminal (`Cmd + Q`), reopen it and try again.

**Windows**
1. Go to **https://www.python.org/downloads/windows/** and download the **Windows installer (64-bit)** for Python 3.14.x.
2. Run it. On the **first screen**, tick **Add python.exe to PATH**, then click **Install Now**.
3. Open **Terminal**: right-click the Start button and choose **Terminal** (on Windows 10, choose
   **Windows PowerShell**). Then type:
   ```
   py -3.14 --version
   ```
   You should see `Python 3.14.x`.

### A3. Open a terminal in the project folder

- **Mac:** open Terminal, type `cd ` (with a space after it), drag the project folder from Finder onto
  the Terminal window, then press Return.
- **Windows 11:** open the project folder in File Explorer, right-click an empty spot and choose
  **Open in Terminal**. On some PCs it's under **Show more options**.
- **Windows 10:** open the project folder in File Explorer, hold **Shift**, right-click an empty spot and
  choose **Open PowerShell window here**.

Check you're in the right place: type `ls` (Mac) or `dir` (Windows). You should see `app.py` in the list.

### A4. Install the app (one time)

Copy and paste these lines one at a time, pressing Return/Enter after each. The second line takes
about a minute and should show `Successfully installed ...` near the end. If a `[notice]` about a new
pip version follows it, you can ignore it.

**Mac**
```
python3.14 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

**Windows**
```
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### A5. Start the game server

First, keep the computer awake for the whole game. If it sleeps, everyone disconnects.
- **Mac:** the `caffeinate -i` at the start of the Mac command below does this. Keep the laptop
  plugged in with the lid open.
- **Windows 11:** **Settings > System > Power & battery** (just **Power** on a desktop PC) **> Screen,
  sleep, & hibernate timeouts**, and set sleep (when plugged in) to **Never**.
- **Windows 10:** **Settings > System > Power & sleep**, and set **When plugged in, PC goes to sleep after** to **Never**.

Then start the server:

**Mac**
```
caffeinate -i venv/bin/python app.py --phone
```

**Windows**
```
.\venv\Scripts\python.exe app.py --phone
```

It prints something like this. Your numbers will differ:

```
  On your iPhone (same Wi-Fi), open:  http://192.168.1.23:8000/
  Admin console:                      http://192.168.1.23:8000/admin
```

Use the address on the **On your iPhone** line. Ignore the lines below it that start with `INFO` or
`WARNING`: the `http://0.0.0.0:8000` address there doesn't work on a phone, and the admin-key warning
is covered in A8.

Write down that address and **leave this window open**: closing it stops the game. To stop the game
yourself, click in this window and press `Ctrl + C` (Control, not Command, on a Mac).

**The first time only**, you may see a firewall question:
- **Windows:** tick **Private networks** and click **Allow access**. Also make sure your home Wi-Fi is set to
  Private, because Windows sets new networks to Public and a Public network blocks your phone:
  **Windows 11:** **Settings > Network & internet > Wi-Fi >** *(your network)* **> Network profile type > Private network**.
  **Windows 10:** **Settings > Network & Internet > Wi-Fi >** *(your network)* **> Network profile > Private**.
- **Mac:** if asked whether Python may "accept incoming network connections", click **Allow**.

### A6. Open it on your iPhone

1. Make sure the iPhone is on the **same Wi-Fi** as the computer, not a guest network. Turn off any VPN
   on the phone and on the computer.
2. Open **Safari** and type the address **exactly** as printed, including `http://` at the start and
   `:8000` at the end. For example: `http://192.168.1.23:8000`
3. Safari shows **Not Secure** next to the address. That's normal for a game on your home network.
   If Safari shows a full-page warning instead, tap the option to continue. If it refuses to open the
   page, go to **Settings > Apps > Safari** and turn off **Not Secure Connection Warning**.
4. **Don't sign up yet.** Do the next step first.

### A7. Add it to your Home Screen

Do this **before** you pick a username. The Home Screen app keeps its own sign-in, separate from Safari.

1. Open the Share menu:
   - If Safari's toolbar shows a **Share** button (a square with an up arrow), tap it.
   - **iOS 27:** touch and hold the address bar (or tap the page-menu button at the left end of the
     address bar), then tap **Share**.
   - **iOS 26:** tap the **•••** button next to the address bar, then tap **Share**.

   Not sure which iOS you have? Look in **Settings > General > About > iOS Version**.
2. Scroll down and tap **Add to Home Screen**. If it isn't in the list, scroll to the bottom, tap
   **Edit Actions** and add it.
3. If you see **Open as Web App**, make sure it's on. Then tap **Add**.

Now go to your Home Screen, tap the **Pick the Play** icon, choose a username, and you're in.

Friends on the same Wi-Fi do A6 and A7 on their own iPhones with the same address, and choose a
username only after adding the icon.

### A8. Run the game

On the computer, open **http://127.0.0.1:8000/admin** in a web browser. The admin key is `admin`.
Create the game, then for each play: **Open Next Play**, **Lock Predictions**, choose what actually
happened, and **Resolve & Score Play**. Every phone updates instantly. The [README](README.md#running-a-game-admin)
has the details and keyboard shortcuts.

Anyone on your Wi-Fi who knows the address could open the admin page. To use your own password,
stop the server (click in its window and press `Ctrl + C`), then start it again like this. Replace
`pick-a-password` with your own password, using only letters and numbers:
- **Mac:** `PTP_ADMIN_KEY='pick-a-password' caffeinate -i venv/bin/python app.py --phone`
- **Windows:** run `$env:PTP_ADMIN_KEY = 'pick-a-password'`, then `.\venv\Scripts\python.exe app.py --phone`

### Every game day after that

1. Open a terminal in the project folder (step A3).
2. Start the server and leave the window open. Use the same password you chose in A8 (if you never set
   one, leave out the `PTP_ADMIN_KEY` part):
   - **Mac:** `PTP_ADMIN_KEY='your-password' caffeinate -i venv/bin/python app.py --phone`
   - **Windows:** `$env:PTP_ADMIN_KEY = 'your-password'`, then `.\venv\Scripts\python.exe app.py --phone`
3. On the computer, open **http://127.0.0.1:8000/admin** and click **Create Game** for today's matchup.
   This ends last week's game; season points carry over. Then run each play as in A8.
4. Tap the Pick the Play icon on your iPhone.

If the icon stops loading one day, the computer's Wi-Fi address has probably changed. Check the
address the server prints. If it's different, delete the icon (touch and hold it, then delete it)
and repeat A6 and A7 with the new address. A new address signs everyone out, and their old usernames
show as taken, so everyone picks a new name (for example, add a number). Season points start again
from zero.

---

## Route A extra: let friends join from anywhere

This gives your computer a temporary **https://** link that works on cellular and any Wi-Fi, using a
free Cloudflare "quick tunnel". No account is needed. As a bonus, the lounge **Invite** button opens
the iPhone share sheet, which needs https.

1. **Install cloudflared** (one time).
   - **Mac:** if you have [Homebrew](https://brew.sh) on an Apple-chip Mac with macOS 15 or newer, run
     `brew install cloudflared`. Otherwise (an Intel Mac, an older macOS, or no Homebrew), download
     `cloudflared-darwin-arm64.tgz` (Apple-chip Macs) or `cloudflared-darwin-amd64.tgz` (Intel Macs) from
     https://github.com/cloudflare/cloudflared/releases/latest and double-click it. This puts a
     `cloudflared` file in your Downloads folder.
   - **Windows:** `winget install --id Cloudflare.cloudflared`, then close the terminal and open a new one.
2. **Window 1:** if the game is already running from A5, press `Ctrl + C` in that window first. Then
   start it again with your own admin password, because the game is now public. You don't need
   `--phone`. Replace `pick-a-password` with your own password, using only letters and numbers:
   - **Mac:** `PTP_ADMIN_KEY='pick-a-password' caffeinate -i venv/bin/python app.py`
   - **Windows:** `$env:PTP_ADMIN_KEY = 'pick-a-password'`, then `.\venv\Scripts\python.exe app.py`
3. **Window 2:** open a second terminal (Mac: press `Cmd + N` in Terminal; Windows: click **+** at the
   top of Terminal) and type:
   ```
   cloudflared tunnel --url http://127.0.0.1:8000
   ```
   If you downloaded cloudflared without Homebrew, first type `cd ~/Downloads` and press Return, then
   use `./cloudflared tunnel --url http://127.0.0.1:8000` instead.
4. After a few seconds a box says **"Your quick Tunnel has been created!"** The line under it is your
   link, something like `https://some-random-words.trycloudflare.com`. If it doesn't open right away,
   wait up to a minute and reload.
5. Open that link on your iPhone, add it to your Home Screen (step A7), and send it to friends. The
   admin console is the same link with `/admin` on the end.

Keep **both** windows open during the game. You get a **new link every time** you start the tunnel,
so send the new one and re-add the Home Screen icon. Each new link signs everyone out, and their old
usernames show as taken, so everyone picks a new name and season points start again from zero. Some
school or work networks block `trycloudflare.com` links. If a friend can't open it, they should switch
to cellular.

---

## Route B: Cloud hosting on Render (no computer needed)

You can do all of this in Safari on your iPhone. This is also the server the native app uses.

1. Go to **https://dashboard.render.com/register** and choose **GitHub** to sign up, using the GitHub
   account that owns `ctjoey/PTP`. If Render asks for a card, it's a verification hold. You won't be
   charged on the Free plan.
2. Tap **+ New** (top right) and choose **Web Service**.
3. Connect GitHub when asked. Because the repository is private, Render asks to install its GitHub App:
   choose **Only select repositories**, pick **PTP**, and approve. Then select **ctjoey/PTP**.
4. Fill in the form:

   | Field | Value |
   | --- | --- |
   | Name | `pick-the-play`, which becomes your link `https://pick-the-play.onrender.com` (or similar) |
   | Branch | `claude/epic-planck-3jvrsr` |
   | Language | `Python 3` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1` |
   | Instance Type (may be labelled **Compute**) | **Free** |

5. Under **Environment Variables**, which may be in an **Advanced** section, add:
   **Key** `PTP_ADMIN_KEY`, **Value** a password only you know. Leave the Python version alone: the code
   already tells Render to use Python 3.14.
6. Tap the button at the bottom to create the service. The first build takes a few minutes. When it
   says it's live, your link is shown at the top of the page.
7. Open the link in Safari on your iPhone, add it to your Home Screen (step A7), and then pick a username.
   The admin console is the same link with `/admin` on the end. Text the link to friends: it works anywhere.

**Know these before game night (Free plan):**
- The server **falls asleep after 15 minutes** with nobody connected, and takes about a minute to wake
  up. Open the link a few minutes before kickoff.
- **When it sleeps, restarts or updates, everything resets**: the game, players, lounges and scores.
  Keep the **admin console open and on-screen** for the whole game, on a device you've set to stay
  awake. Its live connection keeps the server awake through halftime, even if every phone is locked.
  - **iPhone or iPad:** turn off **Low Power Mode**, set **Settings > Display & Brightness > Auto-Lock**
    to **Never** (being plugged in does not stop it locking), and keep Safari on screen.
  - **Laptop:** keep it plugged in with the lid open. On a Mac, open Terminal, run `caffeinate -di` and
    leave that window open. On Windows, set sleep to **Never** as in step A5.
- After a reset the game itself is gone too. The admin creates it again in the admin console, and
  everyone picks a username again (the app may say "Session expired" first).
- Want scores kept between games? Upgrade the service to the paid **Starter** plan (0.5 CPU / 512 MB,
  which may be listed as `0.5c-512mb`; about $7/month), add a **Disk** mounted at `/var/data`
  (1 GB is about $0.25/month), and add the environment variable `PTP_DB_PATH` = `/var/data/game.db`.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| iPhone says Safari can't connect to the server | Check that the server window is still open, the address starts with `http://` (not https) and ends with `:8000`, the phone is on the **same, non-guest** Wi-Fi, VPNs are off, and Python is allowed through the firewall. **Windows:** search Start for **Windows Defender Firewall with Advanced Security**, open **Inbound Rules**, delete every **Python** rule, then restart the server and click **Allow access** (Private networks). **Mac:** **System Settings > Network > Firewall > Options**, and set Python to **Allow incoming connections**. |
| The server printed "Could not detect this computer's Wi-Fi address" | **Mac:** run `ipconfig getifaddr en0` in Terminal. If it prints nothing (common on an iMac, Mac mini or Mac Studio), run `ipconfig getifaddr en1`. **Windows:** run `ipconfig` and use the **IPv4 Address** under "Wireless LAN adapter Wi-Fi". Then open `http://THAT-ADDRESS:8000` on the phone. |
| "That username is taken" in the Home Screen app | Either you signed up in Safari first, or the address changed (a new tunnel link or a new Wi-Fi address), which signs you out. The old name stays reserved, so pick a different name in the app. |
| The green dot at the top right turns orange or grey and stays that way | Swipe the app away in the App Switcher and reopen it. If it keeps happening, update iOS, or turn off **iCloud Private Relay** (Settings > *your name* > iCloud > Private Relay). |
| **Invite** shows "Share this code: 1234" instead of the share sheet | That's normal on home Wi-Fi (http). Read or text the 4-digit code. The share sheet works on https links (tunnel or Render). |
| Windows: typing `python` opens the Microsoft Store | Use the commands exactly as written (`py -3.14` and `.\venv\Scripts\python.exe`). |
| Windows: `py` is not recognized | Close the terminal and open a new one. If that doesn't help, run the Python installer again and choose **Repair**. |
| "running scripts is disabled on this system" | You ran an `activate` script. You don't need it: use the `.\venv\Scripts\python.exe` commands above. |
| Install fails with "Failed building wheel" or "Microsoft Visual C++ ... is required" | The venv was made with the wrong Python (usually 3.15 or a preview version). Delete the `venv` folder, install Python 3.14, and redo step A4. |
| "Pick the Play needs Python 3.11 or newer" | The `venv` folder was made with an old Python. Delete the `venv` folder, redo step A4 exactly as written (`python3.14` on Mac, `py -3.14` on Windows), then start again with the A5 command. |
| "Port 8000 is already in use" | The game is already running in another window. Use that window, or click in it, press `Ctrl + C`, and start again. |
| The admin console says "Invalid admin key." | The server was started with a different password, or without one (then the key is `admin`). Stop it with `Ctrl + C` and start it again with the `PTP_ADMIN_KEY` command from A8. |
| "No such file or directory", "can't open file 'app.py'", or (Windows) "'.\venv\Scripts\python.exe' is not recognized" | The terminal is in the wrong folder, or step A4 wasn't done in this folder. Redo step A3 and check you can see `app.py`, then run A4 if there is no `venv` folder. |
