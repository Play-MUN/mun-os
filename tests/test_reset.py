"""mun-reset (services/mun-reset): the console reset's plan against its README,
the reset carried out on a copy of a console's files, cut short and finished,
links not followed, a Game Card's files untouched, the mark written whole, and
the refusals against stand-ins for the card service and the launcher."""

import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "mun-reset"
sys.path.insert(0, str(SERVICE))

import reset  # noqa: E402


def console(root: Path) -> None:
    """A console's files as a reset finds them, with what it must keep."""
    files = {
        "var/lib/mun-shell/settings.ini": "[interface]\nlanguage=es\n[display]\nsafe-area=94\n",
        "var/lib/mun-shell/settings.ini.Ab12Cd": "[interface]\nlanguage=en\n",
        "var/lib/mun-shell/settings.ini.lock": "",
        "var/cache/mun-shell/fontconfig/aa-le64.cache-9": "cache",
        "var/cache/mun-shell/fontconfig/CACHEDIR.TAG": "tag",
        "var/log/journal/0123456789abcdef/system.journal": "history",
        "var/log/journal/0123456789abcdef/system@1.journal~": "older history",
        "var/log/wtmp": "boots",
        "var/log/btmp": "failed logins",
        "var/log/lastlog": "last logins",
        # Kept:
        "etc/machine-id": "0123456789abcdef\n",
        "etc/localtime": "zone",
        "var/lib/systemd/random-seed": "seed",
        "var/lib/systemd/timers/stamp-fstrim.timer": "",
        "var/log/apt/history.log": "image build",
        "var/lib/dpkg/status": "packages",
        "opt/mun/shell/bin/mun-shell": "binary",
        "run/mun/card/content/game": "a game",
        "run/mun/card/saves/save.json": "{\"collected\": 3}",
        "media/card/saves/save.json.prev": "{\"collected\": 2}",
    }
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def hashes(root: Path) -> dict:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


EXPECTED_GONE = {
    "var/lib/mun-shell/settings.ini", "var/lib/mun-shell/settings.ini.Ab12Cd", "var/lib/mun-shell/settings.ini.lock",
    "var/cache/mun-shell/fontconfig/aa-le64.cache-9", "var/cache/mun-shell/fontconfig/CACHEDIR.TAG",
    "var/log/journal/0123456789abcdef/system.journal", "var/log/journal/0123456789abcdef/system@1.journal~",
}
EXPECTED_EMPTIED = {"var/log/wtmp", "var/log/btmp", "var/log/lastlog"}


