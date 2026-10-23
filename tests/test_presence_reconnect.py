import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

# Construct a synthetic test address without embedding a real device address.
os.environ["PHONE_BT_ADDRESS"] = ":".join(f"{i:02X}" for i in (2, 0, 0, 0, 0, 1))
os.environ["PHONE_PRESENCE_STATE_DIR"] = "/tmp/test-mijia-presence"
SOURCE = Path(__file__).resolve().parents[1] / "src" / "phone_presence_observer.py"
spec = importlib.util.spec_from_file_location("presence_under_test", SOURCE)
presence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presence)


class ReconnectTests(unittest.TestCase):
    @patch.object(presence.subprocess, "run")
    def test_uses_targeted_bluez_dbus_call_not_bluetoothctl(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        accepted, category = presence.request_avrcp_connection()
        self.assertEqual((True, "request_accepted"), (accepted, category))
        command = run.call_args.args[0]
        self.assertEqual("busctl", command[0])
        self.assertIn("ConnectProfile", command)
        self.assertNotIn("bluetoothctl", command[0])
        self.assertIn(presence.UUID, command)

    def test_exponential_backoff_is_bounded(self):
        self.assertEqual([5, 10, 20, 40, 60, 60], [
            presence.retry_delay_seconds(n) for n in range(1, 7)
        ])

    def test_error_classification_is_sanitized(self):
        self.assertEqual("remote_unreachable", presence.classify_connect_error(
            "Call failed: org.bluez.Error.Failed br-connection-page-timeout"))
        self.assertEqual("bluez_not_ready", presence.classify_connect_error(
            "Call failed: org.bluez.Error.NotReady"))
        self.assertEqual("dbus_error", presence.classify_connect_error(
            "Call failed: some-unrecognized-detail"))

    @patch.object(presence.subprocess, "run")
    def test_unreachable_peer_error_is_classified(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 1, "", "Call failed: org.bluez.Error.Failed br-connection-page-timeout"
        )
        self.assertEqual((False, "remote_unreachable"), presence.request_avrcp_connection())

    @patch.object(presence.subprocess, "run", side_effect=subprocess.TimeoutExpired("busctl", 10))
    def test_timeout_is_classified_without_raw_error(self, run):
        self.assertEqual((False, "timeout"), presence.request_avrcp_connection())


if __name__ == "__main__":
    unittest.main()
