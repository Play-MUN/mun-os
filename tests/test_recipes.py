"""Host tests of game recipes (os/builder/recipes.py, `./mun dev build
--recipe`): what a recipe must state, that sources are fetched at their
pinned commit and patches only when they match, and that the host refuses a
bad or oversized recipe before any builder starts. A local Git repository
stands in for the web; no builder or network is used."""

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "os" / "builder"))
sys.path.insert(0, str(ROOT / "vm"))
import recipes  # noqa: E402
import mundev  # noqa: E402

PATCH = """--- a/hello.c
+++ b/hello.c
@@ -1 +1 @@
-int main(void) { return 1; }
+int main(void) { return 0; }
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self):
        # Git runs hooks with the repository being committed in the environment
        # (GIT_DIR, GIT_INDEX_FILE, ...), and the pre-commit hook runs these tests:
        # the repositories they make must not inherit it, or `git -C DIR init` would
        # reinitialise that repository and the fixtures be committed into it
        # (tests/test_hook_safety.py).
        environment = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                              if not key.startswith("GIT_")}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        directory = tempfile.TemporaryDirectory(prefix="mun-recipes-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        upstream = self.root / "upstream"
        upstream.mkdir()
        git(upstream, "init", "-q")
        (upstream / "hello.c").write_text("int main(void) { return 1; }\n")
        git(upstream, "add", "hello.c")
        git(upstream, "commit", "-q", "-m", "first")
        self.commit = git(upstream, "rev-parse", "HEAD")
        self.upstream = upstream

    def recipe(self, directory_name="mygame", **changes):
        directory = self.root / "recipes" / directory_name
        directory.mkdir(parents=True)
        name = directory_name
        (directory / "fix.patch").write_text(PATCH)
        (directory / "build.sh").write_text("#!/bin/sh\ncp \"$RECIPE/source/hello.c\" \"$OUT/\"\n")
        data = {"format": "mun-recipe/1", "name": name, "license": "MIT (engine); data is never a build input",
                "sources": [{"path": "source", "git": self.upstream.as_uri(), "commit": self.commit}],
                "patches": [{"file": "fix.patch", "source": "source",
                             "sha256": hashlib.sha256(PATCH.encode()).hexdigest()}]}
        data.update(changes)
        (directory / "recipe.json").write_text(json.dumps(data))
        return directory


class LoadTests(Fixture):
    def test_a_complete_recipe_loads_and_web_sources_must_be_https(self):
        directory = self.recipe()
        self.assertEqual(recipes.load(directory, allow_local_sources=True)["name"], "mygame")
        with self.assertRaises(recipes.RecipeError, msg="file:// is for tests only"):
            recipes.load(directory)

    def test_what_a_recipe_must_state_is_refused_when_missing_or_wrong(self):
        good_patch = {"file": "fix.patch", "source": "source", "sha256": hashlib.sha256(PATCH.encode()).hexdigest()}
        cases = {
            "format": {"format": "mun-recipe/2"},
            "reserved name": {"name": "mun-collect"},
            "name": {"name": "My Game"},
            "licence": {"license": " "},
            "short commit": {"sources": [{"path": "source", "git": self.upstream.as_uri(), "commit": "abc123"}]},
            "hidden path": {"sources": [{"path": ".git", "git": self.upstream.as_uri(), "commit": self.commit}]},
            "path traversal": {"sources": [{"path": "../out", "git": self.upstream.as_uri(), "commit": self.commit}]},
            "patch digest": {"patches": [dict(good_patch, sha256="0" * 64)]},
            "patch target": {"patches": [dict(good_patch, source="elsewhere")]},
            "patch outside": {"patches": [dict(good_patch, file="../fix.patch")]},
        }
        for index, (label, change) in enumerate(cases.items()):
            with self.subTest(label):
                directory = self.recipe(f"game{index:02d}", **change)
                with self.assertRaises(recipes.RecipeError):
                    recipes.load(directory, allow_local_sources=True)
        without_build = self.recipe("nobuild")
        (without_build / "build.sh").unlink()
        with self.assertRaises(recipes.RecipeError):
            recipes.load(without_build, allow_local_sources=True)


class FetchTests(Fixture):
    def test_sources_come_at_their_commit_patched_and_the_record_names_them(self):
        self.recipe()
        work = self.root / "work"
        records = recipes.fetch(self.root / "recipes", work, allow_local_sources=True)
        self.assertEqual((work / "mygame" / "source" / "hello.c").read_text(), "int main(void) { return 0; }\n")
        self.assertFalse((work / "mygame" / "source" / ".git").exists(), "no repository history into the build")
        self.assertEqual(records[0]["sources"][0]["commit"], self.commit)
        self.assertEqual(json.loads((work / "recipes.json").read_text()), records)
        self.assertRegex(records[0]["recipe_sha256"], r"^[0-9a-f]{64}$")

    def test_a_directory_not_named_after_its_recipe_or_a_missing_commit_stops_the_build(self):
        directory = self.recipe()
        directory.rename(directory.with_name("other"))
        with self.assertRaises(recipes.RecipeError):
            recipes.fetch(self.root / "recipes", self.root / "work", allow_local_sources=True)
        (self.root / "recipes" / "other").rename(self.root / "recipes" / "mygame")
        data = json.loads((self.root / "recipes" / "mygame" / "recipe.json").read_text())
        data["sources"][0]["commit"] = "f" * 40
        (self.root / "recipes" / "mygame" / "recipe.json").write_text(json.dumps(data))
        with self.assertRaises(subprocess.CalledProcessError):
            recipes.fetch(self.root / "recipes", self.root / "work2", allow_local_sources=True)


class HostTests(Fixture):
    def build_args(self, *recipe_dirs):
        return argparse.Namespace(profile="qemu-dev", name="b1", allow_dirty=True, recipe=[str(d) for d in recipe_dirs],
                                  keep_builder=False)

    def test_a_bad_or_oversized_recipe_is_refused_before_any_builder(self):
        bad = self.recipe(license="")
        with patch.object(mundev, "Builder", side_effect=AssertionError("no builder may start")), \
                patch.object(mundev, "BUILDS_ROOT", self.root / "builds"), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(mundev.vm.LabError):
                mundev.cmd_build(self.build_args(bad))
            big = self.root / "big"
            big.mkdir()
            for name in ("recipe.json", "build.sh", "fix.patch"):
                (big / name).write_bytes((bad / name).read_bytes())
            data = json.loads((big / "recipe.json").read_text())
            data.update(license="MIT", sources=[{"path": "source", "git": "https://example.invalid/g", "commit": self.commit}],
                        name="big")
            (big / "recipe.json").write_text(json.dumps(data))
            with (big / "data.pak").open("wb") as handle:
                handle.truncate(mundev.RECIPE_LIMIT + 1)
            with self.assertRaises(mundev.vm.LabError) as refused:
                mundev.cmd_build(self.build_args(big))
            self.assertIn("not game data", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
