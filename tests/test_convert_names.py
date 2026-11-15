"""mun-card convert never lets the card decide where anything is written on the host.

A card image is untrusted input: its directory entries can hold names no file
system call would create (with a slash, or the same name twice). The
conversion reads the image by inode number and writes nothing of it to the
host, so such names can only make it refuse the card.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import convert, image  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from mun_card.source import DebugfsSource  # noqa: E402
from mun_card.validate import validate_card  # noqa: E402


def have_e2fsprogs() -> bool:
    try:
        for tool in ("mke2fs", "debugfs", "e2fsck"):
            image.find_tool(tool)
        return True
    except CardError:
        return False


def envelope(n, fmt="neptune-save/1"):
    return (json.dumps({"format": fmt, "game": "mun.testcard", "content_version": "0.1.0", "schema": 1,
                        "payload": {"n": n}}) + "\n").encode()


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs, e2fsck) not installed")
class ConvertNameTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="convert-names-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.cards = self.base / "gamecards"; self.cards.mkdir()
        # The tool's temporary directory is <base>/a/b/mun-card-convert-*, so
        # a name climbing out of it still lands inside this test's own folder.
        self.tmp = self.base / "a" / "b"; self.tmp.mkdir(parents=True)
        patcher = unittest.mock.patch.object(tempfile, "tempdir", str(self.tmp))
        patcher.start(); self.addCleanup(patcher.stop)

    def snapshot(self):
        """Every path under the test folder outside the tool's temporary
        directory and the card folder, with what a file holds and its mode."""
        found = {}
        for path in sorted(self.base.rglob("*")):
            if self.tmp in path.parents or self.cards in path.parents:
                continue
            info = os.lstat(path)
            found[str(path.relative_to(self.base))] = (
                oct(info.st_mode), path.read_bytes() if path.is_file() and not path.is_symlink() else None)
        return found

    def crafted(self, prepare, renames):
        """An earlier-generation test card built from a staging tree, then
        with directory entries renamed in the image itself: `renames` maps a
        staged name to the same-length name the entry gets. Without metadata
        checksums, so the edited directory block is not refused for its sum."""
        with tempfile.TemporaryDirectory(dir=self.base / "a") as tmp:
            staging = Path(tmp) / "c"; staging.mkdir()
            image.populate(staging, "valid", naming="earlier")
            prepare(staging)
            path = self.cards / "old.img"
            subprocess.run([image.find_tool("mke2fs"), "-q", "-F", "-t", "ext4", "-O", "^metadata_csum",
                            "-d", str(staging), str(path), "8M"], check=True)
        data = bytearray(path.read_bytes())
        for staged, wanted in renames.items():
            self.assertEqual(len(staged), len(wanted))
            at = data.find(staged.encode())
            self.assertTrue(at > 0 and data.find(staged.encode(), at + 1) < 0, staged)
            data[at:at + len(staged)] = wanted.encode()
        path.write_bytes(data)
        return path

    def assert_refused(self, source, before):
        digest = image.sha256_file(source)
        with self.assertRaises(CardError) as ctx:
            convert.convert(source, self.cards / "new.img", self.cards, False)
        self.assertEqual(self.snapshot(), before, "nothing appears or changes outside the temporary directory")
        self.assertEqual(ctx.exception.code, "entry_name")
        self.assertEqual(list(self.tmp.iterdir()), [], "the temporary directory is removed")
        self.assertEqual(image.sha256_file(source), digest, "the source never changes")
        self.assertEqual(sorted(p.name for p in self.cards.iterdir() if p.suffix == ".img"), ["old.img"])
        return ctx.exception

    def test_a_name_with_slashes_writes_nothing_outside_and_is_refused(self):
        climbing = "../../../escaped.txt"
        source = self.crafted(lambda staging: (staging / ("P" * len(climbing))).write_text("the card's bytes\n"),
                              {"P" * len(climbing): climbing})
        self.assertIn(climbing, [name for name, _, _ in DebugfsSource(source, image.find_tool("debugfs")).list()])
        error = self.assert_refused(source, self.snapshot())
        self.assertIn(climbing, error.detail)
        self.assertFalse((self.base / "a" / "escaped.txt").exists())

    def test_a_name_with_slashes_does_not_replace_a_file_of_the_host(self):
        climbing = "../../../kept.txt"
        kept = self.base / "a" / "kept.txt"; kept.write_text("the user's own file\n"); os.chmod(kept, 0o600)
        def prepare(staging):
            (staging / ("P" * len(climbing))).write_text("the card's bytes\n")
            os.chmod(staging / ("P" * len(climbing)), 0o777)
        source = self.crafted(prepare, {"P" * len(climbing): climbing})
        self.assert_refused(source, self.snapshot())
        self.assertEqual(kept.read_text(), "the user's own file\n")
        self.assertEqual(os.stat(kept).st_mode & 0o777, 0o600)

    def test_the_same_name_twice_does_not_write_through_the_cards_link(self):
        # A link and a regular file under one name: extracted by name, the
        # file would be written through the link, wherever it points.
        victim = self.base / "victim.txt"; victim.write_text("the user's own file\n")
        for link_name, file_name in (("twin-a", "twin-b"), ("twin-b", "twin-a")):
            with self.subTest(renamed=file_name):
                def prepare(staging):
                    os.symlink(victim, staging / link_name)
                    (staging / file_name).write_text("the card's bytes\n")
                source = self.crafted(prepare, {file_name: link_name})
                self.assert_refused(source, self.snapshot())
                self.assertEqual(victim.read_text(), "the user's own file\n")
                source.unlink()

    def test_an_ordinary_card_still_converts_and_its_saves_carry_over(self):
        long_target = "content/" + "d" * 80 + "/missing"          # too long to be stored in the inode
        def prepare(staging):
            save_dir = staging / "saves" / "mun.testcard"; save_dir.mkdir(parents=True)
            (save_dir / "save.json").write_bytes(envelope(3))
            (save_dir / "save.json.prev").write_bytes(envelope(2))
            extra = staging / "content" / "deep" / "er"; extra.mkdir(parents=True)
            (extra / 'a "quoted" name with spaces.bin').write_bytes(os.urandom(5000))
            (extra / "empty").write_bytes(b"")
            os.symlink("../hello.txt", extra / "short-link")
            os.symlink(long_target, extra / "long-link")
        source = self.crafted(prepare, {})
        before, digest = self.snapshot(), image.sha256_file(source)
        result = convert.convert(source, self.cards / "new.img", self.cards, False)
        self.assertEqual(result["saves_converted"], ["save.json", "save.json.prev"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertEqual(image.sha256_file(source), digest)
        old = DebugfsSource(source, image.find_tool("debugfs"))
        new = DebugfsSource(self.cards / "new.img", image.find_tool("debugfs"))
        self.assertEqual(validate_card(new).naming, "mun")
        for name, n in (("save.json", 3), ("save.json.prev", 2)):
            document = json.loads(new.read(f"saves/mun.testcard/{name}", 4096))
            self.assertEqual((document["format"], document["payload"]), ("mun-save/1", {"n": n}))
        saves = {f"saves/mun.testcard/{name}" for name in ("save.json", "save.json.prev")}
        files = {path: size for path, size in old.walk() if path not in saves}
        files["mun.toml"] = files.pop("neptune.toml")
        self.assertEqual({path: size for path, size in new.walk() if path not in saves}, files)
        self.assertIn('content/deep/er/a "quoted" name with spaces.bin', files)
        self.assertEqual(result["entries_identical"], len(old.entries()) - 2, "all but the two saves")

    def test_a_link_that_differs_in_the_copy_is_noticed(self):
        # The comparison covers links although no host link is ever made.
        def prepare(staging):
            os.symlink("hello.txt", staging / "content" / "link")
        source = self.crafted(prepare, {})
        real = shutil.copyfile
        def retarget(src, dst, *args, **kwargs):
            real(src, dst, *args, **kwargs)
            if Path(dst).parent == self.cards:
                data = bytearray(Path(dst).read_bytes())
                at = data.find(b"hello.txt\0")           # the link's target, stored in its inode
                while at >= 0 and data[at - 1:at] != b"\0":
                    at = data.find(b"hello.txt\0", at + 1)
                self.assertGreater(at, 0)
                data[at:at + 5] = b"jello"
                Path(dst).write_bytes(data)
        with unittest.mock.patch.object(convert.shutil, "copyfile", retarget):
            with self.assertRaises(CardError) as ctx:
                convert.convert(source, self.cards / "new.img", self.cards, False)
        self.assertEqual(ctx.exception.code, "convert_failed")
        self.assertFalse((self.cards / "new.img").exists())


if __name__ == "__main__":
    unittest.main()
