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
from collections import deque
from datetime import datetime, timedelta

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()

# ==========================================
# COLORED TERMINAL LOGGING (NO LEADING SPACES)
# ==========================================
def log_color(message):
    """Return a pleasing color hex based on the log message purpose."""
    lowered = str(message).lower()
    if any(w in lowered for w in (
        "error", "fail", "timed out", "timeout", "invalid", "not found",
        "not installed", "corrupt", "expired", "cancel", "blocked", "stopped"
    )):
        return "#FF6B7A"
    if any(w in lowered for w in (
        "connected", "paired", "granted", "ready", "success", "logged in",
        "completed", "passed", "started", "valid", "scanned"
    )):
        return "#5CF2A0"
    if any(w in lowered for w in (
        "wait", "enter", "open", "allow", "retry", "requesting",
        "switch", "skip", "popup", "notification"
    )):
        return "#FFD166"
    if any(w in lowered for w in ("captcha", "computed", "security", "qr")):
        return "#C9B8FF"
    return "#5CC8FF"

# ==========================================
# PERSISTENT LOG STORE (Termux home)
#   * every log line is appended to logs.txt and is never removed automatically
#   * stopping / restarting the automation or the script keeps the file; new lines go after the old ones
#   * the file is emptied ONLY when the Clear logs button is pressed
#   * the screen shows just the latest line (the file keeps everything)
# ==========================================
LOG_FILE = os.path.join(os.path.expanduser("~"), "logs.txt")
LOG_SCREEN_LINES = 1
LOG_ENTRIES = deque(maxlen=LOG_SCREEN_LINES)   # (datetime, text) - only what the screen needs
_LOG_LOCK = threading.Lock()
_LOG_STATE = {"file_date": None, "checked": False}

def _date_header(moment):
    return f"{moment:%A, %d %B %Y}"

