#!/usr/bin/env python3
"""Linux local FE95 advertisement receiver for Xiaomi/Mijia BLE sensors.

Uses Bleak active discovery (not a GATT connection), then the installed
xiaomi-ble parser locally. Never logs or exports key material/raw payloads.
"""
import argparse
import asyncio
import json
import logging
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakScanner
from home_assistant_bluetooth import BluetoothServiceInfo
from xiaomi_ble.parser import XiaomiBluetoothDeviceData

logging.disable(logging.CRITICAL)

TARGET_MAC = os.environ.get("MIJIA_SENSOR_MAC", "").strip().upper()
TARGET_PRODUCT_ID = int(os.environ.get("MIJIA_PRODUCT_ID", "0x55B5"), 0)
FE95_NORMALIZED = "0000fe9500001000800000805f9b34fb"
KEY_PATH = Path(os.environ.get("MIJIA_BINDKEY_PATH", "/etc/mijia-ble-bridge/bindkey"))
STATE_PATH = Path(os.environ.get("MIJIA_STATE_PATH", "/var/lib/mijia-ble-bridge/advertisement-state.json"))
STALE_AFTER_SECONDS = 300
WATCHDOG_INTERVAL_SECONDS = 10
STATE_WRITE_MIN_INTERVAL_SECONDS = 30

state = {
    "schema_version": 1,
    "source": "local_fe95_advertisement",
    "target_mac": TARGET_MAC,
    "status": "starting",
    "started_at": None,
    "last_seen_at": None,
    "last_measurement_at": None,
    "temperature_c": None,
    "temperature_observed_at": None,
    "humidity_percent": None,
    "humidity_observed_at": None,
    "rssi_dbm": None,
    "advertisement_count": 0,
    "parsed_frame_count": 0,
    "decode_verified": False,
    "parse_error_count": 0,
    "last_parse_error": None,
}
parser = None
_last_state_write_monotonic = 0.0
_last_written_status = None


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def age_seconds(value):
    if not value:
        return None
    try:
        return max(0.0, (datetime.now().astimezone() -
                         datetime.fromisoformat(value)).total_seconds())
    except (TypeError, ValueError):
        return None


def update_status():
    last_seen_age = age_seconds(state["last_seen_at"])
    temp_age = age_seconds(state["temperature_observed_at"])
    humidity_age = age_seconds(state["humidity_observed_at"])

    if last_seen_age is None:
        state["status"] = "waiting_for_broadcast"
    elif last_seen_age > STALE_AFTER_SECONDS:
        state["status"] = "stale"
    else:
        temp_fresh = (
            state["temperature_c"] is not None
            and temp_age is not None
            and temp_age <= STALE_AFTER_SECONDS
        )
        humidity_fresh = (
            state["humidity_percent"] is not None
            and humidity_age is not None
            and humidity_age <= STALE_AFTER_SECONDS
        )
        if temp_fresh and humidity_fresh:
            state["status"] = "ok"
        elif temp_fresh or humidity_fresh:
            state["status"] = "partial"
        else:
            state["status"] = "broadcast_received_no_values"


def write_state(recompute_status=True, force=False):
    global _last_state_write_monotonic, _last_written_status
    if recompute_status:
        update_status()
    now_mono = time.monotonic()
    status_changed = state["status"] != _last_written_status
    if (
        not force
        and not status_changed
        and now_mono - _last_state_write_monotonic < STATE_WRITE_MIN_INTERVAL_SECONDS
    ):
        return False

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)
    _last_state_write_monotonic = now_mono
    _last_written_status = state["status"]
    return True


def load_previous_state():
    """Restore only known, non-secret state fields across process restart."""
    if not STATE_PATH.is_file():
        return
    try:
        previous = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if (
            not isinstance(previous, dict)
            or previous.get("schema_version") != state["schema_version"]
            or previous.get("target_mac") != TARGET_MAC
        ):
            return
        immutable = {"schema_version", "source", "target_mac", "started_at", "status"}
        for key in state:
            if key not in immutable and key in previous:
                state[key] = previous[key]
    except Exception:
        # Do not expose file contents or exception text.
        state["last_parse_error"] = "previous_state_invalid"


