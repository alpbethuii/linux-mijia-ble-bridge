import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".service", ".example", ".txt", ".gitignore"}
MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")


class PublicSafetyTests(unittest.TestCase):
    def test_no_concrete_mac_addresses_in_public_text(self):
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(MAC_RE.search(text), f"Concrete MAC address found in {path.relative_to(ROOT)}")

    def test_no_unrecognized_32_hex_secrets(self):
        key_re = re.compile(r"(?i)\b[0-9a-f]{32}\b")
        known_public_uuid = "0000fe9500001000800000805f9b34fb"
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for match in key_re.findall(path.read_text(encoding="utf-8")):
                self.assertEqual(known_public_uuid, match.lower(), f"Unexpected 32-hex value in {path.relative_to(ROOT)}")

    def test_bindkey_and_local_env_are_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignore)
        self.assertIn("bindkey", ignore)
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("MIJIA_SENSOR_MAC=XX:XX:XX:XX:XX:XX", example)
        self.assertIn("MIJIA_BINDKEY_PATH=", example)

    def test_receiver_requires_configured_mac_and_local_key(self):
        source = (ROOT / "src" / "mijia_ble_receiver.py").read_text(encoding="utf-8")
        self.assertIn("MIJIA_SENSOR_MAC", source)
        self.assertIn("MIJIA_BINDKEY_PATH", source)
        self.assertIn("local_config_permissions_invalid", source)


if __name__ == "__main__":
    unittest.main()