def _file_write(lines, mode="a"):
    try:
        with open(LOG_FILE, mode, encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
    except Exception:
        pass

def _last_file_date():
    """Date of the last date header already in logs.txt (so a restart does not repeat it)."""
    try:
        size = os.path.getsize(LOG_FILE)
        if size == 0:
            return None
        with open(LOG_FILE, "rb") as fh:
            fh.seek(max(0, size - 8192))
            tail = fh.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(tail):
            try:
                return datetime.strptime(line.strip(), "%A, %d %B %Y").date()
            except ValueError:
                continue
    except Exception:
        pass
    return None

def record_log(text):
    """Append one log line to logs.txt and keep it for the screen. Returns its timestamp."""
    now = datetime.now()
    with _LOG_LOCK:
        LOG_ENTRIES.append((now, text))
        if not _LOG_STATE["checked"]:
            _LOG_STATE["file_date"] = _last_file_date()
            _LOG_STATE["checked"] = True
        lines = []
        if now.date() != _LOG_STATE["file_date"]:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
                lines.append("")
            lines.append(_date_header(now))
            lines.append("")
            _LOG_STATE["file_date"] = now.date()
        lines.append(f"{now:%H:%M:%S}  {text}")
        _file_write(lines, "a")
    return now

def clear_logs():
    """Empty the file immediately and the on-screen logs."""
    with _LOG_LOCK:
        LOG_ENTRIES.clear()
        _LOG_STATE.update(file_date=None, checked=True)
        _file_write([], "w")

SHARE_DIR = "/sdcard/Download"   # the shared copy lives here so other apps can open it

def share_logs():
    """Share the log .txt file through the Android share sheet, started over adb. Returns (ok, message)."""
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
            return False, "No logs to share"
    except OSError:
        return False, "No logs to share"
    if not adb_serial:
        return False, "Share failed: ADB is not connected"

    # The shared copy is always called logs.txt; the previous copy is removed first so it is never stale
    name = "logs.txt"
    path = f"{SHARE_DIR}/{name}"
    try:
        os.makedirs(SHARE_DIR, exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        shutil.copyfile(LOG_FILE, path)
    except Exception as e:
        return False, f"Share failed: could not copy the file ({type(e).__name__})"

    # Register the file with Android, then share its content:// address (file:// is the fallback)
    uri = f"file://{path}"
    run_adb("-s", adb_serial, "shell",
            f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{path}", timeout=10)
    query = f"content query --uri content://media/external/file --projection _id --where \"_display_name='{name}'\""
    for _ in range(6):
        ids = re.findall(r"_id=(\d+)", run_adb("-s", adb_serial, "shell", query, timeout=10))
        if ids:
            uri = f"content://media/external/file/{ids[-1]}"
            break
        time.sleep(0.5)

    out = run_adb("-s", adb_serial, "shell",
                  "am start --grant-read-uri-permission -a android.intent.action.SEND -t text/plain "
                  f"--eu android.intent.extra.STREAM {uri} --es android.intent.extra.SUBJECT 'Replica logs'",
                  timeout=20).lower()
    if "error" in out or "exception" in out:
        return False, "Share failed: Android refused to open the share sheet"
    return True, "Share sheet opened"

def term_log(message):
    """Print a concise, flush-left colored line to the terminal and keep it in the log file."""
    for raw_line in str(message).splitlines():
        clean = raw_line.strip()
        if clean:
            record_log(clean)
            console.print(Text(clean, style=log_color(clean)))

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
        term_log("Storage permission granted")
        return

    term_log("Requesting storage permission...")

    # Bypass the Termux (y/n) prompt by removing the existing symlink folder
    storage_dir = os.path.expanduser("~/storage")
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir, ignore_errors=True)

    # Trigger the permission request
    os.system("termux-setup-storage")

    # Silently wait up to 7 seconds
    for _ in range(7):
        if has_storage_access():
            term_log("Storage permission granted")
            return
        time.sleep(1)

    # If the loop completes without permission being granted
    term_log("Storage permission denied")
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
import base64
import requests
from concurrent.futures import Future

import uiautomator2 as u2
from telethon import TelegramClient, events, errors
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.types import MessageEntityTextUrl

import qrcode
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, RichLog, Static

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
    term_log(f"{prefix}{title}".strip())
    if detail:
        term_log(detail)
    for remaining in range(seconds, 0, -1):
        print(f"\r\033[1;38;2;255;209;102mExiting in {remaining}s\033[0m   ", end="", flush=True)
        time.sleep(1)
    clear_terminal()
    close_termux()

if not os.path.exists(CONFIG_PATH):
    fail_startup("Create config first using Replica app", prefix="")

try:
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

    API_ID = int(config["api_id"])
    API_HASH = config["api_hash"]
    LICENSE_KEY = config.get("license_key", "NONE")
    accounts = config["accounts"]
except Exception as e:
    fail_startup(f"Invalid config.json ({e})", CONFIG_HELP)

HWID = API_HASH

# ==========================================
# LICENSE CHECK
# ==========================================
LICENSE_OK = False
LICENSE_MSG = "checking..."
LICENSE_DAYS = None      # whole days left until the license expires (None if unknown)
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

def days_left(raw):
    """Whole days from today until the expiry date, or None if it cannot be read."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return (parsed.date() - datetime.now().date()).days
    except Exception:
        return None

def license_days_text():
    """Short text for the dashboard: '183 days left'."""
    if LICENSE_DAYS is None:
        return f"Expires on {LICENSE_EXPIRES}" if LICENSE_EXPIRES else "Active"
    if LICENSE_DAYS < 0:
        return "Expired"
    if LICENSE_DAYS == 0:
        return "Expires today"
    if LICENSE_DAYS == 1:
        return "1 day left"
    return f"{LICENSE_DAYS} days left"

def check_license():
    """Verify the license with the server. Updates LICENSE_OK / LICENSE_MSG / LICENSE_EXPIRES / LICENSE_DAYS."""
    global LICENSE_OK, LICENSE_MSG, LICENSE_EXPIRES, LICENSE_DAYS
    LICENSE_DAYS = None
    log("Checking license...")
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
                LICENSE_DAYS = days_left(body.get("expires"))
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
    if LICENSE_OK:
        log(f"License valid: {license_days_text()}" + (f" (until {LICENSE_EXPIRES})" if LICENSE_EXPIRES else ""))
    else:
        log(f"License check failed: {LICENSE_MSG}")
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
    term_log("Corrupted session removed")
    delete_session_files()
    time.sleep(2)

try:
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
except Exception as _e:
    if not is_db_error(_e):
        raise
    term_log("Resetting unreadable session")
    delete_session_files()
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
d = None

# ==========================================
# UI BRIDGE
# ==========================================
def log(message=""):
    """Record a line in the log file and show it in the UI log (or the terminal before the UI exists).
    Safe to call from any thread."""
    app = ui
    if app is None:
        term_log(message)
        return
    for raw_line in str(message).splitlines():
        clean = raw_line.strip()
        if not clean:
            continue
        moment = record_log(clean)
        try:
            app.call_from_thread(app.add_log, moment, clean)
        except RuntimeError:
            # Already on the UI thread
            app.add_log(moment, clean)
        except Exception:
            pass

# Cashcoins earned this session (kept in tenths to avoid float drift)
CASH = {"tenths": 0}
LIKE_REWARD_TENTHS = 11      # 1.1 cashcoins
COMMENT_REWARD_TENTHS = 60   # 6.0 cashcoins

def refresh_ui():
    """Redraw the information panel. Safe to call from any thread."""
    app = ui
    if app is None:
        return
    try:
        app.call_from_thread(app.refresh_panel)
    except RuntimeError:
        # Already on the UI thread
        app.refresh_panel()
    except Exception:
        pass

def add_cash(kind):
    """Add the reward of a finished task and refresh the dashboard."""
    if kind == "like":
        CASH["tenths"] += LIKE_REWARD_TENTHS
    elif kind == "comment":
        CASH["tenths"] += COMMENT_REWARD_TENTHS
    refresh_ui()

def reset_cash():
    """Zero the cashcoins counter. Done when the user presses Start Automation,
    NOT when it stops, so the number of the finished run stays on screen."""
    CASH["tenths"] = 0
    refresh_ui()

class TelethonLogHandler(logging.Handler):
    """Forward Telethon warnings (e.g. failed connection attempts) to the log screen."""
    def emit(self, record):
        try:
            log(f"Telegram: {record.getMessage()}")
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
        return "error: adb not found"
    except subprocess.TimeoutExpired:
        return "error: adb timed out"
    except Exception as e:
        return f"error: {e}"

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
    """Return [(serial, state)] parsed from `adb devices`, prioritizing emulator-5554."""
    devices = []
    listing = False
    for line in run_adb("devices").splitlines():
        if line.startswith("List of devices"):
            listing = True
            continue
        parts = line.split()
        if listing and len(parts) == 2:
            devices.append((parts[0], parts[1]))
    devices.sort(key=lambda item: (0 if item[0].startswith("emulator") else 1))
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

def adb_restart_for_emulator():
    """Restart the ADB server so local port 5555 is auto-registered as emulator-5554."""
    run_adb("kill-server", timeout=20)
    run_adb("start-server", timeout=30)
    time.sleep(2)

def adb_lock_emulator(addr=None):
    """Switch adbd to TCP port 5555 and restart the ADB server so emulator-5554 appears immediately."""
    if addr:
        run_adb("-s", addr, "tcpip", "5555", timeout=20)
    else:
        run_adb("tcpip", "5555", timeout=20)
    time.sleep(3)
    if addr:
        run_adb("disconnect", addr, timeout=10)
    adb_restart_for_emulator()

def adb_poll_ready(base_wait=8, auth_wait=60, announce=False, say=term_log):
    """
    Wait for a working device and return its serial (preferring emulator-5554), or None.
    """
    start = time.time()
    deadline = start + base_wait
    announced = False
    reconnected = False
    while True:
        devices = adb_devices()
        for serial, state in devices:
            if state == "device" and adb_alive(serial):
                if not serial.startswith("emulator"):
                    # Convert non-emulator local connection to emulator-5554 immediately
                    adb_lock_emulator(serial)
                    for s2, st2 in adb_devices():
                        if st2 == "device" and adb_alive(s2):
                            return s2
                return serial
        pending = [(sr, st) for sr, st in devices if st in ("unauthorized", "offline", "connecting", "authorizing")]
        if pending:
            deadline = max(deadline, start + auth_wait)
            if announce and not announced:
                say("Allow USB debugging popup")
                announced = True
            if not reconnected and any(st == "offline" for _, st in pending):
                run_adb("reconnect", "offline", timeout=15)
                reconnected = True
        if time.time() >= deadline:
            return None
        time.sleep(1)

def adb_find_ready(attempts=4, delay=1.5, auth_wait=45):
    """Start the adb server and return the serial of a working device (emulator-5554), or None."""
    adb_prepare()
    if shutil.which("adb") is None:
        return None
    run_adb("start-server", timeout=30)
    serial = adb_poll_ready(base_wait=attempts * delay, auth_wait=auth_wait)
    if not serial and port_open(5555):
        adb_restart_for_emulator()
        serial = adb_poll_ready(base_wait=8, auth_wait=auth_wait)
    return serial

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

# ------------------------------------------
# Automatic wireless debugging setup (notification method only)
#   * ports are found automatically via mDNS (needs: pip install zeroconf)
#   * the pairing code is typed into a Termux notification (needs: pkg install termux-api + Termux:API app)
#   * ADB is locked to port 5555 and server restarted so emulator-5554 shows on first run too
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
        say("Open Wireless debugging")
        notif_ask("ADB pairing", "Tap Enter and type the 6-digit pairing code", code_file)
        say("Enter pairing code in notification")
        if not wait_for_file(code_file, 120):
            say("Pairing timed out")
            return False
        notif_update("Pairing...")

        numbers = read_numbers(code_file)
        if not numbers:
            say("Invalid pairing code")
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
            say("Pairing port not found")
            return False

        out = adb_pair(f"localhost:{port}", code)
        if "successfully paired" in out.lower():
            say("Paired successfully")
            return True
        say("Pairing failed")
        return False
    finally:
        stop.set()

def adb_ask_connect_port(say):
    """Ask for the connect port in the notification if mDNS did not report it."""
    port_file = os.path.join(ADB_WORK_DIR, "cport")
    notif_ask("Wireless debugging port", "Enter the port shown on the Wireless debugging screen", port_file)
    say("Enter connect port in notification")
    if not wait_for_file(port_file, 90):
        say("Port input timed out")
        return None
    notif_update("Connecting...")
    numbers = read_numbers(port_file)
    endpoint = to_endpoint(numbers[-1]) if numbers else None
    if endpoint and adb_try_connect(endpoint):
        return endpoint
    say("Connection failed")
    return None

def adb_auto_setup(say=term_log):
    """
    Fully automatic wireless debugging: find ports, pair with the notification code, connect,
    lock port 5555, and restart the ADB server so emulator-5554 is active immediately.
    """
    if shutil.which("adb") is None or not notif_available():
        return None
    adb_prepare()
    try:
        os.makedirs(ADB_WORK_DIR, exist_ok=True)
        notif_clear()
        run_adb("start-server", timeout=30)

        if port_open(5555):
            adb_restart_for_emulator()
            serial = adb_poll_ready(base_wait=8, auth_wait=60, announce=True, say=say)
            if serial:
                return serial

        addr = adb_find_and_connect(4)
        if not addr:
            if not adb_auto_pair(say):
                return None
            say("Connecting wireless port...")
            addr = adb_find_and_connect(30) or adb_ask_connect_port(say)
            if not addr:
                return None

        say("Locking port 5555...")
        adb_lock_emulator(addr)

        serial = adb_poll_ready(base_wait=12, auth_wait=60, announce=True, say=say)
        return serial
    except Exception:
        say("ADB setup error")
        return None
    finally:
        notif_clear()
        shutil.rmtree(ADB_WORK_DIR, ignore_errors=True)

def ensure_adb_startup():
    """
    At startup, detect an active emulator-5554 connection or run notification-based wireless setup.
    Always locks port 5555 and restarts the ADB server so emulator-5554 shows on the very first run.
    """
    global adb_serial
    adb_prepare()

    if shutil.which("adb") is None:
        fail_startup("ADB not installed (pkg install android-tools)", prefix="")

    term_log("Checking ADB...")
    run_adb("start-server", timeout=30)

    # 1. Check if emulator-5554 (or another device) is already listed
    serial = adb_poll_ready(base_wait=6, auth_wait=60, announce=True, say=term_log)

    # 2. Nothing listed, but port 5555 is open: restart server so it registers as emulator-5554
    if not serial and not adb_devices() and port_open(5555):
        term_log("Starting emulator port...")
        adb_restart_for_emulator()
        serial = adb_poll_ready(base_wait=8, auth_wait=60, announce=True, say=term_log)

    if serial:
        adb_serial = serial
        term_log(f"ADB connected: {serial}")
        return serial

    if not notif_available():
        fail_startup("Install termux-api for ADB setup", prefix="")

    term_log("Starting ADB setup...")
    for attempt in range(3):
        if attempt > 0:
            term_log("Retrying ADB setup...")
        serial = adb_auto_setup(term_log)
        if serial:
            adb_serial = serial
            term_log(f"ADB connected: {serial}")
            return serial

    fail_startup("ADB connection failed", prefix="")

# ==========================================
# DEVICE CONNECTION
# ==========================================
def connect_with_retry(max_retries=5, delay=3):
    """Connect to uiautomator2, retrying on failure. Returns the device or None."""
    for attempt in range(1, max_retries + 1):
        try:
            log(f"Connecting UIAutomator ({attempt}/{max_retries})")
            device = u2.connect(adb_serial)
            device.info
            log("UIAutomator connected")
            return device
        except Exception as e:
            log(f"Connection failed: {e}")
            if attempt < max_retries:
                log(f"Retrying in {delay}s")
                time.sleep(delay)
    log("Device connection failed")
    return None

def cleanup_apps(device, ig_pkg, chrome_pkg):
    """Close Instagram and Chrome."""
    log("Closing apps")
    device.app_stop(chrome_pkg)
    device.app_stop(ig_pkg)

# ==========================================
# DEVICE ACTIONS
# ==========================================
def perform_switch(device, target):
    """Switch Instagram to the target account via the profile switcher."""
    ig_pkg = "com.instagram.android"
    screen_width, screen_height = device.window_size()

    log("Closing Instagram")
    device.app_stop(ig_pkg)
    time.sleep(1.5)
    log("Opening Instagram")
    device.app_start(ig_pkg)

    log("Waiting for the profile tab")
    profile_btn = device(resourceIdMatches=".*profile_tab.*")

    if profile_btn.exists(timeout=25):
        time.sleep(1.5)
        log("Long pressing the profile tab")
        profile_btn.long_click(duration=1.0)
    else:
        log("Profile tab not found")
        device.app_stop(ig_pkg)
        return False

    log("Waiting 3.5s for the account list")
    time.sleep(3.5)

    meta_logo_pattern = "(?i)^(Логотип Meta|Meta logo)$"

    # Scroll the account list until the target is found or the list ends
    while True:
        target_elem = device(text=target)

        if target_elem.exists(timeout=0):
            log(f"Account {target} found, tapping it")
            target_elem.click()
            log("Waiting 3.5s for the switch to finish")
            time.sleep(3.5)
            log("Closing Instagram")
            device.app_stop(ig_pkg)
            log(f"Switched to {target}")
            return True

        if device(descriptionMatches=meta_logo_pattern).exists(timeout=0):
            break

        log("Scrolling the account list")
        device.swipe(screen_width * 0.5, screen_height * 0.8, screen_width * 0.5, screen_height * 0.4, duration=0.5)
        time.sleep(3.5)

    log(f"Account {target} not found")
    device.app_stop(ig_pkg)
    return False

def perform_like(device, post_url):
    """Open the post and like it with a native double tap."""
    ig_pkg = "com.instagram.android"
    chrome_pkg = "com.android.chrome"
    screen_width, screen_height = device.window_size()

    log("Closing Instagram")
    device.app_stop(ig_pkg)
    time.sleep(1.5)
    log("Opening the post")
    device.shell(f'am start -a android.intent.action.VIEW -d "{post_url}" -f 0x10000000 -p {ig_pkg}')

    log("Waiting for the post to load")
    media_options = device(resourceId="com.instagram.android:id/media_option_button")
    if not media_options.exists(timeout=15):
        log("Post did not open")
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

    # Tap point: horizontal center, half a screen width below the 3-dots menu
    bounds = media_options.info['bounds']
    target_x = screen_width // 2
    target_y = bounds['bottom'] + (screen_width // 2)

    log("Post loaded, waiting 3.5s")
    time.sleep(3.5)
    # Both taps run in one shell command to avoid Python-side latency
    log(f"Double tapping at {target_x},{target_y}")
    device.shell(f"input tap {target_x} {target_y} && sleep 0.125 && input tap {target_x} {target_y}")
    log("Waiting 3.5s for the like to register")
    time.sleep(3.5)

    cleanup_apps(device, ig_pkg, chrome_pkg)
    log("Like finished")
    return True

def perform_comment(device, post_url, my_comment):
    """Open the post, publish the comment and save a proof screenshot."""
    ig_pkg = "com.instagram.android"
    chrome_pkg = "com.android.chrome"
    screen_width, screen_height = device.window_size()

    log("Closing Instagram")
    device.app_stop(ig_pkg)
    time.sleep(1.5)
    log("Opening the post")
    device.shell(f'am start -a android.intent.action.VIEW -d "{post_url}" -f 0x10000000 -p {ig_pkg}')

    # Find the comment button, scrolling once if it is not visible
    log("Looking for the comment button")
    comment_btn = device(resourceId="com.instagram.android:id/row_feed_button_comment")
    if not comment_btn.exists(timeout=15):
        log("Comment button not visible, scrolling once")
        time.sleep(2.5)
        device.swipe(screen_width * 0.5, screen_height * 0.80, screen_width * 0.5, screen_height * 0.40, duration=0.7)
        time.sleep(2.5)
        comment_btn = device(resourceId="com.instagram.android:id/row_feed_button_comment")

    if not comment_btn.exists(timeout=5):
        log("Comment button not found")
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

    time.sleep(2.5)
    log("Opening the comment box")
    comment_btn.click()
    time.sleep(2.5)

    input_box = device(resourceIdMatches=".*layout_comment_thread_edittext.*")

    if input_box.exists(timeout=5):
        log("Typing the comment")
        input_box.set_text(my_comment)
        time.sleep(0.5)
    else:
        log("Comment input not found")
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

    post_pattern = "(?i)^(Post|Опубликовать|Допис)$"
    post_btn = device(descriptionMatches=post_pattern)

    if post_btn.exists(timeout=5):
        log("Tapping Post")
        post_btn.click()
        time.sleep(2.5)
    else:
        log("Post button not found")

    # The edit button appears once the comment is published
    log("Waiting for the comment to be published")
    edit_button = device(resourceIdMatches=".*row_comment_textview_edit_button.*")

    success = False
    for attempt in range(15):
        if edit_button.exists():
            success = True
            break
        time.sleep(1.0)

    if success:
        log("Comment published, saving the proof screenshot")
        device.screenshot().save("/sdcard/Proof.png")
        time.sleep(2.5)
        log("Proof screenshot saved")
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return True
    else:
        log("Comment was not confirmed on screen")
        cleanup_apps(device, ig_pkg, chrome_pkg)
        return False

def perform_captcha(device, hidden_url, answer_box):
    """
    Open the captcha page in Chrome immediately and pass the Turnstile check.
    Then wait for the solver result (answer_box) and submit its first answer.
    """
    chrome_pkg = "com.android.chrome"
    log("Opening the captcha page in Chrome")
    device.shell(f'am start -a android.intent.action.VIEW -d "{hidden_url}" -p {chrome_pkg}')

    lang_pattern = "(?i)^(Verify you are human|Подтвердите, что вы человек|Підтвердьте, що ви людина)$"
    success_pattern = "(?i)^(Success!|Успех!|Успіх!)$"

    start_time = time.time()
    tapped = False

    log("Waiting for the Turnstile check")
    while time.time() - start_time < 30:
        # Tap the Turnstile checkbox when it is visible
        device.dump_hierarchy()
        verify_target = device(descriptionMatches=lang_pattern)
        if verify_target.exists():
            bounds = verify_target.info['bounds']
            target_x = (bounds['left'] + bounds['right']) // 2
            target_y = (bounds['top'] + bounds['bottom']) // 2
            if not tapped:
                log("Tapping the verify checkbox")
                tapped = True
            device.click(target_x, target_y)

        time.sleep(1)
        if time.time() - start_time >= 30: break

        # Turnstile passed: wait for the solver, then submit the first answer
        device.dump_hierarchy()
        success_target = device(textMatches=success_pattern)
        if success_target.exists():
            log("Turnstile passed")
            input_element = device(resourceId="captcha-answer")
            if input_element.exists(timeout=5):
                log("Waiting for the solver answer")
                try:
                    answers = answer_box.result(timeout=90)
                except Exception:
                    log("Captcha: solver answer did not arrive in time")
                    return False
                if not answers:
                    log("Captcha: no solver answer to type")
                    return False

                log(f"Typing the answer: {answers[0]}")
                input_element.click()
                input_element.set_text(answers[0])
                time.sleep(0.5)
                device.press("enter")
                log("Answer submitted")
                time.sleep(2.0)
                return True
            log("Captcha: answer box not found on the page")
            return False
        time.sleep(1)

    log("Captcha: Chrome check was not passed within 30s")
    return False

# ==========================================
# TELEGRAM / IMAGE / QR UTILITIES
# ==========================================
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
        log(f"Captcha: unknown emoji {target_emoji!r}")
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
            log(f"Contacting the solver ({attempt}/3)")
            response = requests.post(AI_PROXY_URL, json=payload, timeout=30)
            if response.status_code == 403:
                log(response.json().get('error', 'License Error'))
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
                log("Captcha: solver could not read any image")
            return answers
        except Exception as e:
            if attempt < 3:
                time.sleep(3)
            else:
                log(f"Captcha: solver request failed ({type(e).__name__})")
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
    global RUNNING
    RUNNING = False
    log(f"Stopped: {reason}")
    await asyncio.to_thread(stop_device_apps)
    log("Instagram and Chrome closed")
    refresh_ui()
    ui_set_state("Stopped")

async def kill_everything(reason):
    await stop_automation(reason)

async def smart_send(text_to_send):
    """Send a message to the bot and wait for its reply, retrying once."""
    delay = random.uniform(5.0, 7.0)
    log(f"Waiting {delay:.1f}s before sending: {text_to_send}")
    await asyncio.sleep(delay)
    if not RUNNING: return
    for attempt in range(1, 3):
        bot_replied_event.clear()
        log(f"Clicking: {text_to_send} ({attempt}/2)")
        await client.send_message(TARGET_CHAT, text_to_send, parse_mode=None, link_preview=False)
        log("Waiting for the bot reply (60s max)")
        try:
            await asyncio.wait_for(bot_replied_event.wait(), timeout=60.0)
            log("Bot replied")
            return
        except asyncio.TimeoutError:
            if not RUNNING: return
            if attempt == 1:
                log("No reply in 60s, retrying...")
            else: log("Bot timed out twice")
    await kill_everything("Bot server stopped responding after 2 attempts.")

async def smart_send_photo():
    """Upload the proof screenshot to the bot and wait for its reply."""
    photo_path = "/sdcard/Proof.png"
    delay = random.uniform(3.0, 4.0)
    log(f"Waiting {delay:.1f}s before uploading the proof")
    await asyncio.sleep(delay)
    if not RUNNING: return True
    if not os.path.exists(photo_path):
        log("Proof screenshot file not found")
        return False

    for attempt in range(1, 3):
        bot_replied_event.clear()
        log(f"Uploading proof ({attempt}/2)")
        try:
            await client.send_file(TARGET_CHAT, photo_path)
            log("Waiting for the bot reply (60s max)")
            await asyncio.wait_for(bot_replied_event.wait(), timeout=60.0)
            log("Bot replied to the proof")
            return True
        except asyncio.TimeoutError:
            if attempt == 2:
                log("No reply to the proof")
                return False
            log("No reply in 60s, retrying the upload...")
        except Exception as e:
            log(f"Proof upload error: {type(e).__name__}")
            if attempt == 1: await asyncio.sleep(3.0)
            else: return False

# ==========================================
# ASYNC TASK WRAPPERS
# ==========================================
async def do_switch_account(target_username):
    log(f"Switching to {target_username}")
    success = await asyncio.to_thread(perform_switch, d, target_username)
    if not success:
        log("Account switch failed")
    return success

async def do_like_task(link):
    log(f"Like task detected: {link}")
    success = await asyncio.to_thread(perform_like, d, link)
    if success:
        add_cash("like")
        log("Like completed (+1.1 cashcoins)")
        await smart_send("✅Completed")
    else:
        log("Like failed: post did not open")
        await smart_send("❌Skip")

async def do_comment_task(link, full_msg_text):
    log(f"Comment task detected: {link}")
    comment_text = full_msg_text.split("Text to comment :")[1].strip() if "Text to comment :" in full_msg_text else full_msg_text.strip()
    log(f"Comment text: {' '.join(comment_text.split())[:80]}")

    # Skip comments containing mentions
    if "@" in comment_text:
        log("Skipping: the comment contains a @mention")
        await smart_send("❌Skip")
        return

    log("Posting comment...")
    success = await asyncio.to_thread(perform_comment, d, link, comment_text)
    if success:
        if await smart_send_photo():
            add_cash("comment")
            log("Comment completed (+6.0 cashcoins)")
        else:
            log("Comment proof could not be sent")
            await smart_send("❌Skip")
    else:
        log("Comment failed: could not post it")
        await smart_send("❌Skip")

# ==========================================
# TELEGRAM MESSAGE ROUTER
# ==========================================
@client.on(events.NewMessage(chats=TARGET_CHAT, incoming=True))
async def handle_msg(event):
    """Run the router; anything unexpected is shown in the log instead of vanishing silently."""
    try:
        await process_bot_message(event)
    except Exception as e:
        log(f"Error: {type(e).__name__}: {e}")

async def process_bot_message(event):
    message = event.message
    global pending_task, pending_text, current_account_index, active_phone_account_index

    if not RUNNING: return

    text = getattr(message, 'raw_text', getattr(message, 'text', ''))
    if not text: return
    text_lower = text.lower()
    log(f"Bot: {' '.join(text.split())[:90]}")

    # Ignore informational messages
    if any(word in text_lower for word in ABSOLUTE_BLOCKS):
        log("Ignored: information message")
        return
    if "warnings" in text_lower and "profile's username" not in text_lower and "too many" not in text_lower:
        log("Ignored: warnings message")
        return

    # Stop everything when verification fails
    if "verification failed" in text_lower:
        log("Verification failed")
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
            log("Captcha: could not read the emoji")
            return
        target_emoji = emoji_match.group(2).strip()
        log(f"Captcha target emoji: {target_emoji}")

        # Extract the hidden verification link
        hidden_url = None
        entities = getattr(message, 'entities', None) or getattr(message, 'caption_entities', None)
        if entities:
            for ent in entities:
                if isinstance(ent, MessageEntityTextUrl):
                    hidden_url = ent.url
                    break
        if not hidden_url:
            log("Captcha: no verification link found")
            return

        # Launch Chrome now; it passes Turnstile while the solver is working
        answer_box = Future()
        captcha_task = asyncio.create_task(asyncio.to_thread(perform_captcha, d, hidden_url, answer_box))

        answers = []
        try:
            # Collect the 3 captcha images sent just before this message
            log("Collecting captcha images")
            photo_messages = []
            time_threshold = message.date - timedelta(seconds=15)

            async for past_msg in client.iter_messages(TARGET_CHAT, limit=10):
                if past_msg.date < time_threshold: break
                if past_msg.photo:
                    photo_messages.append(past_msg)
                    if len(photo_messages) == 3: break

            if len(photo_messages) == 3:
                log("Downloading 3 captcha images")
                downloads = await asyncio.gather(*[client.download_media(msg, bytes) for msg in photo_messages])
                downloads.reverse()
                log("Cleaning captcha images")
                b64_images = await asyncio.gather(*[
                    asyncio.to_thread(lambda b=img_bytes: process_image_in_ram(Image.open(io.BytesIO(b))))
                    for img_bytes in downloads
                ])

                # Ordered answers: [emoji match, next answer, next answer]
                log("Sending images to the solver")
                answers = await asyncio.to_thread(get_math_answer, target_emoji, list(b64_images))
            else:
                log(f"Captcha: found {len(photo_messages)} of 3 images")
        finally:
            # Release the Chrome thread: answer list on success, None on failure
            answer_box.set_result(answers if isinstance(answers, list) and answers else None)

        if answers == "LICENSE_ERROR":
            await captcha_task
            log("License error")
            await stop_automation("License error")
            return

        if not answers:
            await captcha_task
            log("Solver returned no answer")
            return

        log(f"Captcha computed: {answers}")

        log("Waiting for the Chrome step to finish")
        captcha_success = await captcha_task
        if not captcha_success:
            log("Captcha Chrome step failed")
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
            log("Comment task paired with its text")
        else:
            pending_task = text
            log("Comment task received, waiting for its text")
            return
    elif pending_task:
        text = f"{pending_task}\n\nText to comment : {text}"
        pending_task = None
        text_lower = text.lower()
        log("Comment text received, task is complete")

    match = re.search(r'(https?://[^\s\]\)\>]+)', text)
    extracted_link = match.group(1) if match else None

    if "choose social network" in text_lower:
        log("Choosing social network: Instagram")
        await smart_send("Instagram")

    # Account selection (rotates through configured accounts)
    elif any(phrase in text_lower for phrase in ["profile's username for tasks completing", "is on review", "please choose account from the list", "too many"]):
        account_info = accounts[current_account_index]
        current_account_index = (current_account_index + 1) % len(accounts)
        log(f"Selecting account: {account_info['username']}")
        await smart_send(account_info["username"])

    # Like task
    elif re.search(r'1\.1\s*cashcoins', text_lower):
        log("Like task offered (1.1 cashcoins)")
        if extracted_link:
            target_index = (current_account_index - 1) % len(accounts)
            if active_phone_account_index != target_index:
                success = await do_switch_account(accounts[target_index]["username"])
                if not success:
                    await smart_send("❌Skip")
                    return
                active_phone_account_index = target_index
            else:
                log("Phone is already on the right account")
            await do_like_task(extracted_link)
        else:
            log("No link in the task, skipping")
            await smart_send("❌Skip")

    # Comment task
    elif re.search(r'6\.0\s*cashcoins', text_lower) or ("leave the comment" in text_lower and extracted_link):
        log("Comment task offered (6.0 cashcoins)")
        target_index = (current_account_index - 1) % len(accounts)
        if active_phone_account_index != target_index:
            success = await do_switch_account(accounts[target_index]["username"])
            if not success:
                await smart_send("❌Skip")
                return
            active_phone_account_index = target_index
        else:
            log("Phone is already on the right account")
        await do_comment_task(extracted_link, text)

    # Unsupported task types
    elif re.search(r'(2\.5|1\.0)\s*cashcoins', text_lower):
        log("Unsupported task type, skipping")
        await smart_send("❌Skip")

    elif "saved successfully" in text_lower:
        log("Task saved, confirming")
        await smart_send("✅Completed")

    elif "no active tasks" in text_lower:
        log("No active tasks, asking again")
        await smart_send("Instagram")

    elif "verification passed" in text_lower:
        log("Verification passed")
        if d:
            log("Closing Chrome")
            await asyncio.to_thread(d.app_stop, "com.android.chrome")
        await smart_send("✅Completed")

    # Any other message is stored as potential comment text
    else:
        if not re.search(r'cashcoins', text_lower) and "verification" not in text_lower:
            pending_text = text
            log("Stored as possible comment text")

# ==========================================
# AUTOMATION CONTROL
# ==========================================
async def start_automation():
    """Connect to the phone, reset the bot chat and start the task flow."""
    global d, RUNNING, pending_task, pending_text

    log("Connecting UIAutomator2...")
    d = await asyncio.to_thread(connect_with_retry)
    if d is None:
        log("Could not connect to the phone")
        return False

    pending_task = None
    pending_text = None
    RUNNING = True
    ui_set_state("Running")
    log("Automation started")

    # Reset the chat and start the task flow (each step stops early if Stop is pressed)
    log("Waiting 7s before resetting the bot chat")
    await asyncio.sleep(7)
    if not RUNNING: return False
    log("Clearing the bot chat history")
    try:
        await client(DeleteHistoryRequest(peer=TARGET_CHAT, max_id=0, revoke=True))
    except Exception as e:
        log(f"Could not clear the chat history: {type(e).__name__}")

    log("Waiting 7s before restarting the bot")
    await asyncio.sleep(7)
    if not RUNNING: return False
    log("Bot restarted")
    log("Sending /start")
    await client.send_message(TARGET_CHAT, "/start", parse_mode=None, link_preview=False)
    delay = random.uniform(5.0, 7.0)
    log(f"Waiting {delay:.1f}s before opening Tasks")
    await asyncio.sleep(delay)
    if not RUNNING: return False
    log("Opening Tasks")
    await client.send_message(TARGET_CHAT, "📝Tasks📝", parse_mode=None, link_preview=False)
    return True

# ==========================================
# MAIN UI (Textual - clean dashboard)
# ==========================================
def license_error_text():
    """Short, friendly text for a failed license check."""
    known = {"invalid license key": "Invalid license key", "license expired": "License expired"}
    return known.get((LICENSE_MSG or "").strip().lower(), LICENSE_MSG)


# ==========================================
# DASHBOARD THEME
# ==========================================
THEME = {
    "label":   "#8F89AD",   # row labels
    "muted":   "#6F6A8C",   # timestamps, hints
    "divider": "#1D1A33",   # thin lines between rows
}

def _lerp_hex(a, b, t):
    a = a.lstrip("#"); b = b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))

def gradient_text(text, start, end, bold=True):
    """Colour a string with a smooth left-to-right gradient."""
    out = Text(justify="center")
    last = max(1, len(text) - 1)
    for i, ch in enumerate(text):
        style = _lerp_hex(start, end, i / last)
        out.append(ch, style=f"bold {style}" if bold else style)
    return out

def make_title():
    """Gradient heading with a small accent bar underneath."""
    title = gradient_text("AUTOMATION DASHBOARD", "#C9B8FF", "#5CC8FF")
    bar = gradient_text("━" * 8, "#7C5CFF", "#3FB6FF", bold=False)
    out = Text(justify="center")
    out.append_text(title)
    out.append("\n")
    out.append_text(bar)
    return out

_HAPTIC_BUSY = threading.Lock()

def haptic_tick():
    """One minimal 'tak' tick through Termux:API (runs in the background, never blocks the UI)."""
    if shutil.which("termux-vibrate") is None:
        return
    def run():
        if not _HAPTIC_BUSY.acquire(blocking=False):
            return                      # a tick is already playing; skip instead of piling up
        try:
            _quiet(["termux-vibrate", "-d", "20", "-f"], timeout=5)
        finally:
            _HAPTIC_BUSY.release()
    threading.Thread(target=run, daemon=True).start()

def info_row(label, value, color, dot="●", pill_bg=None):
    """One key/value row: label pinned left, value pinned right (symmetrical edges)."""
    grid = Table.grid(expand=True)
    grid.add_column(no_wrap=True, width=11)
    grid.add_column(justify="right", ratio=1, overflow="fold")
    value_text = Text(justify="right")
    if pill_bg:
        value_text.append(f" {dot} {value} ", style=f"bold {color} on {pill_bg}")
    else:
        value_text.append(f"{value} ", style=color)
        value_text.append(dot, style=color)
    grid.add_row(Text(label, style=f"bold {THEME['label']}"), value_text)
    return grid

class BotApp(App):
    """Minimal automation dashboard: info tile, live log with Clear/Share, and state-aware controls."""

    CSS = """
    Screen {
        background: #000000;
        color: #F5EFFF;
    }

    #title {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
        text-align: center;
        background: #000000;
    }

    #info_card, #log_card {
        width: 1fr;
        height: auto;
        margin: 1 2 0 2;
        padding: 1 2;
        background: #000000;
        border: round #4A3F91;
        border-title-color: #B69DFF;
        border-title-style: bold;
        border-title-align: left;
    }

    #panel {
        width: 100%;
        height: auto;
        background: #000000;
    }

    #log {
        width: 100%;
        height: auto;
        min-height: 2;
        color: #DAD3EE;
        padding: 0;
        background: #000000;
    }

    #actions {
        width: 1fr;
        height: auto;
        margin: 1 2 0 2;
    }

    #log_actions {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
    }

    Button {
        min-width: 0;
        height: 3;
        margin: 0;
        text-style: bold;
        background: #000000;
        color: #F1ECFF;
        border: round #4A3F91;
    }

    Button:hover, Button:focus {
        background-tint: #000000 0%;
        text-style: bold;
    }

    #actions Button {
        width: 100%;
    }

    #log_actions Button {
        width: 1fr;
    }

    #actions Button.start {
        background: #071F13;
        color: #5CF2A0;
        border: round #2BE37F;
    }
    #actions Button.start:hover, #actions Button.start:focus {
        background: #0B2E1C;
    }

    #actions Button.stop {
        background: #261B05;
        color: #FFD166;
        border: round #FFB020;
    }
    #actions Button.stop:hover, #actions Button.stop:focus {
        background: #33240A;
    }

    #exit {
        dock: bottom;
        width: 100%;
        margin: 0 2 1 2;
        background: #1F080E;
        color: #FF8A96;
        border: round #FF4D61;
    }
    #exit:hover, #exit:focus {
        background: #2D0C15;
    }

    #clear_logs {
        background: #0A0914;
        color: #A29BC0;
        border: round #4A3F91;
    }
    #clear_logs:hover, #clear_logs:focus {
        background: #121022;
    }

    #share_logs {
        margin-left: 2;
        background: #120E2B;
        color: #B69DFF;
        border: round #8F73FF;
    }
    #share_logs:hover, #share_logs:focus {
        background: #1B1540;
    }

    #actions Button:disabled,
    #log_actions Button:disabled,
    #exit:disabled {
        background: #000000;
        color: #4A4565;
        border: round #1F1B2E;
    }
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("s", "start", "Start"),
        ("x", "stop", "Stop"),
        ("ctrl+q", "quit", "Terminal"),
    ]

    UI_LOG_LINES = LOG_SCREEN_LINES      # newest lines shown on screen (logs.txt keeps everything)

    def __init__(self, account_name):
        super().__init__()
        self.account_name = account_name
        self.logged_in = bool(account_name)
        self.adb_text = ADB_NAME or adb_serial or "checking..."
        self.state_text = "Starting up..."
        self._busy = False
        self._opened_at = time.time()

    def compose(self) -> ComposeResult:
        yield Static(make_title(), id="title")
        with Vertical(id="info_card"):
            yield Static(id="panel")
        with Vertical(id="actions"):
            yield Button("Start Automation", id="toggle_run", classes="start")
        with Vertical(id="log_card"):
            yield Static(id="log")
            with Horizontal(id="log_actions"):
                yield Button("Clear logs", id="clear_logs")
                yield Button("Share logs", id="share_logs")
        yield Button("Exit", id="exit")          # docked to the very bottom of the screen

    # ---- panel / log helpers ----
    def state_style(self):
        lowered = self.state_text.lower()
        if RUNNING:
            return "#5CF2A0"
        if any(word in lowered for word in ("error", "failed", "offline", "contact developer")):
            return "#FF6B7A"
        if any(word in lowered for word in ("not logged", "not connected", "scan", "starting", "checking", "logging", "wait")):
            return "#FFD166"
        if lowered.startswith("ready"):
            return "bold #7CB7FF"
        return "bold #F1ECFF"

    def update_buttons(self):
        """Swap Start/Stop according to state and lock Exit while the automation is active."""
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

            locked = RUNNING or self._busy
            for button_id in ("#exit", "#clear_logs", "#share_logs"):
                self.query_one(button_id, Button).disabled = locked
        except Exception:
            pass

    def state_palette(self):
        """(text colour, pill background) for the STATUS row."""
        lowered = self.state_text.lower()
        if RUNNING:
            return "#5CF2A0", "#0C2B1B"
        if any(word in lowered for word in ("error", "failed", "offline", "contact developer")):
            return "#FF6B7A", "#2B0F15"
        if any(word in lowered for word in ("not logged", "not connected", "scan", "starting", "checking", "logging", "wait")):
            return "#FFD166", "#2B2209"
        if lowered.startswith("ready"):
            return "#7CB7FF", "#0E1F36"
        return "#F1ECFF", "#1D1A30"

    def refresh_panel(self):
        if self.account_name:
            account = ("Account", self.account_name, "#FFFFFF", "●")
        else:
            account = ("Account", "Not logged in", "#FFD166", "◌")

        if LICENSE_OK:
            soon = LICENSE_DAYS is not None and LICENSE_DAYS <= 7
            license_row = ("License", license_days_text(), "#FFD166" if soon else "#5CF2A0", "●")
        elif LICENSE_MSG == "checking...":
            license_row = ("License", "Verifying...", "#FFD166", "◌")
        else:
            license_row = ("License", license_error_text(), "#FF6B7A", "●")

        if adb_serial:
            device = ("Device", self.adb_text, "#5CC8FF", "●")
        else:
            device = ("Device", self.adb_text, "#FFD166", "◌")

        cash = ("Cashcoins", f"{CASH['tenths'] / 10:.1f}", "#FFD166", "●")
        fg, bg = self.state_palette()

        rows = [info_row(label.upper(), value, color, dot)
                for label, value, color, dot in (account, license_row, device, cash)]
        rows.append(info_row("STATUS", self.state_text, fg, "●"))

        parts = []
        for index, row in enumerate(rows):
            if index:
                parts.append(Rule(characters="─", style=THEME["divider"]))
            parts.append(row)
        try:
            self.query_one("#panel", Static).update(Group(*parts))
        except Exception:
            pass
        self.update_buttons()

    # ---- log view ----
    def reload_logs(self):
        """Show only the latest log line (everything else stays in logs.txt)."""
        try:
            box = self.query_one("#log", Static)
        except Exception:
            return
        with _LOG_LOCK:
            items = list(LOG_ENTRIES)[-1:]
        grid = Table.grid(expand=True, padding=(0, 2, 0, 0))
        grid.add_column(no_wrap=True)                 # timestamp
        grid.add_column(ratio=1, overflow="fold")     # message wraps under itself
        if items:
            for moment, text in items:
                grid.add_row(Text(f"{moment:%H:%M:%S}", style=THEME["muted"]),
                             Text(text, style=log_color(text)))
        else:
            grid.add_row(Text("--:--:--", style=THEME["muted"]),
                         Text("No activity yet", style=THEME["muted"]))
        box.update(grid)

    def add_log(self, moment, text):
        self.reload_logs()

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
        self.query_one("#info_card").border_title = "System information"
        self.query_one("#log_card").border_title = "System logs"
        self.refresh_panel()
        self.reload_logs()                      # shows the latest lines logged before the dashboard opened
        self.run_worker(self.boot(), exclusive=False)

    def on_unmount(self):
        global ui
        ui = None

    async def boot(self):
        log("Dashboard started")
        # License and device are checked at the same time
        license_task = asyncio.create_task(asyncio.to_thread(check_license))
        try:
            await self.ensure_adb()
        except Exception as e:
            log(f"Startup error: {e}")
        await license_task
        self.refresh_panel()
        self.update_ready_state()

    def update_ready_state(self):
        """Show what the user should do next."""
        if RUNNING or self._busy:
            return
        if not LICENSE_OK and LICENSE_MSG != "checking...":
            self.set_state("Contact developer")
            return
        if not adb_serial:
            self.set_state("ADB not connected")
        else:
            self.set_state("Ready to start")

    # ---- ADB ----
    async def ensure_adb(self):
        global adb_serial
        log("Checking ADB device...")
        if adb_serial and await asyncio.to_thread(adb_alive, adb_serial):
            name = await asyncio.to_thread(get_device_name, adb_serial)
            self.set_adb(name)
            log(f"ADB device ready: {name}")
            self.update_ready_state()
            return True
        self.set_adb("checking...")
        serial = await asyncio.to_thread(adb_find_ready)
        if not serial:
            if shutil.which("adb") is None:
                log("ADB not installed")
            elif notif_available():
                log("Starting ADB setup...")
                self.set_state("Waiting for ADB pairing")
                serial = await asyncio.to_thread(adb_auto_setup, log)
            else:
                log("Termux:API not installed")
        if serial:
            adb_serial = serial
            name = await asyncio.to_thread(get_device_name, serial)
            self.set_adb(name)
            log(f"ADB device ready: {name}")
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
        log("Leaving to the terminal")
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
        log("Exit pressed, closing Termux")
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
            log("Start pressed")
            reset_cash()         # a new run starts from zero
            self.set_state("Starting...")
            # Re-verify the license and the device at the same time
            _, adb_ok = await asyncio.gather(asyncio.to_thread(check_license), self.ensure_adb())
            self.refresh_panel()
            if not LICENSE_OK:
                log(f"Start blocked, license: {license_error_text()}")
                self.set_state("Contact developer")
                return
            if not adb_ok:
                log("Start blocked: ADB is not connected")
                self.set_state("Stopped")
                return
            ok = await start_automation()
            if not ok and not RUNNING:
                self.set_state("Stopped")
        except Exception as e:
            log(f"Start failed: {e}")
            self.set_state("Stopped")
        finally:
            self._busy = False
            self.refresh_panel()

    async def stop_flow(self):
        if not RUNNING:
            log("Not running")
            return
        log("Stop pressed")
        await stop_automation("Stopped by user")

    async def clear_logs_flow(self):
        clear_logs()
        self.reload_logs()

    async def share_logs_flow(self):
        log("Preparing logs to share...")
        ok, message = await asyncio.to_thread(share_logs)
        log(message)

    async def on_button_pressed(self, event: Button.Pressed):
        button_id = event.button.id
        haptic_tick()
        if button_id == "toggle_run":
            if RUNNING:
                self.action_stop()
            else:
                self.action_start()
        elif button_id == "clear_logs":
            await self.clear_logs_flow()
        elif button_id == "share_logs":
            self.run_worker(self.share_logs_flow(), exclusive=False)
        elif button_id == "exit":
            await self.action_exit()