class PlanTest(unittest.TestCase):
    def test_the_plan_is_the_readmes_table(self):
        # Every row the README says the reset deletes or empties, and only
        # those, are in PLAN.
        table = (SERVICE / "README.md").read_text(encoding="utf-8")
        rows = [line for line in table.splitlines() if line.startswith("| `/")]
        named = set()
        for row in rows:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if cells[2].startswith(("deleted", "emptied")):
                named.update(re.findall(r"`/([^`]+)`", cells[0]))
        planned = {pattern.rstrip("/") for pattern, _ in reset.PLAN}
        self.assertEqual({name.rstrip("/") for name in named}, planned)

    def test_the_plan_stays_in_var_and_never_names_a_card(self):
        for pattern, how in reset.PLAN:
            self.assertTrue(pattern.startswith(("var/lib/mun-shell/", "var/cache/mun-shell", "var/log/")), pattern)
            self.assertNotIn("..", pattern)
            self.assertIn(how, ("file", "contents", "truncate"))
        self.assertEqual(reset.MARK, "var/lib/mun-reset/pending")


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        console(self.root)
        self.before = hashes(self.root)
        reset.write_mark(self.root)

    def tearDown(self):
        self.dir.cleanup()

    def check_reset(self):
        after = hashes(self.root)
        for name in EXPECTED_GONE:
            self.assertNotIn(name, after)
        for name in EXPECTED_EMPTIED:
            self.assertEqual((self.root / name).stat().st_size, 0, name)
        kept = {name: digest for name, digest in self.before.items() if name not in EXPECTED_GONE | EXPECTED_EMPTIED}
        self.assertEqual({name: after.get(name) for name in kept}, kept, "a file outside the plan changed")
        # The directories systemd made for the services stay.
        for directory in ("var/cache/mun-shell", "var/log/journal", "var/lib/mun-shell"):
            self.assertTrue((self.root / directory).is_dir(), directory)
        self.assertFalse(os.path.lexists(self.root / reset.MARK))

    def test_a_reset_deletes_the_plan_and_keeps_everything_else(self):
        self.assertTrue(reset.apply(self.root, log=lambda _: None))
        self.check_reset()

    def test_without_the_mark_nothing_happens(self):
        os.unlink(self.root / reset.MARK)
        self.assertFalse(reset.apply(self.root, log=lambda _: None))
        self.assertEqual(hashes(self.root), self.before)

    def test_a_reset_runs_again_without_harm(self):
        reset.apply(self.root, log=lambda _: None)
        reset.write_mark(self.root)
        reset.apply(self.root, log=lambda _: None)
        self.check_reset()

    def test_a_reset_cut_short_after_any_step_is_finished_by_the_next_boot(self):
        steps = len(reset.targets(self.root))
        self.assertGreater(steps, 5)
        for cut in range(steps + 1):
            with self.subTest(cut=cut):
                self.tearDown()
                self.setUp()
                calls = {"n": 0}
                real_remove, real_truncate = reset.remove, reset.truncate

                def counted(real):
                    def step(path):
                        if calls["n"] == cut:
                            raise OSError("power lost")
                        calls["n"] += 1
                        return real(path)
                    return step

                with mock.patch.object(reset, "remove", counted(real_remove)), \
                        mock.patch.object(reset, "truncate", counted(real_truncate)), \
                        mock.patch.object(reset, "fsync_directory", lambda path: None) as _:
                    try:
                        reset.apply(self.root, log=lambda _: None)
                        finished = True
                    except OSError:
                        finished = False
                if not finished:
                    # The mark is still there: the next boot finishes it.
                    self.assertTrue((self.root / reset.MARK).exists())
                    self.assertTrue(reset.apply(self.root, log=lambda _: None))
                self.check_reset()

    def test_links_are_removed_never_followed(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        precious = Path(outside.name) / "save.json"
        precious.write_text("a save")
        card = Path(outside.name) / "card"
        card.mkdir()
        (card / "game").write_text("a game")
        # A link where the settings file goes, inside the journal, and the
        # journal directory's entries pointing at a card.
        settings = self.root / "var/lib/mun-shell/settings.ini"
        settings.unlink()
        settings.symlink_to(precious)
        (self.root / "var/log/journal/card").symlink_to(card, target_is_directory=True)
        (self.root / "var/log/wtmp").unlink()
        (self.root / "var/log/wtmp").symlink_to(precious)
        reset.apply(self.root, log=lambda _: None)
        self.assertEqual(precious.read_text(), "a save")
        self.assertEqual((card / "game").read_text(), "a game")
        self.assertFalse(os.path.lexists(settings))
        self.assertFalse(os.path.lexists(self.root / "var/log/journal/card"))
        self.assertTrue(os.path.islink(self.root / "var/log/wtmp"), "a link is not truncated through")

    def test_a_directory_that_is_a_link_is_not_emptied(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "keep").write_text("keep")
        cache = self.root / "var/cache/mun-shell"
        for path in sorted(cache.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        cache.rmdir()
        cache.symlink_to(outside.name, target_is_directory=True)
        reset.apply(self.root, log=lambda _: None)
        self.assertEqual((Path(outside.name) / "keep").read_text(), "keep")


class LogTest(unittest.TestCase):
    def test_each_line_is_written_as_it_happens(self):
        self.assertIs(reset.apply.__defaults__[1], reset.say)
        with mock.patch("builtins.print") as printed:
            reset.say("mun-reset: deleted /var/log/wtmp")
        printed.assert_called_once_with("mun-reset: deleted /var/log/wtmp", flush=True)


class MarkTest(unittest.TestCase):
    def test_the_mark_is_written_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mark = reset.write_mark(root, now=0)
            self.assertEqual(json.loads(mark.read_text()), {"format": "mun-reset/1", "requested": "1970-01-01T00:00:00Z"})
            self.assertEqual(os.stat(mark.parent).st_mode & 0o777, 0o700)
            self.assertEqual(sorted(p.name for p in mark.parent.iterdir()), ["pending"], "no temporary file is left")


class Stand:
    """A service on a UNIX socket that sends one snapshot to each client."""

    def __init__(self, path: Path, message):
        self.path = path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.message = message
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            with client:
                if self.message is not None:
                    client.sendall((json.dumps(self.message) + "\n").encode())

    def close(self):
        self.server.close()


class RefusalTest(unittest.TestCase):
    def setUp(self):
        # Short: AF_UNIX paths are limited to about 100 bytes.
        self.dir = tempfile.TemporaryDirectory(dir="/tmp")
        self.stands = []

    def tearDown(self):
        for stand in self.stands:
            stand.close()
        self.dir.cleanup()

    def services(self, cards, state):
        n = len(self.stands)
        cardd, launchd = Path(self.dir.name) / f"c{n}.sock", Path(self.dir.name) / f"l{n}.sock"
        self.stands += [Stand(cardd, None if cards is None else {"type": "snapshot", "cards": cards}),
                        Stand(launchd, {"type": "snapshot", "state": state})]
        return str(cardd), str(launchd)

    def refused(self, cards, state, system="running"):
        cardd, launchd = self.services(cards, state)
        try:
            reset.refusal(cardd, launchd, system_state=system)
        except reset.Refused as refused:
            return refused.status
        return 0

    def test_an_empty_slot_and_an_idle_launcher_may_reset(self):
        self.assertEqual(self.refused([], "idle"), 0)

    def test_any_card_in_the_slot_refuses(self):
        for state in ("reading", "valid", "invalid", "released", "waiting"):
            with self.subTest(state=state):
                self.assertEqual(self.refused([{"slot": "c1", "state": state}], "idle"), reset.EXIT_CARD)

    def test_a_game_running_or_starting_refuses(self):
        for state in ("preparing", "starting", "running", "saving", "stopping"):
            with self.subTest(state=state):
                self.assertEqual(self.refused([], state), reset.EXIT_GAME)

    def test_shutting_down_refuses(self):
        self.assertEqual(self.refused([], "idle", system="stopping"), reset.EXIT_STOPPING)

    def test_a_service_that_does_not_answer_refuses(self):
        self.assertEqual(self.refused(None, "idle"), reset.EXIT_NO_ANSWER)
        missing = str(Path(self.dir.name) / "none.sock")
        with self.assertRaises(reset.Refused) as caught:
            reset.refusal(missing, missing, system_state="running")
        self.assertEqual(caught.exception.status, reset.EXIT_NO_ANSWER)

    def test_a_refused_request_starts_nothing(self):
        with mock.patch.object(reset, "refusal", side_effect=reset.Refused(reset.EXIT_CARD, "a card")), \
                mock.patch.object(reset.subprocess, "run") as run:
            self.assertEqual(reset.request(), reset.EXIT_CARD)
            run.assert_not_called()

    def test_an_accepted_request_starts_the_commit_unit_and_waits(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(reset, "refusal"), mock.patch.object(reset.subprocess, "run", return_value=done) as run:
            self.assertEqual(reset.request(), 0)
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0], ["systemctl", "start", "mun-reset-commit.service"])
        failed = mock.Mock(returncode=1, stderr="Job failed")
        with mock.patch.object(reset, "refusal"), mock.patch.object(reset.subprocess, "run", return_value=failed):
            self.assertEqual(reset.request(), 1)

    def test_commit_marks_and_restarts(self):
        done = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(reset.subprocess, "run", return_value=done) as run:
            self.assertEqual(reset.commit(Path(directory)), 0)
            self.assertTrue((Path(directory) / reset.MARK).is_file())
            self.assertEqual(run.call_args[0][0], ["systemctl", "reboot"])

    def test_a_restart_that_cannot_be_asked_for_takes_the_mark_back(self):
        failed = mock.Mock(returncode=1, stderr="no systemd")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(reset.subprocess, "run", return_value=failed):
            self.assertEqual(reset.commit(Path(directory)), 1)
            self.assertFalse(os.path.lexists(Path(directory) / reset.MARK))

    def test_a_restart_that_raises_takes_the_mark_back_too(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(reset.subprocess, "run", side_effect=OSError(12, "Cannot allocate memory")):
            self.assertEqual(reset.commit(Path(directory)), 1)
            self.assertFalse(os.path.lexists(Path(directory) / reset.MARK))

    def test_a_failure_is_one_line_never_a_traceback(self):
        with mock.patch.object(reset.os, "geteuid", return_value=0), \
                mock.patch.object(reset, "commit", side_effect=OSError(30, "Read-only file system")), \
                mock.patch("sys.stderr") as stderr:
            self.assertEqual(reset.main(["commit"]), 1)
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual(written.count("\n"), 1)
        self.assertIn("mun-reset: commit failed:", written)


class DeployTest(unittest.TestCase):
    def test_the_unit_runs_before_the_journal_and_the_console(self):
        unit = (SERVICE / "deploy" / "mun-reset.service").read_text()
        self.assertIn("ConditionPathExists=/var/lib/mun-reset/pending", unit)
        self.assertIn("DefaultDependencies=no", unit)
        for before in ("systemd-journal-flush.service", "systemd-update-utmp.service", "mun-cardd.service",
                       "mun-launchd.service", "mun-shell.service"):
            self.assertRegex(unit, rf"(?m)^Before=.*\b{re.escape(before)}\b")
        self.assertIn("ExecStart=/usr/local/libexec/mun-reset apply", unit)

    def test_the_commit_unit_runs_outside_the_shell_and_survives_its_restart(self):
        unit = (SERVICE / "deploy" / "mun-reset-commit.service").read_text()
        self.assertIn("ExecStart=/usr/local/libexec/mun-reset commit", unit)
        self.assertRegex(unit, r"(?m)^DefaultDependencies=no$")
        self.assertNotRegex(unit, r"(?m)^\[Install\]", "nothing starts it but a request")
        self.assertEqual(reset.COMMIT_UNIT, "mun-reset-commit.service")

    def test_the_shell_may_ask_for_a_request_and_nothing_else(self):
        rule = (ROOT / "services" / "mun-shell" / "deploy" / "mun-shell.sudoers").read_text()
        commands = [line for line in rule.splitlines() if "mun-reset" in line]
        self.assertTrue(commands)
        self.assertIn("/usr/local/libexec/mun-reset request", rule)
        self.assertNotIn("mun-reset apply", rule)
        self.assertNotIn("mun-reset commit", rule)
        self.assertNotRegex(rule, r"mun-reset\s*(,|$)", "the rule names the request, not the helper alone")


if __name__ == "__main__":
    unittest.main()
