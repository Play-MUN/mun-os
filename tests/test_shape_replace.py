"""Replacing a card image's MUN Shape package with debugfs (the laboratory's
preview on a disposable copy): every name reaches debugfs as one argument,
so a name with a space removes and writes the file it names, not pieces of
it, and a name debugfs cannot take as one argument is refused."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import shapetools  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from mun_card.image import find_tool  # noqa: E402
from mun_card.source import DebugfsSource  # noqa: E402

try:
    MKE2FS, DEBUGFS = find_tool("mke2fs"), find_tool("debugfs")
except CardError:
    MKE2FS = DEBUGFS = None


@unittest.skipIf(MKE2FS is None, "e2fsprogs is not installed")
class ReplacePackageNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mun shape "))       # a space in the host path too
        tree = self.tmp / "tree"
        (tree / "content" / "mun-shape" / "world").mkdir(parents=True)
        (tree / "content" / "mun-shape" / "world" / "old layer.png").write_bytes(b"old")
        (tree / "content" / "mun-shape" / "shape.json").write_bytes(b"{}")
        self.image = self.tmp / "card.img"
        subprocess.run([MKE2FS, "-q", "-F", "-t", "ext4", "-d", str(tree), str(self.image), "8M"],
                       check=True, capture_output=True)
        self.staged = self.tmp / "staged"
        (self.staged / "world").mkdir(parents=True)
        (self.staged / "world" / "new layer.png").write_bytes(b"new!")
        (self.staged / "shape.json").write_bytes(b'{"a": 1}')

    def names(self, relative):
        return sorted(name for name, _, _ in DebugfsSource(self.image, DEBUGFS).list(relative))

    def test_names_with_spaces_are_removed_and_written_whole(self):
        shapetools.replace_package(self.image, "content", self.staged)
        self.assertEqual(self.names("content/mun-shape/world"), ["new layer.png"], "the old file is gone")
        self.assertEqual(self.names("content/mun-shape"), ["shape.json", "world"])

    def test_a_name_debugfs_cannot_take_whole_is_refused_before_anything_is_written(self):
        (self.staged / 'quote".png').write_bytes(b"x")
        before = self.image.read_bytes()
        with self.assertRaises(CardError) as refused:
            shapetools.replace_package(self.image, "content", self.staged)
        self.assertEqual(refused.exception.code, "shape_write_failed")
        self.assertEqual(self.image.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