# ==========================================
# PRE-UI LOGIN (terminal, QR shown as an image)
# ==========================================
QR_PNG_PATH = "/sdcard/login_qr.png"
QR_TIMEOUT = 30          # Telegram QR tokens live about 30 seconds
RETRY_WAIT = 120         # seconds to wait after an expired QR before Termux closes

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
        return ("Replica app is not installed.\n"
                "Install it first, open it once, grant storage access, create your config, then run this script again.")
    resolved = run_adb("-s", adb_serial, "shell", "pm", "resolve-activity", "--brief",
                       "-a", "android.intent.action.VIEW", "-d", f"file://{QR_PNG_PATH}",
                       "-t", "image/png", "-p", COMPANION_PKG)
    if "no activity found" in resolved.lower():
        return ("Replica app is outdated.\n"
                "Install the latest version of Replica, then run this script again.")
    return None

def open_qr_image(path=QR_PNG_PATH):
    """Open the PNG in the Replica app. Returns None on success, otherwise an error text."""
    if not adb_serial:
        return "ADB is not connected."
    out = run_adb("-s", adb_serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                  "-d", f"file://{path}", "-t", "image/png", "-p", COMPANION_PKG, timeout=15)
    lowered = out.lower()
    if "error" in lowered or "exception" in lowered or "unable to resolve" in lowered:
        return f"Could not open Replica app: {out}"
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
    """Ask for the Telegram 2FA password using standard visible input."""
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
            term_log("Wrong 2FA password")
        except errors.FloodWaitError as e:
            term_log(f"Flood wait: {e.seconds}s")
            return False

