"""Host tests of the downloadable image (vm/bundle.py, `./mun dev bundle`,
`./mun get`): what a bundle holds, what `get` refuses, and that it installs
nothing it has not verified. A local directory and a loopback HTTP server
stand in for the web; no QEMU or guest is used."""

import argparse
import contextlib
import errno
import hashlib
import http.server
import io
import json
import lzma
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))
import bundle  # noqa: E402
import mundev  # noqa: E402

BUILD_ID = "20260929T123647Z-aee686f3d1fd"
IMAGE = "mun-os-0.1.0-dev-qemu-arm64.qcow2"


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-bundle-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.messages = []

    def report(self, text):
        self.messages.append(text)

    def build(self, image_bytes=b"qcow2-image" * 1000) -> Path:
        build = self.root / "builds" / "b1"
        build.mkdir(parents=True)
        (build / IMAGE).write_bytes(image_bytes)
        info = {"format": 1, "name": "MUN OS", "version": "0.1.0-dev", "environment": "qemu-arm64", "release": False,
                "build_id": BUILD_ID, "built": {"finished": "2026-09-29T12:37:58Z"},
                "source": {"commit": "a" * 40, "describe": "aee686f", "clean": True},
                "artifacts": {IMAGE: {"sha256": hashlib.sha256(image_bytes).hexdigest(), "size": len(image_bytes)}}}
        (build / "BUILD-INFO.json").write_text(json.dumps(info))
        return build

    def card(self, name, content=b"ext4" + b"\0" * 4096) -> Path:
        path = self.root / "made" / f"{name}.img"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def bundle(self) -> Path:
        destination = self.root / "bundle"
        licence = self.root / "made" / "LICENSE"
        licence.parent.mkdir(parents=True, exist_ok=True)
        licence.write_text("Apache License\n")
        notice = self.root / "made" / "NOTICE"
        notice.write_text("MUN OS\nCopyright 2026 Iván Moreno Mendoza\n")
        bundle.make(self.build(), destination, {"collect": (self.card("collect"), "MUN Collect"),
                                                "demo": (self.card("demo", b"demo" * 100), "MUN Test Card")},
                    (licence, notice))
        return destination

    def get(self, location, name=None):
        return bundle.get(str(location), self.root / "installed", self.root / "cards", name, report=self.report)


class MakeTests(Fixture):
    def test_a_bundle_holds_the_image_its_record_and_packed_cards_with_their_digests(self):
        destination = self.bundle()
        manifest = json.loads((destination / "release.json").read_text())
        self.assertEqual((manifest["format"], manifest["build_id"], manifest["release"]), ("mun-bundle/1", BUILD_ID, False))
        names = {entry["name"]: entry for entry in manifest["files"]}
        self.assertEqual(set(names), {IMAGE, "BUILD-INFO.json", "card-collect.img.xz", "card-demo.img.xz", "LICENSE", "NOTICE"})
        for name, entry in names.items():
            self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), entry["sha256"])
        self.assertEqual(lzma.decompress((destination / "card-demo.img.xz").read_bytes()), b"demo" * 100)
        self.assertFalse((self.root / "bundle.partial").exists())

    def test_an_image_that_does_not_match_its_record_is_not_bundled(self):
        build = self.build()
        (build / IMAGE).write_bytes(b"changed")
        with self.assertRaises(bundle.BundleError):
            bundle.make(build, self.root / "bundle", {})
        self.assertFalse((self.root / "bundle").exists())


