#!/usr/bin/env python3
"""The shell job's qmllint gate (services/mun-shell/tests/ci/qmllint_check.py):
known warnings pass wherever they move in their file, a new one fails, and a
log with nothing recognised does not pass for a clean one."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qmllint_check", ROOT / "services/mun-shell/tests/ci/qmllint_check.py")
qmllint_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qmllint_check)

UNQUALIFIED = "Warning: /build/qml/ArcMenu.qml:{line}:9: Unqualified access [unqualified]"
MISSING = "Warning: /build/qml/OrbitalMenu.qml:40:17: Member \"currentEntry\" not found on type \"QQuickItem\" [missing-property]"


class QmllintCheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.baseline = self.dir / "baseline.txt"
        self.baseline.write_text("qml/ArcMenu.qml\tUnqualified access\tunqualified\n" * 2)

    def check(self, *lines, write=False):
        log = self.dir / "qmllint.log"
        log.write_text("".join(f"{line}\n" for line in lines))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = qmllint_check.main([str(log), str(self.baseline)] + (["--write"] if write else []))
        return code, out.getvalue() + err.getvalue()

    def test_the_known_warnings_pass_wherever_they_move_in_their_file(self):
        code, said = self.check("[1/2] Linting qml/ArcMenu.qml", UNQUALIFIED.format(line=10), UNQUALIFIED.format(line=88))
        self.assertEqual(code, 0, said)
        self.assertIn("2 warnings, 0 new, 0 gone", said)

    def test_a_new_warning_fails_and_is_named(self):
        code, said = self.check(UNQUALIFIED.format(line=10), UNQUALIFIED.format(line=11), MISSING)
        self.assertEqual(code, 1)
        self.assertIn("new qmllint warning: qml/OrbitalMenu.qml | Member \"currentEntry\"", said)

    def test_one_more_of_a_known_kind_in_the_same_file_fails(self):
        code, _ = self.check(*(UNQUALIFIED.format(line=line) for line in (10, 11, 12)))
        self.assertEqual(code, 1)

    def test_fewer_warnings_pass_and_are_listed_for_the_baseline_to_shrink(self):
        code, said = self.check(UNQUALIFIED.format(line=10))
        self.assertEqual(code, 0, said)
        self.assertIn("no longer reported", said)

    def test_a_log_with_nothing_recognised_does_not_pass_for_a_clean_one(self):
        for lines in ((), ("ninja: no work to do.",), ("Warning: a format this check does not know",)):
            with self.subTest(lines=lines):
                code, said = self.check(*lines)
                self.assertEqual(code, 1)
                self.assertIn("no warning recognised", said)

    def test_with_an_empty_baseline_an_empty_log_passes(self):
        self.baseline.write_text("")
        self.assertEqual(self.check()[0], 0)

    def test_missing_files_and_wrong_arguments_are_usage_errors(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            self.assertEqual(qmllint_check.main([str(self.dir / "absent.log"), str(self.baseline)]), 2)
            self.assertEqual(qmllint_check.main([str(self.baseline)]), 2)
            self.assertEqual(qmllint_check.main([str(self.baseline), str(self.baseline), "--force"]), 2)
        self.assertIn("not found", out.getvalue())

    def test_write_records_a_baseline_the_same_log_then_passes(self):
        lines = (UNQUALIFIED.format(line=3), MISSING)
        self.assertEqual(self.check(*lines, write=True)[0], 0)
        self.assertEqual(self.check(*lines)[0], 0)
        self.assertEqual(self.check(*lines, MISSING)[0], 1)


if __name__ == "__main__":
    unittest.main()
