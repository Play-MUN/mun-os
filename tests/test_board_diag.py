"""The q6a-dev profile's diagnostic dump (extra/usr/lib/mun/board-diag), run
for real under each POSIX shell found: a temporary destination and state
directory (BOARD_DIAG_DIR, BOARD_DIAG_STATE), and stand-ins for journalctl,
systemctl and lsmod first in PATH. Linux only: the script reads /proc."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "os/mkosi/mkosi.profiles/q6a-dev/extra/usr/lib/mun/board-diag"
SHELLS = [s for s in ("dash", "bash", "sh") if shutil.which(s)]

# Default stand-ins: a short journal, a running shell, no modules.
JOURNALCTL = """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo "mun-shell: display: card0 HDMI-A-1 (msm_dpu)" ;;
    *) echo "kernel: Linux version test"; echo "systemd: Started." ;;
esac
"""
SYSTEMCTL = """#!/bin/sh
case "$1" in
    show) printf 'ActiveState=active\\nSubState=running\\n' ;;
    *) ;;
esac
"""
LSMOD = "#!/bin/sh\necho 'Module Size Used by'\n"


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/sys/kernel/random/boot_id").exists(),
                     "board-diag reads /proc: run on Linux (a Debian container on other hosts)")
class BoardDiagTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="board-diag-test-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.tools = self.work / "tools"
        self.tools.mkdir()
        self.tool("journalctl", JOURNALCTL)
        self.tool("systemctl", SYSTEMCTL)
        self.tool("lsmod", LSMOD)

    def tool(self, name, text):
        path = self.tools / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_diag(self, shell, mode="boot", kms=None):
        dest = self.work / f"dest-{shell}"
        state = self.work / f"state-{shell}"
        dest.mkdir(exist_ok=True)
        env = dict(os.environ, PATH=f"{self.tools}:{os.environ['PATH']}", BOARD_DIAG_DIR=str(dest),
                   BOARD_DIAG_STATE=str(state), BOARD_DIAG_KMS=str(kms or self.work / "no-kms.json"),
                   TMPDIR=str(self.work))
        result = subprocess.run([shell, str(SCRIPT), mode], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = sorted(dest.glob(f"boot-*/{mode}"))
        self.assertEqual(len(runs), 1, f"one finished run: {list(dest.iterdir())}")
        self.assertEqual(list(dest.glob(".partial-*")), [], "no partial run is left")
        lines = (runs[0] / "STATUS.txt").read_text().splitlines()
        status = {line.split("\t")[0]: line.split("\t")[1:] for line in lines}
        return runs[0], status, result.stdout

    def each_shell(self):
        self.assertTrue(SHELLS)
        for shell in SHELLS:
            with self.subTest(shell=shell):
                for entry in self.work.glob("dest-*"):
                    shutil.rmtree(entry)
                yield shell

    def test_a_good_run_is_complete_and_kms_not_written_yet_is_absent(self):
        for shell in self.each_shell():
            run, status, out = self.run_diag(shell)
            self.assertEqual(status["summary.txt"][0], "ok")
            self.assertEqual(status["shell.txt"][0], "ok")
            self.assertEqual(status["kms.json"][0], "absent")
            self.assertFalse((run / "kms.json").exists(), "no file stands for an absent source")
            self.assertEqual(status["journal.txt"][0], "ok")
            self.assertEqual(status["run"][0], "complete")
            self.assertIn("complete: every file read and written", out)

    def test_a_kms_configuration_that_exists_is_copied_as_it_is(self):
        kms = self.work / "kms.json"
        kms.write_text('{"device": "/dev/dri/card0"}\n')
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell, kms=kms)
            self.assertEqual(status["kms.json"][0], "ok")
            self.assertEqual((run / "kms.json").read_text(), kms.read_text())

    def test_a_source_that_cannot_be_read_is_failed_and_its_error_stays_out_of_the_file(self):
        unreadable = self.work / "kms-dir"
        unreadable.mkdir()
        for shell in self.each_shell():
            run, status, out = self.run_diag(shell, kms=unreadable)
            self.assertEqual(status["kms.json"][0], "failed")
            self.assertIn("could not be read (exit", status["kms.json"][1])
            self.assertEqual((run / "kms.json").read_bytes(), b"", "the error text is not taken for JSON")
            self.assertEqual(status["run"][0], "incomplete")
            self.assertIn("kms.json", status["run"][1])
            self.assertIn("incomplete", out)

    def test_a_command_that_fails_after_some_output_is_failed_with_its_output_kept(self):
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo partial; echo "journal broken" >&2; exit 42 ;;
    *) echo "kernel: Linux version test" ;;
esac
""")
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell)
            self.assertEqual(status["shell.txt"][0], "failed")
            self.assertEqual(status["shell.txt"][1], "could not be read (exit 42): journal broken")
            self.assertEqual((run / "shell.txt").read_text(), "partial\n")
            self.assertEqual(status["run"][0], "incomplete")

    def test_the_end_of_the_journal_keeps_journalctls_failure_not_tails(self):
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo "shell line" ;;
    *) echo "some of the journal"; echo "cannot open the journal" >&2; exit 5 ;;
