"""The options mun-cardd mounts a card with, and what the offline check reads
of a card's own error policy. No root and no real mount: the commands the
service would run are captured."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from mun_card import ext4, image  # noqa: E402
from mun_card.errors import CardError  # noqa: E402


def superblock_image(path: Path, errors: int, state: int = ext4.STATE_CLEAN) -> Path:
    """The first two kibibytes of an ext4 image: magic, state and s_errors."""
    raw = bytearray(4096)
    raw[1024 + 0x38:1024 + 0x3A] = ext4.EXT4_MAGIC.to_bytes(2, "little")
    raw[1024 + 0x3A:1024 + 0x3C] = state.to_bytes(2, "little")
    raw[1024 + 0x3C:1024 + 0x3E] = errors.to_bytes(2, "little")
    path.write_bytes(bytes(raw))
    return path


class MountErrorPolicyTests(unittest.TestCase):
    """A card's superblock chooses what ext4 does on a metadata error unless
    the mount says otherwise, and one of its choices stops the kernel. Every
    mount and remount the service performs names the policy itself."""

    def setUp(self):
        self.calls = []
        self.mounter = cardd.Mounter(Path(tempfile.mkdtemp()))
        self.card = cardd.Card(slot="c1", device="vdc", serial="NPT-game")

        def run(cmd, **kwargs):
            self.calls.append(cmd)
            return Mock(returncode=0, stderr="", stdout="")
        for p in (patch.object(cardd.subprocess, "run", run),
                  patch.object(cardd.ext4, "check_mountable", lambda path: {})):
            p.start(); self.addCleanup(p.stop)

    def options(self, cmd):
        self.assertEqual(cmd[0], "mount")
        return cmd[cmd.index("-o") + 1].split(",")

    def test_the_first_mount_is_read_only_and_fixes_the_error_policy(self):
        self.card.mount = self.mounter.mount(self.card, "/dev/vdc")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:3], ["mount", "-t", "ext4"])
        self.assertEqual(self.calls[0][-2:], ["/dev/vdc", self.card.mount])
        self.assertEqual(self.options(self.calls[0]), ["ro", "nosuid", "nodev", "noexec", "errors=remount-ro"])

    def test_each_remount_carries_the_same_policy(self):
        self.card.mount = self.mounter.mount(self.card, "/dev/vdc")
        self.mounter.remount(self.card, rw=True)
        self.mounter.remount(self.card, rw=False)
        writable, read_only = (self.options(cmd) for cmd in self.calls[1:])
        self.assertEqual(writable, ["remount", "rw", "nosuid", "nodev", "noexec", "errors=remount-ro"])
        self.assertEqual(read_only, ["remount", "ro", "nosuid", "nodev", "noexec", "errors=remount-ro"])
        for cmd in self.calls[1:]:
            self.assertEqual(cmd[-1], self.card.mount)

    def test_no_command_the_service_runs_can_ask_for_a_panic(self):
        self.card.mount = self.mounter.mount(self.card, "/dev/vdc")
        self.mounter.remount(self.card, rw=True)
        self.mounter.remount(self.card, rw=False)
        self.mounter.unmount(self.card)
        mounts = [cmd for cmd in self.calls if cmd[0] == "mount"]
        self.assertEqual(len(mounts), 3)
        for cmd in mounts:
            policies = [option for option in self.options(cmd) if option.startswith("errors=")]
            self.assertEqual(policies, ["errors=remount-ro"], cmd)

    def test_a_save_remounts_with_the_policy_both_ways(self):
        # The whole write, as the service performs it on a real directory.
        root = Path(tempfile.mkdtemp())
        (root / "saves").mkdir()
        info = {"id": "mun.collect", "saves": "saves", "version": "0.1.0"}
        card = cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True,
                          info=info, mount=str(root))
        outcome = cardd._write_save(self.mounter, card, info, b'{"format": "neptune-save/1", "payload": {}}\n',
                                    0, lambda: 0)
        self.assertTrue(outcome["ok"])
        self.assertEqual([self.options(cmd)[:2] for cmd in self.calls], [["remount", "rw"], ["remount", "ro"]])
        for cmd in self.calls:
            self.assertIn("errors=remount-ro", self.options(cmd))


class SuperblockErrorPolicyTests(unittest.TestCase):
    """The offline check reports the policy the card asks for, as the kernel
    would read it, and does not refuse a card for it: the console's mount
    overrides it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_the_policy_is_reported_as_the_kernel_reads_it(self):
        for value, expected in ((1, "continue"), (2, "remount-ro"), (3, "panic"), (0, "remount-ro"), (9, "remount-ro")):
            with self.subTest(s_errors=value):
                info = ext4.read_superblock(superblock_image(self.tmp / f"sb-{value}.img", value))
                self.assertEqual(info["errors_policy"], expected)

    def test_a_clean_card_that_asks_for_a_panic_is_still_mountable_and_says_so(self):
        info = ext4.check_mountable(superblock_image(self.tmp / "panic.img", 3))
        self.assertEqual(info["errors_policy"], "panic")

    def test_a_card_marked_with_errors_is_still_refused_whatever_its_policy(self):
        for value in (1, 2, 3):
            with self.subTest(s_errors=value):
                path = superblock_image(self.tmp / f"errors-{value}.img", value,
                                        state=ext4.STATE_CLEAN | ext4.STATE_ERRORS)
                with self.assertRaises(CardError) as ctx:
                    ext4.check_mountable(path)
                self.assertEqual(ctx.exception.code, "image_has_errors")

    def test_the_policy_of_a_real_image_is_read_where_tune2fs_writes_it(self):
        try:
            mke2fs, tune2fs = image.find_tool("mke2fs"), image.find_tool("tune2fs")
        except CardError:
            self.skipTest("e2fsprogs (mke2fs, tune2fs) not installed")
        import subprocess
        path = self.tmp / "real.img"
        subprocess.run([mke2fs, "-q", "-F", "-t", "ext4", str(path), "8M"], check=True, capture_output=True)
        self.assertEqual(ext4.check_mountable(path)["errors_policy"], "continue")
        for policy in ("panic", "remount-ro", "continue"):
            subprocess.run([tune2fs, "-e", policy, str(path)], check=True, capture_output=True)
            self.assertEqual(ext4.check_mountable(path)["errors_policy"], policy)


