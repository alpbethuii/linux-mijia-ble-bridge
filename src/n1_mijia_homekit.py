#!/usr/bin/env python3
"""Read-only HomeKit exposure for the N1's existing Mijia BLE sensor state.

This service never accesses Bluetooth, bindkeys, Xiaomi APIs, or the plug actuator.
It reads only the existing non-secret sensor-state JSON and exports temperature and
humidity as two HomeKit sensor accessories through HAP-python.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_SENSOR
from zeroconf import IPVersion, InterfaceChoice
from zeroconf.asyncio import AsyncZeroconf

STATE_PATH = Path(os.environ.get("N1_MIJIA_STATE_PATH", "/var/lib/mijia-ble-bridge/advertisement-state.json"))
PERSIST_PATH = "/var/lib/n1-mijia-homekit/accessory.state"
PORT = 51826
STALE_AFTER_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 30
POLL_INTERVAL_SECONDS = 10
PIN_ENV_NAME = "N1_HOMEKIT_PINCODE"
PIN_PATTERN = re.compile(r"^[0-9]{3}-[0-9]{2}-[0-9]{3}$")

_LOG = logging.getLogger("n1_mijia_homekit")


def load_pincode(environ: dict[str, str] | None = None) -> bytes:
    """Read a stable setup code from the root-only systemd environment file."""
    source = os.environ if environ is None else environ
    value = source.get(PIN_ENV_NAME, "").strip()
    if not PIN_PATTERN.fullmatch(value):
        raise ValueError("homekit_pincode_missing_or_invalid")
    return value.encode("ascii")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _age_seconds(value: Any, now: datetime) -> float | None:
    stamp = _parse_timestamp(value)
    if stamp is None:
        return None
    return (now - stamp).total_seconds()


def snapshot_is_fresh(snapshot: dict[str, Any], now: datetime | None = None) -> bool:
    """Validate sensor status and both independent measurement timestamps."""
    now = now or datetime.now().astimezone()
    if not isinstance(snapshot, dict) or snapshot.get("status") != "ok":
        return False
    if snapshot.get("decode_verified") is not True:
        return False
    for key in ("last_seen_at", "temperature_observed_at", "humidity_observed_at"):
        age = _age_seconds(snapshot.get(key), now)
        if age is None or age > STALE_AFTER_SECONDS or age < -MAX_FUTURE_SKEW_SECONDS:
            return False
    try:
        temperature = float(snapshot["temperature_c"])
        humidity = float(snapshot["humidity_percent"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(temperature) or not -50.0 <= temperature <= 100.0:
        return False
    if not math.isfinite(humidity) or not 0.0 <= humidity <= 100.0:
        return False
    return True


def read_fresh_snapshot(path: Path = STATE_PATH, now: datetime | None = None) -> dict[str, Any] | None:
    """Read only the receiver's state file; reject loose permissions and stale data."""
    try:
        metadata = path.stat()
        if metadata.st_mode & 0o077:
            return None
        raw = path.read_text(encoding="utf-8")
        if len(raw) > 64 * 1024:
            return None
        snapshot = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not snapshot_is_fresh(snapshot, now):
        return None
    return {
        "status": "ok",
        "decode_verified": True,
        "temperature_c": round(float(snapshot["temperature_c"]), 1),
        "humidity_percent": round(float(snapshot["humidity_percent"]), 1),
        "last_seen_at": snapshot["last_seen_at"],
        "temperature_observed_at": snapshot["temperature_observed_at"],
        "humidity_observed_at": snapshot["humidity_observed_at"],
    }


class _SensorAccessory(Accessory):
    """Base class that makes stale source data unavailable rather than lying."""

    category = CATEGORY_SENSOR

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._snapshot: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return self._snapshot is not None and snapshot_is_fresh(self._snapshot)

    def _refresh_snapshot(self) -> dict[str, Any] | None:
        snapshot = read_fresh_snapshot()
        self._snapshot = snapshot
        return snapshot


class MijiaTemperatureAccessory(_SensorAccessory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        service = self.add_preload_service("TemperatureSensor", chars=["Name"])
        service.configure_char("Name", value="Temperature")
        self._temperature = service.configure_char("CurrentTemperature")
        self.set_info_service(
            manufacturer="Xiaomi / N1 local bridge",
            model="MJWSD06MMC HomeKit sensor",
            serial_number="N1-MIJIA-TEMP-01",
            firmware_revision="1.0.0",
        )
        self._refresh()

    def _refresh(self) -> None:
        snapshot = self._refresh_snapshot()
        if snapshot is not None:
            self._temperature.set_value(snapshot["temperature_c"], should_notify=True)

    @Accessory.run_at_interval(POLL_INTERVAL_SECONDS)
    def run(self) -> None:
        self._refresh()


class MijiaHumidityAccessory(_SensorAccessory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        service = self.add_preload_service("HumiditySensor", chars=["Name"])
        service.configure_char("Name", value="Humidity")
        self._humidity = service.configure_char("CurrentRelativeHumidity")
        self.set_info_service(
            manufacturer="Xiaomi / N1 local bridge",
            model="MJWSD06MMC HomeKit sensor",
            serial_number="N1-MIJIA-HUM-01",
            firmware_revision="1.0.0",
        )
        self._refresh()

    def _refresh(self) -> None:
        snapshot = self._refresh_snapshot()
        if snapshot is not None:
            self._humidity.set_value(snapshot["humidity_percent"], should_notify=True)

    @Accessory.run_at_interval(POLL_INTERVAL_SECONDS)
    def run(self) -> None:
        self._refresh()


class MijiaHomeKitBridge(Bridge):
    """Avoid writing the pairing code or QR payload to persistent journal logs."""

    def setup_message(self) -> None:
        _LOG.info("HomeKit setup code is configured locally and is not emitted to logs")


def build_bridge(driver: AccessoryDriver) -> Bridge:
    """Keep child accessory order and names stable after HomeKit pairing."""
    bridge = MijiaHomeKitBridge(driver, "N1 Mijia BLE Bridge")
    bridge.add_accessory(MijiaTemperatureAccessory(driver, "N1 Mijia Temperature"))
    bridge.add_accessory(MijiaHumidityAccessory(driver, "N1 Mijia Humidity"))
    return bridge


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-state", action="store_true", help="print a sanitized state check and exit")
    args = parser.parse_args()

    if args.check_state:
        snapshot = read_fresh_snapshot()
        if snapshot is None:
            print(json.dumps({"available": False, "reason": "sensor_state_missing_stale_or_invalid"}))
            return 2
        print(json.dumps({"available": True, **snapshot}, ensure_ascii=False))
        return 0

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s: %(message)s")
    try:
        pincode = load_pincode()
    except ValueError as exc:
        _LOG.error("%s", exc)
        return 2

    # N1 has IPv6 disabled globally. Explicit IPv4-only mDNS avoids zeroconf's
    # default adapter enumeration failing on that host configuration.
    advertiser = AsyncZeroconf(
        interfaces=InterfaceChoice.Default,
        ip_version=IPVersion.V4Only,
    )
    driver = AccessoryDriver(
        port=PORT,
        pincode=pincode,
        persist_file=PERSIST_PATH,
        async_zeroconf_instance=advertiser,
    )
    driver.add_accessory(accessory=build_bridge(driver))
    signal.signal(signal.SIGTERM, driver.signal_handler)
    signal.signal(signal.SIGINT, driver.signal_handler)
    _LOG.info("Starting read-only HomeKit sensor bridge on port %s", PORT)
    _LOG.info("Source state freshness limit: %s seconds", STALE_AFTER_SECONDS)
    driver.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
