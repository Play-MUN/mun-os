"""Host tests of `./mun dev` (vm/mundev.py): what a build is made from, how its
results are checked, and how image guests are created. No builder VM, QEMU
or network is used; the build itself runs in a builder (os/README.md)."""

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))
import mundev  # noqa: E402

vm = mundev.vm


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    def enterContext(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def setUp(self):
        # Git runs hooks with the repository being committed in the environment
        # (GIT_DIR, GIT_INDEX_FILE, ...), and the pre-commit hook runs these tests:
        # the repositories they make must not inherit it, or `git -C DIR init` would
        # reinitialise that repository and the fixtures be committed into it
        # (tests/test_hook_safety.py).
        self.enterContext(patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                                  if not key.startswith("GIT_")}, clear=True))
        directory = tempfile.TemporaryDirectory(prefix="mundev-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.object(mundev, "BUILDS_ROOT", self.root / "builds"))
        self.enterContext(patch.object(mundev, "BUILDERS_ROOT", self.root / "builders"))
        self.enterContext(patch.object(mundev, "GUESTS_ROOT", self.root / "guests"))
        self.enterContext(patch.object(vm, "GUESTS_ROOT", self.root / "guests"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def build(self, name="b1", shape=None):
        build = self.root / "builds" / name
        build.mkdir(parents=True)
        image = build / "mun-os-0.1.0-dev-qemu-arm64.qcow2"
        image.write_bytes(b"image bytes")
        digest = hashlib.sha256(b"image bytes").hexdigest()
        info = {"build_id": f"id-{name}", "version": "0.1.0-dev", "environment": "qemu-arm64", "release": False,
                "built": {"finished": f"2026-09-27T00:00:0{name[-1]}Z"}, "source": {"describe": f"c0ffee{name[-1]}"},
                "artifacts": {image.name: {"sha256": digest}}}
        if shape:
            info["shape"] = {"format": shape}
        (build / "BUILD-INFO.json").write_text(json.dumps(info))
        (build / "SHA256SUMS").write_text(f"{digest}  {image.name}\n")
        return build


class SourceTests(Fixture):
    def repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "dev@example.invalid")
        git(repo, "config", "user.name", "Dev")
        (repo / ".gitignore").write_text(".local/\n")
        (repo / "tracked.txt").write_text("one\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "first")
        return repo

    def test_a_clean_checkout_is_built_from_its_commit(self):
        repo = self.repo()
        with patch.object(mundev, "REPO_ROOT", repo):
            state = mundev.source_state()
            self.assertTrue(state["clean"])
            self.assertEqual(state["commit"], git(repo, "rev-parse", "HEAD"))
            archive = self.root / "a.tar"
            digest = mundev.source_archive(True, archive)
        self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
        with tarfile.open(archive) as tar:
            self.assertEqual(sorted(tar.getnames()), [".gitignore", "tracked.txt"])

    def test_uncommitted_changes_are_seen_and_ignored_files_never_sent(self):
        repo = self.repo()
        (repo / "tracked.txt").write_text("changed\n")
        (repo / "new.txt").write_text("new\n")
        (repo / ".local").mkdir()
        (repo / ".local" / "secret.img").write_text("private card\n")
        with patch.object(mundev, "REPO_ROOT", repo):
            state = mundev.source_state()
            self.assertFalse(state["clean"])
            self.assertTrue(state["describe"].endswith("-dirty"))
            archive = self.root / "d.tar"
            mundev.source_archive(False, archive)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            self.assertEqual(sorted(names), [".gitignore", "new.txt", "tracked.txt"])
            self.assertEqual(tar.extractfile("tracked.txt").read(), b"changed\n")

    def test_a_dirty_build_needs_an_explicit_request(self):
        args = argparse.Namespace(profile="qemu-dev", name="b1", allow_dirty=False, recipe=None, keep_builder=False)
        with patch.object(mundev, "source_state", return_value={"clean": False}), \
                patch.object(mundev, "builder_base_image", side_effect=AssertionError("nothing may start")):
            with self.assertRaises(vm.LabError) as ctx:
                mundev.cmd_build(args)
        self.assertIn("--allow-dirty", str(ctx.exception))

    def test_only_known_profiles_and_new_names_are_built(self):
        args = argparse.Namespace(profile="release", name="b1", allow_dirty=False, recipe=None, keep_builder=False)
        with self.assertRaises(vm.LabError):
            mundev.cmd_build(args)
        (self.root / "builds" / "b1").mkdir(parents=True)
        args.profile = "qemu-dev"
        with self.assertRaises(vm.LabError) as ctx:
            mundev.cmd_build(args)
        self.assertIn("exists", str(ctx.exception))


class BuilderTests(Fixture):
    def test_each_builder_has_its_own_disk_key_and_seed(self):
        base = self.root / "base.qcow2"
        base.write_bytes(b"base")
        with patch.object(vm, "run") as run, patch.object(vm, "which", side_effect=lambda name: name), \
                patch.object(vm, "make_seed_iso"):
            run.side_effect = lambda cmd, **kw: (Path(cmd[-1]).with_suffix(".pub").write_text("ssh-ed25519 AAAA test")
                                                if cmd[0] == "ssh-keygen" else None)
            builder = mundev.Builder("b1")
            builder.create(base)
            with self.assertRaises(vm.LabError):
                mundev.Builder("b1").create(base)
        overlay = next(call.args[0] for call in run.call_args_list if call.args[0][0] == "qemu-img")
        self.assertIn(str(base), overlay, "the pinned image is only a backing file")
        user_data = (builder.seed_dir / "user-data").read_text()
        self.assertIn("ssh-ed25519 AAAA test", user_data)
        self.assertIn("ssh_pwauth: false", user_data)

    def test_the_builder_image_must_match_its_pin(self):
        cache = self.root / "cache"
        cache.mkdir()
        spec = mundev.inputs()["builder"]
        (cache / spec["image"]).write_bytes(b"not the pinned image")
        with patch.object(mundev, "CACHE_ROOT", cache):
            with self.assertRaises(vm.LabError):
                mundev.builder_base_image()


class ResultTests(Fixture):
    def test_results_are_checked_against_their_sums(self):
        build = self.build()
        mundev.verify_sums(build)
        (build / "mun-os-0.1.0-dev-qemu-arm64.qcow2").write_bytes(b"tampered")
        with self.assertRaises(vm.LabError):
            mundev.verify_sums(build)

    def test_results_are_published_under_the_final_name_with_the_host_note(self):
        built = self.build("tmp")
        partial = self.root / "builds" / "b3.partial"
        (partial / "logs").mkdir(parents=True)
        (partial / "logs" / "build.log").write_text("from the builder stream\n")
        built.rename(partial / "results")
        (partial / "results" / "logs").mkdir()
        (partial / "results" / "logs" / "mkosi.log").write_text("mkosi\n")
        final = self.root / "builds" / "b3"
        info = mundev.publish_build(partial, final, "build b3 finished in 1 s")
        self.assertEqual(info["build_id"], "id-tmp")
        self.assertFalse(partial.exists())
        self.assertEqual(sorted(p.name for p in (final / "logs").iterdir()), ["build.log", "host.log", "mkosi.log"])
        self.assertIn("finished in 1 s", (final / "logs" / "host.log").read_text())
        self.assertTrue((final / "mun-os-0.1.0-dev-qemu-arm64.qcow2").is_file())

    def test_a_result_that_fails_its_sums_is_not_published(self):
        built = self.build("tmp")
        partial = self.root / "builds" / "b4.partial"
        (partial / "logs").mkdir(parents=True)
        built.rename(partial / "results")
        (partial / "results" / "mun-os-0.1.0-dev-qemu-arm64.qcow2").write_bytes(b"torn copy")
        with self.assertRaises(vm.LabError):
            mundev.publish_build(partial, self.root / "builds" / "b4", "note")
        self.assertFalse((self.root / "builds" / "b4").exists())
        self.assertTrue(partial.is_dir(), "a failed build stays .partial, with its logs")

    def test_a_guest_disk_is_a_new_overlay_over_the_untouched_image(self):
        build = self.build()
        with patch.object(vm, "run") as run, patch.object(vm, "which", side_effect=lambda name: name):
            mundev.create_guest("one", build)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["qemu-img", "create", "-q", "-f"])
        self.assertIn(str(build / "mun-os-0.1.0-dev-qemu-arm64.qcow2"), command)
        guest = json.loads((self.root / "guests" / "one" / "guest.json").read_text())
        self.assertEqual((guest["build"], guest["build_id"], guest["release"]), ("b1", "id-b1", False))
        with self.assertRaises(vm.LabError):
            mundev.create_guest("one", build)

    def test_a_guest_refuses_an_image_that_does_not_match_its_build_info(self):
        build = self.build()
        (build / "mun-os-0.1.0-dev-qemu-arm64.qcow2").write_bytes(b"other")
        with patch.object(vm, "run"), patch.object(vm, "which", side_effect=lambda name: name):
            with self.assertRaises(vm.LabError):
                mundev.create_guest("one", build)

    def test_run_keeps_a_guest_on_its_build_and_refuses_invalid_names(self):
        self.build("b1")
        self.build("b2")
        with patch.object(vm, "run"), patch.object(vm, "which", side_effect=lambda name: name), \
                patch.object(vm, "main", return_value=0) as main:
            mundev.cmd_run(argparse.Namespace(guest="one", build="b1", window=False, audio=None, wait=5))
            self.assertEqual(main.call_args.args[0][:3], ["--instance", "one", "start"])
            with self.assertRaises(vm.LabError):
                mundev.cmd_run(argparse.Namespace(guest="one", build="b2", window=False, audio=None, wait=5))
            for invalid in ("a", "../x", "Dev"):
                with self.assertRaises(vm.LabError):
                    mundev.cmd_run(argparse.Namespace(guest=invalid, build=None, window=False, audio=None, wait=5))
            mundev.cmd_run(argparse.Namespace(guest="two", build=None, window=True, audio=None, wait=5))
        self.assertEqual(json.loads((self.root / "guests" / "two" / "guest.json").read_text())["build"], "b2",
                         "without --build a new guest takes the latest build")
        self.assertEqual(main.call_args.args[0][2:], ["open"], "the window picks this host's audible backend itself")


class ShapeConsoleTests(Fixture):
    """`./mun dev shape` takes only a console whose build records MUN Shape,
    and says so before a guest is made or started."""

    def shape_args(self, guest, build=None):
        folder = self.root / "pkg"
        folder.mkdir(exist_ok=True)
        return argparse.Namespace(folder=str(folder), guest=guest, build=build, base=None, title="Shape preview",
                                  watch=True, window=True, audio=None)

    def test_an_existing_guest_of_an_older_build_is_refused_and_left_as_it_is(self):
        self.build("b1")
        self.build("b2", shape="mun-shape/1")
        with patch.object(vm, "run"), patch.object(vm, "which", side_effect=lambda name: name):
            mundev.create_guest("consola", self.root / "builds" / "b1")
        guest = (self.root / "guests" / "consola" / "guest.json").read_bytes()
        with patch.object(vm, "main") as main, patch.object(vm, "select_instance") as select:
            with self.assertRaises(vm.LabError) as refused:
                mundev.cmd_shape(self.shape_args("consola"))
        self.assertIn("guest consola runs build b1", str(refused.exception))
        self.assertIn("build b2", str(refused.exception))
        main.assert_not_called()
        select.assert_not_called()
        self.assertEqual((self.root / "guests" / "consola" / "guest.json").read_bytes(), guest)

    def test_a_new_guest_is_not_made_from_a_build_that_records_no_shape(self):
        self.build("b1")
        with patch.object(vm, "run") as run, patch.object(vm, "main") as main:
            with self.assertRaises(vm.LabError) as refused:
                mundev.cmd_shape(self.shape_args("shape", build="b1"))
        self.assertIn("./mun dev build", str(refused.exception))
        run.assert_not_called()
        main.assert_not_called()
        self.assertFalse((self.root / "guests" / "shape").exists(), "no guest made")

    def test_the_list_says_which_builds_and_guests_show_shape(self):
        self.build("b1")
        self.build("b2", shape="mun-shape/1")
        with patch.object(vm, "run"), patch.object(vm, "which", side_effect=lambda name: name):
            mundev.create_guest("old", self.root / "builds" / "b1")
            mundev.create_guest("new", self.root / "builds" / "b2")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mundev.cmd_list(argparse.Namespace())
        lines = out.getvalue().splitlines()
        self.assertTrue(any(line.startswith("build b1") and line.endswith("shape not recorded") for line in lines))
        self.assertTrue(any(line.startswith("build b2") and line.endswith("shape mun-shape/1") for line in lines))
        self.assertTrue(any(line.startswith("guest new") and line.endswith("shape mun-shape/1") for line in lines))
        self.assertTrue(any(line.startswith("guest old") and line.endswith("shape not recorded") for line in lines))


class LicenceTests(unittest.TestCase):
    def test_a_download_carries_the_texts_the_image_carries(self):
        texts = mundev.licence_texts()
        names = [text.name for text in texts]
        self.assertEqual(names[:3], ["LICENSE", "NOTICE", "NAME-AND-LOGO.txt"])
        self.assertTrue(any(name.endswith("-OFL.txt") for name in names), "and the typefaces' licences")
        for text in texts:
            self.assertTrue(text.is_file(), text)
            self.assertTrue(text.name in ("LICENSE", "NOTICE") or text.name.endswith(".txt"),
                            f"{text.name}: a name every version of ./mun get accepts")
        image = (ROOT / "os" / "mkosi" / "mkosi.build.chroot").read_text()
        for name in names[:3]:
            self.assertIn(f'"$MUN/{name}" "$DESTDIR/usr/share/doc/mun-os/{name}"', image)


if __name__ == "__main__":
    unittest.main()
