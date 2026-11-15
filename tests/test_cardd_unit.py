"""mun-cardd's systemd unit: how the console keeps its card service."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unit_file(name: str) -> dict:
    """A unit file of deploy/ as {section: [(key, value), ...]}."""
    sections, current = {}, None
    for line in (ROOT / "services" / "mun-cardd" / "deploy" / name).read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
        elif line and not line.startswith("#") and current is not None:
            key, _, value = line.partition("=")
            current.append((key, value))
    return sections


class CardServiceUnitTests(unittest.TestCase):
    """Nothing else on the console reads a card, writes a save or releases a
    card safely: the unit of the card service may never stay stopped."""

    def test_the_card_service_is_restarted_however_often_and_however_it_ended(self):
        unit = unit_file("mun-cardd.service")
        service = dict(unit["Service"])
        self.assertEqual(service["Restart"], "always")
        self.assertIn(("StartLimitIntervalSec", "0"), unit["Unit"],
                      "no start limit: five quick failures must not leave the console without a card reader")
        self.assertNotIn("StartLimitIntervalSec", service, "systemd reads it in [Unit] only")
        self.assertNotIn("StartLimitBurst", dict(unit["Unit"]))
        self.assertGreaterEqual(float(service["RestartSec"]), 1, "a service that keeps failing must not spin")

    def test_it_is_still_the_same_service_started_with_the_console(self):
        unit = unit_file("mun-cardd.service")
        service = dict(unit["Service"])
        self.assertEqual(service["ExecStart"], "/usr/bin/python3 /opt/mun/cardd/cardd.py")
        self.assertEqual(service["Type"], "simple")
        self.assertIn(("WantedBy", "multi-user.target"), unit["Install"])
        self.assertIn(("Before", "mun-shell.service"), unit["Unit"])


if __name__ == "__main__":
    unittest.main()
