"""A card image does not carry who made it, nor on what machine.

`mke2fs -d` copies each staged file's owner and mode into the image, so the
same source gave another image (and another card hash) for another user or
another umask. Every entry is owned by 0:0 and has the mode its kind calls
for, whoever packages it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import image  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from mun_card.source import DebugfsSource  # noqa: E402


def have_e2fsprogs() -> bool:
    try:
        for tool in ("mke2fs", "debugfs", "dumpe2fs"):
            image.find_tool(tool)
        return True
    except CardError:
        return False


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="card-owner-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.game = self.base / "mun-collect"
        self.game.write_bytes(b"\x7fELF a game")

    def card(self, name, umask=None):
        """A game card with a data tree, packaged under `umask`."""
        previous = os.umask(umask) if umask is not None else None
        try:
            staging = self.base / f"staging-{name}"
            staging.mkdir()
            image.populate(staging, "game", "Prueba", self.game)
            (staging / "content" / "data" / "levels").mkdir(parents=True)
            (staging / "content" / "data" / "levels" / "one.bin").write_bytes(b"level" * 100)
            path = self.base / f"{name}.img"
            image.create_image(path, staging, 8)
        finally:
            if previous is not None:
                os.umask(previous)
        return path

    def entries(self, path):
        """Every entry of the image, lost+found aside: path -> (mode, uid, gid)."""
        debugfs = image.find_tool("debugfs")
        found, pending = {}, [""]
        while pending:
            directory = pending.pop()
            listing = subprocess.run([debugfs, "-R", f'ls -l "/{directory}"', str(path)], capture_output=True,
                                     text=True, check=True).stdout
            for line in listing.splitlines():
                match = DebugfsSource._LS.match(line)
                if not match or match.group(7).strip() in ("..", "lost+found") or (directory and match.group(7).strip() == "."):
                    continue
                name = match.group(7).strip()
                entry = f"{directory}/{name}".strip("/") if name != "." else "."
                found[entry] = (int(match.group(2), 8), int(match.group(4)), int(match.group(5)))
                if name != "." and int(match.group(2), 8) & 0o170000 == 0o040000:
                    pending.append(entry)
        return found

    def test_every_entry_is_owned_by_root_whoever_packages_the_card(self):
        entries = self.entries(self.card("a"))
        self.assertIn("content/data/levels/one.bin", entries)
        self.assertIn("content/mun-collect", entries)
        self.assertEqual({path: owner[1:] for path, owner in entries.items() if owner[1:] != (0, 0)}, {})

    def test_the_same_source_gives_the_same_image_under_any_umask(self):
        digests = {oct(umask): image.sha256_file(self.card(f"u{umask:o}", umask)) for umask in (0o022, 0o002, 0o077)}
        self.assertEqual(len(set(digests.values())), 1, digests)

    def test_modes_are_what_each_kind_of_entry_calls_for(self):
        for umask in (0o022, 0o077):
            with self.subTest(umask=oct(umask)):
                entries = self.entries(self.card(f"m{umask:o}", umask))
                modes = {path: mode & 0o7777 for path, (mode, _, _) in entries.items()}
                self.assertEqual(modes["content/mun-collect"], 0o755, "the game stays executable")
                self.assertEqual(modes["content/data/levels/one.bin"], 0o644)
                self.assertEqual(modes["mun.toml"], 0o644)
                for directory in (".", "content", "content/data/levels", "saves"):
                    self.assertEqual(modes[directory], 0o755, directory)

    def test_two_builds_in_different_seconds_are_still_the_same(self):
        first = image.sha256_file(self.card("t1"))
        import time
        time.sleep(1.1)
        self.assertEqual(image.sha256_file(self.card("t2")), first)


if __name__ == "__main__":
    unittest.main()
