#!/usr/bin/python3
"""Observe one paired phone's AVRCP control profile without touching smart plugs.

The device address is supplied through PHONE_BT_ADDRESS in a root-only
systemd EnvironmentFile. Reconnection uses a targeted BlueZ D-Bus method rather
than spawning bluetoothctl, with bounded exponential backoff and sanitized logs.
"""
import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path

ADDRESS = os.environ.get("PHONE_BT_ADDRESS", "").strip().upper()
SUBJECT = os.environ.get("PHONE_BT_SUBJECT", "paired_phone").strip() or "paired_phone"
UUID = "0000110c-0000-1000-8000-00805f9b34fb"  # AVRCP Target/control profile
BLUEZ_PATH = (
    "/org/bluez/hci0/dev_" + ADDRESS.replace(":", "_")
    if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", ADDRESS)
    else None
)
ROOT = Path(os.environ.get("PHONE_PRESENCE_STATE_DIR", "/run/mijia-ble-bridge-presence"))
STATE = ROOT / "state.json"
POLL_SEC = 2.0
CONNECT_TIMEOUT_SEC = 8
RETRY_BASE_SEC = 5
RETRY_MAX_SEC = 60
FAILURE_LOG_INTERVAL_SEC = 600


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def observe():
    if BLUEZ_PATH is None:
        return None
    try:
        p = subprocess.run(
            ["busctl", "--system", "get-property", "org.bluez", BLUEZ_PATH,
             "org.bluez.Device1", "Connected"],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode or not p.stdout.strip().startswith("b "):
        return None
    return p.stdout.split()[1].lower() == "true"


def classify_connect_error(detail):
    """Return a stable, non-sensitive category; never persist raw D-Bus text."""
    message = str(detail or "").lower()
    if any(token in message for token in (
        "host is down", "br-connection-page-timeout", "connection timed out",
        "connection refused", "br-connection-create-socket",
    )):
        return "remote_unreachable"
    if "org.bluez.error.notready" in message:
        return "bluez_not_ready"
    if "org.bluez.error.notavailable" in message:
        return "profile_not_available"
    if "org.bluez.error.inprogress" in message:
        return "in_progress"
    if any(token in message for token in (
        "org.freedesktop.dbus.error.accessdenied", "access denied",
        "permission denied",
    )):
        return "permission_denied"
    return "dbus_error"


def request_avrcp_connection():
    """Request only the selected profile through a one-shot D-Bus method call."""
    if BLUEZ_PATH is None:
        return False, "device_address_not_configured"
    try:
        p = subprocess.run(
            ["busctl", "--system", f"--timeout={CONNECT_TIMEOUT_SEC}", "call",
             "org.bluez", BLUEZ_PATH, "org.bluez.Device1", "ConnectProfile",
             "s", UUID],
            capture_output=True, text=True, timeout=CONNECT_TIMEOUT_SEC + 2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError:
        return False, "command_unavailable"
    if p.returncode == 0:
        return True, "request_accepted"
    return False, classify_connect_error(p.stderr or p.stdout)


def retry_delay_seconds(consecutive_failures):
    """Exponential retry delay: 5, 10, 20, 40, then at most 60 seconds."""
    failures = max(1, int(consecutive_failures))
    exponent = min(failures - 1, 4)
    return min(RETRY_BASE_SEC * (2 ** exponent), RETRY_MAX_SEC)


def write_state(obj):
    temp = ROOT / "state.tmp"
    temp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temp, STATE)


def main():
    if BLUEZ_PATH is None:
        print("Phone AVRCP presence observer stopped: PHONE_BT_ADDRESS is missing or invalid", flush=True)
        raise SystemExit(2)

    ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    old = object()
    last_change = timestamp()
    failures = 0
    attempts = 0
    last_result = "not_attempted"
    last_error_class = None
    last_failure_log_at = 0.0
    next_attempt_at = 0.0
    print("Phone AVRCP presence observer started (no plug control)", flush=True)

    while True:
        val = observe()
        if val is not old and val != old:
            old = val
            last_change = timestamp()
            print("Bluetooth link " + (
                "connected" if val is True else
                "disconnected" if val is False else "unknown"
            ), flush=True)

        if val is True:
            failures = 0
            next_attempt_at = 0.0
            last_error_class = None

        if val is False and time.monotonic() >= next_attempt_at:
            attempts += 1
            accepted, category = request_avrcp_connection()
            val = observe()
            if val is not old and val != old:
                old = val
                last_change = timestamp()
                print("Bluetooth link " + (
                    "connected" if val is True else
                    "disconnected" if val is False else "unknown"
                ), flush=True)

            if val is True:
                failures = 0
                next_attempt_at = 0.0
                last_result = "success"
                last_error_class = None
                print("AVRCP reconnect succeeded", flush=True)
            else:
                failures += 1
                if accepted:
                    category = "request_accepted_not_connected"
                previous_error_class = last_error_class
                last_result = category
                delay = retry_delay_seconds(failures)
                next_attempt_at = time.monotonic() + delay
                now_mono = time.monotonic()
                if (failures == 1 or category != previous_error_class
                        or now_mono - last_failure_log_at >= FAILURE_LOG_INTERVAL_SEC):
                    last_error_class = category
                    print(
                        "AVRCP reconnect deferred; category=" + category
                        + "; consecutive_failures=" + str(failures)
                        + "; retry_in_seconds=" + str(delay),
                        flush=True,
                    )
                    last_failure_log_at = now_mono

        delay_remaining = max(0.0, next_attempt_at - time.monotonic())
        write_state({
            "schema": 1,
            "subject": SUBJECT,
            "link": "connected" if val is True else "disconnected" if val is False else "unknown",
            "observed_at": timestamp(),
            "last_transition_at": last_change,
            "reconnect_attempts_this_run": attempts,
            "reconnect_failures_consecutive": failures,
            "last_reconnect_result": last_result,
            "last_reconnect_error_class": last_error_class,
            "retry_delay_seconds": int(delay_remaining) if delay_remaining else 0,
            "display_control_enabled": False,
            "away_verified": False,
        })
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