esac
""")
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell, mode="shutdown")
            self.assertEqual(status["journal.txt"][0], "failed")
            self.assertIn("could not be read (exit 5)", status["journal.txt"][1])
            self.assertEqual(status["run"][0], "incomplete")

    def test_a_journal_past_the_budget_is_cut_and_the_run_still_complete(self):
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo "shell line" ;;
    *) awk 'BEGIN { for (i = 0; i < 40000; ++i) printf "journal line %06d %080d\\n", i, 0 }' ;;
esac
""")
        for mode in ("boot", "shutdown"):
            for shell in self.each_shell():
                with self.subTest(mode=mode):
                    run, status, _ = self.run_diag(shell, mode=mode)
                    self.assertEqual(status["journal.txt"][0], "truncated")
                    self.assertEqual(status["run"][0], "complete")
                    self.assertIn("cut at its limit: journal.txt", status["run"][1])
                    total = sum(f.stat().st_size for f in run.iterdir())
                    self.assertLess(total, 2 * 1024 * 1024 + 4096, "a run stays within its budget")
                    text = (run / "journal.txt").read_text()
                    self.assertTrue(text.startswith("journal line 000000") if mode == "boot"
                                    else text.rstrip().endswith("journal line 039999 " + "0" * 80),
                                    "the beginning at boot, the end at shutdown")

    def test_a_failed_read_stays_failed_when_its_output_is_also_cut(self):
        # At shutdown journalctl has finished, with its own error, before the
        # end is taken: a long journal that failed to be read is not a cut.
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo "shell line" ;;
    *) awk 'BEGIN { for (i = 0; i < 40000; ++i) printf "journal line %06d %080d\\n", i, 0 }'
       echo "journal corrupted" >&2; exit 5 ;;
esac
""")
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell, mode="shutdown")
            self.assertEqual(status["journal.txt"][0], "failed")
            self.assertIn("could not be read (exit 5): journal corrupted", status["journal.txt"][1])
            self.assertIn("output went past", status["journal.txt"][1], "the cut is said too")
            self.assertEqual(status["run"][0], "incomplete")
            self.assertIn("failed or skipped: journal.txt", status["run"][1])
            self.assertIn("cut at its limit: journal.txt", status["run"][1])

    def test_a_command_stopped_by_the_cut_is_only_cut(self):
        # More than a pipe holds past the limit: the command is still writing
        # when the writer stops, and ends on SIGPIPE. That is the cut itself.
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) awk 'BEGIN { for (i = 0; i < 4000; ++i) printf "shell line %06d %080d\\n", i, 0 }' ;;
    *) echo "kernel: Linux version test" ;;
esac
""")
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell)
            self.assertEqual(status["shell.txt"][0], "truncated")
            self.assertEqual(status["run"][0], "complete")
            self.assertIn("cut at its limit: shell.txt", status["run"][1])

    def test_a_write_that_stops_part_way_is_failed(self):
        # Writes to regular files stop at 512 bytes for the journal's writer
        # alone (the only limit above 300 000 bytes).
        self.tool("head", """#!/bin/sh
[ "$1" = -c ] && [ "$2" -gt 300000 ] && ulimit -f 1
exec /usr/bin/head "$@"
""")
        self.tool("journalctl", """#!/bin/sh
case "$*" in
    *"-u mun-shell"*) echo "shell line" ;;
    *) awk 'BEGIN { for (i = 0; i < 100; ++i) printf "journal line %06d %080d\\n", i, 0 }' ;;
esac
""")
        for shell in self.each_shell():
            run, status, _ = self.run_diag(shell)
            self.assertEqual(status["journal.txt"][0], "failed")
            self.assertIn("not written in full", status["journal.txt"][1])
            self.assertLess((run / "journal.txt").stat().st_size, 100 * 101)
            self.assertEqual(status["run"][0], "incomplete")


if __name__ == "__main__":
    unittest.main()