async def expired_and_exit():
    """QR expired: show the 2 minute countdown, delete the incomplete session, clear the screen, close Termux."""
    remove_qr_png()
    bring_termux_to_front()
    clear_terminal()
    term_log("QR code expired. Wait 2 minutes.")
    for remaining in range(RETRY_WAIT, 0, -1):
        print(f"\r\033[1;38;2;255;209;102mTry again in {remaining // 60}:{remaining % 60:02d}\033[0m   ", end="", flush=True)
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
    clear_terminal()

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

    term_log("Creating QR code...")
    qr_login = await client.qr_login()
    problem = None
    try:
        await asyncio.to_thread(save_qr_png, qr_login.url)
        problem = await asyncio.to_thread(open_qr_image)
    except Exception as e:
        problem = f"QR image error: {e}"
    if problem:
        remove_qr_png()
        term_log(f"Error: {problem}")
        sys.exit(1)
    term_log("QR opened. Scan now.")

    wait_task = asyncio.create_task(qr_login.wait(timeout=QR_TIMEOUT))
    start = time.time()
    while not wait_task.done():
        remaining = max(0, QR_TIMEOUT - int(time.time() - start))
        print(f"\r\033[1;38;2;255;209;102mWaiting for scan: {remaining:>2}s\033[0m  ", end="", flush=True)
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

    clear_terminal()
    term_log("QR code scanned")

    if need_password and not await ask_password_terminal():
        term_log("Login cancelled")
        return False

    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
    term_log(f"Logged in: {name}")
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
        term_log(f"Session check failed: {e}")
        return None
    if authorized:
        name = "Telegram account"
        try:
            me = await client.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or me.username or "Unknown"
            term_log(f"Logged in: {name}")
        except Exception:
            pass
        return name
    return await terminal_login() or None

