"""Where the app keeps its files, first-run setup, and console handling.

Works the same when run from source (python main.py) and as a standalone program built
with PyInstaller: settings (.env) and downloaded rides (fit/) live next to the program.
"""

import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key

FROZEN = getattr(sys, "frozen", False)  # True when running as a PyInstaller program
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
ENV_PATH = APP_DIR / ".env"
FIT_DIR = APP_DIR / "fit"

CREDENTIALS = {
    "zwift": [("ZWIFT_USERNAME", "Zwift login email", False), ("ZWIFT_PASSWORD", "Zwift password", True)],
    "garmin": [("GARMIN_EMAIL", "Garmin Connect login email", False), ("GARMIN_PASSWORD", "Garmin Connect password", True)],
}


def configure_console():
    """Never crash on emoji output when the console or a redirect isn't UTF-8 (Windows)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def load_settings():
    load_dotenv(ENV_PATH)


def ensure_credentials(*services):
    """Asks for missing logins of the given services ("zwift", "garmin") and saves them to .env."""
    missing = [item for service in services for item in CREDENTIALS[service] if not os.getenv(item[0])]
    if not missing:
        return
    if not sys.stdin.isatty():
        names = ", ".join(name for name, _, _ in missing)
        raise ValueError(f"Missing settings: {names}. Add them to {ENV_PATH}.")

    print(f"\nFirst-time setup: your logins are saved in {ENV_PATH}")
    ENV_PATH.touch(exist_ok=True)
    for name, label, secret in missing:
        value = ""
        while not value:
            value = (getpass.getpass if secret else input)(f"  {label}: ").strip()
        set_key(str(ENV_PATH), name, value)
        os.environ[name] = value
    if os.name != "nt":
        ENV_PATH.chmod(0o600)  # passwords: readable only by you
    print()


def pause_if_double_clicked():
    """On Windows, a double-clicked program's window closes at once; keep it open to read the result."""
    if not (FROZEN and os.name == "nt"):
        return
    try:
        import ctypes

        # Only this process attached to the console means it was opened by double-click
        if ctypes.windll.kernel32.GetConsoleProcessList((ctypes.c_uint * 2)(), 2) <= 1:
            input("\nPress Enter to close this window...")
    except (OSError, AttributeError, EOFError):
        pass
