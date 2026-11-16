"""A card image a live guest holds is never replaced under it.

The attach registry (.local/gamecards/attached.json) is where every guest
records the cards it holds. Replacing an image by rename leaves a running
guest on the unlinked file: what it saves there is gone when it lets go.
Both tools that replace a card image ask the registry first.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

from mun_card import cli, image  # noqa: E402
from mun_card.errors import CardError  # noqa: E402

SPEC = importlib.util.spec_from_file_location("munvm_card_replace", ROOT / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)

DEAD_PID = 2 ** 22 + 7          # above every pid a host hands out


def have_e2fsprogs() -> bool:
    try:
        for tool in ("mke2fs", "debugfs", "dumpe2fs"):
            image.find_tool(tool)
        return True
    except CardError:
        return False


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="card-replace-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cards = self.root / "gamecards"
        self.cards.mkdir()

    def held(self, name, pid, **more):
        (self.cards / "attached.json").write_text(json.dumps({name: dict({"instance": "bb", "pid": pid}, **more)}))

    def registry(self):
        return json.loads((self.cards / "attached.json").read_text())


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class CardToolTests(Fixture):
    def create(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, "CARD_ROOT", self.cards), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["create", *arguments, "--size", "8"])
        return status, err.getvalue()

    def test_create_force_refuses_a_card_a_live_guest_holds(self):
        self.assertEqual(self.create("collect")[0], 0)
        card = self.cards / "collect.img"
        before = (os.stat(card).st_ino, image.sha256_file(card))
        self.held("collect", os.getpid(), attachment="x")
        status, said = self.create("collect", "--force", "--title", "Another")
        self.assertEqual(status, 1)
        self.assertIn("card_in_use", said)
        self.assertEqual((os.stat(card).st_ino, image.sha256_file(card)), before, "the guest's image is the same file")
        self.assertEqual(self.registry(), {"collect": {"instance": "bb", "pid": os.getpid(), "attachment": "x"}})
        self.assertEqual(sorted(p.name for p in self.cards.glob("*.img*")), ["collect.img"], "nothing half-made left")

    def test_create_force_replaces_a_card_no_live_guest_holds(self):
        self.assertEqual(self.create("collect")[0], 0)
        card = self.cards / "collect.img"
        first = image.sha256_file(card)
        self.held("collect", DEAD_PID)                  # its QEMU is gone: a stale record
        self.assertEqual(self.create("collect", "--force", "--title", "Another")[0], 0)
        self.assertNotEqual(image.sha256_file(card), first)
        self.assertNotIn("collect", self.registry(), "the tool's own hold is released")
        self.held("other", os.getpid())                 # another card held: this one is free
        self.assertEqual(self.create("collect", "--force")[0], 0)
        self.assertEqual(self.registry(), {"other": {"instance": "bb", "pid": os.getpid()}})

    def test_a_guest_cannot_attach_a_card_while_it_is_replaced(self):
        self.assertEqual(self.create("collect")[0], 0)
        refused = []
        real = image.create_image

        def attach_meanwhile(*args, **kwargs):
            # The reservation's pid is this test's own process, standing in
            # for the run of mun-card that holds it.
            with patch.object(vm, "ATTACH_REGISTRY", self.cards / "attached.json"), \
                    patch.object(vm, "INSTANCE", "aa"), patch.object(vm, "read_pid", return_value=os.getpid()), \
                    patch.object(vm.host, "process_command", return_value="python3 ./mun card create --force"):
                with self.assertRaises(vm.LabError) as ctx:
                    vm.registry_claim("collect")
                refused.append(str(ctx.exception))
            return real(*args, **kwargs)
        with patch.object(image, "create_image", attach_meanwhile):
            self.assertEqual(self.create("collect", "--force")[0], 0)
        self.assertEqual(len(refused), 1)
        self.assertIn("being replaced", refused[0])

    def test_an_image_outside_the_lab_does_not_touch_the_registry(self):
        elsewhere = self.root / "elsewhere" / "collect.img"
        elsewhere.parent.mkdir()
        self.held("collect", os.getpid())
        self.assertEqual(self.create(str(elsewhere))[0], 0)
        self.assertEqual(self.create(str(elsewhere), "--force")[0], 0, "the registry names lab cards, not this file")
        self.assertEqual(self.registry(), {"collect": {"instance": "bb", "pid": os.getpid()}})


class LabToolTests(Fixture):
    def live(self, *words):
        """A process running for the test whose command line holds `words`,
        as a guest's QEMU names its pidfile and a card tool its own name."""
        import subprocess
        # "; :" keeps the shell from replacing itself with sleep, which would drop the words.
        process = subprocess.Popen(["sh", "-c", "sleep 60; :", *map(str, words)])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def setUp(self):
        super().setUp()
        guest = self.root / "guests" / "aa"
        guest.mkdir(parents=True)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in (("CARD_ROOT", self.cards), ("ATTACH_REGISTRY", self.cards / "attached.json"),
                            ("INSTANCE", "aa"), ("PATHS", {key: guest / key for key in vm.PATHS}),
                            ("GUESTS_ROOT", self.root / "guests")):
            self.patches.enter_context(patch.object(vm, name, value))
        self.patches.enter_context(patch.object(vm, "read_pid", return_value=None))     # this guest is off
        self.patches.enter_context(patch.object(vm, "which", side_effect=lambda name: name))
        self.run = self.patches.enter_context(patch.object(vm, "run"))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        (self.cards / "collect.img").write_bytes(b"a card with saves")

    # A pid in the registry may belong to another process once its guest is
    # gone: only the guest's QEMU, which names its instance's pidfile, or the
    # card tool that reserved it, still holds the card.
    def test_a_guest_record_whose_pid_now_runs_something_else_is_stale(self):
        self.held("collect", os.getpid(), attachment="x")       # alive, but this test runner
        with patch.object(vm, "read_pid", return_value=4242):
            vm.registry_claim("collect", "mine")
        self.assertEqual(self.registry()["collect"]["instance"], "aa")
        vm.cmd_card_create(argparse.Namespace(name="other", size="1G", force=True))
        self.run.assert_called()

    def test_a_reservation_whose_pid_now_runs_something_else_is_stale(self):
        self.held("collect", os.getpid(), instance="mun-card convert", purpose="convert")
        vm.registry_claim("collect", "mine")
        self.assertEqual(self.registry()["collect"]["instance"], "aa")

    def test_the_guest_that_holds_it_is_still_seen(self):
        self.held("collect", self.live(self.root / "guests" / "bb" / "qemu.pid"), attachment="x")
        with self.assertRaises(vm.LabError):
            vm.registry_claim("collect", "mine")

    def test_card_create_force_refuses_a_card_another_live_guest_holds(self):
        self.held("collect", self.live(self.root / "guests" / "bb" / "qemu.pid"), attachment="x")
        with self.assertRaises(vm.LabError) as refused:
            vm.cmd_card_create(argparse.Namespace(name="collect", size="1G", force=True))
        self.assertIn("instance bb", str(refused.exception))
        self.run.assert_not_called()
        self.assertEqual((self.cards / "collect.img").read_bytes(), b"a card with saves")

    def test_card_create_force_refuses_a_card_being_converted(self):
        self.held("collect", self.live("mun-card", "convert"), instance="mun-card convert", purpose="convert")
        with self.assertRaises(vm.LabError):
            vm.cmd_card_create(argparse.Namespace(name="collect", size="1G", force=True))
        self.run.assert_not_called()

    def test_card_create_force_replaces_a_card_no_live_guest_holds(self):
        self.held("collect", DEAD_PID)
        vm.cmd_card_create(argparse.Namespace(name="collect", size="1G", force=True))
        self.assertEqual(self.run.call_args.args[0][:2], ["qemu-img", "create"])
        (self.cards / "attached.json").unlink()
        vm.cmd_card_create(argparse.Namespace(name="collect", size="1G", force=True))
        self.assertEqual(self.run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
