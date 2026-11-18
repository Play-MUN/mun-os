#!/usr/bin/env python3
"""The host tests are safe to run from a Git hook.

The repository's pre-commit hook runs `make check`, and Git runs hooks with
the repository it is committing in the environment (GIT_DIR, GIT_INDEX_FILE,
...). A test that makes a repository of its own with `git -C DIR init` would
otherwise reinitialise the repository being committed (as a bare one) and
commit its fixtures there. Here the tests that make repositories run with
that environment pointing at a separate repository, which must come out
unchanged.

Nor may they run what the contributor configured for their own work: global
or system configuration with hooks, templates, a file-system monitor or
signing would otherwise run in every fixture repository, also in the
processes the code under test starts. Here they run under such a
configuration, and none of it may run.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitenv  # noqa: E402


def clean_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(repo, environment, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=environment).stdout


@unittest.skipUnless(GIT, "needs git")
class HookEnvironmentTests(unittest.TestCase):
    def test_the_tests_that_make_repositories_leave_the_committing_one_alone(self):
        with tempfile.TemporaryDirectory(prefix="mun-hook-") as tmp:
            sentinel = Path(tmp) / "sentinel"
            sentinel.mkdir()
            own = gitenv.environment(Path(tmp))
            git(sentinel, own, "init", "-q")
            (sentinel / "kept.txt").write_text("kept\n")
            git(sentinel, own, "add", "kept.txt")
            git(sentinel, own, "commit", "-q", "-m", "sentinel")

            def state():
                hooks = sorted(path.name for path in (sentinel / ".git" / "hooks").iterdir()) \
                    if (sentinel / ".git" / "hooks").is_dir() else []
                return ((sentinel / ".git" / "config").read_text(), git(sentinel, own, "rev-parse", "HEAD"),
                        git(sentinel, own, "for-each-ref"), git(sentinel, own, "ls-files", "--stage"), hooks)

            before = state()
            environment = clean_environment()
            environment.update(GIT_DIR=str(sentinel / ".git"), GIT_INDEX_FILE=str(sentinel / ".git" / "index"))
            run = subprocess.run([sys.executable, "-m", "unittest", "tests.test_mundev.SourceTests", "tests.test_recipes",
                                  "tests.test_shape_export"],
                                 cwd=ROOT, env=environment, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            self.assertEqual(state(), before)

    @unittest.skipUnless(os.name == "posix", "the hostile hooks are POSIX scripts")
    def test_the_tests_that_make_repositories_run_none_of_the_contributors_git(self):
        with tempfile.TemporaryDirectory(prefix="mun-hook-") as tmp:
            hostile = Path(tmp) / "hostile"
            log = hostile / "ran.log"
            # Each program a contributor may configure appends its name to the
            # log: a hook, a template's hook, a file-system monitor, a signing
            # program. The hooks let Git go on, so that every later one is
            # reached; the signing program fails, as a signing key that is not
            # at hand does.
            programs = hostile / "bin"
            hooks = hostile / "hooks"
            template = hostile / "template" / "hooks"
            for directory in (programs, hooks, template):
                directory.mkdir(parents=True)
            def program(path, status=0):
                path.write_text(f'#!/bin/sh\necho "{path.name} $*" >> "{log}"\nexit {status}\n')
                path.chmod(0o755)
            for name in ("pre-commit", "commit-msg", "post-commit", "post-checkout", "reference-transaction"):
                program(hooks / name)
                program(template / name)
            program(programs / "gpg", status=1)
            program(programs / "fsmonitor")
            configuration = (f'[core]\n\thooksPath = "{hooks}"\n\tfsmonitor = "{programs / "fsmonitor"}"\n'
                             f'[init]\n\ttemplateDir = "{template.parent}"\n'
                             f'[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n'
                             f'[gpg]\n\tprogram = "{programs / "gpg"}"\n'
                             f'[user]\n\tname = Contributor\n\temail = contributor@example.invalid\n')
            home = hostile / "home"
            (home / ".config" / "git").mkdir(parents=True)
            (home / ".gitconfig").write_text(configuration)
            (home / ".config" / "git" / "config").write_text(configuration)
            (hostile / "system.gitconfig").write_text(configuration)
            # The machine's own configuration too: this host's `git`, first on
            # PATH, reads the hostile file as its system configuration
            # (GIT_CONFIG_SYSTEM set as it starts, where no caller's cleaning
            # of GIT_* variables reaches it), as /etc/gitconfig would be read.
            machine = hostile / "machine"
            machine.mkdir()
            (machine / "git").write_text(f'#!/bin/sh\nGIT_CONFIG_SYSTEM="{hostile / "system.gitconfig"}"\n'
                                          f'export GIT_CONFIG_SYSTEM\nexec "{GIT}" "$@"\n')
            (machine / "git").chmod(0o755)
            environment = clean_environment()
            environment.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                               GIT_CONFIG_SYSTEM=str(hostile / "system.gitconfig"),
                               PATH=f"{machine}{os.pathsep}{environment.get('PATH', '')}")
            run = subprocess.run([sys.executable, "-m", "unittest", "tests.test_mundev.SourceTests", "tests.test_recipes",
                                  "tests.test_shape_export"],
                                 cwd=ROOT, env=environment, capture_output=True, text=True)
            ran = sorted({line.split()[0] for line in log.read_text().splitlines()}) if log.exists() else []
            self.assertEqual(ran, [], "the contributor's Git ran in the tests' repositories")
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
