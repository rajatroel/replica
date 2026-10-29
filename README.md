# Replica

**Replica** is an Android automation tool that runs entirely on one phone through **Termux**. It listens to the Telegram task bot **@SmmKingdomTasksBot**, performs the Instagram **like** and **comment** tasks for you, solves the security captcha, uploads proof, and keeps a running log.

> **Disclaimer:** Replica automates third-party apps (Instagram, Telegram, Chrome) and a third-party bot. Those services may restrict or ban accounts that use automation. You use this software entirely at your own risk.

---

## Table of Contents

1. [How It Works](#1-how-it-works)
2. [Requirements and Compatibility](#2-requirements-and-compatibility)
3. [Required Apps](#3-required-apps)
4. [Get Your Telegram API ID and API Hash](#4-get-your-telegram-api-id-and-api-hash)
5. [Create Your Config in the Replica App](#5-create-your-config-in-the-replica-app)
6. [Install Termux Packages and Replica](#6-install-termux-packages-and-replica)
7. [First Run: Step by Step](#7-first-run-step-by-step)
8. [Connect ADB (Wireless Debugging)](#8-connect-adb-wireless-debugging)
9. [Log In to Telegram (First Time Only)](#9-log-in-to-telegram-first-time-only)
10. [Using the Automation](#10-using-the-automation)
11. [Daily Use](#11-daily-use)
12. [Logs and Sending Them to the Developer](#12-logs-and-sending-them-to-the-developer)
13. [Troubleshooting](#13-troubleshooting)
14. [Files Used by Replica](#14-files-used-by-replica)
15. [Security Notes](#15-security-notes)
16. [FAQ](#16-faq)
17. [Support](#17-support)

---

## 1. How It Works

Replica is made of three cooperating parts, all on the **same phone**:

| Part | Role |
|------|------|
| **Termux** (`replica.py`) | The brain. Talks to Telegram, reads bot messages, controls the phone, solves captchas, writes logs. |
| **Replica app** (`com.replica`) | Companion app. Creates `config.json` and displays the Telegram login QR code as an image. |
| **ADB (Wireless Debugging)** | Lets Termux control the phone's screen (open Instagram, tap, type, screenshot) through UIAutomator2. |

**Task flow once started:**

1. Replica clears the bot chat, restarts the bot, sends `/start`, and opens **Tasks**.
2. It picks **Instagram** and rotates through the accounts listed in your config.
3. **Like task** (1.1 cashcoins): opens the post in Instagram and double-taps to like.
4. **Comment task** (6.0 cashcoins): posts the requested comment, takes a proof screenshot, uploads it to the bot.
5. **Security check:** opens the captcha page in Chrome, passes the verification, sends the captcha images to the solver, and types the answer.
6. Tasks it cannot do (for example comments containing an `@mention`) are skipped automatically.

---

## 2. Requirements and Compatibility

| Item | Requirement |
|------|-------------|
| Android version | **Android 11 or newer** (Wireless Debugging with pairing code is not available on older versions) |
| CPU architecture | **64-bit (arm64-v8a)** only. 32-bit phones are not supported |
| Network | Wi-Fi connected (needed to enable Wireless Debugging) and working internet |
| Storage | Free space for Termux packages (about 1 to 2 GB recommended) |
| Second phone | **One more phone with your Telegram account** logged in, used once to scan the login QR code |
| Telegram | A personal Telegram account and its own API credentials (Section 4) |
| License | A valid Replica license key (entered in the Replica app) |

**How to check that your phone is 64-bit:** in Termux run

```bash
uname -m
```

`aarch64` means 64-bit and compatible. `armv7l` or `armv8l` means 32-bit and not compatible.

> **Tip:** disable battery optimization for Termux, Termux:API, and the Replica app, and keep the screen awake while automating. Aggressive battery savers on some brands (Xiaomi, Oppo, Vivo, Realme, Samsung) may kill Termux in the background.

---

## 3. Required Apps

Install **all** of the following on the phone that will run the automation.

| # | App | Package name | Notes |
|---|-----|--------------|-------|
| 1 | **Termux** | `com.termux` | Install from **F-Droid** or the official **GitHub releases**. Do **not** use the Google Play version (outdated). |
| 2 | **Termux:API** | `com.termux.api` | Must come from the **same source** as Termux (both F-Droid or both GitHub). Needed for the pairing notification. |
| 3 | **Replica app** | `com.replica` | Download from this repository's **Releases** page. Open it once and grant storage access. |
| 4 | **Telegram** (regular) | `org.telegram.messenger` | Needed so the account and the bot chat exist on the device. |
| 5 | **Instagram** (regular) | `com.instagram.android` | Log in to **every** Instagram account you want to use. |
| 6 | **Google Chrome** | `com.android.chrome` | Used to pass the captcha verification page. |

Also needed on the **second phone**: regular **Telegram**, logged in to your account.

**Before first use, make sure:**

- Instagram is logged in to all your accounts, and they appear in the account switcher (long-press the profile tab to check).
- The usernames in your config match the Instagram usernames **exactly**.
- You have opened **@SmmKingdomTasksBot** at least once with your Telegram account and registered/linked your Instagram accounts there.
- Chrome opens normally and is not stuck on a first-run welcome screen.

---

## 4. Get Your Telegram API ID and API Hash

Replica logs in as *you* on Telegram, which requires your own API credentials.

1. Open **https://my.telegram.org** in a browser.
2. Enter your phone number with country code (for example `+91XXXXXXXXXX`) and tap **Next**.
3. Telegram sends a login code **inside the Telegram app** (not by SMS). Enter it on the website.
4. Click **API development tools**.
5. If you have no application yet, fill in the form:
   - **App title:** any name, for example `MyApp`
   - **Short name:** any short name, for example `myapp`
   - **Platform:** Android (or Other)
   - Other fields can stay empty.
6. Click **Create application**.
7. Copy these two values:
   - **App api_id** (a number)
   - **App api_hash** (a 32-character string)

> **Important**
> - Never share your `api_hash` publicly.
> - Your Replica license is **tied to your `api_hash`**. If you change to different API credentials later, your license check will fail and you must contact the developer.
> - If the my.telegram.org page shows an error, try again after some time, try a different network, or disable any VPN.

---

## 5. Create Your Config in the Replica App

Replica reads its settings from `/sdcard/config.json`. You do **not** need to write this file by hand: the **Replica app creates it for you**.

1. Install and open the **Replica app**.
2. Grant **storage permission** when asked.
3. Fill in:
   - **API ID**: from Section 4
   - **API Hash**: from Section 4
   - **License key**: the key you received from the developer
   - **Instagram accounts**: your Instagram usernames, exactly as they appear in the account switcher
4. Tap **Save**. This creates `config.json`.

For reference, the finished file looks like this:

```json
{
  "api_id": 1234567,
  "api_hash": "0123456789abcdef0123456789abcdef",
  "license_key": "YOUR-LICENSE-KEY",
  "accounts": [
    { "username": "first_instagram_user" },
    { "username": "second_instagram_user" }
  ]
}
```

If the file is missing or invalid, Replica shows *"Create config first using Replica app"* or *"Invalid config.json"* and closes after a short countdown. Open the Replica app, correct the values, tap **Save**, and run again.

---

## 6. Install Termux Packages and Replica

Open **Termux** and run the following.

**Step 1: update Termux and give it storage access**

```bash
pkg update -y && pkg upgrade -y
termux-setup-storage
```

Tap **Allow** on the permission popup.

**Step 2: install system packages**

```bash
pkg install -y python android-tools termux-api git
```

Depending on your Termux version, you may also need the prebuilt scientific packages used for image cleaning:

```bash
pkg install -y python-numpy opencv-python python-pillow
```

**Step 3: install Python libraries**

```bash
pip install rich textual telethon uiautomator2 qrcode requests zeroconf
```

> `zeroconf` lets Replica discover the Wireless Debugging ports automatically. Without it you must type the ports manually.

**Step 4: get Replica**

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

(or copy `replica.py` onto the phone by any method and `cd` into its folder).

---

## 7. First Run: Step by Step

A quick overview of everything that happens the very first time:

1. Install the apps from Section 3.
2. Get API ID and API Hash (Section 4).
3. Create the config in the Replica app (Section 5).
4. Install Termux packages (Section 6).
5. Run the script:

   ```bash
   python replica.py
   ```
6. Tap **CONTINUE** on the first screen (**CANCEL** simply closes it).
7. Replica checks storage permission, then ADB. On a first run it walks you through **pairing** (Section 8).
8. Replica checks that the Replica app is installed.
9. Replica connects to Telegram. If you are not logged in yet, it starts **QR login** (Section 9).
10. The **dashboard** opens. Press **START AUTOMATION** (Section 10).

You only do the pairing and QR login once. After that, starting the script goes straight to the dashboard (unless your phone was restarted, see Section 8).

---

## 8. Connect ADB (Wireless Debugging)

ADB lets Replica control the screen. Everything happens on the same phone; no computer is required.

### 8.1 Enable Developer Options

1. Open **Settings > About phone**.
2. Tap **Build number** 7 times until you see *"You are now a developer"*.

### 8.2 Enable Wireless Debugging

1. Connect the phone to **Wi-Fi**.
2. Open **Settings > System > Developer options**.
3. Turn on **Wireless debugging** and allow it for your current network.

### 8.3 Pair with Replica

When you run the script and no ADB device is found, Replica starts automatic setup:

1. Replica says **"Open Wireless debugging"**. Go to **Developer options > Wireless debugging**.
2. Tap **Pair device with pairing code**. A 6-digit code appears.
3. **Pull down the notification shade.** Replica has posted an **"ADB pairing"** notification with a reply box.
4. Tap **Enter**, type the **6-digit pairing code**, and send it.
5. Replica finds the pairing port automatically and pairs. If it cannot find the port, type the code and the port together in the notification (for example `123456 37123`).
6. Replica then connects, and if the connect port is not found automatically it asks for it in a notification. Type the **port number** shown on the main Wireless debugging screen.
7. Replica locks ADB to port **5555**, so the device appears as `emulator-5554`.
8. If Android shows **"Allow USB debugging?"**, tick **Always allow** and tap **Allow**.

You will see `ADB connected: emulator-5554` in the terminal when it works.

> **Tips**
> - **Keep the Wireless debugging pairing screen open** while entering the code. If you close it, the code becomes invalid.
> - If the notification is not shown, check that **Termux:API** is installed and that notifications are allowed for Termux and Termux:API.
> - Setup retries up to 3 times automatically.

### 8.4 After a Restart

The port 5555 connection lasts until the phone is rebooted. After a reboot, turn Wireless debugging back on and repeat the pairing steps.

---

## 9. Log In to Telegram (First Time Only)

If Replica finds no saved Telegram session, it shows a **TELEGRAM LOGIN** screen. You log in by scanning a QR code with your **second phone**.

1. Take the **second phone** that has your Telegram account.
2. Open Telegram, then **Settings > Devices > Link Desktop Device**.
3. Keep the scanner open and ready.
4. On the Replica phone, tap **CONTINUE**.
5. The QR code opens as an **image inside the Replica app** on the Replica phone.
6. Point the second phone at the QR code. **You have about 30 seconds.**
7. If your account has **Two-Step Verification**, Replica asks for **`ENTER 2FA PASSWORD:`**. Type it and press Enter.
8. On success you see `Logged in: <your name>` and the dashboard opens.

**If the QR code expires:** Replica shows *"QR code expired. Wait 2 minutes."*, cleans up, and closes Termux. Wait the 2 minutes, then run `python replica.py` again. Waiting avoids Telegram flood limits.

Your login is saved as `/sdcard/userbot.session`. You will not need to scan again unless the session is deleted or invalidated.

---

## 10. Using the Automation

The dashboard shows your account, device name, license status (days left), cashcoins earned this session, and the latest log line.

| Button | What it does |
|--------|--------------|
| **START AUTOMATION** | Starts the task loop. Resets the session cashcoin counter. |
| **STOP AUTOMATION** | Stops the loop and closes Instagram and Chrome. Telegram stays connected. |
| **CLEAR** | Deletes the contents of `logs.txt`. |
| **SHARE** | Opens the Android share sheet to send `logs.txt` (Section 12). |
| **EXIT** | Fully closes Replica and Termux. Disabled while the automation is running. Stop first, then Exit. |

**Starting:**

1. Make sure the phone is unlocked and the screen is on.
2. Tap **START AUTOMATION**.
3. Watch the log line change: license check, UIAutomator connecting, bot reset, `/start`, Tasks, then tasks start flowing.

**Start is blocked when:**

- the license is invalid or expired (the reason is shown, for example *Invalid license key* or *License expired*),
- ADB is not connected,
- Telegram is not logged in.

**While it runs:**

- Do **not** touch or use the phone. Replica is tapping the screen.
- Do not open other apps or let the screen lock.
- Do not log out of Instagram or change the active Instagram account manually.
- Account switching between your configured Instagram accounts is automatic.

**Stopping:** tap **STOP AUTOMATION**. Replica also stops by itself if the captcha verification fails, the license is rejected, or the bot does not answer twice in a row.

---

## 11. Daily Use

1. Open Termux.
2. Run:

   ```bash
   cd <your-repo>
   python replica.py
   ```
3. Tap **CONTINUE**, wait for the dashboard.
4. Tap **START AUTOMATION**.
5. When finished: **STOP AUTOMATION**, then **EXIT**.

If the phone was restarted since last time, redo the ADB pairing (Section 8.4).

---

## 12. Logs and Sending Them to the Developer

Every event is saved to **`~/logs.txt`** (inside Termux's home folder). New lines are appended after old ones, grouped by date, and the file is only emptied when you press **CLEAR**. Stopping, restarting, or crashing never deletes it.

### If something goes wrong, please do this

1. **Do not press CLEAR.**
2. Reproduce the problem if you can, so the log contains it.
3. On the dashboard, tap **SHARE**. Replica copies the log to `/sdcard/Download/logs.txt` and opens the Android share sheet.
4. Send `logs.txt` to the developer through Telegram, email, or any app.

**If the dashboard does not open** (startup errors close Termux after a short countdown), run this in Termux instead:

```bash
cp ~/logs.txt /sdcard/Download/logs.txt
```

Then send `/sdcard/Download/logs.txt` (use your file manager) to the developer.

### Explain the situation

Logs show *what* happened. Please also tell the developer, in a short message:

- what you were doing when the problem occurred,
- what you expected and what happened instead,
- your phone brand, model, and Android version,
- your Termux and Replica app versions,
- whether it is the first run or it worked before,
- anything you changed recently (phone restart, app updates, new Instagram account).

A screenshot of the dashboard or terminal helps too. **Remove or hide your `api_hash`, license key, and any passwords** from screenshots.

---

## 13. Troubleshooting

| Message / Symptom | Meaning | Fix |
|-------------------|---------|-----|
| `Create config first using Replica app` | `/sdcard/config.json` is missing | Open the Replica app, fill in your details, tap **Save** |
| `Invalid config.json (...)` | Config is corrupt or fields are missing | Re-save from the Replica app |
| `Storage permission denied` | Termux has no storage access | Run `termux-setup-storage`, tap **Allow**, run again |
| `ADB not installed` | `adb` missing | `pkg install android-tools` |
| `Termux-API not installed` | `termux-notification` command missing | `pkg install termux-api` **and** install the Termux:API app |
| `Allow USB debugging popup` | Android is waiting for your approval | Tap **Allow**, tick **Always allow** |
| `Pairing timed out` / `Invalid pairing code` / `Pairing failed` | Wrong or expired code | Keep the pairing screen open, enter the fresh code quickly |
| `Pairing port not found` | Automatic port discovery failed | Install `zeroconf` or type `code port` in the notification |
| `ADB connection failed` | All 3 setup attempts failed | Make sure Wi-Fi is on, Wireless debugging is on, and try again |
| `Replica app error` / `Replica app is not installed` | Companion app missing | Install the Replica app and open it once |
| `Replica app is outdated` | Old Replica app version | Install the latest version from Releases |
| `QR code expired. Wait 2 minutes.` | QR not scanned within 30 s | Wait 2 minutes, run again, scan faster |
| `Wrong 2FA password` | Incorrect Telegram cloud password | Type it again |
| `Flood wait: Ns` | Telegram asks you to wait | Wait that many seconds before trying again |
| `Telegram timed out` / `Telegram connection failed` | No or unstable internet | Check connection, disable VPN, retry |
| `Corrupted session removed` / `Session corrupted, restarting...` | Session file was damaged | Automatic. You may need to scan the QR code again |
| `Invalid license key` / `License expired` | License problem | Check the key in the Replica app, or contact the developer |
| `License check failed: server offline` | License server unreachable | Check internet and retry later |
| `Profile tab not found` / `Account ... not found` | Instagram is not ready or the username does not match | Update and log in to Instagram, and check the usernames in your config exactly |
| `Post did not open` | Instagram could not open the post link | Check Instagram is updated and logged in |
| `Comment button not found` / `Comment was not confirmed on screen` | Instagram layout or language differs, or the comment was rejected | Send logs to the developer |
| `Captcha: Chrome check was not passed within 30s` | Chrome did not complete the verification | Open Chrome manually once, finish its welcome screen, retry |
| `Captcha: solver request failed` / `Solver returned no answer` | Solver server or license problem | Retry; if it persists, send logs |
| `CAPTCHA verification failed` | The bot rejected the answer | Automation stops. Press **START** again |
| `Bot server stopped responding after 2 attempts` | The bot did not reply twice | Check the bot manually in Telegram, then restart |
| `Connection failed` (UIAutomator) | Screen control failed to start | Re-check ADB, restart the script |

> **Note about language:** Replica recognizes Instagram and Chrome screens in **English, Russian, and Ukrainian**. If your phone uses another language, switch the phone or app language to English.

If your problem is not listed, follow Section 12 and send the logs to the developer.

---

## 14. Files Used by Replica

| Path | Purpose |
|------|---------|
| `/sdcard/config.json` | Your settings (created by the Replica app) |
| `/sdcard/userbot.session` | Saved Telegram login session |
| `/sdcard/login_qr.png` | Temporary QR image, removed after login |
| `/sdcard/Proof.png` | Latest comment proof screenshot |
| `~/logs.txt` | Persistent log (Termux home folder) |
| `/sdcard/Download/logs.txt` | Copy created when you tap **SHARE** |

---

## 15. Security Notes

- **Never share `config.json`, `userbot.session`, your `api_hash`, or your license key.** Anyone with the session file can access your Telegram account.
- Remove secrets from logs and screenshots before posting them anywhere public. When in doubt, send logs privately to the developer only.
- To revoke Replica's Telegram access at any time: Telegram > **Settings > Devices** and terminate the session.
- Deleting `/sdcard/userbot.session` forces a fresh QR login on the next run.

---

## 16. FAQ

**Do I need a computer?**
No. Everything runs on the phone.

**Why do I need a second phone?**
Telegram's QR login must be scanned by a device already logged in to your account. It is needed only the first time.

**Do I need Wi-Fi all the time?**
Wi-Fi is needed to enable Wireless Debugging and pair. Internet is needed throughout for Telegram, Instagram, and the license/solver servers.

**Can I use the phone while it runs?**
No. Replica controls the screen. Using the phone at the same time will break tasks.

**Why does it say "emulator-5554"?**
Replica locks ADB to local port 5555, which Android tools list as `emulator-5554`. This is normal.

**Can I run it on a 32-bit phone?**
No. A 64-bit (arm64) device is required.

**My license says "Invalid" after I changed my API credentials.**
The license is tied to your `api_hash`. Contact the developer.

---

## 17. Support

If you hit a problem you cannot solve:

1. Follow Section 12 (do not clear logs, use **SHARE**).
2. Send the log file together with a short description of what happened.

**Developer contact:** `<your Telegram / email here>`

Bug reports and suggestions can also be opened as **GitHub Issues** (attach logs with secrets removed).

---

<p align="center">Made for Android 11+ · 64-bit · Termux</p>
