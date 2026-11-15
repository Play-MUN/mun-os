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


class BoardSoundTests(unittest.TestCase):
    BOARD = ROOT / "os/mkosi/mkosi.profiles/q6a-dev/extra/usr/lib"

    def test_the_board_shell_opens_its_sound_device_once_the_route_is_set(self):
        # On the board the sound card appears before its route to HDMI is
        # set, and a device opened in between plays to nothing and fails. The
        # route's script says when it is set by writing a file; the shell
        # waits for the file its unit names. The two must name the same one.
        script = (self.BOARD / "mun/board-audio-route").read_text(encoding="utf-8")
        written = re.search(r"(?m)^ready=(\S+)$", script).group(1)
        self.assertEqual(script.count(': >"$ready"'), 2, "written where the route is found set and where it is set")
        drop_in = (self.BOARD / "systemd/system/mun-shell.service.d/70-board-audio.conf").read_text(encoding="utf-8")
        named = re.findall(r"(?m)^Environment=MUN_SHELL_SOUND_READY=(\S+)$", drop_in)
        self.assertEqual(named, [written], "the shell's unit names the file the script writes")
        # The file is only waited for while a device is expected, so the wait
        # is bounded by the same unit, and the script runs with the shell.
        self.assertRegex(drop_in, r"(?m)^Environment=MUN_SHELL_SOUND_WAIT=\d+$")
        self.assertRegex(drop_in, r"(?m)^Wants=mun-board-audio\.service$")
        main = source("src/main.cpp")
        self.assertIn('sounds.openAfter(qEnvironmentVariable("MUN_SHELL_SOUND_READY"));', main)
        # /run is the only place the shell's unit lets it see that the system
        # wrote something after boot; the file is read there, never written.
        self.assertTrue(written.startswith("/run/"))


class SettingsTests(unittest.TestCase):
    def test_a_failed_write_does_not_fail_every_write_after_it(self):
        # QSettings reports the first error it met for as long as the object
        # lives. Each write must be judged by its own outcome: an object that
        # has reported an error is replaced, on the same file, before the
        # next write, and the new one is made before the old one goes, so the
        # values not written yet (Qt keeps them per file) always have one.
        settings = source("src/shellsettings.cpp")
        sync = settings.split("void ShellSettings::sync()\n{", 1)[1].split("\n}\n", 1)[0]
        fresh = re.search(r"if \(m_file->status\(\) != QSettings::NoError\)\s+"
                          r"m_file = std::make_unique<QSettings>\(m_file->fileName\(\), QSettings::IniFormat\);", sync)
        self.assertIsNotNone(fresh, "an object that reported an error is replaced")
        self.assertLess(fresh.end(), sync.index("m_file->sync();"), "before the write it judges")
        self.assertLess(sync.index("m_file->sync();"), sync.index("m_file->status() == QSettings::AccessError"),
                        "and the write's outcome is read after it")
        self.assertIn("std::unique_ptr<QSettings> m_file;", source("src/shellsettings.h"))


class CommentTests(unittest.TestCase):
    def test_comments_speak_to_any_reader(self):
        # A comment explains the code to whoever reads it; who asked for a
        # thing, noted in passing, is not something a reader can use.
        for path in sorted([*(SHELL / "qml").glob("*.qml"), *(SHELL / "qml").glob("*.js"),
                            *(SHELL / "src").glob("*.cpp"), *(SHELL / "src").glob("*.h")]):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?i)\((?:the )?owner(?:'s)?[,)]", path.name)


class UnitTests(unittest.TestCase):
    def test_the_shell_has_a_memory_ceiling_far_above_what_it_may_hold(self):
        # Without one, a fault that keeps allocating takes the console's
        # memory until the kernel picks a process to kill among them all.
        unit = source("deploy/mun-shell.service")
        ceiling = re.search(r"(?m)^MemoryMax=(\d+)([MG])$", unit)
        self.assertIsNotNone(ceiling, "the unit bounds the shell's memory")
        ceiling_mib = int(ceiling.group(1)) * (1024 if ceiling.group(2) == "G" else 1)
        # What the shell holds by itself (measured, at most about 250 MiB)
        # and what a Game Card may add, as the sources bound it: a world's
        # budget at the largest mode offered, and a motion's decoded images.
        world = re.search(r"if \(pixels <= 2560LL \* 1440\)\s+return (\d+) \* kMiB;", source("src/shapeworld.cpp"))
        motion = re.search(r"kMotionDecodedMaxBytes = (\d+) \* 1024 \* 1024;", source("src/motionscene.cpp"))
        healthy = 250 + int(world.group(1)) + int(motion.group(1))
        self.assertGreaterEqual(ceiling_mib, 3 * healthy, "a healthy shell never comes near it")
        self.assertLessEqual(ceiling_mib, 2048, "and it is reached long before the console's memory is gone")
        # Reaching it ends the shell as any failure does: started again, the
        # decoding markers kept. Nothing may slow it down instead: a ceiling
        # that only throttles would leave the interface on screen, frozen.
        self.assertNotRegex(unit, r"(?m)^MemoryHigh=")
        self.assertRegex(unit, r"(?m)^OOMPolicy=stop$")
        self.assertRegex(unit, r"(?m)^Restart=on-failure$")
        self.assertRegex(unit, r'(?m)^ExecStopPost=.*"\$\$SERVICE_RESULT" = success')


if __name__ == "__main__":
    unittest.main()