def load_local_parser():
    if not KEY_PATH.is_file():
        raise RuntimeError("local_config_missing")
    mode = stat.S_IMODE(KEY_PATH.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("local_config_permissions_invalid")
    raw = KEY_PATH.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", raw):
        raise RuntimeError("local_config_format_invalid")
    return XiaomiBluetoothDeviceData(bindkey=bytes.fromhex(raw))


def on_advertisement(device, advertisement):
    address = (getattr(device, "address", None) or "").upper()
    if address != TARGET_MAC:
        return

    raw_service_data = advertisement.service_data or {}
    service_data = {}
    for uuid, raw_payload in raw_service_data.items():
        if str(uuid).lower().replace("-", "") != FE95_NORMALIZED:
            continue
        payload = bytes(raw_payload)
        # MiBeacon product ID is in the clear header; reject unrelated FE95 devices.
        if len(payload) < 4:
            continue
        product_id = payload[2] | (payload[3] << 8)
        if product_id != TARGET_PRODUCT_ID:
            continue
        service_data["0000fe95-0000-1000-8000-00805f9b34fb"] = payload

    if not service_data:
        return

    stamp = now_iso()
    state["advertisement_count"] += 1
    state["last_seen_at"] = stamp
    state["rssi_dbm"] = getattr(advertisement, "rssi", None)

    try:
        info = BluetoothServiceInfo(
            address=device.address,
            manufacturer_data=advertisement.manufacturer_data,
            name=advertisement.local_name or device.name,
            rssi=advertisement.rssi,
            service_data=service_data,
            service_uuids=advertisement.service_uuids,
            source="n1-local-fe95",
        )
        update = parser.update(info)
        measurement_value_found = False
        measurement_value_changed = False
        state["last_parse_error"] = None

        for device_key, sensor_value in update.entity_values.items():
            key = str(device_key.key).lower()
            value = sensor_value.native_value
            if value is None:
                continue
            if key == "temperature":
                numeric_value = float(value)
                if state["temperature_c"] != numeric_value:
                    measurement_value_changed = True
                state["temperature_c"] = numeric_value
                state["temperature_observed_at"] = stamp
                measurement_value_found = True
            elif key == "humidity":
                numeric_value = float(value)
                if state["humidity_percent"] != numeric_value:
                    measurement_value_changed = True
                state["humidity_percent"] = numeric_value
                state["humidity_observed_at"] = stamp
                measurement_value_found = True

        if measurement_value_found:
            state["parsed_frame_count"] += 1
            state["last_measurement_at"] = stamp
        state["decode_verified"] = bool(getattr(parser, "bindkey_verified", False))
        write_state(force=measurement_value_changed)
    except Exception as exc:
        # Keep diagnostics non-sensitive: exception class only, never payload or key.
        state["parse_error_count"] += 1
        state["last_parse_error"] = type(exc).__name__
        write_state()


async def main(duration_seconds=None, state_path=None):
    global parser, STATE_PATH
    if state_path is not None:
        STATE_PATH = Path(state_path)
    load_previous_state()
    state["started_at"] = now_iso()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", TARGET_MAC):
        state["status"] = "configuration_error"
        state["last_parse_error"] = "target_mac_missing_or_invalid"
        write_state(recompute_status=False, force=True)
        if duration_seconds is not None:
            print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        return 2
    if not 0 <= TARGET_PRODUCT_ID <= 0xFFFF:
        state["status"] = "configuration_error"
        state["last_parse_error"] = "product_id_out_of_range"
        write_state(recompute_status=False, force=True)
        if duration_seconds is not None:
            print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        return 2
    try:
        parser = load_local_parser()
    except Exception as exc:
        state["status"] = str(exc) if str(exc).startswith("local_config_") else "local_config_error"
        write_state(recompute_status=False, force=True)
        if duration_seconds is not None:
            print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        return 2

    write_state(force=True)
    scanner = BleakScanner(
        on_advertisement,
        scanning_mode="active",
        bluez={"filters": {"DuplicateData": True}},
    )
    try:
        await scanner.start()
    except Exception as exc:
        state["status"] = "scanner_start_error"
        state["last_parse_error"] = type(exc).__name__
        write_state(recompute_status=False, force=True)
        if duration_seconds is not None:
            print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        return 3

    state["status"] = "waiting_for_broadcast"
    write_state(force=True)
    try:
        if duration_seconds is None:
            while True:
                await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
                before = state["status"]
                update_status()
                if state["status"] != before:
                    write_state(force=True)
        else:
            await asyncio.sleep(duration_seconds)
    finally:
        await scanner.stop()

    update_status()
    write_state(force=True)
    if duration_seconds is not None:
        # One-shot mode emits only the sanitized state, never payload/key data.
        print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Local FE95 receiver")
    arg_parser.add_argument(
        "--duration-seconds", type=int, default=None,
        help="bounded local diagnostic; omitted for continuous service mode",
    )
    arg_parser.add_argument(
        "--state-path", default=None,
        help="optional state path override (use /run for one-shot diagnostics)",
    )
    args = arg_parser.parse_args()
    if args.duration_seconds is not None and not 60 <= args.duration_seconds <= 1800:
        arg_parser.error("--duration-seconds must be between 60 and 1800")
    raise SystemExit(asyncio.run(main(args.duration_seconds, args.state_path)))