class RefusedCardIsNamedTests(unittest.TestCase):
    """A card the service refuses is shown as invalid by the shell; the
    service's own journal must name it and say why, once per insertion."""

    def setUp(self):
        import io
        from contextlib import redirect_stdout
        from test_cardd import Harness
        self.tmp = Path(tempfile.mkdtemp())
        self.commands = []

        def run(cmd, **kwargs):
            self.commands.append(cmd)
            return Mock(returncode=0, stderr="", stdout="")
        p = patch.object(cardd.subprocess, "run", run); p.start(); self.addCleanup(p.stop)
        self.journal = io.StringIO()
        silenced = redirect_stdout(self.journal); silenced.__enter__(); self.addCleanup(silenced.__exit__, None, None, None)
        self.Harness = Harness

    def lines(self):
        return [line for line in self.journal.getvalue().splitlines() if "refused" in line]

    def insert(self, image, validator=None):
        h = self.Harness(mounter=cardd.Mounter(self.tmp / "mounts"), validator=validator)
        h.manager.device_added("vdc", "NPT-card01", str(image))
        if h.manager.cards["vdc"].state == "reading":
            h.pump()
        return h

    def test_a_card_marked_with_errors_is_named_with_its_reason_once(self):
        image = superblock_image(self.tmp / "errors.img", 1, state=ext4.STATE_CLEAN | ext4.STATE_ERRORS)
        h = self.insert(image)
        card = h.manager.snapshot()["cards"][0]
        self.assertEqual((card["state"], card["error"]["code"]), ("invalid", "image_has_errors"))
        self.assertEqual(self.commands, [], "refused before any mount")
        self.assertEqual(len(self.lines()), 1)
        for word in ("c1", "vdc", "NPT-card01", "image_has_errors", "marcado con errores", "errores anotados"):
            self.assertIn(word, self.lines()[0])
        h.manager.device_added("vdc", "NPT-card01", str(image))       # what every rescan does
        self.assertEqual(len(self.lines()), 1, "once per insertion")

    def test_the_reason_tells_recorded_errors_from_an_unclean_unmount(self):
        for state, expected in ((ext4.STATE_CLEAN | ext4.STATE_ERRORS, "errores anotados"), (ext4.STATE_ERRORS, "errores anotados"),
                                (0, "no se desmontó limpiamente")):
            with self.subTest(state=state):
                with self.assertRaises(CardError) as ctx:
                    ext4.check_mountable(superblock_image(self.tmp / "state.img", 1, state=state))
                self.assertEqual(ctx.exception.code, "image_has_errors")
                self.assertIn(expected, ctx.exception.detail)

    def test_a_card_refused_by_validation_is_named_too(self):
        def validator(mount):
            raise CardError("manifest_missing", "No se encontró mun.toml ni neptune.toml en la tarjeta")
        h = self.insert(superblock_image(self.tmp / "clean.img", 1), validator)
        self.assertEqual(h.manager.snapshot()["cards"][0]["state"], "invalid")
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("manifest_missing", self.lines()[0]); self.assertIn("vdc", self.lines()[0])

    def test_a_valid_card_is_not_reported_as_refused(self):
        h = self.insert(superblock_image(self.tmp / "clean.img", 1))
        self.assertEqual(h.manager.snapshot()["cards"][0]["state"], "valid")
        self.assertEqual(self.lines(), [])


if __name__ == "__main__":
    unittest.main()