class GetTests(Fixture):
    def test_a_bundle_from_a_directory_is_installed_as_a_build_with_its_origin_and_cards(self):
        source = self.bundle()
        build, cards = self.get(source)
        self.assertEqual(build.name, "d0929-123647", "named after its build id")
        self.assertEqual(sorted(p.name for p in build.iterdir()), sorted([IMAGE, "BUILD-INFO.json", "ORIGIN.json", "licences"]))
        self.assertEqual((build / "licences" / "LICENSE").read_text(), "Apache License\n", "the licence goes with the image")
        self.assertIn("Iván Moreno Mendoza", (build / "licences" / "NOTICE").read_text(), "and the notice")
        self.assertEqual(json.loads((build / "ORIGIN.json").read_text())["source"], str(source))
        self.assertEqual(sorted(card.name for card in cards), ["collect.img", "demo.img"])
        self.assertEqual((self.root / "cards" / "demo.img").read_bytes(), b"demo" * 100)
        self.assertFalse(any(p.name.endswith((".download", ".partial")) for p in (self.root / "installed").iterdir()))

    def test_getting_it_again_changes_nothing_and_a_card_with_saves_is_never_replaced(self):
        source = self.bundle()
        (self.root / "cards").mkdir()
        (self.root / "cards" / "collect.img").write_bytes(b"my saves")
        build, cards = self.get(source)
        self.assertEqual([card.name for card in cards], ["demo.img"])
        self.assertEqual((self.root / "cards" / "collect.img").read_bytes(), b"my saves")
        self.assertTrue(any("left as it is" in message for message in self.messages))
        again, cards = self.get(source)
        self.assertEqual((again, cards), (build, []))

    def test_a_card_that_appears_while_one_is_unpacked_is_kept_as_it_is(self):
        # The window the review found: the name is free when checked, then
        # another install or a player's card takes it before publication.
        source = self.bundle()
        cards = self.root / "cards"
        real_open = bundle.lzma.open

        def open_then_someone_saves(path, *args, **kwargs):
            if Path(path).name == "card-collect.img.xz":
                cards.mkdir(exist_ok=True)
                (cards / "collect.img").write_bytes(b"ANOTHER INSTALL COMPLETED; PLAYER SAVED HERE")
            return real_open(path, *args, **kwargs)
        with patch.object(bundle.lzma, "open", side_effect=open_then_someone_saves):
            _build, installed = self.get(source)
        self.assertEqual((cards / "collect.img").read_bytes(), b"ANOTHER INSTALL COMPLETED; PLAYER SAVED HERE")
        self.assertEqual([card.name for card in installed], ["demo.img"])
        self.assertEqual(sorted(p.name for p in cards.iterdir()), ["collect.img", "demo.img"], "no temporary left")

    def test_without_hard_links_nothing_partial_ever_takes_a_card_name(self):
        # The follow-up review's case: with no hard links, a copy into the
        # final name that fails half-way left a partial card, which the next
        # attempt then kept as a player's. Now such a file system is refused
        # with the name left free, and a later attempt installs the card.
        source = self.bundle()
        cards = self.root / "cards"
        with patch.object(bundle, "WINDOWS", False), \
                patch.object(bundle.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no hard links")):
            with self.assertRaises(bundle.BundleError) as refused:
                self.get(source)
        self.assertIn("no hard links", str(refused.exception))
        self.assertEqual(sorted(p.name for p in cards.iterdir()), [], "no card and no temporary left")
        _build, installed = self.get(source)
        self.assertEqual(sorted(card.name for card in installed), ["collect.img", "demo.img"])
        self.assertEqual((cards / "demo.img").read_bytes(), b"demo" * 100)

    def test_on_windows_without_hard_links_a_rename_publishes_and_never_replaces(self):
        # Windows' rename refuses an existing name (os.rename there); emulated
        # here, where it would replace.
        source = self.bundle()
        cards = self.root / "cards"
        cards.mkdir()
        (cards / "collect.img").write_bytes(b"my saves")
        real_rename = bundle.os.rename

        def windows_rename(old, new):
            if Path(new).exists():
                raise FileExistsError(errno.EEXIST, "exists", str(new))
            real_rename(old, new)
        with patch.object(bundle, "WINDOWS", True), \
                patch.object(bundle.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no hard links")), \
                patch.object(bundle.os, "rename", side_effect=windows_rename):
            _build, installed = self.get(source)
        self.assertEqual([card.name for card in installed], ["demo.img"])
        self.assertEqual((cards / "collect.img").read_bytes(), b"my saves")
        self.assertEqual((cards / "demo.img").read_bytes(), b"demo" * 100)
        self.assertEqual(sorted(p.name for p in cards.iterdir()), ["collect.img", "demo.img"], "no temporary left")

    def test_a_failed_publication_leaves_neither_a_card_nor_a_temporary(self):
        # Interrupted at the last step (a rename that fails on Windows): the
        # name stays free and the retry installs the whole card.
        source = self.bundle()
        cards = self.root / "cards"
        with patch.object(bundle, "WINDOWS", True), \
                patch.object(bundle.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no hard links")), \
                patch.object(bundle.os, "rename", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(OSError):
                self.get(source)
        self.assertFalse((cards / "collect.img").exists())
        self.assertFalse(any(p.name.endswith(".part") for p in cards.iterdir()))
        _build, installed = self.get(source)
        self.assertIn("collect.img", [card.name for card in installed])
        self.assertEqual((cards / "collect.img").read_bytes(), b"ext4" + b"\0" * 4096)

    def test_a_tampered_file_is_refused_and_nothing_is_installed(self):
        source = self.bundle()
        (source / IMAGE).write_bytes((source / IMAGE).read_bytes()[:-1] + b"!")
        with self.assertRaises(bundle.BundleError):
            self.get(source)
        installed = self.root / "installed"
        self.assertFalse((installed / "d0929-123647").exists())
        self.assertFalse((self.root / "cards").exists())

    def test_a_file_larger_than_declared_is_cut_off(self):
        source = self.bundle()
        with (source / "BUILD-INFO.json").open("ab") as handle:
            handle.write(b" " * 10)
        with self.assertRaises(bundle.BundleError) as refused:
            self.get(source)
        self.assertIn("larger", str(refused.exception))

    def test_a_name_in_use_by_another_image_is_refused(self):
        source = self.bundle()
        other = self.root / "installed" / "mine"
        other.mkdir(parents=True)
        (other / "BUILD-INFO.json").write_text(json.dumps({"build_id": "20260101T000000Z-000000000000"}))
        with self.assertRaises(bundle.BundleError):
            self.get(source, name="mine")


class ManifestTests(Fixture):
    def manifest(self):
        return json.loads((self.bundle() / "release.json").read_text())

    def test_untrusted_manifests_are_refused(self):
        good = self.manifest()
        bundle.check_manifest(good)
        def variant(change):
            data = json.loads(json.dumps(good))
            change(data)
            return data
        bad = [
            variant(lambda d: d.update(format="mun-bundle/2")),
            variant(lambda d: d.update(build_id="../../etc")),
            variant(lambda d: d.update(release="no")),
            variant(lambda d: d["files"][0].update(name="../escape.qcow2")),
            variant(lambda d: d["files"][0].update(name="/abs.qcow2")),
            variant(lambda d: d["files"][0].update(name="sub/dir.qcow2")),
            variant(lambda d: d["files"][0].update(sha256="xyz")),
            variant(lambda d: d["files"][0].update(size=-1)),
            variant(lambda d: d["files"].append(dict(d["files"][0]))),
            variant(lambda d: next(e for e in d["files"] if e["kind"] == "card").update(card="other")),
            variant(lambda d: next(e for e in d["files"] if e["kind"] == "licence").update(name="LICENSE.exe")),
            variant(lambda d: [entry.update(kind="card") for entry in d["files"] if entry["kind"] == "image"]),
            variant(lambda d: d.update(files=[entry for entry in d["files"] if entry["kind"] != "image"])),
            [], "text", None,
        ]
        for data in bad:
            with self.assertRaises(bundle.BundleError, msg=str(data)[:120]):
                bundle.check_manifest(data)

    def test_locations_are_directories_manifests_or_web_addresses(self):
        self.assertFalse(bundle.Source("/some/dir").remote)
        self.assertEqual(bundle.Source("/some/dir").url("card-a.img.xz"), "/some/dir/card-a.img.xz")
        self.assertTrue(bundle.Source(r"C:\Users\me\bundle").base.endswith("release.json"), "a Windows drive is a path")
        web = bundle.Source("https://github.com/Play-MUN/mun-os/releases/download/v0/release.json")
        self.assertEqual(web.url("card-demo.img.xz"),
                         "https://github.com/Play-MUN/mun-os/releases/download/v0/card-demo.img.xz")
        self.assertTrue(bundle.Source("http://127.0.0.1:8000").insecure)
        with self.assertRaises(bundle.BundleError):
            bundle.Source("ftp://example.invalid/bundle")


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """A static server that honours `Range: bytes=N-`, as release hosts do."""
    served_ranges = []

    def log_message(self, *args):
        pass

    def send_head(self):
        requested = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not requested or not path.is_file():
            return super().send_head()
        start = int(requested.split("=")[1].rstrip("-"))
        RangeHandler.served_ranges.append(start)
        handle = path.open("rb")
        size = path.stat().st_size
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.send_header("Content-Length", str(size - start))
        self.end_headers()
        return handle


class HttpTests(Fixture):
    def serve(self, directory, handler=RangeHandler):
        factory = lambda *args, **kwargs: handler(*args, directory=str(directory), **kwargs)  # noqa: E731
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_a_bundle_is_fetched_over_http_with_a_warning_about_plain_http(self):
        url = self.serve(self.bundle())
        build, cards = self.get(url)
        self.assertEqual(len(cards), 2)
        self.assertTrue((build / IMAGE).is_file())
        self.assertTrue(any("plain http" in message for message in self.messages))

    def test_an_interrupted_download_resumes_where_it_stopped(self):
        source = self.bundle()
        url = self.serve(source)
        staging = self.root / "installed" / "d0929-123647.download"
        staging.mkdir(parents=True)
        whole = (source / IMAGE).read_bytes()
        (staging / (IMAGE + ".part")).write_bytes(whole[:5000])
        RangeHandler.served_ranges = []
        build, _ = self.get(url)
        self.assertEqual((build / IMAGE).read_bytes(), whole)
        self.assertIn(5000, RangeHandler.served_ranges)

    def test_a_server_without_ranges_sends_the_whole_file_and_it_still_verifies(self):
        source = self.bundle()
        url = self.serve(source, handler=http.server.SimpleHTTPRequestHandler)
        staging = self.root / "installed" / "d0929-123647.download"
        staging.mkdir(parents=True)
        (staging / (IMAGE + ".part")).write_bytes(b"stale bytes that are not the image")
        with contextlib.redirect_stderr(io.StringIO()):
            build, _ = self.get(url)
        self.assertEqual((build / IMAGE).read_bytes(), (source / IMAGE).read_bytes())


class CommandTests(Fixture):
    def test_a_second_download_waits_for_the_first_and_says_so(self):
        source = self.bundle()
        builds = self.root / "installed"
        with patch.object(mundev, "BUILDS_ROOT", builds), patch.object(mundev.vm, "CARD_ROOT", self.root / "cards"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            holder = subprocess.Popen([sys.executable, "-c", (
                "import fcntl, os, sys, time\n"
                f"os.makedirs({str(builds)!r}, exist_ok=True)\n"
                f"fd = os.open({str(builds / '.downloads.lock')!r}, os.O_RDWR | os.O_CREAT)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('held', flush=True)\n"
                "time.sleep(30)\n")], stdout=subprocess.PIPE, text=True)
            self.addCleanup(holder.kill)
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(mundev.vm.LabError) as busy:
                mundev.install(str(source))
            self.assertIn("busy", str(busy.exception))
            holder.kill()
            holder.wait()
            self.assertTrue(mundev.install(str(source)).is_dir(), "once the first has finished, the second proceeds")

    def test_bundle_makes_the_cards_from_the_builds_own_game_and_refuses_downloads(self):
        build = self.build()
        made = []

        def card_tool(command, **kwargs):
            made.append(command)
            Path(command[3]).write_bytes(b"card")
            return argparse.Namespace(returncode=0, stdout="", stderr="")
        with patch.object(mundev, "BUILDS_ROOT", self.root / "builds"), \
                patch.object(mundev.subprocess, "run", side_effect=card_tool), \
                contextlib.redirect_stdout(io.StringIO()):
            mundev.cmd_bundle(argparse.Namespace(build="b1", out=str(self.root / "out")))
            (build / "ORIGIN.json").write_text("{}")
            with self.assertRaises(mundev.vm.LabError):
                mundev.cmd_bundle(argparse.Namespace(build="b1", out=str(self.root / "out2")))
        info = json.loads((build / "BUILD-INFO.json").read_text())
        info["games"] = {"mun-collect": {}, "mygame": {}}
        (build / "BUILD-INFO.json").write_text(json.dumps(info))
        (build / "ORIGIN.json").unlink()
        with patch.object(mundev, "BUILDS_ROOT", self.root / "builds"), \
                patch.object(mundev.subprocess, "run", side_effect=card_tool), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(mundev.vm.LabError) as refused:
            mundev.cmd_bundle(argparse.Namespace(build="b1", out=str(self.root / "out3")))
        self.assertIn("mygame", str(refused.exception), "a build with a third-party game is never bundled")
        game = [command for command in made if "--variant" in command][0]
        self.assertIn(str(build / "games" / "mun-collect" / "mun-collect"), game)
        manifest = json.loads((self.root / "out" / "release.json").read_text())
        self.assertEqual(sorted(e["card"] for e in manifest["files"] if e["kind"] == "card"), ["collect", "demo"])


if __name__ == "__main__":
    unittest.main()
