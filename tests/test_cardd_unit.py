"""mun-cardd's systemd unit: how the console keeps its card service."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))


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


MIB = 1024 * 1024


def size(value: str) -> int:
    """A systemd size: bytes, or with K, M or G."""
    factor = {"K": 1024, "M": MIB, "G": 1024 * MIB}.get(value[-1:], 1)
    return int(value[:-1] if factor != 1 else value) * factor


class CardServiceBoundsTests(unittest.TestCase):
    """The unit's memory and task bounds against what the service was
    measured to need (services/mun-cardd/README.md): in a QEMU image guest,
    170 MiB at most, for the largest valid directory save written over an
    earlier one while eight clients left 16 MiB unread; four tasks."""

    MEASURED_MEMORY = 170 * MIB
    MEASURED_TASKS = 4

    def test_no_valid_save_is_slowed_or_cut_by_the_bounds(self):
        service = dict(unit_file("mun-cardd.service")["Service"])
        high, top = size(service["MemoryHigh"]), size(service["MemoryMax"])
        self.assertGreaterEqual(high, self.MEASURED_MEMORY * 3 // 2, "half again above the measured peak")
        self.assertGreater(top, high, "MemoryMax ends a runaway service only past MemoryHigh")
        self.assertGreaterEqual(int(service["TasksMax"]), 8 * self.MEASURED_TASKS)

    def test_the_bounds_measured_with_are_the_service_s_own(self):
        # The peak grows with these: change one and measure the service again.
        import cardd
        from mun_card.validate import DIRECTORY_SAVES_MAX_BYTES
        self.assertEqual(DIRECTORY_SAVES_MAX_BYTES, 8 * MIB)
        self.assertEqual(cardd.OUTBOX_TOTAL_MAX_BYTES, 16 * MIB)
        self.assertEqual(cardd.MAX_CLIENTS, 8)