async def restart_with_fresh_session():
    """The session turned out to be unusable at runtime: delete it and restart the script."""
    term_log("Session corrupted, restarting...")
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
    Connect to Telegram before the UI opens, printing concise colored progress.
    """
    term_log("Connecting Telegram...")
    task = asyncio.ensure_future(client.connect())
    waited = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=5)
        if done:
            try:
                task.result()
                term_log("Telegram connected")
                return True
            except Exception as e:
                if is_db_error(e):
                    await restart_with_fresh_session()
                term_log(f"Telegram connection failed: {e}")
                return False
        waited += 5
        if waited >= max_wait:
            task.cancel()
            term_log("Telegram timed out")
            return False
        term_log(f"Waiting for Telegram ({waited}s)")

async def main():
    # Show Telethon connection warnings in the terminal now and in the log screen later
    handler = TelethonLogHandler(level=logging.WARNING)
    logging.getLogger("telethon").addHandler(handler)

    # 1. Check for an active background ADB device (emulator-5554); if none, run notification setup
    await asyncio.to_thread(ensure_adb_startup)

    # 1b. The Replica app must be installed (it shows the login QR code)
    problem = await asyncio.to_thread(check_companion_app)
    if problem:
        fail_startup("Replica app error", problem)

    # 2. Connect to Telegram, log in if needed, then launch the UI
    account_name = None
    if await connect_telegram_early():
        account_name = await login_before_ui()
    if not account_name:
        fail_startup("Telegram login incomplete", prefix="")

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
        term_log("Script stopped")
    else:
        term_log(f"Dashboard closed (code: {getattr(app, 'return_code', None)})")

if __name__ == "__main__":
    loop.run_until_complete(main())
