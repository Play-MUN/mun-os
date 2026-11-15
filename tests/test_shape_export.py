"""Exporting a MUN Shape package with its sources at one commit
(docs/shape.md, "Exporting a package"): what Git has, nothing else, every
file listed with its SHA-256; the same commit and paths, the same bytes,
whatever the checkout knows of remotes; refused while something is not
committed or a new file is not tracked; and a verification that takes
nothing for an export but a whole one, pinned by its identity.

Each test makes a Git repository of its own in a temporary folder.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import shapeexport  # noqa: E402
from mun_card.errors import CardError  # noqa: E402

HAVE_GIT = shutil.which("git") is not None


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(HAVE_GIT, "Git is not installed")
class ExportTests(unittest.TestCase):
    def setUp(self):
        # Git runs hooks with the repository being committed in the
        # environment (GIT_DIR, GIT_INDEX_FILE, ...), and the pre-commit
        # hook runs these tests: the repositories they make must not inherit
        # it (tests/test_hook_safety.py).
        environment = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                              if not key.startswith("GIT_")}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-export-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        shutil.copytree(ROOT / "examples" / "shape" / "sea", self.repo / "shapes" / "sea")
        (self.repo / "studio").mkdir()
        (self.repo / "studio" / "make.py").write_text("print('the sea')\n")
        (self.repo / "studio" / ".gitignore").write_text("render/\n")
        (self.repo / "studio" / "render").mkdir()
        (self.repo / "studio" / "render" / "frame.png").write_bytes(b"ignored: a bake's output")
        (self.repo / "LICENSE").write_text("the licence\n")
        (self.repo / "private").mkdir()
        (self.repo / "private" / "notes.md").write_text("never exported\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "shapes", "studio", "LICENSE")
        self.commit("the sea")
        self.out = self.tmp / "out"

    def commit(self, message):
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def export(self, out=None):
        return shapeexport.export(self.repo / "shapes" / "sea", out or self.out, [self.repo / "studio"])

    def manifest(self, target):
        return json.loads((target / "export.json").read_text())

    def test_the_package_its_sources_and_notices_at_the_commit(self):
        commit = git(self.repo, "rev-parse", "HEAD").strip()
        target = self.export()
        self.assertEqual(target.name, f"sea-{commit[:12]}")
        manifest = self.manifest(target)
        self.assertEqual(set(manifest), {"format", "name", "source", "package", "files"})
        self.assertEqual(manifest["source"], {"commit": commit, "package": "shapes/sea", "sources": ["studio"]})
        self.assertEqual(manifest["package"], {"format": "mun-shape/1", "motion_draft": None})
        files = set(manifest["files"])
        self.assertIn("package/shape.json", files)
        self.assertIn("package/world/backdrop.png", files)
        self.assertIn("sources/studio/make.py", files)
        self.assertIn("notices/LICENSE", files)
        self.assertFalse(any("render" in f or "private" in f for f in files), "only what Git tracks under the paths given")
        for path, entry in manifest["files"].items():
            self.assertEqual(entry["bytes"], (target / path).stat().st_size, path)
        self.assertEqual(shapeexport.verify(target), [])
        self.assertEqual(shapeexport.verify(target, expect=shapeexport.identity(target)), [])

    def test_the_same_commit_gives_the_same_identity_whatever_the_checkout_knows(self):
        first = self.export(self.tmp / "a")
        # what a checkout learns of remotes is not part of the export
        git(self.repo, "remote", "add", "origin", "https://example.invalid/shapes.git")
        git(self.repo, "update-ref", "refs/remotes/origin/main", git(self.repo, "rev-parse", "HEAD").strip())
        second = self.export(self.tmp / "b")
        git(self.repo, "remote", "set-url", "origin", "https://example.invalid/elsewhere.git")
        third = self.export(self.tmp / "c")
        self.assertEqual({shapeexport.identity(t) for t in (first, second, third)}, {shapeexport.identity(first)})
        self.assertEqual((first / "export.json").read_bytes(), (third / "export.json").read_bytes())
        # it is said, as information, when exporting
        self.assertEqual(shapeexport.context(self.repo / "shapes" / "sea"),
                         {"origin": "https://example.invalid/elsewhere.git", "remote_branches": ["origin/main"]})

    def test_what_is_not_committed_is_refused(self):
        (self.repo / "shapes" / "sea" / "shape.json").write_text("{}")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_uncommitted")
        git(self.repo, "checkout", "--", ".")
        (self.repo / "studio" / "make.py").write_text("print('changed')\n")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_uncommitted")

    def test_a_new_file_git_does_not_track_is_refused_an_ignored_one_is_not(self):
        (self.repo / "studio" / "fish.js").write_text("// a new source, not added\n")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_untracked")
        self.assertIn("studio/fish.js", refused.exception.detail)
        (self.repo / "studio" / "fish.js").unlink()
        (self.repo / "shapes" / "sea" / "notes.txt").write_text("beside the package\n")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_untracked")
        (self.repo / "shapes" / "sea" / "notes.txt").unlink()
        # studio/render/ is ignored: a bake's output, not a source
        self.assertEqual(shapeexport.verify(self.export()), [])

    def test_a_file_the_package_uses_that_git_does_not_have(self):
        (self.repo / ".gitignore").write_text("shapes/sea/card/new.png\n")
        document = json.loads((self.repo / "shapes" / "sea" / "shape.json").read_text())
        document["card"]["window"] = "card/new.png"
        shutil.copy(self.repo / "shapes" / "sea" / "card" / "window.png", self.repo / "shapes" / "sea" / "card" / "new.png")
        (self.repo / "shapes" / "sea" / "shape.json").write_text(json.dumps(document))
        git(self.repo, "add", ".gitignore", "shapes/sea/shape.json")
        self.commit("names a file Git ignores")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_untracked")

    def test_a_package_no_console_would_use(self):
        (self.repo / "shapes" / "sea" / "shape.json").write_text('{"format": "mun-shape/2"}')
        git(self.repo, "add", "shapes")
        self.commit("a later major version")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_unused")

    def test_verify_sees_a_file_changed_missing_or_added(self):
        target = self.export()
        (target / "sources" / "studio" / "make.py").write_text("print('edited apart')\n")
        (target / "package" / "world" / "rays.png").unlink()
        (target / "package" / "extra.png").write_bytes(b"x")
        self.assertEqual(shapeexport.verify(target), ["package/world/rays.png: missing", "sources/studio/make.py: changed",
                                                      "package/extra.png: not in the export"])

    def test_verify_takes_nothing_for_an_export_but_a_whole_one(self):
        target = self.export()
        whole = self.manifest(target)

        def problems_with(manifest):
            (target / "export.json").write_text(json.dumps(manifest))
            return shapeexport.verify(target)
        empty = self.tmp / "empty"
        empty.mkdir()
        (empty / "export.json").write_text(json.dumps({"format": shapeexport.FORMAT}))
        self.assertTrue(shapeexport.verify(empty), "a manifest with nothing in it is not an export")
        no_package = dict(whole, files={k: v for k, v in whole["files"].items() if k != "package/shape.json"})
        self.assertIn("package/shape.json: not listed", problems_with(no_package))
        outside = dict(whole, files=dict(whole["files"], **{"package/../../etc/x": {"sha256": "0" * 64, "bytes": 1}}))
        self.assertIn("'package/../../etc/x': not a path an export holds", problems_with(outside))
        bad = dict(whole, files=dict(whole["files"], **{"package/shape.json": {"sha256": "abc", "bytes": 1}}))
        self.assertIn("package/shape.json: no valid sha256 and bytes", problems_with(bad))
        self.assertIn("export.json: no valid source (commit, package, sources)",
                      problems_with(dict(whole, source={"commit": "main"})))
        (target / "export.json").write_text(json.dumps(whole, indent=1) + "\n")
        self.assertEqual(shapeexport.verify(target), [])
        pinned = shapeexport.verify(target, expect="0" * 64)
        self.assertEqual(len(pinned), 1)
        self.assertIn("not the " + "0" * 64 + " expected", pinned[0])

    def test_verify_reads_the_package_as_a_console_would(self):
        target = self.export()
        manifest = self.manifest(target)
        unusable = b'{"format": "mun-shape/2"}'
        (target / "package" / "shape.json").write_bytes(unusable)
        manifest["files"]["package/shape.json"] = {"sha256": hashlib.sha256(unusable).hexdigest(), "bytes": len(unusable)}
        (target / "export.json").write_text(json.dumps(manifest))
        self.assertIn("package: a console would not use it (unused)", shapeexport.verify(target))

    def nothing_written(self, out=None):
        out = out or self.out
        self.assertEqual(sorted(os.listdir(out)) if out.exists() else [], [], "no export, whole or partial, is left")

    def test_a_declared_source_the_commit_lacks_or_that_gives_nothing_is_refused(self):
        for sources in ([self.repo / "studio", self.repo / "studoi"], [self.repo / "studio" / "render"]):
            # a typo; a folder of ignored files only
            source = sources[-1]
            with self.assertRaises(CardError) as refused:
                shapeexport.export(self.repo / "shapes" / "sea", self.out, sources)
            self.assertEqual(refused.exception.code, "export_source_missing", source)
            self.assertEqual(refused.exception.detail, source.relative_to(self.repo).as_posix())
            self.nothing_written()

    def test_a_source_that_is_the_package_or_inside_another_is_refused(self):
        (self.repo / "studio" / "tools").mkdir()
        (self.repo / "studio" / "tools" / "bake.py").write_text("print('bake')\n")
        git(self.repo, "add", "studio")
        self.commit("tools")
        for sources in ([self.repo / "shapes" / "sea"], [self.repo / "shapes"], [self.repo / "shapes" / "sea" / "world"],
                        [self.repo / "studio", self.repo / "studio" / "tools"]):
            with self.assertRaises(CardError) as refused:
                shapeexport.export(self.repo / "shapes" / "sea", self.out, sources)
            self.assertEqual(refused.exception.code, "export_source", sources)
            self.nothing_written()

    def test_the_same_sources_in_any_order_or_repeated_give_the_same_export(self):
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "check.py").write_text("print('check')\n")
        git(self.repo, "add", "tools")
        self.commit("tools")
        package = self.repo / "shapes" / "sea"
        first = shapeexport.export(package, self.tmp / "a", [self.repo / "studio", self.repo / "tools"])
        second = shapeexport.export(package, self.tmp / "b", [self.repo / "tools", self.repo / "studio",
                                                               self.repo / "studio" / "."])
        self.assertEqual((first / "export.json").read_bytes(), (second / "export.json").read_bytes())
        self.assertEqual(self.manifest(first)["source"]["sources"], ["studio", "tools"])

    def test_names_an_export_cannot_hold_are_refused_when_exporting(self):
        for name in ("my fish.js", "a+b.js", "pez-ñ.js"):
            (self.repo / "studio" / name).write_text("// a source\n")
            git(self.repo, "add", "studio")
            self.commit(f"add {name}")
            with self.assertRaises(CardError) as refused:
                self.export()
            self.assertEqual(refused.exception.code, "export_path", name)
            self.nothing_written()
            git(self.repo, "rm", "-q", f"studio/{name}")
            self.commit(f"remove {name}")
        # Names that differ only in case are one file where case is not told
        # apart (this Mac's own disk among them): refused as verify refuses them.
        self.assertEqual(shapeexport._case_twins(["sources/studio/make.py", "sources/studio/Make.py", "package/a"]),
                         ["sources/studio/make.py"])

    def test_links_and_submodules_are_refused_never_written_as_files(self):
        (self.repo / "studio" / "licence").symlink_to("../LICENSE")
        git(self.repo, "add", "studio")
        self.commit("a link among the sources")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual((refused.exception.code, refused.exception.detail), ("export_symlink", "studio/licence"))
        self.nothing_written()
        git(self.repo, "rm", "-q", "studio/licence")
        self.commit("no link")
        (self.repo / "linked").symlink_to("studio")
        git(self.repo, "add", "linked")
        self.commit("a source that is a link")
        with self.assertRaises(CardError) as refused:
            shapeexport.export(self.repo / "shapes" / "sea", self.out, [self.repo / "linked"])
        self.assertEqual((refused.exception.code, refused.exception.detail), ("export_symlink", "linked"))
        commit = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},studio/vendor")
        (self.repo / "studio" / "vendor").mkdir()      # as a submodule not checked out leaves it
        self.commit("a submodule among the sources")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual((refused.exception.code, refused.exception.detail), ("export_submodule", "studio/vendor"))
        self.nothing_written()

    def test_the_package_is_checked_from_the_commits_bytes(self):
        seen = []
        original = shapeexport.shape.check_package

        def recording(source, *args, **kwargs):
            seen.append((Path(source.root) / "shape.json").read_bytes())
            return original(source, *args, **kwargs)
        with patch.object(shapeexport.shape, "check_package", recording):
            self.export()
        committed = subprocess.run(["git", "-C", str(self.repo), "show", "HEAD:shapes/sea/shape.json"], check=True,
                                   capture_output=True).stdout
        self.assertEqual(seen[0], committed, "the verdict is the commit's")
        # Changes Git is told not to show are refused, not exported unseen.
        (self.repo / "shapes" / "sea" / "shape.json").write_text('{"format": "mun-shape/2"}')
        git(self.repo, "update-index", "--assume-unchanged", "shapes/sea/shape.json")
        self.assertEqual(git(self.repo, "status", "--porcelain", "--", "shapes"), "", "status shows nothing")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_uncommitted")

    def test_export_json_is_the_same_bytes_on_every_system(self):
        with patch.object(Path, "write_text", side_effect=AssertionError("a text layer would change the bytes")):
            target = self.export()
        data = (target / "export.json").read_bytes()
        self.assertEqual(data, shapeexport.manifest_bytes(json.loads(data)))
        self.assertTrue(data.endswith(b"}\n") and b"\r" not in data)

    def test_an_export_that_cannot_be_written_or_verified_leaves_nothing(self):
        self.out.mkdir()
        os.chmod(self.out, 0o500)
        self.addCleanup(os.chmod, self.out, 0o700)
        if os.access(self.out, os.W_OK):
            self.skipTest("this user writes anywhere")
        with self.assertRaises(CardError) as refused:
            self.export()
        self.assertEqual(refused.exception.code, "export_write")
        os.chmod(self.out, 0o700)
        self.nothing_written()
        with patch.object(shapeexport, "verify", return_value=["sources/studio/make.py: changed"]):
            with self.assertRaises(CardError) as refused:
                self.export()
        self.assertEqual(refused.exception.code, "export_verify")
        self.nothing_written()

    def test_verify_answers_with_problems_never_a_failure(self):
        target = self.export()
        manifest = self.manifest(target)

        def package_json(data):
            (target / "package" / "shape.json").write_bytes(data)
            manifest["files"]["package/shape.json"] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            (target / "export.json").write_text(json.dumps(manifest))
            return shapeexport.verify(target)
        for data in (b"[]", b"{", b'"text"'):
            problems = package_json(data)
            self.assertTrue(problems and all(isinstance(p, str) for p in problems), (data, problems))
        target = self.export(self.tmp / "again")
        (target / "sources" / "studio" / "make.py").chmod(0)
        self.addCleanup((target / "sources" / "studio" / "make.py").chmod, 0o600)
        if not os.access(target / "sources" / "studio" / "make.py", os.R_OK):
            self.assertIn("sources/studio/make.py: unreadable (Permission denied)", shapeexport.verify(target))
        from mun_card import cli
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["shape", "verify-export", str(target)]), 2)

    def test_a_manifest_nested_too_deeply_is_not_an_export_not_a_failure(self):
        # A manifest of nothing but opening brackets, deeper than the JSON
        # reader goes: the answer is "not an export" (status 2), as for any
        # manifest that is not JSON, never a traceback.
        from mun_card import cli
        target = self.export()
        for deep in (b"[" * 200000, b'{"a":' * 100000 + b"1" + b"}" * 100000):
            (target / "export.json").write_bytes(deep)
            problems = shapeexport.verify(target)
            self.assertEqual(len(problems), 1, problems)
            self.assertTrue(problems[0].startswith("export.json: "), problems)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(cli.main(["shape", "verify-export", str(target)]), 2)
            self.assertNotIn("Traceback", out.getvalue())
        # The package's own document is read by the checker's strict
        # profile, which refuses depth before it parses: the same answer.
        target = self.export(self.tmp / "deep-package")
        manifest = self.manifest(target)
        data = b'{"format": "mun-shape/1.1", "scene": ' + b"[" * 30000 + b"]" * 30000 + b"}"
        (target / "package" / "shape.json").write_bytes(data)
        manifest["files"]["package/shape.json"] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        (target / "export.json").write_text(json.dumps(manifest))
        self.assertEqual(shapeexport.verify(target), ["package: a console would not use it (unused)"])

    def test_verify_takes_only_what_export_writes(self):
        target = self.export()
        whole = self.manifest(target)

        def problems_with(manifest):
            (target / "export.json").write_text(json.dumps(manifest))
            return shapeexport.verify(target)
        files = whole["files"]
        cases = {
            "unknown keys published_in": dict(whole, published_in=["origin/main"]),
            "no valid source": dict(whole, source=dict(whole["source"], sources=[7])),
            "no valid source ": dict(whole, source=dict(whole["source"], package="../shapes")),
            "under no declared source": dict(whole, source=dict(whole["source"], sources=["tools"])),
            "the source tools gives no file": dict(whole, source=dict(whole["source"], sources=["studio", "tools"])),
            "not a notice an export holds": dict(whole, files=dict(files, **{"notices/README": files["notices/LICENSE"]})),
            "differs from another path only in case": dict(whole, files=dict(files, **{
                "package/World/rays.png": files["package/world/rays.png"]})),
            "no valid package": dict(whole, package={"format": "mun-shape/1", "motion_draft": "1"}),
        }
        for said, manifest in cases.items():
            self.assertTrue(any(said.strip() in p for p in problems_with(manifest)), (said, problems_with(manifest)))
        (target / "export.json").write_bytes(shapeexport.manifest_bytes(whole))
        self.assertEqual(shapeexport.verify(target), [])
        (target / "package" / "empty").mkdir()
        self.assertEqual(shapeexport.verify(target), ["package/empty/: not in the export"])
        (target / "package" / "empty").rmdir()
        # a file the package does not use, listed as if it did
        (target / "package" / "unused.png").write_bytes(b"x")
        extra = dict(whole, files=dict(files, **{"package/unused.png": {"sha256": hashlib.sha256(b"x").hexdigest(),
                                                                          "bytes": 1}}))
        self.assertEqual(problems_with(extra), ["package/unused.png: in the export, not used by the package"])

    def test_the_export_never_acts_on_a_hooks_repository(self):
        # The environment of a pre-commit hook, pointing at a sentinel
        # repository: its configuration, index and references must come out
        # the same (a reinitialised repository turns bare without any new
        # file). The whole module also runs under it: test_hook_safety.py.
        sentinel = self.tmp / "sentinel"
        sentinel.mkdir()
        git(sentinel, "init", "-q")
        (sentinel / "kept.txt").write_text("kept\n")
        git(sentinel, "add", "kept.txt")
        git(sentinel, "commit", "-q", "-m", "sentinel")

        def state():
            return ((sentinel / ".git" / "config").read_bytes(), (sentinel / ".git" / "index").read_bytes(),
                    git(sentinel, "for-each-ref"), git(sentinel, "rev-parse", "HEAD"), git(sentinel, "ls-files", "--stage"))
        before = state()
        with patch.dict(os.environ, {"GIT_DIR": str(sentinel / ".git"), "GIT_INDEX_FILE": str(sentinel / ".git" / "index")}):
            target = self.export()
            problems = shapeexport.verify(target)
            where = shapeexport.context(self.repo / "shapes" / "sea")
        self.assertEqual(problems, [])
        self.assertEqual(where["remote_branches"], [])
        self.assertEqual(state(), before, "the sentinel's configuration, index and references are untouched")

    def test_the_reef_example_and_its_studio_are_tracked_whole(self):
        # What the export of the official example takes from this repository.
        tracked = git(ROOT, "ls-files", "examples/shape/reef", "examples/shape/reef-studio").split()
        self.assertIn("examples/shape/reef/shape.json", tracked)
        self.assertIn("examples/shape/reef-studio/sea.js", tracked)
        self.assertFalse(any("/render/" in path or "node_modules" in path for path in tracked))


if __name__ == "__main__":
    unittest.main()
