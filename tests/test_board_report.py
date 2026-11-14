"""The q6a-dev profile's boot report (extra/usr/lib/mun/board-report), run
for real with stand-ins for journalctl, systemctl, systemd-analyze and ip as
the only commands on PATH, a temporary state directory and boot id
(BOARD_REPORT_STATE, BOARD_REPORT_BOOT_ID). A query that fails, answers in
part, does not answer in time or is missing must leave its values unknown
and say so, never read as a healthy boot. Any POSIX host runs it."""

import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "os/mkosi/mkosi.profiles/q6a-dev/extra/usr/lib/mun/board-report"


def entry(seconds, message, kernel=False):
    fields = {"MESSAGE": message, "__MONOTONIC_TIMESTAMP": str(int(seconds * 1e6))}
    if kernel:
        fields["_SOURCE_MONOTONIC_TIMESTAMP"] = fields["__MONOTONIC_TIMESTAMP"]
    return json.dumps(fields)


KERNEL = [entry(5.2, "remoteproc remoteproc0: remote processor adsp is now up", True),
          entry(5.3, "qcom,apr x: Adding APR/GPR dev: gprsvc:service:2:1", True)]
AUDIO = [entry(10.9, "HDMI audio route set at 10.9 s")]
SHELL = [entry(5.6, "Started mun-shell.service - MUN Shell (console UI on tty1)."),
         entry(11.1, "mun-shell: the sound device came 4.5 s after the shell first looked for it"),
         entry(13.2, "qml: mun-shell: start-up: waited 4.30 s for the sound device: it had played for 2.03 s"),
         entry(13.2, "qml: mun-shell: start-up: the picture follows the sound")]
ANALYZE = ("Startup finished in 5.450s (firmware) + 999ms (loader) + 1.541s (kernel) + 2.004s (initrd)"
           " + 7.500s (userspace) = 17.495s\n")


def lines_out(entries):
    return "".join(f"echo '{e}'\n" for e in entries)


class BoardReportTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="board-report-test-"))
        self.tools = self.work / "tools"
        self.tools.mkdir()
        self.state = self.work / "state"
        (self.work / "boot_id").write_text("a8d21261-0b74-4da3-bafe-3df0cbacaf12\n")
        self.journal()
        self.tool("systemctl", "")
        self.tool("systemd-analyze", f"printf '%s' '{ANALYZE}'\n")
        self.tool("ip", "echo '2: end0    inet 192.168.2.5/24 brd 192.168.2.255 scope global end0'\n")

    def tearDown(self):
        shutil.rmtree(self.work, True)

    def tool(self, name, body):
        path = self.tools / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def journal(self, kernel=None, shell=None):
        """journalctl answering each selection; `kernel` or `shell` replace
        that selection's answer (a shell snippet)."""
        self.tool("journalctl", f"""case "$*" in
    *" -k"*) {kernel or lines_out(KERNEL)} ;;
    *"-u mun-board-audio.service"*) {lines_out(AUDIO)} ;;
    *"-u mun-shell.service"*) {shell or lines_out(SHELL)} ;;
esac
""")

    def record(self, timeout="10"):
        env = {"PATH": str(self.tools), "BOARD_REPORT_STATE": str(self.state),
               "BOARD_REPORT_BOOT_ID": str(self.work / "boot_id"), "BOARD_REPORT_TIMEOUT": timeout}
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "record"], env=env, capture_output=True,
                                text=True, timeout=60)
        kept = (self.state / "boots.jsonl").read_text().splitlines()
        return result, json.loads(kept[-1])

    def show(self):
        env = {"PATH": str(self.tools), "BOARD_REPORT_STATE": str(self.state)}
        return subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True, text=True,
                              timeout=60).stdout

    def test_a_boot_whose_queries_all_answer_is_complete(self):
        result, line = self.record()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(line["evidence"], "complete")
        self.assertNotIn("incomplete", line)
        self.assertEqual(line["failed"], [], "systemctl answered: no unit failed")
        self.assertEqual(line["address"], "192.168.2.5")
        self.assertEqual((line["firmware"], line["userspace"]), (5.45, 7.5))
        self.assertEqual((line["dsp_up"], line["audio_route"], line["startup_began"]), (5.2, 10.9, 13.2))
        self.assertEqual(line["startup_clock"], "sound")

    def test_systemctl_that_fails_leaves_the_failed_units_unknown(self):
        self.tool("systemctl", "echo 'Failed to connect to bus' >&2\nexit 1\n")
        result, line = self.record()
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(line["failed"], "not asked is not 'no unit failed'")
        self.assertEqual(line["evidence"], "incomplete")
        self.assertIn("exit 1: Failed to connect to bus", line["incomplete"]["failed units"])
        self.assertIn("failed units", result.stderr)

    def test_part_of_an_answer_from_a_command_that_failed_is_not_used(self):
        self.tool("systemctl", "echo 'mun-board-diag.service loaded failed failed'\nexit 1\n")
        self.journal(shell=lines_out(SHELL[:2]) + "exit 1")
        result, line = self.record()
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(line["failed"])
        self.assertNotIn("shell_started", line, "the shell's journal, cut short, is not read as its record")
        self.assertIn("journal mun-shell.service", line["incomplete"])

    def test_a_query_without_an_answer_in_time_is_incomplete(self):
        # Only the stand-ins are on PATH: sleep by its own path.
        self.journal(kernel=f"{shutil.which('sleep')} 5")
        result, line = self.record(timeout="1")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("dsp_up", line)
        self.assertIn("no answer within 1 s", line["incomplete"]["journal kernel"])
        self.assertEqual(line["audio_route"], 10.9, "the other journals still count")

    def test_a_missing_command_is_incomplete(self):
        (self.tools / "ip").unlink()
        result, line = self.record()
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(line["address"])
        self.assertEqual(line["incomplete"]["address"], "ip: not found")

    def test_the_table_tells_unknown_from_none(self):
        self.record()
        self.tool("systemctl", "exit 1\n")
        self.record()
        table = self.show().splitlines()
        self.assertTrue(table[1].endswith("  -"), table[1])
        self.assertIn("  ?  INCOMPLETE: failed units", table[2])


if __name__ == "__main__":
    unittest.main()
