import os
import sys
import time
import shutil
import socket
import sqlite3
import subprocess
import threading
import tty
import termios

# ==========================================
# TERMINAL & KEYBOARD UTILITIES
# ==========================================
def clear_terminal():
    """Clear the terminal screen completely."""
    os.system("clear" if os.name != "nt" else "cls")

def start_gate():
    """First screen: Continue starts the script, Cancel returns to a cleared terminal."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Button, Static

    class GateApp(App):
        CSS = """
        Screen { background: #000000; color: #F5EFFF; align: center top; }
        #gate_card { width: 94%; height: auto; margin-top: 1; border: round #9D7BFF; background: #000000; padding: 1 2; }
        #gate_msg { width: 100%; text-align: center; color: #E8DEFF; text-style: bold; }
        #gate_card Button { width: 100%; min-width: 0; height: 3; text-style: bold; margin-top: 1; }
        #go { background: #03170B; color: #5CF2A0; border: round #2BE37F; }
        #cancel { background: #2A0710; color: #FF8A96; border: round #FF4D61; }
        """
        ENABLE_COMMAND_PALETTE = False

        def compose(self) -> ComposeResult:
            with Vertical(id="gate_card"):
                yield Static("Press continue to setup", id="gate_msg")
                yield Button("Continue", id="go")
                yield Button("Cancel", id="cancel")

        def on_button_pressed(self, event: Button.Pressed):
            # Keep the screen (and its mouse tracking) alive for a moment so the finger-lift of
            # the tap is still swallowed by the app; exiting instantly lets Termux treat it as a
            # tap on the terminal and pop the keyboard up.
            for btn in self.query(Button):
                btn.disabled = True
            choice = event.button.id == "go"
            self.set_timer(0.8, lambda: self.exit(choice))

    return GateApp().run() is True

# Clear terminal immediately when the script starts
clear_terminal()

# First screen: Continue starts everything, Cancel leaves just a cleared terminal
if not os.environ.pop("REPLICA_SKIP_GATE", None) and not start_gate():
    clear_terminal()
    sys.exit(0)
clear_terminal()

# ==========================================
# STORAGE PERMISSION CHECK
# ==========================================
def has_storage_access():
    return os.access('/sdcard/', os.R_OK)

def release_termux():
    """Release the wakelock and stop TermuxService (removes the notification, closes the app)."""
    try:
        subprocess.run(
            ["termux-wake-unlock"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            [
                "am", "startservice", "--user", "0",
                "-n", "com.termux/com.termux.app.TermuxService",
                "-a", "com.termux.service_stop"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

def close_termux():
    """Releases wakelock, removes notification, and exits Termux completely without prompt."""
    release_termux()
    # os._exit skips every finally block / shutdown hook, so nothing can relaunch Termux
    # or recreate files (e.g. the Telegram session) after this point.
    sys.stdout.flush()
    os._exit(0)

async def exit_termux_now():
    """
    The one exit logic used by the Exit button and the QR-expired exit:
    release the wakelock, stop TermuxService, end the process immediately (no relaunch).
    """
    await asyncio.to_thread(release_termux)
    os._exit(0)

def ensure_storage_permission():
    if has_storage_access():
        print("Storage permission granted")
        return

    print("Requesting storage permission...")

    # Bypass the Termux (y/n) prompt by removing the existing symlink folder
    storage_dir = os.path.expanduser("~/storage")
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir, ignore_errors=True)

    # Trigger the permission request
    os.system("termux-setup-storage")

    # Silently wait up to 7 seconds
    for _ in range(7):
        if has_storage_access():
            print("Storage permission granted")
            return
        time.sleep(1)

    # If the loop completes without permission being granted
    print("Storage permission not granted exiting...")
    close_termux()

ensure_storage_permission()

import asyncio

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import random
import json
import logging
import re
import io
import textwrap
import base64
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import Future
from datetime import datetime, timedelta

import uiautomator2 as u2
from telethon import TelegramClient, events, errors
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.types import MessageEntityTextUrl

import qrcode
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Static

from PIL import Image, ImageOps
import cv2
import numpy as np

# ==========================================
# CONFIGURATION
# ==========================================
# Image cleaning kernels: gap healing and text thickening
HEAL_K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
BOLD_K = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

# Image preprocessing before sending to the solver
UPSCALE = 3   # Enlarge small captchas
PAD = 40      # White border (px) so shapes touching the edge are not cut off

TARGET_CHAT = "@SmmKingdomTasksBot"
SESSION_PATH = '/sdcard/userbot'
AI_PROXY_URL = "https://replica.imrajatroel.workers.dev"

# Captcha emoji -> label returned by the solver
EMOJI_TO_LABEL = {
    "☎": "telephone",
    "😎": "sunglasses_face",
    "■": "square",
    "⚠": "warning_sign",
    "✔": "checkmark",
    "▲": "triangle",
    "★": "star",
    "✈": "airplane",
    "☹": "sad_face",
    "✖": "cross",
    "☺": "happy_face",
    "☀": "sun",
    "●": "dome",
    "◆": "diamond",
}

COMPANION_PKG = "com.replica"          # Replica companion app: config editor + QR image viewer
CONFIG_PATH = "/sdcard/config.json"
CONFIG_HELP = ("Install the Replica app first, open it, fill in your details and tap Save\n"
               "to create config.json, then run this script again.")

def fail_startup(title, detail="", seconds=7, prefix="ERROR: "):
    """Show a startup error, count down, then run the exit logic (clear screen + close Termux)."""
    print(f"\n{prefix}{title}")
    if detail:
        print(detail)
    print()
    for remaining in range(seconds, 0, -1):
        print(f"\rExiting ({remaining})   ", end="", flush=True)
        time.sleep(1)
    clear_terminal()
    close_termux()

if not os.path.exists(CONFIG_PATH):
    fail_startup("Please create your config first using Replica...", prefix="")

try:
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

    API_ID = int(config["api_id"])
    API_HASH = config["api_hash"]
    LICENSE_KEY = config.get("license_key", "NONE")
    accounts = config["accounts"]
except Exception as e:
    fail_startup(f"config.json is invalid or incomplete ({e}).", CONFIG_HELP)

HWID = API_HASH

# ==========================================
# LICENSE CHECK
# ==========================================
LICENSE_OK = False
LICENSE_MSG = "checking..."
LICENSE_EXPIRES = ""     # human readable expiry date, e.g. "31 Dec 2026" (empty if the server didn't send one)

def format_expiry(raw):
    """Turn the server's expiry value into '31 Dec 2026'. Falls back to the raw text."""
    raw = str(raw or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y")
    except Exception:
        return raw

def check_license():
    """Verify the license with the server. Updates LICENSE_OK / LICENSE_MSG / LICENSE_EXPIRES."""
    global LICENSE_OK, LICENSE_MSG, LICENSE_EXPIRES
    try:
        response = requests.post(AI_PROXY_URL, json={"license_key": LICENSE_KEY, "hwid": HWID, "action": "verify"}, timeout=10)
        if response.status_code == 403:
            LICENSE_OK = False
            LICENSE_EXPIRES = ""
            LICENSE_MSG = response.json().get("error", "Invalid or Expired License")
        elif response.status_code == 200:
            body = response.json()
            if body.get("success") is True:
                LICENSE_OK = True
                LICENSE_MSG = "valid"
                LICENSE_EXPIRES = format_expiry(body.get("expires"))
            else:
                LICENSE_OK = False
                LICENSE_EXPIRES = ""
                LICENSE_MSG = body.get("error", "unexpected server reply")
        else:
            # Any other status (500, 1101 worker error, ...) is NOT a valid license
            LICENSE_OK = False
            LICENSE_EXPIRES = ""
            try:
                LICENSE_MSG = response.json().get("error", f"server error (HTTP {response.status_code})")
            except Exception:
                LICENSE_MSG = f"server error (HTTP {response.status_code})"
    except Exception as e:
        LICENSE_OK = False
        LICENSE_EXPIRES = ""
        LICENSE_MSG = f"server offline ({e})"
    return LICENSE_OK

# ==========================================
# GLOBALS
# ==========================================
current_account_index = 0
active_phone_account_index = None  
pending_task = None
pending_text = None
bot_replied_event = asyncio.Event()

RUNNING = False          # True while the automation is active
ui = None                # the running BotApp (None before the UI starts)
QUIT_REASON = ""         # set when the user leaves the dashboard on purpose
EXIT_TERMUX = False      # set by the Exit button: close Termux completely after the UI stops

# Bot messages that must never trigger any action
ABSOLUTE_BLOCKS = [
    "replenished",
    "audit",
    "wasn't completed",
    "arena",
    "news",
    "thank you, we will check it",
    "new order in the system",
    "money was sent",
    "rename",
    "penalty was applied",
    "screenshots failed",
    "demand",
    "withdraw",
    "+1",
    "you've set account",
    "was not rewarded",
    "language :",
    "cancel notifications",
    "promo codes and news",
    "my cashcoins :",
    "please write to our manager"
]

SOFT_BLOCKS = ["warnings"]

def delete_session_files():
    """Delete userbot.session and its journal if they exist."""
    for path in (f"{SESSION_PATH}.session", f"{SESSION_PATH}.session-journal"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

def session_is_corrupt():
    """True when the session file exists but is not a healthy Telethon session database."""
    path = f"{SESSION_PATH}.session"
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return True
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return "sessions" not in tables
        finally:
            conn.close()
    except Exception:
        return True

def is_db_error(error):
    text = str(error).lower()
    return isinstance(error, sqlite3.Error) or "database" in text or "malformed" in text

if session_is_corrupt():
    print("Session file is corrupted. Deleting it - you will need to log in again.")
    delete_session_files()
    time.sleep(2)

try:
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
except Exception as _e:
    if not is_db_error(_e):
        raise
    print("Session file could not be opened. Deleting it - you will need to log in again.")
    delete_session_files()
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
d = None 

# ==========================================
# UI BRIDGE
# ==========================================
def log(message=""):
    """Write a line to the UI log. Safe to call from any thread."""
    app = ui
    if app is None:
        print(message)
        return
    try:
        app.call_from_thread(app.add_log, str(message))
    except RuntimeError:
        # Already on the UI thread
        app.add_log(str(message))
    except Exception:
        pass

# Session statistics shown on the dashboard (cashcoins kept in tenths to avoid float drift)
STATS = {"likes": 0, "comments": 0, "skipped": 0, "captchas": 0, "tenths": 0}
RUN_STARTED = None   # time.time() when the automation started (None until the first start)
RUN_ENDED = None     # time.time() when it stopped (None while running); freezes the runtime on screen
LIKE_REWARD_TENTHS = 11      # 1.1 cashcoins
COMMENT_REWARD_TENTHS = 60   # 6.0 cashcoins

# ------------------------------------------
# Health monitor: what the dashboard shows so the user never has to dig through Telegram
# ------------------------------------------
STALL_WARN_SECONDS = 300     # running, but no task finished for 5 min -> LAST TASK turns yellow
STALL_BAD_SECONDS = 600      # ... for 10 min -> red

HEALTH = {
    "last_ok_ts": None,        # time of the last successfully finished task (like / comment)
    "error_ts": None,          # last error
    "error_msg": "",
    "stop_ts": None,           # last stop
    "stop_msg": "",
}

# Short reasons collected by the worker threads so the message handler can report *why* a CAPTCHA step failed
ISSUES = {"solver": "", "chrome": ""}

def fmt_time(ts):
    """Exact time of an event: 14:35:02 for today, 27 Oct 14:35:02 for an older day."""
    moment = datetime.fromtimestamp(ts)
    if moment.date() == datetime.now().date():
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%d %b %H:%M:%S")

def fmt_age(seconds):
    """How long ago something happened: 45s, 2m 05s, 1h 02m."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"

def build_rows_text(rows, label_w, width):
    """Render (label, value, style) rows; long values wrap under the value column."""
    text = Text()
    first = True
    for label, value, style in rows:
        lines = textwrap.wrap(str(value), width=max(12, width - label_w - 3), break_long_words=True) or [""]
        for i, line in enumerate(lines):
            if not first:
                text.append("\n")
            first = False
            text.append(f"{label if i == 0 else '':<{label_w}} ", style="bold #9A93B5")
            text.append("│ ", style="#4A4565")
            text.append(line, style=style)
    return text

def refresh_ui():
    """Redraw the statistics and health panels. Safe to call from any thread."""
    app = ui
    if app is None:
        return
    try:
        app.call_from_thread(app.refresh_stats)
    except RuntimeError:
        # Already on the UI thread
        app.refresh_stats()
    except Exception:
        pass

def record_error(reason):
    """Remember the time and reason of the latest error (shown on the dashboard)."""
    HEALTH["error_ts"] = time.time()
    HEALTH["error_msg"] = " ".join(str(reason).split())[:140] or "Unknown error"
    refresh_ui()

def record_stop(reason):
    """Remember the time and reason of the latest stop (shown on the dashboard)."""
    HEALTH["stop_ts"] = time.time()
    HEALTH["stop_msg"] = " ".join(str(reason).split())[:140] or "Unknown reason"
    refresh_ui()

def reset_health():
    """Clear the health monitor (done when the user presses Start Automation)."""
    HEALTH["last_ok_ts"] = None
    HEALTH["error_ts"] = None
    HEALTH["error_msg"] = ""
    HEALTH["stop_ts"] = None
    HEALTH["stop_msg"] = ""

def record_stat(kind):
    """Count a finished task and refresh the dashboard tile."""
    if kind == "like":
        STATS["likes"] += 1
        STATS["tenths"] += LIKE_REWARD_TENTHS
        HEALTH["last_ok_ts"] = time.time()
    elif kind == "comment":
        STATS["comments"] += 1
        STATS["tenths"] += COMMENT_REWARD_TENTHS
        HEALTH["last_ok_ts"] = time.time()
    elif kind == "skip":
        STATS["skipped"] += 1
    elif kind == "captcha":
        STATS["captchas"] += 1
    refresh_ui()

def reset_stats():
    """Zero the task counters and the runtime. Done when the user presses Start Automation,
    NOT when it stops, so the numbers of the finished run stay on screen."""
    global RUN_STARTED, RUN_ENDED
    RUN_STARTED = None
    RUN_ENDED = None
    for key in STATS:
        STATS[key] = 0
    refresh_ui()

def runtime_text():
    """Time since the automation started as HH:MM:SS (00:00:00 while stopped)."""
    if RUN_STARTED is None:
        return "00:00:00"
    seconds = max(0, int((RUN_ENDED or time.time()) - RUN_STARTED))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

class TelethonLogHandler(logging.Handler):
    """Forward Telethon warnings (e.g. failed connection attempts) to the log screen."""
    def emit(self, record):
        try:
            log(f"[telegram] {record.getMessage()}")
        except Exception:
            pass

def ui_set_state(text):
    if ui is not None:
        try:
            ui.set_state(text)
        except Exception:
            pass

# ==========================================
# ADB WIRELESS DEBUGGING
# ==========================================
ADB_TMP_DIR = os.path.join(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"), "tmp")
adb_serial = None

def adb_prepare():
    """Termux needs a writable TMPDIR for adb."""
    prefix = os.environ.get("PREFIX", "")
    if prefix:
        tmp_dir = os.path.join(prefix, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        os.environ["TMPDIR"] = tmp_dir
    else:
        os.makedirs(ADB_TMP_DIR, exist_ok=True)
        os.environ["TMPDIR"] = ADB_TMP_DIR

def run_adb(*args, timeout=20):
    """Run an adb command and return its combined output."""
    try:
        result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)
        return (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return "error: adb not found (run: pkg install android-tools)"
    except subprocess.TimeoutExpired:
        return "error: adb timed out"
    except Exception as e:
        return f"error: {e}"

def run_adb_shell_cmd(command, show_output=False):
    """Run an adb shell string command (used by the startup adb.py flow)."""
    result = subprocess.run(f"adb {command}", shell=True, capture_output=True, text=True)
    if show_output and result.stdout:
        print(result.stdout.strip())
    return (result.stdout + result.stderr).lower()

ADB_NAME = ""

def get_device_name(serial):
    """Marketing name of the phone (e.g. 'Redmi Note 13'); falls back to the model, then the serial."""
    def prop(key):
        if not serial:
            return ""
        out = run_adb("-s", serial, "shell", "getprop", key, timeout=8).strip()
        return "" if out.lower().startswith("error") else out
    name = prop("ro.product.marketname") or prop("ro.config.marketing_name")
    if not name:
        name = prop("ro.product.model")
        brand = prop("ro.product.manufacturer")
        if name and brand and brand.lower() not in name.lower():
            name = f"{brand.title()} {name}"
    return name or serial or ""

async def load_device_name():
    global ADB_NAME
    ADB_NAME = await asyncio.to_thread(get_device_name, adb_serial)

def hide_keyboard():
    """
    Close the soft keyboard with one BACK key press.
    Only called right after a first-time login that needed the 2FA password (the only time
    the keyboard is open); never call it while the keyboard is closed, or Termux minimizes.
    """
    try:
        target_args = ("-s", adb_serial) if adb_serial else ()
        run_adb(*target_args, "shell", "input", "keyevent", "4", timeout=5)
    except Exception:
        pass

def adb_devices():
    """Return [(serial, state)] parsed from `adb devices`."""
    devices = []
    listing = False
    for line in run_adb("devices").splitlines():
        if line.startswith("List of devices"):
            listing = True
            continue
        parts = line.split()
        if listing and len(parts) == 2:
            devices.append((parts[0], parts[1]))
    return devices

def adb_alive(serial):
    """True when the device really answers shell commands."""
    return "ok" in run_adb("-s", serial, "shell", "echo", "ok", timeout=10).split()

def port_open(port=5555, host="127.0.0.1", timeout=1.0):
    """True when something is listening on host:port (e.g. the background adbd on 5555)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def adb_poll_ready(base_wait=6, auth_wait=60, announce=False):
    """
    Wait for a working device and return its serial, or None.
    - base_wait: seconds to wait for any device to show up in `adb devices`.
    - auth_wait: if a device is listed but unauthorized/offline (the "Allow this computer"
      popup), keep waiting up to this long so the user has time to tap Allow.
    """
    start = time.time()
    deadline = start + base_wait
    announced = False
    reconnected = False
    while True:
        devices = adb_devices()
        for serial, state in devices:
            if state == "device" and adb_alive(serial):
                return serial
        pending = [(sr, st) for sr, st in devices if st in ("unauthorized", "offline", "connecting", "authorizing")]
        if pending:
            deadline = max(deadline, start + auth_wait)
            if announce and not announced:
                print("Device found. Tap ALLOW on the 'Allow USB debugging' popup (tick 'Always allow')...")
                announced = True
            if not reconnected and any(st == "offline" for _, st in pending):
                run_adb("reconnect", "offline", timeout=15)
                reconnected = True
        if time.time() >= deadline:
            return None
        time.sleep(1)

def adb_find_ready(attempts=4, delay=1.5, auth_wait=45):
    """Start the adb server and return the serial of a working device, or None."""
    adb_prepare()
    if shutil.which("adb") is None:
        return None
    run_adb("start-server", timeout=30)
    return adb_poll_ready(base_wait=attempts * delay, auth_wait=auth_wait)

def adb_wait_ready(timeout=30):
    """Wait until a device is authorized and answering. Returns its serial or None."""
    return adb_poll_ready(base_wait=timeout, auth_wait=timeout)

def to_endpoint(value):
    """Turn '37123' into 'localhost:37123'; accept 'host:port' as is; None if invalid."""
    value = (value or "").strip()
    if value.isdigit():
        return f"localhost:{value}" if 1 <= int(value) <= 65535 else None
    m = re.fullmatch(r"[\w.\-]+:(\d{1,5})", value)
    if m and 1 <= int(m.group(1)) <= 65535:
        return value
    return None

def adb_pair(endpoint, code):
    return run_adb("pair", endpoint, code, timeout=30)

def adb_connect(endpoint):
    return run_adb("connect", endpoint, timeout=30)

def adb_lock_tcpip_5555():
    """Lock port 5555 and restart the ADB server so emulator-5554 persists in the background."""
    run_adb("tcpip", "5555", timeout=20)
    time.sleep(3)
    run_adb("kill-server", timeout=20)
    run_adb("start-server", timeout=30)
    time.sleep(2)

# ------------------------------------------
# Automatic wireless debugging setup (no manual IP / port typing)
#   * ports are found automatically via mDNS (needs: pip install zeroconf)
#   * the pairing code is typed into a Termux notification (needs: pkg install termux-api + Termux:API app)
#   * everything runs on localhost, then ADB is locked to port 5555
#   * the notification is always removed when the setup ends, whatever the outcome
# ------------------------------------------
ADB_NOTIF_ID = "replica_adb"
ADB_WORK_DIR = os.path.join(os.path.expanduser("~"), ".replica_adb")

def notif_available():
    """True when the Termux:API notification command is installed."""
    return shutil.which("termux-notification") is not None

def _quiet(cmd, timeout=10):
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:
        pass

def notif_ask(title, content, outfile):
    """Post a notification with a reply box. The typed reply is written to outfile."""
    try:
        os.remove(outfile)
    except OSError:
        pass
    _quiet(["termux-notification", "--id", ADB_NOTIF_ID, "--priority", "max",
            "--title", title, "--content", content,
            "--button1", "Enter", "--button1-action", f'echo "$REPLY" > "{outfile}"'])

def notif_update(content):
    """Replace the reply notification in place (clears Android's 'sending' state)."""
    _quiet(["termux-notification", "--id", ADB_NOTIF_ID, "--title", "ADB pairing",
            "--content", content, "--priority", "low", "--alert-once"])

def notif_clear():
    """Remove the notification for good. Repeated because some ROMs re-show a replied notification."""
    if not notif_available():
        return
    for i in range(3):
        _quiet(["termux-notification-remove", ADB_NOTIF_ID])
        if i < 2:
            time.sleep(0.8)

def wait_for_file(path, seconds):
    """Wait until path exists and is not empty."""
    for _ in range(int(seconds)):
        try:
            if os.path.getsize(path) > 0:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False

def read_numbers(path):
    """All digit groups typed into a notification reply."""
    try:
        with open(path, "r") as f:
            return re.findall(r"\d+", f.read())
    except OSError:
        return []

def adb_discover(kind, timeout, stop=None, sink=None):
    """
    Find wireless debugging ports through mDNS ('pair' or 'connect').
    Found ports are appended to `sink` while searching; the list is also returned.
    Returns an empty list when zeroconf is not installed.
    """
    ports = sink if sink is not None else []
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceInfo
    except Exception:
        return ports

    stype = "_adb-tls-pairing._tcp.local." if kind == "pair" else "_adb-tls-connect._tcp.local."
    names = []

    class Listener:
        def add_service(self, zc, t, n):
            if n not in names:
                names.append(n)
        def update_service(self, zc, t, n):
            if n not in names:
                names.append(n)
        def remove_service(self, zc, t, n):
            pass

    zc = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, stype, Listener())
        end = time.time() + timeout
        first = None
        resolved = set()
        while time.time() < end and not (stop is not None and stop.is_set()):
            for name in list(names):
                if name in resolved:
                    continue
                info = ServiceInfo(stype, name)
                if info.request(zc, 3000) and info.port:
                    resolved.add(name)
                    if info.port not in ports:
                        ports.append(info.port)
            if ports and first is None:
                first = time.time()
            if first and time.time() - first > 1.5:
                break
            time.sleep(0.2)
    except Exception:
        pass
    finally:
        try:
            if zc is not None:
                zc.close()
        except Exception:
            pass
    return ports

def adb_try_connect(endpoint):
    return "connected to" in run_adb("connect", endpoint, timeout=15).lower()

def adb_find_and_connect(timeout):
    """Connect to the first discovered wireless debugging port. Returns 'localhost:port' or None."""
    for port in adb_discover("connect", timeout):
        endpoint = f"localhost:{port}"
        if adb_try_connect(endpoint):
            return endpoint
    return None

def adb_auto_pair(say):
    """Pair using a code typed in the notification; the pairing port is found automatically."""
    code_file = os.path.join(ADB_WORK_DIR, "code")
    found = []
    stop = threading.Event()
    worker = threading.Thread(target=adb_discover, args=("pair", 120, stop, found), daemon=True)
    worker.start()
    try:
        say("Open Wireless debugging > Pair device with pairing code")
        notif_ask("ADB pairing", "Tap Enter and type the 6-digit pairing code", code_file)
        say("Waiting for pairing code (use the notification)")
        if not wait_for_file(code_file, 120):
            say("No pairing code entered")
            return False
        notif_update("Pairing...")

        numbers = read_numbers(code_file)
        if not numbers:
            say("Pairing code not understood")
            return False
        code = numbers[0]
        port = numbers[1] if len(numbers) > 1 else None
        if not port:
            for _ in range(15):
                if found:
                    break
                time.sleep(1)
            port = str(found[-1]) if found else None
        if not port:
            say("Pairing port not detected (reply with: code port)")
            return False

        out = adb_pair(f"localhost:{port}", code)
        if "successfully paired" in out.lower():
            return True
        say("Pairing failed")
        return False
    finally:
        stop.set()

def adb_ask_connect_port(say):
    """Fallback when the connect port cannot be discovered: ask for it in the notification."""
    port_file = os.path.join(ADB_WORK_DIR, "cport")
    notif_ask("Wireless debugging port", "Enter the port shown on the Wireless debugging screen", port_file)
    say("Waiting for connect port (use the notification)")
    if not wait_for_file(port_file, 90):
        say("No port entered")
        return None
    notif_update("Connecting...")
    numbers = read_numbers(port_file)
    endpoint = to_endpoint(numbers[-1]) if numbers else None
    if endpoint and adb_try_connect(endpoint):
        return endpoint
    say("Could not connect on that port")
    return None

def adb_auto_setup(say=print):
    """
    Fully automatic wireless debugging: find ports, pair with the notification code, connect,
    lock port 5555. Returns the device serial, or None (caller can fall back to manual entry).
    The notification is removed in every case.
    """
    if shutil.which("adb") is None or not notif_available():
        return None
    adb_prepare()
    try:
        os.makedirs(ADB_WORK_DIR, exist_ok=True)
        notif_clear()   # drop a leftover notification from an interrupted earlier run
        run_adb("start-server", timeout=30)

        addr = None
        if adb_try_connect("localhost:5555"):
            addr = "localhost:5555"
        else:
            addr = adb_find_and_connect(4)
            if not addr:
                if not adb_auto_pair(say):
                    return None
                addr = adb_find_and_connect(30) or adb_ask_connect_port(say)
                if not addr:
                    return None

        if addr != "localhost:5555":
            run_adb("-s", addr, "tcpip", "5555", timeout=20)
            time.sleep(3)
            run_adb("disconnect", addr, timeout=10)
            adb_try_connect("localhost:5555")

        serial = adb_poll_ready(base_wait=10, auth_wait=60)
        if serial:
            say("ADB connected")
        return serial
    except Exception as e:
        say(f"ADB auto setup error: {e}")
        return None
    finally:
        notif_clear()
        shutil.rmtree(ADB_WORK_DIR, ignore_errors=True)

def ensure_adb_startup():
    """
    At startup, detect an already open background ADB port (emulator-5554 / 5555).
    Only when nothing is reachable (after giving the authorization popup time) ask
    for the pairing details.
    """
    global adb_serial
    adb_prepare()

    if shutil.which("adb") is None:
        print("ERROR: adb not found (run: pkg install android-tools)")
        return None

    print("Checking existing ADB connection...")
    run_adb("start-server", timeout=30)

    # 1. A device may already be listed (emulator-5554). Wait for it, including the Allow popup.
    serial = adb_poll_ready(base_wait=6, auth_wait=60, announce=True)

    # 2. Nothing listed but port 5555 is open: connect to it directly
    if not serial and not adb_devices() and port_open(5555):
        print("Background ADB port 5555 is open. Connecting...")
        run_adb("connect", "localhost:5555", timeout=15)
        serial = adb_poll_ready(base_wait=8, auth_wait=60, announce=True)

    if serial:
        adb_serial = serial
        print(f"ADB already connected: {serial}")
        return serial

    print("No usable background ADB port detected. Running first-time ADB setup...\n")

    # 3. Automatic setup: ports found by mDNS, pairing code typed in a notification
    if notif_available():
        for attempt in range(2):
            serial = adb_auto_setup(print)
            if serial:
                adb_serial = serial
                print(f"\nADB successfully connected ({serial}). Moving to UI...\n")
                return serial
            if attempt == 0:
                print("Retrying automatic setup...\n")
        print("Automatic setup failed. Falling back to manual entry.\n")
    else:
        print("Tip: install termux-api (pkg install termux-api + Termux:API app) for automatic ADB setup.\n")

    while True:
        connect_ip_port = input("Enter device IP & PORT: ").strip()
        pair_ip_port = input("Enter pairing IP & PORT: ").strip()
        pair_code = input("Enter pairing CODE: ").strip()

        print("\nConnecting...\n")

        conn_target = to_endpoint(connect_ip_port) or connect_ip_port
        pair_target = to_endpoint(pair_ip_port) or pair_ip_port

        if pair_target and pair_code:
            run_adb_shell_cmd(f"pair {pair_target} {pair_code}")

        for _ in range(3):
            output = run_adb_shell_cmd(f"connect {conn_target}")
            if "connected" in output and "failed" not in output:
                break
            time.sleep(2)

        # Lock port 5555 and restart the server so emulator-5554 persists
        run_adb_shell_cmd("tcpip 5555")
        time.sleep(3)
        run_adb_shell_cmd("kill-server")
        run_adb_shell_cmd("start-server")
        time.sleep(2)

        run_adb_shell_cmd("devices", show_output=True)

        serial = adb_poll_ready(base_wait=10, auth_wait=60, announce=True)
        if serial:
            adb_serial = serial
            print(f"\nADB successfully connected ({serial}). Moving to UI...\n")
            return serial

        print("\nADB device not detected or not authorized yet. Please try again.\n")

# ==========================================
# DEVICE CONNECTION
# ==========================================
def connect_with_retry(max_retries=5, delay=3):
    """Connect to uiautomator2, retrying on failure. Returns the device or None."""
    for attempt in range(1, max_retries + 1):
        try:
            log(f"Connecting to uiautomator2... (Attempt {attempt}/{max_retries})")
            device = u2.connect(adb_serial)
            device.info  
            log("UIAutomator Connection successful!")
            return device
        except Exception as e:
            log(f"Connection failed: {e}")
            if attempt < max_retries:
                time.sleep(delay)
    log("Could not connect to the device.")
    return None

def cleanup_apps(device, ig_pkg, chrome_pkg):
    """Close Instagram and Chrome."""
    log("Cleaning up apps...")
    device.app_stop(chrome_pkg)
    device.app_stop(ig_pkg)

# ==========================================
# DEVICE ACTIONS
# ==========================================
def perform_switch(device, target):
    """Switch Instagram to the target account via the profile switcher."""
    ig_pkg = "com.instagram.android"
    screen_width, screen_height = device.window_size()

    device.app_stop(ig_pkg)
    time.sleep(1.5)
    device.app_start(ig_pkg)

    profile_btn = device(resourceIdMatches=".*profile_tab.*")
    
    if profile_btn.exists(timeout=25):
        time.sleep(1.5)
        profile_btn.long_click(duration=1.0)
    else:
        log("Unknown error")
        device.app_stop(ig_pkg)
        return False
        
    time.sleep(3.5)

    meta_logo_pattern = "(?i)^(Логотип Meta|Meta logo)$"

    # Scroll the account list until the target is found or the list ends
    while True:
        target_elem = device(text=target)

        if target_elem.exists(timeout=0):
            target_elem.click()
            time.sleep(3.5)
            device.app_stop(ig_pkg)
            return True

        if device(descriptionMatches=meta_logo_pattern).exists(timeout=0):
            break

        device.swipe(screen_width * 0.5, screen_height * 0.8, screen_width * 0.5, screen_height * 0.4, duration=0.5)
        time.sleep(3.5) 

    log(f"Account '{target}' NOT found.")
    device.app_stop(ig_pkg)
    return False

def perform_like(device, post_url):
    """Open the post and like it with a native double tap."""
    ig_pkg = "com.instagram.android"
    chrome_pkg = "com.android.chrome"
    screen_width, screen_height = device.window_size()

    device.app_stop(ig_pkg)
    time.sleep(1.5)
    device.shell(f'am start -a android.intent.action.VIEW -d "{post_url}" -f 0x10000000 -p {ig_pkg}')

    media_options = device(resourceId="com.instagram.android:id/media_option_button")
    if not media_options.exists(timeout=15):
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

    # Tap point: horizontal center, half a screen width below the 3-dots menu
    bounds = media_options.info['bounds']
    target_x = screen_width // 2
    target_y = bounds['bottom'] + (screen_width // 2)

    time.sleep(3.5)
    # Both taps run in one shell command to avoid Python-side latency
    device.shell(f"input tap {target_x} {target_y} && sleep 0.125 && input tap {target_x} {target_y}")
    time.sleep(3.5)

    cleanup_apps(device, ig_pkg, chrome_pkg)
    return True

def perform_comment(device, post_url, my_comment):
    """Open the post, publish the comment and save a proof screenshot."""
    ig_pkg = "com.instagram.android"
    chrome_pkg = "com.android.chrome"
    screen_width, screen_height = device.window_size()

    device.app_stop(ig_pkg)
    time.sleep(1.5)
    device.shell(f'am start -a android.intent.action.VIEW -d "{post_url}" -f 0x10000000 -p {ig_pkg}')

    # Find the comment button, scrolling once if it is not visible
    comment_btn = device(resourceId="com.instagram.android:id/row_feed_button_comment")
    if not comment_btn.exists(timeout=15):
        time.sleep(2.5)
        device.swipe(screen_width * 0.5, screen_height * 0.80, screen_width * 0.5, screen_height * 0.40, duration=0.7)
        time.sleep(2.5) 
        comment_btn = device(resourceId="com.instagram.android:id/row_feed_button_comment")

    if not comment_btn.exists(timeout=5):
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

    time.sleep(2.5)
    comment_btn.click()
    time.sleep(2.5) 

    input_box = device(resourceIdMatches=".*layout_comment_thread_edittext.*")
    
    if input_box.exists(timeout=5):
        input_box.set_text(my_comment)
        time.sleep(0.5) 
    else:
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False
        
    post_pattern = "(?i)^(Post|Опубликовать|Допис)$"
    post_btn = device(descriptionMatches=post_pattern)

    if post_btn.exists(timeout=5):
        post_btn.click()
        time.sleep(2.5)
    
    # The edit button appears once the comment is published
    edit_button = device(resourceIdMatches=".*row_comment_textview_edit_button.*")
    
    success = False
    for attempt in range(15):
        if edit_button.exists():
            success = True
            break
        time.sleep(1.0)
        
    if success:
        device.screenshot().save("/sdcard/Proof.png")
        time.sleep(2.5)
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return True
    else:
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

def perform_captcha(device, hidden_url, answer_box):
    """
    Open the captcha page in Chrome immediately and pass the Turnstile check.
    Then wait for the solver result (answer_box) and submit its first answer.
    """
    chrome_pkg = "com.android.chrome"
    ISSUES["chrome"] = "Chrome check was not passed within 30s"
    device.shell(f'am start -a android.intent.action.VIEW -d "{hidden_url}" -p {chrome_pkg}')
    
    lang_pattern = "(?i)^(Verify you are human|Подтвердите, что вы человек|Підтвердьте, що ви людина)$"
    success_pattern = "(?i)^(Success!|Успех!|Успіх!)$"

    start_time = time.time()

    while time.time() - start_time < 30:
        # Tap the Turnstile checkbox when it is visible
        device.dump_hierarchy()
        verify_target = device(descriptionMatches=lang_pattern)
        if verify_target.exists():
            bounds = verify_target.info['bounds']
            target_x = (bounds['left'] + bounds['right']) // 2
            target_y = (bounds['top'] + bounds['bottom']) // 2
            device.click(target_x, target_y)
            
        time.sleep(1)
        if time.time() - start_time >= 30: break

        # Turnstile passed: wait for the solver, then submit the first answer
        device.dump_hierarchy()
        success_target = device(textMatches=success_pattern)
        if success_target.exists():
            ISSUES["chrome"] = "captcha answer box not found on the page"
            input_element = device(resourceId="captcha-answer")
            if input_element.exists(timeout=5):
                try:
                    answers = answer_box.result(timeout=90)
                except Exception:
                    ISSUES["chrome"] = "solver answer did not arrive in time"
                    return False
                if not answers:
                    ISSUES["chrome"] = "no solver answer to type"
                    return False

                input_element.click()
                input_element.set_text(answers[0])
                time.sleep(0.5)
                device.press("enter")
                time.sleep(2.0)
                return True
            break
        time.sleep(1)
        
    return False

# ==========================================
# TELEGRAM / IMAGE / QR UTILITIES
# ==========================================
def build_qr_text(url, border=2):
    """Render a QR code as half-block characters (black on white, scannable on any theme)."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    if len(matrix) % 2:
        matrix.append([False] * len(matrix[0]))

    lines = []
    for y in range(0, len(matrix), 2):
        row = ""
        for top, bottom in zip(matrix[y], matrix[y + 1]):
            row += "█" if top and bottom else "▀" if top else "▄" if bottom else " "
        lines.append(row)
    return Text("\n".join(lines), style="black on white", no_wrap=True)

def process_image_in_ram(pil_img):
    """Clean a captcha image and return it as a base64 PNG."""
    try:
        img_array = np.array(pil_img.convert("RGB"))
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        # 1. Remove gray background lines
        _, base_img = cv2.threshold(gray, 15, 255, cv2.THRESH_BINARY)
        if np.sum(base_img == 0) < 50:
            _, base_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            if np.sum(base_img == 0) < 50:
                base_img = gray
                
        # Invert: text white, background black
        inverted = cv2.bitwise_not(base_img)
        
        # 2. Heal small gaps in strokes
        healed = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, HEAL_K)
        
        # 3. Drop tiny leftover fragments
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(healed, connectivity=8)
        clean_foreground = np.zeros_like(healed)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] >= 8:
                clean_foreground[labels == i] = 255
                
        # 4. Thicken the text
        bolded = cv2.dilate(clean_foreground, BOLD_K, iterations=1)
                
        # 5. Invert back: black text on white background
        final_img = cv2.bitwise_not(bolded)

        # Grayscale, pad, upscale and encode as PNG
        img = Image.fromarray(final_img).convert("L")
        if PAD:
            img = ImageOps.expand(img, border=PAD, fill=255)
        w, h = img.size
        if UPSCALE and UPSCALE > 1 and max(w, h) < 900:
            img = img.resize((w * UPSCALE, h * UPSCALE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None

def normalize_emoji(raw):
    """Strip whitespace and invisible variation selectors (e.g. '⚠️' -> '⚠')."""
    return re.sub(r'[\s\ufe0e\ufe0f\u200d]', '', raw or "")

def get_math_answer(target_emoji, b64_images):
    """
    Send the captcha images to the solver.
    Returns the answers ordered as [emoji match, next answer, next answer],
    "LICENSE_ERROR" on a license failure, or [] if nothing usable came back.
    """
    target_label = EMOJI_TO_LABEL.get(normalize_emoji(target_emoji))
    if not target_label:
        log(f"Unknown emoji: {target_emoji!r}")
        ISSUES["solver"] = f"unknown emoji {target_emoji!r}"
        return []

    payload = {
        "license_key": LICENSE_KEY,
        "hwid": HWID,
        "action": "solve",
        "target_emoji": target_emoji,
        "images": b64_images
    }
    for attempt in range(1, 4):
        try:
            response = requests.post(AI_PROXY_URL, json=payload, timeout=30)
            if response.status_code == 403:
                log(f"\n{response.json().get('error', 'License Error')}\n")
                return "LICENSE_ERROR"

            results = response.json()["results"]

            matched = []
            others = []
            for r in results:
                if r.get("answer") is None:
                    continue
                if r.get("label") == target_label:
                    matched.append(str(r["answer"]))
                else:
                    others.append(str(r["answer"]))
            answers = matched + others
            if not answers:
                ISSUES["solver"] = "solver could not read any captcha image"
            return answers
        except Exception as e:
            if attempt < 3: time.sleep(3) 
            else:
                ISSUES["solver"] = f"solver request failed ({type(e).__name__})"
                return []

def stop_device_apps():
    """Close Chrome and Instagram on the phone."""
    if d is None:
        return
    for pkg in ("com.android.chrome", "com.instagram.android"):
        try:
            d.app_stop(pkg)
        except Exception:
            pass

async def stop_automation(reason="Stopped by user"):
    """Stop handling bot messages and close the apps. The Telegram session stays connected."""
    global RUNNING, RUN_ENDED
    was_running = RUNNING
    RUNNING = False
    if was_running:
        RUN_ENDED = time.time()       # freeze the runtime; numbers stay until the next Start
    log(f"Stopped: {reason}")
    if was_running and reason not in ("Quit", "Exit"):
        record_stop(reason)
        if reason != "Stopped by user":
            record_error(reason)      # a stop that was not the user's choice is also an error
    await asyncio.to_thread(stop_device_apps)
    refresh_ui()
    ui_set_state("Stopped")

async def kill_everything(reason):
    await stop_automation(reason)

async def smart_send(text_to_send):
    """Send a message to the bot and wait for its reply, retrying once."""
    await asyncio.sleep(random.uniform(5.0, 7.0))
    if not RUNNING: return
    if text_to_send == "❌Skip":
        record_stat("skip")
    for attempt in range(1, 3): 
        bot_replied_event.clear() 
        log(f"Clicking : {text_to_send} (Attempt {attempt}/2)")
        await client.send_message(TARGET_CHAT, text_to_send, parse_mode=None, link_preview=False)
        try:
            await asyncio.wait_for(bot_replied_event.wait(), timeout=60.0)
            return 
        except asyncio.TimeoutError:
            if not RUNNING: return
            if attempt == 1:
                log("Bot didn't reply in 60s! Trying ONE more time...")
                record_error("Bot did not reply within 60s (retrying)")
            else: log("Bot still didn't reply after 2nd attempt!")
    await kill_everything("Bot server stopped responding after 2 attempts.")

async def smart_send_photo():
    """Upload the proof screenshot to the bot and wait for its reply."""
    photo_path = "/sdcard/Proof.png"
    await asyncio.sleep(random.uniform(3.0, 4.0))
    if not RUNNING: return True
    if not os.path.exists(photo_path): return False
    
    for attempt in range(1, 3): 
        bot_replied_event.clear()
        log(f"Uploading screenshot... (Attempt {attempt}/2)")
        try:
            await client.send_file(TARGET_CHAT, photo_path)
            await asyncio.wait_for(bot_replied_event.wait(), timeout=60.0)
            return True
        except asyncio.TimeoutError:
            if attempt == 2: return False
        except Exception as e:
            if attempt == 1: await asyncio.sleep(3.0)
            else: return False

# ==========================================
# ASYNC TASK WRAPPERS
# ==========================================
async def do_switch_account(target_username):
    log(f"Switching to: {target_username}")
    success = await asyncio.to_thread(perform_switch, d, target_username)
    if not success:
        log("Account switching failed (Not found / Banned)")
        record_error(f"Could not switch Instagram to {target_username}")
    return success

async def do_like_task(link):
    log(f"Like task detected")
    success = await asyncio.to_thread(perform_like, d, link)
    if success:
        record_stat("like")
        await smart_send("✅Completed")
    else:
        record_error("Like failed: Instagram post did not open")
        await smart_send("❌Skip")
    
async def do_comment_task(link, full_msg_text):
    log("Comment task detected")
    comment_text = full_msg_text.split("Text to comment :")[1].strip() if "Text to comment :" in full_msg_text else full_msg_text.strip()
    
    # Skip comments containing mentions
    if "@" in comment_text:
        await smart_send("❌Skip")
        return
        
    log("Performing action...")
    success = await asyncio.to_thread(perform_comment, d, link, comment_text)
    if success:
        if await smart_send_photo():
            record_stat("comment")
        else:
            record_error("Comment proof screenshot could not be sent")
            await smart_send("❌Skip")
    else:
        record_error("Comment failed: could not post it on Instagram")
        await smart_send("❌Skip")

# ==========================================
# TELEGRAM MESSAGE ROUTER
# ==========================================
@client.on(events.NewMessage(chats=TARGET_CHAT, incoming=True))
async def handle_msg(event):
    """Run the router; anything unexpected is shown on the dashboard instead of vanishing silently."""
    try:
        await process_bot_message(event)
    except Exception as e:
        record_error(f"Unexpected error: {type(e).__name__}: {e}")
        log(f"Unexpected error: {type(e).__name__}: {e}")

async def process_bot_message(event):
    message = event.message
    global pending_task, pending_text, current_account_index, active_phone_account_index
    
    if not RUNNING: return

    text = getattr(message, 'raw_text', getattr(message, 'text', ''))
    if not text: return
    text_lower = text.lower()
    
    # Ignore informational messages
    if any(word in text_lower for word in ABSOLUTE_BLOCKS): return
    if "warnings" in text_lower and "profile's username" not in text_lower and "too many" not in text_lower: return

    # Stop everything when verification fails
    if "verification failed" in text_lower:
        log("Verification Failed!")
        await stop_automation("CAPTCHA verification failed")
        return

    bot_replied_event.set()

    # ------------------------------------------
    # Captcha: open Chrome immediately, solve in parallel
    # ------------------------------------------
    if "security check" in text_lower:
        log("Captcha detected")
        emoji_match = re.search(r'(?i)(link)(.*?)👉', text, re.DOTALL)
        if not emoji_match:
            record_error("Captcha: could not read the emoji from the bot message")
            return
        target_emoji = emoji_match.group(2).strip()

        # Extract the hidden verification link
        hidden_url = None
        entities = getattr(message, 'entities', None) or getattr(message, 'caption_entities', None)
        if entities:
            for ent in entities:
                if isinstance(ent, MessageEntityTextUrl):
                    hidden_url = ent.url
                    break
        if not hidden_url:
            record_error("Captcha: no verification link in the bot message")
            return
        ISSUES["solver"] = ""
        ISSUES["chrome"] = ""

        # Launch Chrome now; it passes Turnstile while the solver is working
        answer_box = Future()
        captcha_task = asyncio.create_task(asyncio.to_thread(perform_captcha, d, hidden_url, answer_box))

        answers = []
        try:
            # Collect the 3 captcha images sent just before this message
            photo_messages = []
            time_threshold = message.date - timedelta(seconds=15)
            
            async for past_msg in client.iter_messages(TARGET_CHAT, limit=10):
                if past_msg.date < time_threshold: break
                if past_msg.photo:
                    photo_messages.append(past_msg)
                    if len(photo_messages) == 3: break
            
            if len(photo_messages) == 3:
                downloads = await asyncio.gather(*[client.download_media(msg, bytes) for msg in photo_messages])
                downloads.reverse()
                b64_images = [process_image_in_ram(Image.open(io.BytesIO(img_bytes))) for img_bytes in downloads]
                
                # Ordered answers: [emoji match, next answer, next answer]
                answers = await asyncio.to_thread(get_math_answer, target_emoji, b64_images)
            else:
                ISSUES["solver"] = f"found {len(photo_messages)} of 3 captcha images"
        finally:
            # Release the Chrome thread: answer list on success, None on failure
            answer_box.set_result(answers if isinstance(answers, list) and answers else None)

        if answers == "LICENSE_ERROR":
            await captcha_task
            log("License error from proxy.")
            await stop_automation("License error")
            return

        if not answers:
            await captcha_task
            log("No valid answer received from solver.")
            record_error(f"Captcha: {ISSUES['solver'] or 'solver returned no answer'}")
            return
            
        log(f"Captcha answers computed : {answers}")
        
        captcha_success = await captcha_task
        if not captcha_success:
            log("Failed to complete Captcha UI sequence.")
            record_error(f"Captcha: {ISSUES['chrome'] or 'could not finish the Chrome step'}")
        return 

    # ------------------------------------------
    # Task routing
    # ------------------------------------------
    # Pair a comment task with its comment text (text may arrive before or after)
    if "leave the comment" in text_lower:
        if pending_text:
            text = f"{text}\n\nText to comment : {pending_text}"
            pending_text = None
            text_lower = text.lower() 
        else:
            pending_task = text
            return
    elif pending_task:
        text = f"{pending_task}\n\nText to comment : {text}"
        pending_task = None
        text_lower = text.lower() 

    match = re.search(r'(https?://[^\s\]\)\>]+)', text)
    extracted_link = match.group(1) if match else None

    if "choose social network" in text_lower:
        await smart_send("Instagram")
        
    # Account selection (rotates through configured accounts)
    elif any(phrase in text_lower for phrase in ["profile's username for tasks completing", "is on review", "please choose account from the list", "too many"]):
        account_info = accounts[current_account_index]
        current_account_index = (current_account_index + 1) % len(accounts)
        await smart_send(account_info["username"])
        
    # Like task
    elif re.search(r'1\.1\s*cashcoins', text_lower):
        if extracted_link: 
            target_index = (current_account_index - 1) % len(accounts)
            if active_phone_account_index != target_index:
                success = await do_switch_account(accounts[target_index]["username"])
                if not success:
                    await smart_send("❌Skip")
                    return 
                active_phone_account_index = target_index
            await do_like_task(extracted_link)
        else: 
            await smart_send("❌Skip")
            
    # Comment task
    elif re.search(r'6\.0\s*cashcoins', text_lower) or ("leave the comment" in text_lower and extracted_link):
        target_index = (current_account_index - 1) % len(accounts)
        if active_phone_account_index != target_index:
            success = await do_switch_account(accounts[target_index]["username"])
            if not success:
                await smart_send("❌Skip")
                return 
            active_phone_account_index = target_index
        await do_comment_task(extracted_link, text)

    # Unsupported task types
    elif re.search(r'(2\.5|1\.0)\s*cashcoins', text_lower):
        await smart_send("❌Skip")
        
    elif "saved successfully" in text_lower:
        await smart_send("✅Completed")
        
    elif "no active tasks" in text_lower:
        await smart_send("Instagram")
        
    elif "verification passed" in text_lower:
        log("Verification successful")
        record_stat("captcha")
        if d:
            await asyncio.to_thread(d.app_stop, "com.android.chrome")
        await smart_send("✅Completed")
  
    # Any other message is stored as potential comment text
    else:
        if not re.search(r'cashcoins', text_lower) and "verification" not in text_lower:
            pending_text = text

# ==========================================
# AUTOMATION CONTROL
# ==========================================
async def start_automation():
    """Connect to the phone, reset the bot chat and start the task flow."""
    global d, RUNNING, pending_task, pending_text, RUN_STARTED

    log("Initializing UIAutomator2 connection...")
    d = await asyncio.to_thread(connect_with_retry)
    if d is None:
        record_error("Could not connect to the phone (uiautomator2)")
        return False

    pending_task = None
    pending_text = None
    RUNNING = True
    RUN_STARTED = time.time()
    ui_set_state("Running")
    log("AUTOMATION STARTED SUCCESSFULLY")

    # Reset the chat and start the task flow (each step stops early if Stop is pressed)
    await asyncio.sleep(7)
    if not RUNNING: return False
    try:
        await client(DeleteHistoryRequest(peer=TARGET_CHAT, max_id=0, revoke=True))
    except Exception:
        pass

    await asyncio.sleep(7)
    if not RUNNING: return False
    log("Bot restarted")
    await client.send_message(TARGET_CHAT, "/start", parse_mode=None, link_preview=False)
    await asyncio.sleep(random.uniform(5.0, 7.0))
    if not RUNNING: return False
    await client.send_message(TARGET_CHAT, "📝Tasks📝", parse_mode=None, link_preview=False)
    return True

# ==========================================
# MAIN UI (Textual - Redesigned Modern Dashboard)
# ==========================================
class AdbScreen(ModalScreen):
    """Wireless debugging setup: pair with code, connect by port, and lock tcpip 5555."""

    CSS = """
    AdbScreen { align: center middle; background: #000000 92%; }
    #adb_box { width: 94%; height: auto; border: round #9D7BFF; background: #000000; padding: 1 2; }
    #adb_box Input { background: #000000; border: round #4F46A5; color: #F5EFFF; margin-top: 1; }
    #adb_box Input:focus { border: round #B69DFF; }
    #adb_title { text-style: bold; color: #C9B8FF; margin-bottom: 1; text-align: center; width: 100%; }
    #adb_hint { color: #B8B0D0; }
    #adb_buttons { height: auto; margin-top: 1; }
    #adb_buttons Button { width: 1fr; height: 3; margin-right: 1; border: round #4A4565; text-style: bold; }
    #adb_go { background: #064E3B; color: #5CF2A0; border: round #2BE37F; }
    #adb_cancel { background: #1F2937; color: #F1ECFF; border: round #4A4565; }
    #adb_status { margin-top: 1; color: #FFD166; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="adb_box"):
            yield Static("⚡ ADB WIRELESS SETUP", id="adb_title")
            yield Static(
                "Settings > Developer options > Wireless debugging.\n"
                "Pairing port + code: 'Pair device with pairing code' (first time only).\n"
                "Connect port: shown on the main Wireless debugging screen.",
                id="adb_hint",
            )
            yield Input(placeholder="Device IP & PORT (or connect port)", id="conn_port")
            yield Input(placeholder="Pairing IP & PORT (blank if already paired)", id="pair_port")
            yield Input(placeholder="Pairing CODE", id="pair_code")
            with Horizontal(id="adb_buttons"):
                yield Button("Connect", id="adb_go")
                yield Button("Cancel", id="adb_cancel")
            yield Static("", id="adb_status")

    def on_mount(self):
        self.query_one("#conn_port", Input).focus()

    def say(self, message):
        self.query_one("#adb_status", Static).update(Text(message))

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "adb_cancel":
            self.dismiss(None)
        elif event.button.id == "adb_go":
            self.run_worker(self.do_connect(), exclusive=True)

    async def do_connect(self):
        conn_port = self.query_one("#conn_port", Input).value.strip()
        pair_port = self.query_one("#pair_port", Input).value.strip()
        pair_code = self.query_one("#pair_code", Input).value.strip()
        button = self.query_one("#adb_go", Button)

        pair_endpoint = to_endpoint(pair_port) if pair_port else None
        conn_endpoint = to_endpoint(conn_port)
        if pair_port and not pair_endpoint:
            self.say("Pairing IP & PORT is not valid.")
            return
        if pair_port and not pair_code:
            self.say("Enter the pairing code.")
            return
        if not conn_endpoint:
            self.say("Enter the Wireless debugging device IP & PORT.")
            return

        button.disabled = True
        try:
            adb_prepare()
            if pair_endpoint:
                self.say("Pairing...")
                out = await asyncio.to_thread(adb_pair, pair_endpoint, pair_code)
                log(f"adb pair: {out}")

            self.say("Connecting...")
            connected = False
            for _ in range(3):
                out = await asyncio.to_thread(adb_connect, conn_endpoint)
                log(f"adb connect: {out}")
                if "connected" in out.lower() and "failed" not in out.lower():
                    connected = True
                    break
                await asyncio.sleep(2)

            if not connected:
                self.say(f"Connect failed:\n{out}")
                return

            self.say("Locking background port 5555 and restarting ADB server...")
            await asyncio.to_thread(adb_lock_tcpip_5555)

            self.say("Waiting for emulator/device authorization...")
            serial = await asyncio.to_thread(adb_wait_ready, 30)
            if serial:
                self.dismiss(serial)
            else:
                self.say("Device not authorized yet. Allow the popup and press Connect again.")
        finally:
            button.disabled = False

def license_error_text():
    """Short, friendly text for a failed license check."""
    known = {"invalid license key": "Invalid license key", "license expired": "License expired"}
    return known.get((LICENSE_MSG or "").strip().lower(), LICENSE_MSG)

class BotApp(App):
    """Modern Automation dashboard with internal tile headings and full-width state-aware controls."""

    CSS = """
    Screen {
        background: #000000;
        color: #F5EFFF;
    }

    #header_card {
        width: 100%;
        height: auto;
        margin: 0 1;
        padding: 0 2;
        background: #000000;
        border: round #9D7BFF;
    }

    #title {
        width: 100%;
        height: auto;
        text-align: center;
        text-style: bold;
        color: #E8DEFF;
    }

    #stats_card, #health_card {
        width: 100%;
        height: auto;
        margin: 0 1;
        padding: 0 2;
        background: #000000;
        border: round #5E4FB3;
    }

    #stats_panel, #health_panel {
        width: 100%;
        height: auto;
    }

    #info_card {
        width: 100%;
        height: auto;
        margin: 0 1;
        padding: 0 2;
        background: #000000;
        border: round #5E4FB3;
    }

    .tile_heading {
        width: 100%;
        height: auto;
        color: #B69DFF;
        text-style: bold;
        margin-bottom: 1;
    }

    #panel {
        width: 100%;
        height: auto;
    }

    #buttons {
        width: 100%;
        height: auto;
        margin: 0 1;
    }

    #buttons Button {
        width: 100%;
        min-width: 0;
        height: 3;
        text-style: bold;
        background: #000000;
        color: #F1ECFF;
        border: round #5E4FB3;
    }

    #buttons Button.start {
        background: #03170B;
        color: #5CF2A0;
        border: round #2BE37F;
    }

    #buttons Button.stop {
        background: #1F1202;
        color: #FFD166;
        border: round #FFB020;
    }

    #buttons Button.login {
        background: #120A2E;
        color: #E8DEFF;
        border: round #B69DFF;
    }

    #buttons Button:focus {
        text-style: bold;
    }

    #buttons Button:disabled {
        background: #000000;
        color: #4A4565;
        border: round #1F1B2E;
    }

    #bottom_buttons {
        width: 100%;
        height: auto;
        margin: 0 1;
    }

    #bottom_buttons Button {
        width: 1fr;
        min-width: 0;
        height: 3;
        text-style: bold;
        background: #000000;
        color: #F1ECFF;
        border: round #5E4FB3;
    }

    #bottom_buttons Button.exit {
        background: #2A0710;
        color: #FF8A96;
        border: round #FF4D61;
    }

    #bottom_buttons Button:disabled {
        background: #000000;
        color: #4A4565;
        border: round #1F1B2E;
    }

    #log_card {
        width: 100%;
        height: 1fr;
        min-height: 4;
        background: #000000;
        margin: 0 1;
        padding: 0 2;
        border: round #5E4FB3;
    }

    RichLog {
        background: #000000;
        background-tint: #000000 0%;
    }

    #log {
        width: 100%;
        height: 1fr;
        background: #000000;
        background-tint: #000000 0%;
        color: #DAD3EE;
        padding: 0;
        scrollbar-size: 0 0;
    }

    #log:focus {
        background: #000000;
        background-tint: #000000 0%;
    }
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("s", "start", "Start"),
        ("x", "stop", "Stop"),
        ("ctrl+q", "quit", "Terminal"),
    ]

    def __init__(self, account_name):
        super().__init__()
        self.account_name = account_name
        self.logged_in = bool(account_name)
        self.tg_state = "logged_in" if account_name else "logged_out"
        self._login_busy = False
        self.account_text = "checking..."
        self.tg_state = "checking"     # checking | logged_in | logged_out | offline
        self.adb_text = ADB_NAME or adb_serial or "checking..."
        self.state_text = "Starting up..."
        self._busy = False
        self._opened_at = time.time()
        self._cooldown = 0             # seconds left before Login can be pressed again

    def compose(self) -> ComposeResult:
        with Vertical(id="header_card"):
            yield Static("Automation dashboard", id="title")
        with Vertical(id="info_card"):
            yield Static("System information", classes="tile_heading")
            yield Static(id="panel")
        with Vertical(id="stats_card"):
            yield Static("Task statistics", classes="tile_heading")
            yield Static(id="stats_panel")
        with Vertical(id="health_card"):
            yield Static("System monitor", classes="tile_heading")
            yield Static(id="health_panel")
        with Vertical(id="buttons"):
            yield Button("Start Automation", id="toggle_run", classes="start")
        with Vertical(id="log_card"):
            yield Static("System logs", classes="tile_heading")
            yield RichLog(id="log", wrap=True, min_width=10, markup=False, highlight=False, max_lines=2000)
        with Horizontal(id="bottom_buttons"):
            yield Button("Exit", id="exit", classes="exit")

    # ---- panel / log helpers ----
    def state_style(self):
        lowered = self.state_text.lower()
        if RUNNING:
            return "bold #5CF2A0"
        if any(word in lowered for word in ("error", "failed", "offline", "contact developer")):
            return "bold #FF6B7A"
        if any(word in lowered for word in ("not logged", "not connected", "scan", "starting", "checking", "logging", "wait")):
            return "bold #FFD166"
        if lowered.startswith("ready"):
            return "bold #7CB7FF"
        return "bold #F1ECFF"

    def update_buttons(self):
        """Replace Start/Stop button according to state and grey out all other buttons while running."""
        try:
            toggle_btn = self.query_one("#toggle_run", Button)
            if RUNNING:
                toggle_btn.label = "Stop Automation"
                toggle_btn.remove_class("start")
                toggle_btn.add_class("stop")
                toggle_btn.disabled = False
            else:
                toggle_btn.label = "Start Automation"
                toggle_btn.remove_class("stop")
                toggle_btn.add_class("start")
                toggle_btn.disabled = self._busy or not (self.logged_in and LICENSE_OK)

            # Grey out all other buttons whenever automation is running
            lock_others = RUNNING or self._busy
            self.query_one("#exit", Button).disabled = lock_others

        except Exception:
            pass

    def refresh_panel(self):
        if self.account_name:
            account_val = f"● {self.account_name}"
            account_style = "bold #FFFFFF"
        else:
            account_val = f"◌ {self.account_text}"
            account_style = "bold #FFD166"

        if LICENSE_OK:
            license_val = f"● Expires on {LICENSE_EXPIRES}" if LICENSE_EXPIRES else "● ACTIVE (VALID)"
            license_style = "bold #5CF2A0"
        elif LICENSE_MSG == "checking...":
            license_val = "◌ VERIFYING..."
            license_style = "bold #FFD166"
        else:
            license_val = f"● {license_error_text()}"
            license_style = "bold #FF6B7A"

        if adb_serial:
            adb_val = f"● {self.adb_text}"
            adb_style = "bold #5CC8FF"
        else:
            adb_val = f"◌ {self.adb_text}"
            adb_style = "bold #FFD166"

        rows = [
            ("ACCOUNT", account_val, account_style),
            ("LICENSE", license_val, license_style),
            ("DEVICE", adb_val, adb_style),
            ("STATUS", f"● {self.state_text}", self.state_style()),
        ]
        text = Text()
        for index, (label, value, style) in enumerate(rows):
            if index:
                text.append("\n")
            text.append(f"{label:<8} ", style="bold #9A93B5")
            text.append("│ ", style="#4A4565")
            text.append(str(value), style=style)
        panel = self.query_one("#panel", Static)
        panel.update(text)
        self.update_buttons()

    def add_log(self, message):
        """Show only the latest log line: each new message replaces the previous one."""
        try:
            box = self.query_one("#log", RichLog)
        except Exception:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        box.clear()
        for line in (str(message).splitlines() or [""]):
            lowered = line.lower()
            if any(w in lowered for w in ("error", "failed", "not found", "stopped", "expired", "cancel")):
                color = "#FF8A96"
            elif any(w in lowered for w in ("success", "connected", "logged in", "started", "valid", "passed", "computed", "completed")):
                color = "#7DF5B2"
            elif any(w in lowered for w in ("captcha", "scan", "switching", "task detected", "clicking", "uploading")):
                color = "#9CD3FF"
            else:
                color = "#DAD3EE"
            entry = Text()
            entry.append(f"{stamp}  ", style="#9A93B5")
            entry.append(line, style=color)
            box.write(entry)

    def refresh_stats(self):
        rows = [
            ("LIKES", str(STATS["likes"]), "bold #7CB7FF"),
            ("COMMENTS", str(STATS["comments"]), "bold #B69DFF"),
            ("SKIPPED", str(STATS["skipped"]), "bold #FFD166"),
            ("CAPTCHA", str(STATS["captchas"]), "bold #FF8BD1"),
            ("CASHCOINS", f"{STATS['tenths'] / 10:.1f}", "bold #5CF2A0"),
            ("RUNTIME", runtime_text(), "bold #F1ECFF"),
        ]
        # Nothing yet (0, 0.0, 00:00:00) is shown as a quiet "none"
        rows = [(label, "None", "#9A93B5") if value in ("0", "0.0", "00:00:00") else (label, value, style)
                for label, value, style in rows]
        text = Text()
        for index, (label, value, style) in enumerate(rows):
            if index:
                text.append("\n")
            text.append(f"{label:<10} ", style="bold #9A93B5")
            text.append("│ ", style="#4A4565")
            text.append(value, style=style)
        try:
            self.query_one("#stats_panel", Static).update(text)
        except Exception:
            pass
        self.refresh_health()

    def refresh_health(self):
        """Health monitor: is it working, what went wrong and when, CAPTCHA results."""
        now = time.time()
        muted = "#9A93B5"
        rows = []

        # Last finished task; while running it turns yellow / red when nothing has finished for a while
        last_ok = HEALTH["last_ok_ts"]
        if last_ok:
            value = f"{fmt_time(last_ok)} · {fmt_age(now - last_ok)} ago"
            style = "bold #F1ECFF"
        else:
            value = "waiting for first task" if RUNNING else "None"
            style = muted
        if RUNNING:
            idle = now - (last_ok or RUN_STARTED or now)
            if idle >= STALL_BAD_SECONDS:
                style = "bold #FF6B7A"
            elif idle >= STALL_WARN_SECONDS:
                style = "bold #FFD166"
            else:
                style = "bold #5CF2A0"
        rows.append(("LAST TASK", value, style))

        if HEALTH["error_ts"]:
            rows.append(("LAST ERROR", f"{fmt_time(HEALTH['error_ts'])} · {HEALTH['error_msg']}", "bold #FF6B7A"))
        else:
            rows.append(("LAST ERROR", "None", muted))

        if HEALTH["stop_ts"]:
            rows.append(("LAST STOP", f"{fmt_time(HEALTH['stop_ts'])} · {HEALTH['stop_msg']}", "bold #FFD166"))
        else:
            rows.append(("LAST STOP", "None", muted))

        try:
            panel = self.query_one("#health_panel", Static)
            panel.update(build_rows_text(rows, 11, panel.size.width or 37))
        except Exception:
            pass

    def set_state(self, state):
        self.state_text = state
        self.refresh_panel()

    def set_adb(self, text):
        self.adb_text = text
        self.refresh_panel()

    # ---- lifecycle ----
    def on_mount(self):
        global ui
        ui = self
        self._opened_at = time.time()
        self.refresh_panel()
        self.refresh_stats()
        self.set_interval(1, self.refresh_stats)   # keeps the runtime ticking
        self.run_worker(self.boot(), exclusive=False)

    def on_unmount(self):
        global ui
        ui = None

    async def boot(self):
        await asyncio.to_thread(check_license)
        self.refresh_panel()

        # Account and device are always checked (even with a bad license), at the same time
        try:
            await self.ensure_adb()
        except Exception as e:
            log(f"Startup error: {e}")
        self.update_ready_state()

    def update_ready_state(self):
        """Show what the user should do next."""
        if RUNNING or self._busy or self._login_busy:
            return
        if not LICENSE_OK and LICENSE_MSG != "checking...":
            self.set_state("Contact developer")
            return
        if not adb_serial:
            self.set_state("ADB not connected")
        else:
            self.set_state("Ready to start")

    async def wait_with_progress(self, coro, label, tick=5):
        """Await a slow call and log a progress line every few seconds."""
        task = asyncio.ensure_future(coro)
        waited = 0
        while True:
            done, _ = await asyncio.wait({task}, timeout=tick)
            if done:
                return task.result()
            waited += tick
            log(f"{label}... ({waited}s)")

    # ---- Telegram login ----
    def set_logged_in(self, name, text="Not logged in"):
        self.account_name = name
        self.logged_in = bool(name)
        self.account_text = text
        self.tg_state = "logged_in" if name else ("offline" if text == "connection failed" else "logged_out")
        self.refresh_panel()

    async def check_login(self, quiet=False):
        """Connect to Telegram and check the saved session. Returns True when logged in."""
        self.account_text = "checking..."
        self.tg_state = "checking"
        self.refresh_panel()
        try:
            if not client.is_connected():
                log("Connecting to Telegram...")
                await self.wait_with_progress(client.connect(), "Still connecting to Telegram")
            authorized = await self.wait_with_progress(client.is_user_authorized(), "Checking Telegram session")
            if authorized:
                me = await self.wait_with_progress(client.get_me(), "Reading account")
                name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
                self.set_logged_in(name)
                return True
            self.set_logged_in(None)
        except Exception as e:
            self.set_logged_in(None, "connection failed")
            log(f"Telegram connection failed: {e}")
            log("Press Login to retry")
        return False

    # ---- ADB ----
    async def open_adb_screen(self):
        """Show the ADB setup screen and return the connected serial (or None)."""
        done = asyncio.get_running_loop().create_future()
        self.push_screen(AdbScreen(), lambda result: done.done() or done.set_result(result))
        return await done

    async def ensure_adb(self):
        global adb_serial
        if adb_serial and await asyncio.to_thread(adb_alive, adb_serial):
            self.set_adb(await asyncio.to_thread(get_device_name, adb_serial))
            self.update_ready_state()
            return True
        self.set_adb("checking...")
        serial = await asyncio.to_thread(adb_find_ready)
        if not serial:
            if shutil.which("adb") is None:
                log("adb not found. Run: pkg install android-tools")
            if notif_available():
                log("ADB not connected - starting automatic setup")
                self.set_state("Waiting for ADB pairing")
                serial = await asyncio.to_thread(adb_auto_setup, log)
            if not serial:
                log("ADB not connected - opening manual setup")
                serial = await self.open_adb_screen()
        if serial:
            adb_serial = serial
            self.set_adb(await asyncio.to_thread(get_device_name, serial))
            self.update_ready_state()
            return True
        self.set_adb("not connected")
        log("ADB not connected")
        self.update_ready_state()
        return False

    # ---- actions ----
    def action_start(self):
        self.run_worker(self.start_flow(), exclusive=False)

    def action_stop(self):
        self.run_worker(self.stop_flow(), exclusive=False)

    async def action_quit(self):
        global QUIT_REASON
        if time.time() - self._opened_at < 1.5:
            return   # ignore stray taps/keys right at launch
        QUIT_REASON = "terminal"
        await stop_automation("Quit")
        try:
            await client.disconnect()
        except Exception:
            pass
        self.exit()

    async def action_exit(self):
        """Exit button: stop everything, then close Termux completely (see close_termux)."""
        global EXIT_TERMUX
        if time.time() - self._opened_at < 1.5:
            return   # ignore stray taps/keys right at launch
        EXIT_TERMUX = True
        await stop_automation("Exit")
        try:
            await client.disconnect()
        except Exception:
            pass
        # Close Termux right now, with the dashboard still showing (no flash back to the terminal)
        await exit_termux_now()

    async def start_flow(self):
        if RUNNING or self._busy:
            log("Already running")
            return
        self._busy = True
        self.update_buttons()
        try:
            if not self.logged_in:
                log("Not logged in")
                return
            reset_stats()        # a new run starts from zero
            reset_health()
            self.set_state("Starting...")
            await asyncio.to_thread(check_license)
            self.refresh_panel()
            if not LICENSE_OK:
                record_error(f"Start blocked, license: {license_error_text()}")
                self.set_state("Contact developer")
                return
            if not await self.ensure_adb():
                record_error("Start blocked: ADB is not connected")
                self.set_state("Stopped")
                return
            ok = await start_automation()
            if not ok and not RUNNING:
                self.set_state("Stopped")
        except Exception as e:
            log(f"Start failed: {e}")
            record_error(f"Start failed: {e}")
            self.set_state("Stopped")
        finally:
            self._busy = False
            self.refresh_panel()

    async def stop_flow(self):
        if not RUNNING:
            log("Not running")
            return
        await stop_automation("Stopped by user")

    async def on_button_pressed(self, event: Button.Pressed):
        button_id = event.button.id
        if button_id == "toggle_run":
            if RUNNING:
                self.action_stop()
            else:
                self.action_start()
        elif button_id == "exit":
            await self.action_exit()

# ==========================================
# PRE-UI LOGIN (terminal, QR shown as an image)
# ==========================================
QR_PNG_PATH = "/sdcard/login_qr.png"
QR_TIMEOUT = 30          # Telegram QR tokens live about 30 seconds
RETRY_WAIT = 120         # seconds to wait after an expired QR before Termux closes
console = Console()

def save_qr_png(url, path=QR_PNG_PATH):
    """Save the login QR code as a large, sharp PNG."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=14, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)

def check_companion_app():
    """Make sure the Replica app is installed and can show images. Returns an error text, or None if fine."""
    if not adb_serial:
        return None
    listing = run_adb("-s", adb_serial, "shell", "pm", "list", "packages", COMPANION_PKG)
    if f"package:{COMPANION_PKG}" not in listing.split():
        return ("The Replica app is not installed.\n"
                "Install it first, open it once, grant storage access, create your config, then run this script again.")
    resolved = run_adb("-s", adb_serial, "shell", "pm", "resolve-activity", "--brief",
                       "-a", "android.intent.action.VIEW", "-d", f"file://{QR_PNG_PATH}",
                       "-t", "image/png", "-p", COMPANION_PKG)
    if "no activity found" in resolved.lower():
        return ("Your Replica app is too old to show the login QR code.\n"
                "Install the latest version of Replica, then run this script again.")
    return None

def open_qr_image(path=QR_PNG_PATH):
    """Open the PNG in the Replica app. Returns None on success, otherwise an error text."""
    if not adb_serial:
        return "ADB is not connected, so the Replica app cannot be opened."
    out = run_adb("-s", adb_serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                  "-d", f"file://{path}", "-t", "image/png", "-p", COMPANION_PKG, timeout=15)
    lowered = out.lower()
    if "error" in lowered or "exception" in lowered or "unable to resolve" in lowered:
        return f"Could not open the Replica app: {out}"
    return None

def remove_qr_png(path=QR_PNG_PATH):
    try:
        os.remove(path)
    except OSError:
        pass

def bring_termux_to_front():
    """Return to Termux after the image viewer was opened."""
    try:
        if adb_serial:
            run_adb("-s", adb_serial, "shell", "am", "start", "-n",
                    "com.termux/com.termux.app.TermuxActivity", timeout=10)
        else:
            subprocess.run(["am", "start", "-n", "com.termux/com.termux.app.TermuxActivity"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass

def render_full_width_continue_button():
    """Render a full-width Continue button in the terminal."""
    width = max(20, console.size.width)
    pad_line = " " * width
    label_line = "Continue".center(width)
    console.print(Text(pad_line, style="bold #EADDFF on #4F378B"))
    console.print(Text(label_line, style="bold #EADDFF on #4F378B"))
    console.print(Text(pad_line, style="bold #EADDFF on #4F378B"))
    console.print()

def wait_for_continue_button():
    """
    Wait for the user to tap the Continue button on screen (using terminal mouse tracking
    so Termux does not pop up the soft keyboard) or press Enter/Space.
    """
    if not sys.stdin.isatty():
        input()
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # Enable X10 + SGR mouse reporting so tapping the button in Termux sends click coords
        sys.stdout.write("\033[?1000h\033[?1006h")
        sys.stdout.flush()
        buf = ""
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch in ("\r", "\n", " "):
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            buf += ch
            # Match SGR mouse event: \033[<btn;col;rowM or \033[<btn;col;rowm
            m = re.search(r"\033\[<(\d+);(\d+);(\d+)([Mm])", buf)
            if m:
                btn = int(m.group(1))
                action = m.group(4)
                # Trigger on tap release ('m') or click so no trailing bytes remain in stdin
                if btn == 0 and action == "m":
                    break
                buf = ""
            elif len(buf) > 32:
                buf = buf[-16:]
    finally:
        sys.stdout.write("\033[?1000l\033[?1006l")
        sys.stdout.flush()
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

async def ask_password_terminal():
    """Ask for the Telegram 2FA password using standard visible input like ADB setup."""
    while True:
        try:
            password = (await asyncio.to_thread(input, "Enter 2FA password: ")).strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if not password:
            return False
        try:
            await client.sign_in(password=password)
            return True
        except errors.PasswordHashInvalidError:
            print("\nWrong 2FA password. Please try again.\n")
        except errors.FloodWaitError as e:
            print(f"\nToo many attempts. Telegram asks you to wait {e.seconds}s.\n")
            return False

async def expired_and_exit():
    """QR expired: show the 2 minute countdown, delete the incomplete session, clear the screen, close Termux."""
    remove_qr_png()
    bring_termux_to_front()
    clear_terminal()
    console.print("\n[bold #F2B8B5]QR code expired.[/] [#E6E1E5]Wait 2 minutes before trying again.[/]")
    for remaining in range(RETRY_WAIT, 0, -1):
        print(f"\r  Try again in {remaining // 60}:{remaining % 60:02d}   ", end="", flush=True)
        await asyncio.sleep(1)
    print()

    # Disconnect and close the session first so the file is released, then delete it
    try:
        await client.disconnect()
    except Exception:
        pass
    try:
        client.session.close()
    except Exception:
        pass

    def delete_session_files():
        for path in (f"{SESSION_PATH}.session", f"{SESSION_PATH}.session-journal"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    delete_session_files()
    await asyncio.sleep(0.5)
    delete_session_files()   # in case anything recreated it while shutting down

    clear_terminal()
    await exit_termux_now()

async def terminal_login():
    """
    Guided Telegram login shown before the UI. Returns True when logged in,
    False when cancelled. Exits Termux if the QR code expires.
    """
    # Clear previous startup logs when the login helping box appears
    clear_terminal()

    console.print()
    console.print(Panel(
        "[bold #E6E1E5]You are not logged in to Telegram yet.[/]\n"
        "[#CAC4D0]You will log in by scanning a QR code with another phone.[/]\n\n"
        "[bold #D0BCFF]Before you continue[/]\n"
        "[#CAC4D0]1. Take the other phone that has your Telegram account.\n"
        "2. Open Telegram, then Settings > Devices > Link Desktop Device.\n"
        "3. Keep the scanner open and ready.\n"
        "4. Press Continue below. The QR code opens as a picture on this phone.\n"
        "5. Point the other phone at it. You have 30 seconds.[/]",
        title="[bold #D0BCFF]TELEGRAM LOGIN[/]", border_style="#4F378B", padding=(1, 2),
    ))
    render_full_width_continue_button()

    try:
        await asyncio.to_thread(wait_for_continue_button)
    except (EOFError, KeyboardInterrupt):
        return False

    console.print("[#938F99]Creating QR code...[/]")
    qr_login = await client.qr_login()
    problem = None
    try:
        await asyncio.to_thread(save_qr_png, qr_login.url)
        problem = await asyncio.to_thread(open_qr_image)
    except Exception as e:
        problem = f"Could not create the QR image: {e}"
    if problem:
        remove_qr_png()
        console.print(f"\n[bold #F2B8B5]ERROR:[/] [#E6E1E5]{problem}[/]")
        console.print("[#CAC4D0]Make sure the Replica app is installed and has storage access, then run the script again.[/]")
        sys.exit(1)
    console.print("[#B7F0C8]QR code opened in the Replica app. Scan it with your other phone now.[/]")

    wait_task = asyncio.create_task(qr_login.wait(timeout=QR_TIMEOUT))
    start = time.time()
    while not wait_task.done():
        remaining = max(0, QR_TIMEOUT - int(time.time() - start))
        print(f"\r  Waiting for scan... {remaining:>2}s  ", end="", flush=True)
        await asyncio.sleep(0.5)
    print()

    need_password = False
    try:
        await wait_task
    except errors.SessionPasswordNeededError:
        need_password = True
    except asyncio.TimeoutError:
        await expired_and_exit()
        return False
    finally:
        remove_qr_png()
        bring_termux_to_front()

    # Clear the terminal as soon as the QR code is scanned successfully
    clear_terminal()
    console.print("[#B7F0C8]QR code scanned.[/]\n")

    if need_password and not await ask_password_terminal():
        console.print("[#FFD9A0]Login cancelled.[/]")
        return False

    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
    console.print(f"\n[bold #B7F0C8]Logged in as {name}[/]")
    # The keyboard is only open when the 2FA password was typed: close it, then wait before the UI
    if need_password:
        await asyncio.to_thread(hide_keyboard)
        await asyncio.sleep(3)
    else:
        await asyncio.sleep(2)
    return name

async def login_before_ui():
    """After Telegram connects: return the account name if logged in, otherwise run the guided login."""
    try:
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=30)
    except Exception as e:
        if is_db_error(e):
            await restart_with_fresh_session()
        print(f"Could not check the Telegram session: {e}")
        return None
    if authorized:
        name = "Telegram account"
        try:
            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
            console.print(f"[bold #B7F0C8]Logged in as {name}[/]")
        except Exception:
            pass
        return name
    return await terminal_login() or None

async def restart_with_fresh_session():
    """The session turned out to be unusable at runtime: delete it and restart the script."""
    print("\nSession file is corrupted. Deleting it and restarting for a fresh login...")
    try:
        await client.disconnect()
    except Exception:
        pass
    delete_session_files()
    await asyncio.sleep(2)
    os.environ["REPLICA_SKIP_GATE"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def connect_telegram_early(max_wait=60):
    """
    Connect to Telegram before the UI opens, like the original login flow did,
    printing progress so a slow network is visible. The UI opens either way.
    """
    print("Connecting to Telegram...")
    task = asyncio.ensure_future(client.connect())
    waited = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=5)
        if done:
            try:
                task.result()
                print("Telegram connected.")
                return True
            except Exception as e:
                if is_db_error(e):
                    await restart_with_fresh_session()
                print(f"Telegram connection failed: {e}")
                return False
        waited += 5
        if waited >= max_wait:
            task.cancel()
            print("Telegram is not responding.")
            return False
        print(f"Still connecting to Telegram... ({waited}s)")

async def main():
    # Show Telethon connection warnings in the terminal now and in the log screen later
    handler = TelethonLogHandler(level=logging.WARNING)
    logging.getLogger("telethon").addHandler(handler)

    # 1. Check for an active background ADB device (e.g. emulator-5554); if none, run adb.py logic
    await asyncio.to_thread(ensure_adb_startup)

    # 1b. The Replica app must be installed (it shows the login QR code)
    problem = await asyncio.to_thread(check_companion_app)
    if problem:
        fail_startup("Replica app problem.", problem)

    # 2. Connect to Telegram, log in if needed, then launch the UI
    account_name = None
    if await connect_telegram_early():
        account_name = await login_before_ui()
    if not account_name:
        fail_startup("Telegram login was not completed. Run the script again.", prefix="")

    await load_device_name()
    app = BotApp(account_name)
    await app.run_async()
    try:
        await client.disconnect()
    except Exception:
        pass
    if QUIT_REASON or EXIT_TERMUX:
        clear_terminal()       # intentional exit (Terminal / Ctrl+Q / Exit): clear the screen first
        if EXIT_TERMUX:
            close_termux()
        print("Script fully stopped.")
    else:
        # The dashboard closed by itself: keep the screen so the error above stays readable
        print(f"\nDashboard closed unexpectedly (return code: {getattr(app, 'return_code', None)}). "
              "Scroll up for the error details.")

if __name__ == "__main__":
    loop.run_until_complete(main())
