import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if importlib.util.find_spec("pyhap") is None:
    raise unittest.SkipTest("HAP-python is optional; install requirements-homekit.txt to run these tests")
SOURCE = ROOT / "src" / "n1_mijia_homekit.py"
if not SOURCE.exists():
    SOURCE = ROOT / "n1_mijia_homekit.py"
spec = importlib.util.spec_from_file_location("n1_mijia_homekit", SOURCE)
homekit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = homekit
spec.loader.exec_module(homekit)
from pyhap.accessory_driver import AccessoryDriver


def fresh_state(now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    return {
        "schema_version": 1,
        "status": "ok",
        "decode_verified": True,
        "temperature_c": 22.6,
        "humidity_percent": 53.0,
        "last_seen_at": stamp,
        "temperature_observed_at": stamp,
        "humidity_observed_at": stamp,
    }


class SnapshotTests(unittest.TestCase):
    def test_pincode_requires_a_configured_stable_value(self):
        with self.assertRaisesRegex(ValueError, "homekit_pincode_missing_or_invalid"):
            homekit.load_pincode({})
        self.assertEqual(homekit.load_pincode({"N1_HOMEKIT_PINCODE": "234-56-789"}), b"234-56-789")

    def write_state(self, directory, data, mode=0o600):
        path = Path(directory) / "state.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_fresh_verified_state_is_exportable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory, fresh_state())
            result = homekit.read_fresh_snapshot(path)
            self.assertIsNotNone(result)
            self.assertEqual(result["temperature_c"], 22.6)
            self.assertEqual(result["humidity_percent"], 53.0)
            self.assertTrue(homekit.snapshot_is_fresh(result))

    def test_stale_measurement_is_not_exported(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=homekit.STALE_AFTER_SECONDS + 1)).isoformat()
        data = fresh_state(now)
        data["last_seen_at"] = old
        data["temperature_observed_at"] = old
        data["humidity_observed_at"] = old
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory, data)
            self.assertIsNone(homekit.read_fresh_snapshot(path, now))

    def test_unverified_or_non_ok_state_is_not_exported(self):
        for change in ({"decode_verified": False}, {"status": "stale"}):
            data = fresh_state()
            data.update(change)
            with tempfile.TemporaryDirectory() as directory:
                path = self.write_state(directory, data)
                self.assertIsNone(homekit.read_fresh_snapshot(path))

    def test_invalid_ranges_and_nonfinite_values_are_rejected(self):
        for key, value in (("temperature_c", 101), ("humidity_percent", -1), ("humidity_percent", float("nan"))):
            data = fresh_state()
            data[key] = value
            with tempfile.TemporaryDirectory() as directory:
                path = self.write_state(directory, data)
                self.assertIsNone(homekit.read_fresh_snapshot(path))

    def test_insecure_file_permissions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory, fresh_state(), mode=0o644)
            self.assertIsNone(homekit.read_fresh_snapshot(path))

    def test_bridge_contains_two_stable_sensor_accessories(self):
        driver = AccessoryDriver(address="127.0.0.1", port=51826, persist_file="/tmp/not-written-by-this-test")
        bridge = homekit.build_bridge(driver)
        self.assertEqual(bridge.display_name, "N1 Mijia BLE Bridge")
        self.assertEqual([a.display_name for a in bridge.accessories.values()], ["N1 Mijia Temperature", "N1 Mijia Humidity"])
        services = [s.display_name for a in bridge.accessories.values() for s in a.services]
        self.assertIn("TemperatureSensor", services)
        self.assertIn("HumiditySensor", services)


if __name__ == "__main__":
    unittest.main(verbosity=2)
