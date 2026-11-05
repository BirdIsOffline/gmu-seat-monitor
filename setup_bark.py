"""Connect an existing GMU monitor to free Bark iPhone push notifications."""

from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit
import re

PATH = Path(__file__).with_name("bark_key.txt")


def main():
    print("GMU -> Bark iPhone notification setup")
    print("1. Install 'Bark - Custom Notifications' on your iPhone.")
    print("2. Open Bark, allow notifications, and test a notification in the app.")
    print("3. Copy the example/test push URL Bark shows you.")
    print("4. Paste that URL below. It will be hidden as you paste for privacy.")
    print("   Example format: https://api.day.app/YOUR_DEVICE_KEY/test")
    print("   (The URL is a secret. Do not send it to anyone.)")
    raw = getpass("\nPaste your Bark URL (input hidden), then press Enter: ").strip()
    try:
        parsed = urlsplit(raw)
        if parsed.scheme != "https" or parsed.hostname != "api.day.app" or parsed.port is not None or parsed.username is not None or parsed.password is not None:
            raise ValueError("Expected a Bark URL beginning with https://api.day.app/")
        path_parts = parsed.path.strip("/").split("/")
        key = path_parts[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key) or key == 'push':
            raise ValueError("Could not find a valid Bark device key in that URL.")
    except (ValueError, TypeError) as exc:
        print(f"Invalid Bark URL: {exc}")
        return 1
    PATH.write_text(key + "\n", encoding="utf-8")
    print("Bark configured successfully. Your key was saved locally and not displayed.")
    print("Lock your iPhone and run: py monitor.py --test-notification")
    print("No GMU password or account information is used or saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
