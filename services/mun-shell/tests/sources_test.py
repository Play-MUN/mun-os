#!/usr/bin/env python3
"""Host-side checks of what the shell's sources, its unit and a board
profile's units promise each other, read as text: no build, no Qt, no
device. What they pin is behaviour that only a running console shows
(tests/behaviour.py and a guest check it); here a change that breaks the
promise fails `make check` (tests/Makefile here runs this file)."""
import re
import unittest
from pathlib import Path

SHELL = Path(__file__).resolve().parents[1]
ROOT = SHELL.parents[1]


def source(name: str) -> str:
    return (SHELL / name).read_text(encoding="utf-8")


class SoundDeviceTests(unittest.TestCase):
    def test_a_missing_device_is_explained_once_and_retried_quietly(self):
        # Awake, the worker tries a device that is not there every kRetry for
        # as long as the interface is shown. ALSA's library explains every
        # failed opening with several lines of its own: they belong to the
        # first failure, which the shell reports, and to no retry after it.
        player = source("src/systemsounds.cpp")
        opening = re.search(r"alsaQuiet = ([^;]+);\s+int error = snd_pcm_open\(", player)
        self.assertIsNotNone(opening, "ALSA's messages are decided at each opening")
        terms = {term.strip() for term in opening.group(1).split("||")}
        self.assertIn("expected(now)", terms, "quiet while a device is expected")
        self.assertIn("reported", terms, "and once the failure has been reported")
        # `reported` is taken back only when a device plays: a later failure
        # is a new one, explained again.
        cleared = [m.start() for m in re.finditer(r"(?<!bool )\breported = false;", player)]
        self.assertEqual(len(cleared), 1)
        plays = player.index("if (!advanceSaid && health.advancingSince() >= 0) {")
        self.assertTrue(plays < cleared[0] < player.index("deadOpens = 0;", plays), "only as the device plays")
        handler = player.split("void alsaMessage(", 1)[1].split("\n}\n", 1)[0]
        self.assertRegex(handler, r"if \(alsaQuiet\.load\([^)]*\)\)\s+return;", "a quiet opening prints nothing")
        self.assertIn("snd_lib_error_set_handler(alsaMessage);", player)


if __name__ == "__main__":
    unittest.main()
