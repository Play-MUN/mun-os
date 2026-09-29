"""The game-unit cleanup helper (deploy/mun-launch-cleanup), run for real
with a fake `systemctl` on PATH and a temporary launch root."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "services" / "mun-launchd" / "deploy" / "mun-launch-cleanup"

FAKE_SYSTEMCTL = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1" in
    is-active) [ "$FAKE_LAUNCHD" = active ] && exit 0 || exit 3 ;;
    list-units) [ -n "$FAKE_GAMES" ] && echo "$FAKE_GAMES loaded active running Game session"; exit 0 ;;
    start) exit "${FAKE_START_STATUS:-0}" ;;
esac
exit 0
"""


class CleanupHelperTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.root = base / "launch"
        self.root.mkdir()
        bin_dir = base / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = base / "systemctl.log"
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", MUN_LAUNCH_ROOT=str(self.root),
                        FAKE_LOG=str(self.log), FAKE_LAUNCHD="inactive", FAKE_GAMES="")

    def session(self, sid, directory_saves=True):
        dest = self.root / sid
        (dest / "work" / ".Sample" / "save").mkdir(parents=True)
        (dest / "work" / ".Sample" / "save" / "save-0000.sav").write_bytes(b"slot")
        (dest / "session.json").write_text('{"card_id": "sample.lab"}')
        (dest / "game").write_bytes(b"\x7fELF copy")
        if directory_saves:
            (dest / "sync").mkdir()
        return dest

    def run_helper(self, arg, **env):
        result = subprocess.run(["sh", str(HELPER), arg], env=dict(self.env, **env), capture_output=True, text=True)
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result.returncode, calls

    def started_shell(self, calls):
        return any(c.startswith("start") and "mun-shell.service" in c for c in calls)

    def test_only_the_copy_goes_and_the_session_record_stays(self):
        for directory_saves in (True, False):
            dest = self.session(f"s{int(directory_saves)}", directory_saves)
            code, _ = self.run_helper(dest.name)
            self.assertEqual(code, 0)
            self.assertFalse((dest / "game").exists(), "the staged copy is removed")
            self.assertTrue((dest / "session.json").exists(), "the record the next launcher reports from stays")
            self.assertEqual((dest / "work" / ".Sample" / "save" / "save-0000.sav").read_bytes(), b"slot")

    def test_with_a_launcher_running_the_shell_is_left_to_it(self):
        dest = self.session("slive")
        code, calls = self.run_helper(dest.name, FAKE_LAUNCHD="active")
        self.assertEqual(code, 0)
        self.assertFalse(self.started_shell(calls), "the launcher restores it after the session's last save")

    def test_without_a_launcher_the_shell_comes_back(self):
        dest = self.session("sgone")
        code, calls = self.run_helper(dest.name)
        self.assertEqual(code, 0)
        self.assertTrue(self.started_shell(calls))

    def test_another_running_game_keeps_the_shell_down(self):
        dest = self.session("sone")
        code, calls = self.run_helper(dest.name, FAKE_GAMES="mun-game-sother.service")
        self.assertEqual(code, 0)
        self.assertFalse(self.started_shell(calls))

    def test_a_failed_removal_still_brings_the_shell_back_and_is_reported(self):
        dest = self.session("sstuck")
        (dest / "game").unlink()
        (dest / "game").mkdir()          # rm -f cannot remove a directory
        code, calls = self.run_helper(dest.name)
        self.assertEqual(code, 1, "the unit ends failed: the failure stays visible")
        self.assertTrue(self.started_shell(calls))

    def test_a_bad_session_id_touches_nothing(self):
        dest = self.session("skeep")
        for bad in ("", "../skeep", ".hidden", "a/b"):
            code, calls = self.run_helper(bad)
            self.assertEqual(code, 64, bad)
            self.assertFalse(self.started_shell(calls))
        self.assertTrue((dest / "game").exists())

    def test_restore_only_starts_the_shell_when_no_game_runs(self):
        code, calls = self.run_helper("--restore-only", FAKE_LAUNCHD="active")
        self.assertEqual(code, 0)
        self.assertTrue(self.started_shell(calls), "the launcher failed: that is why the restore unit runs")


if __name__ == "__main__":
    unittest.main()
