# Launch Pick the Play on your iPhone

Pick the Play is a **web app**, so there is nothing to install from the App Store. A server runs the
game, your iPhone opens it in Safari, and you add it to your Home Screen. After that it opens
full-screen from its own icon, like a regular app.

## Choose your route

| | **Route A: Home Wi-Fi** | **Route B: Cloud (no computer)** |
| --- | --- | --- |
| Where the game runs | Your Mac or Windows PC | A free [Render](https://render.com) server |
| You need | A computer that stays on during the game | A free Render account (you can sign up on the iPhone) |
| Who can join | Phones on your Wi-Fi ([add a tunnel](#route-a-extra-let-friends-join-from-anywhere) so anyone can) | Anyone you send the link to |
| Cost | Free | Free (Render may place a $1 card-verification hold) |
| Scores and players | Kept on your computer | **Reset** whenever the free server sleeps, restarts or updates |
| Setup time | About 15 minutes the first time | About 10 minutes |

If you have a computer, **Route A** is the most reliable. **Route B** works entirely from your phone.

---

## Route A: Home Wi-Fi

### A1. Download the code (one time)

1. On your computer, go to **https://github.com/ctjoey/PTP** and sign in. The repository is private,
   so if you see "Page not found", you aren't signed in with the account that owns it.
2. Click the green **Code** button, then **Download ZIP**.
3. Unzip it. On a Mac, double-click the ZIP in Downloads. On Windows, right-click it and choose **Extract All...**.
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
   You should see `Python 3.14.x`. If a pop-up says the command needs "command line developer tools",
   click **Cancel**: Python didn't install, so run the installer again.

**Windows**
1. Go to **https://www.python.org/downloads/windows/** and download the **Windows installer (64-bit)** for Python 3.14.x.
2. Run it. On the **first screen**, tick **Add python.exe to PATH**, then click **Install Now**.
3. Open **Terminal** (right-click the Start button and choose **Terminal**) and type:
   ```
   py -3.14 --version
   ```
   You should see `Python 3.14.x`.

### A3. Open a terminal in the project folder

- **Mac:** open Terminal, type `cd ` (with a space after it), drag the project folder from Finder onto
  the Terminal window, then press Return.
- **Windows:** open the project folder in File Explorer, right-click an empty spot and choose
  **Open in Terminal**. On some PCs it's under **Show more options**.

Check you're in the right place: type `ls` (Mac) or `dir` (Windows). You should see `app.py` in the list.

### A4. Install the app (one time)

Copy and paste these lines one at a time, pressing Return/Enter after each. The second line takes
about a minute and should end with `Successfully installed ...`.

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

**Mac**
```
venv/bin/python app.py --phone
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

Write down that address and **leave this window open**: closing it stops the game.

**The first time only**, you may see a firewall question:
- **Windows:** tick **Private networks** and click **Allow access**. Also make sure your home Wi-Fi is set to
  Private: **Settings > Network & internet > Wi-Fi >** *(your network)* **> Network profile type > Private network**.
  Windows sets new networks to Public, and a Public network blocks your phone.
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

- **iOS 26:** tap the **•••** button next to the address bar, tap **Share**, scroll down and tap
  **Add to Home Screen**. Make sure **Open as Web App** is on, then tap **Add**.
- **iOS 18:** tap the **Share** button (a square with an arrow), scroll down, tap **Add to Home Screen**, then **Add**.

Now go to your Home Screen, tap the **Pick the Play** icon, choose a username, and you're in.

### A8. Run the game

On the computer, open **http://127.0.0.1:8000/admin** in a web browser. The admin key is `admin`.
Create the game, then for each play: **Open Next Play**, **Lock Predictions**, choose what actually
happened, and **Resolve & Score Play**. Every phone updates instantly. The [README](README.md#running-a-game-admin)
has the details and keyboard shortcuts.

Anyone on your Wi-Fi who knows the address could open the admin page. To use your own password,
start the server like this instead:
- **Mac:** `PTP_ADMIN_KEY='pick-a-password' venv/bin/python app.py --phone`
- **Windows:** run `$env:PTP_ADMIN_KEY = "pick-a-password"`, then `.\venv\Scripts\python.exe app.py --phone`

### Every game day after that

1. Open a terminal in the project folder (step A3).
2. Run the start command from step A5 and leave the window open.
3. Tap the Pick the Play icon on your iPhone.

Keep the computer plugged in and awake. If it sleeps, everyone disconnects.
- **Mac:** start with `caffeinate -i venv/bin/python app.py --phone` and keep the lid open.
- **Windows:** **Settings > System > Power & battery > Screen, sleep, & hibernate timeouts**, and set sleep (when plugged in) to **Never**.

If the icon stops loading one day, the computer's Wi-Fi address has probably changed. Check the
address the server prints. If it's different, delete the icon (touch and hold it, then delete it)
and repeat A6 and A7 with the new address.

---

## Route A extra: let friends join from anywhere

This gives your computer a temporary **https://** link that works on cellular and any Wi-Fi, using a
free Cloudflare "quick tunnel". No account is needed. As a bonus, the lounge **Invite** button opens
the iPhone share sheet, which needs https.

1. **Install cloudflared** (one time).
   - **Mac** with [Homebrew](https://brew.sh): `brew install cloudflared`. Without Homebrew, download
     `cloudflared-darwin-arm64.tgz` (Apple-chip Macs) or `cloudflared-darwin-amd64.tgz` (Intel Macs) from
     https://github.com/cloudflare/cloudflared/releases/latest, double-click it, and use `./cloudflared`
     from that folder in step 3.
   - **Windows:** `winget install --id Cloudflare.cloudflared`, then close the terminal and open a new one.
2. **Window 1:** start the game. It's public now, so **set your own admin password**. You don't need `--phone`.
   - **Mac:** `PTP_ADMIN_KEY='pick-a-password' venv/bin/python app.py`
   - **Windows:** `$env:PTP_ADMIN_KEY = "pick-a-password"`, then `.\venv\Scripts\python.exe app.py`
3. **Window 2** (a second terminal):
   ```
   cloudflared tunnel --url http://127.0.0.1:8000
   ```
4. After a few seconds a box says **"Your quick Tunnel has been created!"** The line under it is your
   link, something like `https://some-random-words.trycloudflare.com`. If it doesn't open right away,
   wait up to a minute and reload.
5. Open that link on your iPhone, add it to your Home Screen (step A7), and send it to friends. The
   admin console is the same link with `/admin` on the end.

Keep **both** windows open during the game. You get a **new link every time** you start the tunnel,
so send the new one and re-add the Home Screen icon. Some school or work networks block
`trycloudflare.com` links. If a friend can't open it, they should switch to cellular.

---

## Route B: Cloud hosting on Render (no computer needed)

You can do all of this in Safari on your iPhone.

1. Go to **https://dashboard.render.com/register** and choose **GitHub** to sign up, using the GitHub
   account that owns `ctjoey/PTP`. If Render asks for a card, it's a verification hold. You won't be
   charged on the Free instance type.
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
   | Instance Type | **Free** |

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
- **When it sleeps, restarts or updates, everything resets**: players, lounges and scores. Keep the
  **admin console open and on-screen** on a device that won't auto-lock (a laptop or plugged-in iPad
  is ideal). Its live connection keeps the server awake through halftime, even if every phone is locked.
  If the admin console is on your iPhone, set **Settings > Display & Brightness > Auto-Lock** to
  **Never** for the game, and keep Safari on screen.
- If players disappear after a reset, everyone just picks a username again.
- Want scores kept between games? Upgrade the service to the paid **Starter** type (about $7/month),
  add a **Disk** mounted at `/var/data` (1 GB is about $0.25/month), and add the environment variable
  `PTP_DB_PATH` = `/var/data/game.db`.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| iPhone says Safari can't connect to the server | Check that the server window is still open, the address starts with `http://` (not https) and ends with `:8000`, the phone is on the **same, non-guest** Wi-Fi, VPNs are off, and (Windows) Python is allowed through the firewall on a **Private** network. |
| The server printed "Could not detect this computer's Wi-Fi address" | **Mac:** run `ipconfig getifaddr en0` in Terminal. **Windows:** run `ipconfig` and use the **IPv4 Address** under "Wireless LAN adapter Wi-Fi". Then open `http://THAT-ADDRESS:8000` on the phone. |
| "That username is taken" in the Home Screen app | You signed up in Safari first, and that name is already used. Pick a different name in the app. |
| The green dot at the top right turns orange or grey and stays that way | Swipe the app away in the App Switcher and reopen it. If it keeps happening, update iOS, or turn off **iCloud Private Relay** (Settings > *your name* > iCloud > Private Relay). |
| **Invite** shows "Share this code: 1234" instead of the share sheet | That's normal on home Wi-Fi (http). Read or text the 4-digit code. The share sheet works on https links (tunnel or Render). |
| Windows: typing `python` opens the Microsoft Store | Use the commands exactly as written (`py -3.14` and `.\venv\Scripts\python.exe`). |
| Windows: `py` is not recognized | Close the terminal and open a new one. If that doesn't help, run the Python installer again and choose **Repair**. |
| "running scripts is disabled on this system" | You ran an `activate` script. You don't need it: use the `.\venv\Scripts\python.exe` commands above. |
| Install fails with "Failed building wheel" or "Microsoft Visual C++ ... is required" | The venv was made with the wrong Python (usually 3.15 or a preview version). Delete the `venv` folder, install Python 3.14, and redo step A4. |
| "Pick the Play needs Python 3.11 or newer" | The command used an old Python. Use `python3.14` (Mac) or `py -3.14` (Windows) as shown in A4, or the `venv/...` commands in A5. |
| "Port 8000 is already in use" | The game is already running in another window. Use that window, or close it and start again. |
| "No such file or directory" or "can't open file 'app.py'" | The terminal is in the wrong folder. Redo step A3 and check you can see `app.py`. |
