#!/usr/bin/env python3
"""The host tests are safe to run from a Git hook.

The repository's pre-commit hook runs `make check`, and Git runs hooks with
the repository it is committing in the environment (GIT_DIR, GIT_INDEX_FILE,
...). A test that makes a repository of its own with `git -C DIR init` would
otherwise reinitialise the repository being committed (as a bare one) and
commit its fixtures there. Here the tests that make repositories run with
that environment pointing at a separate repository, which must come out
unchanged.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


def clean_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=s", "-c", "user.email=s@example.invalid", *args],
                          check=True, capture_output=True, text=True, env=clean_environment()).stdout


@unittest.skipUnless(GIT, "needs git")
class HookEnvironmentTests(unittest.TestCase):
    def test_the_tests_that_make_repositories_leave_the_committing_one_alone(self):
        with tempfile.TemporaryDirectory(prefix="mun-hook-") as tmp:
            sentinel = Path(tmp) / "sentinel"
            sentinel.mkdir()
            git(sentinel, "init", "-q")
            (sentinel / "kept.txt").write_text("kept\n")
            git(sentinel, "add", "kept.txt")
            git(sentinel, "commit", "-q", "-m", "sentinel")

            def state():
                return ((sentinel / ".git" / "config").read_text(), git(sentinel, "rev-parse", "HEAD"),
                        git(sentinel, "for-each-ref"), git(sentinel, "ls-files", "--stage"))

            before = state()
            environment = clean_environment()
            environment.update(GIT_DIR=str(sentinel / ".git"), GIT_INDEX_FILE=str(sentinel / ".git" / "index"))
            run = subprocess.run([sys.executable, "-m", "unittest", "tests.test_mundev.SourceTests", "tests.test_recipes"],
                                 cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            self.assertEqual(state(), before)


if __name__ == "__main__":
    unittest.main()
