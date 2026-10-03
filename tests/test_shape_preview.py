"""Host tests of the laboratory's MUN Shape preview (vm/shapepreview.py,
`./mun dev shape`): its cards are its own, each change is a new insertion
after the previous one left by the safe path, a game is never interrupted,
and a card it starts from is only read. The console is replaced by mocks;
the cards are real images (e2fsprogs)."""

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))
import shapepreview  # noqa: E402
from mun_card import cli as card_cli, image, shapetools  # noqa: E402

vm = shapepreview.vm
SAMPLES = ROOT / "examples" / "shape"


def have_e2fsprogs() -> bool:
    try:
        image.find_tool("mke2fs")
        image.find_tool("debugfs")
        return True
    except Exception:
        return False


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Console:
    """A stand-in for a running laboratory console: which cards are in, and
    whether a game is being played; records what the preview asks of it."""

    def __init__(self, cards=(), playing=False, refuse=0):
        self.cards = {name: {"slot": "card-slot-1"} for name in cards}
        self.playing = playing
        self.refuse = refuse           # releases to refuse before one succeeds
        self.shell_up = True
        self.calls = []

    def attach(self, args):
        self.calls.append(("attach", args.name))
        self.cards[args.name] = {"slot": "card-slot-1"}

    def detach(self, name, timeout=30.0, abrupt=False, **_):
        self.calls.append(("detach", name, abrupt))
        if self.refuse:
            self.refuse -= 1
            raise vm.LabError("guest refused to release: card_busy")
        del self.cards[name]

    def command(self, script, timeout=120.0, **_):
        if script.startswith("systemctl is-active"):
            self.calls.append(("shell?",))
            return SimpleNamespace(stdout="active\n" if self.shell_up else "activating\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="running\n" if self.playing else "idle\n", stderr="", returncode=0)

    def patches(self, card_root: Path):
        return [patch.object(vm, "read_pid", return_value=4242), patch.object(vm, "guest_ready", return_value=True),
                patch.object(vm, "load_state", side_effect=lambda: {"cards": dict(self.cards)}),
                patch.object(vm, "cmd_card_attach", side_effect=self.attach),
                patch.object(vm, "detach_card", side_effect=self.detach),
                patch.object(vm, "guest_command", side_effect=self.command),
                patch.object(vm, "reconcile_released", return_value=[]),
                patch.object(vm, "CARD_ROOT", card_root)]


class NamesAndFolderTests(unittest.TestCase):
    def test_two_cards_per_guest_within_the_laboratory_names(self):
        for guest in ("shape", "a1", "a-very-long-gues"):
            names = shapepreview.card_names(guest)
            self.assertEqual(len(set(names)), 2)
            for name in names:
                vm.card_serial(name)       # raises for a name the laboratory refuses
        self.assertNotEqual(shapepreview.card_names("one"), shapepreview.card_names("two"))

    def test_a_change_of_content_name_or_size_is_seen_and_hidden_files_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "shape.json").write_text("{}")
            before = shapepreview.folder_digest(folder)
            (folder / ".DS_Store").write_text("x")
            self.assertEqual(shapepreview.folder_digest(folder), before)
            (folder / "world").mkdir()
            (folder / "world" / "a.png").write_bytes(b"1")
            self.assertNotEqual(shapepreview.folder_digest(folder), before)


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-preview-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cards = self.tmp / "gamecards"
        self.cards.mkdir()
        self.build = self.tmp / "build"
        (self.build / "games" / "mun-collect").mkdir(parents=True)
        (self.build / "games" / "mun-collect" / "mun-collect").write_bytes(b"\x7fELF stand-in")
        self.folder = self.tmp / "pkg"
        shutil.copytree(SAMPLES / "sea", self.folder)
        self.out = io.StringIO()

    def preview(self, base=None):
        return shapepreview.Preview(self.folder, "shape", self.build, base, "Shape preview",
                                    say=lambda text: self.out.write(text + "\n"))

    def run_with(self, console: Console, action):
        with contextlib.ExitStack() as stack:
            for item in console.patches(self.cards):
                stack.enter_context(item)
            return action()

    def test_the_first_card_is_made_from_the_folder_and_inserted(self):
        console = Console()
        with patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0):
            self.run_with(console, lambda: self.preview().run(threading.Event(), watch=False, follow=False))
        self.assertEqual([call for call in console.calls if call[0] != "shell?"], [("attach", "pv0-shape")])
        self.assertTrue((self.cards / "pv0-shape.img").is_file())
        self.assertIn("LISTO", self.out.getvalue())

    def test_the_first_card_waits_for_the_shell_so_that_it_arrives(self):
        console = Console()
        console.shell_up = False
        stop = threading.Event()
        with contextlib.ExitStack() as stack:
            for item in console.patches(self.cards):
                stack.enter_context(item)
            stack.enter_context(patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0))
            worker = threading.Thread(target=self.preview().run, args=(stop, False, False))
            worker.start()
            time.sleep(2.5)
            self.assertNotIn(("attach", "pv0-shape"), console.calls, "nothing inserted before the shell runs")
            console.shell_up = True
            worker.join(15)
        self.assertIn(("attach", "pv0-shape"), console.calls)

    def test_it_does_not_start_beside_another_card_and_touches_none(self):
        (self.cards / "mine.img").write_bytes(b"a player's card")
        console = Console(cards=["mine"])
        with patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0):
            self.run_with(console, lambda: self.preview().run(threading.Event(), watch=False, follow=False))
        self.assertEqual([call for call in console.calls if call[0] != "shell?"], [])
        self.assertEqual((self.cards / "mine.img").read_bytes(), b"a player's card")
        self.assertIn("no other card", self.out.getvalue())

    def test_a_change_waits_while_a_game_is_played(self):
        console = Console(cards=["pv0-shape"], playing=True)
        changed = self.run_with(console, lambda: self.preview().change())
        self.assertFalse(changed)
        self.assertEqual(console.calls, [])
        self.assertIn("waits for it to end", self.out.getvalue())

    def test_a_change_is_a_new_card_after_the_previous_left_safely(self):
        console = Console(cards=["pv0-shape"])
        (self.cards / "pv0-shape.img").write_bytes(b"in the console")
        self.assertTrue(self.run_with(console, lambda: self.preview().change()))
        self.assertEqual(console.calls, [("detach", "pv0-shape", False), ("attach", "pv1-shape")])
        self.assertFalse((self.cards / "pv0-shape.img").exists(), "the card that left is deleted")
        self.assertTrue((self.cards / "pv1-shape.img").is_file())

    def test_a_release_the_console_refuses_is_tried_again_and_nothing_is_forced(self):
        console = Console(cards=["pv0-shape"], refuse=1)
        preview = self.preview()
        self.assertFalse(self.run_with(console, preview.change))
        self.assertEqual(console.calls, [("detach", "pv0-shape", False)])
        self.assertTrue(self.run_with(console, preview.change))
        self.assertEqual(console.calls[1:], [("detach", "pv0-shape", False), ("attach", "pv1-shape")])

    def test_a_folder_the_console_would_not_use_leaves_the_card_in(self):
        (self.folder / "shape.json").write_text("{ not json")
        console = Console(cards=["pv0-shape"])
        self.assertTrue(self.run_with(console, lambda: self.preview().change()))
        self.assertEqual(console.calls, [])
        self.assertIn("would not be used", self.out.getvalue())

    def test_watching_takes_a_change_once_the_folder_is_still(self):
        console = Console()
        stop = threading.Event()
        preview = self.preview()
        with contextlib.ExitStack() as stack:
            for item in console.patches(self.cards):
                stack.enter_context(item)
            stack.enter_context(patch.object(shapepreview, "POLL_SECONDS", 0.05))
            stack.enter_context(patch.object(shapepreview, "SETTLE_SECONDS", 0.1))
            stack.enter_context(patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0))
            worker = threading.Thread(target=preview.run, args=(stop, True, True))
            worker.start()
            deadline = time.monotonic() + 30
            while ("attach", "pv0-shape") not in console.calls and time.monotonic() < deadline:
                time.sleep(0.05)
            document = (self.folder / "shape.json").read_text().replace('"seconds": 3.2', '"seconds": 2.0')
            (self.folder / "shape.json").write_text(document)
            while ("attach", "pv1-shape") not in console.calls and time.monotonic() < deadline:
                time.sleep(0.05)
            stop.set()
            worker.join(10)
        self.assertEqual([call for call in console.calls if call[0] != "shell?"],
                         [("attach", "pv0-shape"), ("detach", "pv0-shape", False), ("attach", "pv1-shape")])

    def test_a_base_card_is_copied_and_never_written(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = card_cli.main(["create", str(self.cards / "mygame.img"), "--shape", str(SAMPLES / "paper")])
        self.assertEqual(code, 0)
        before = sha256(self.cards / "mygame.img")
        console = Console()
        with patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0):
            self.run_with(console, lambda: self.preview(base=self.cards / "mygame.img")
                          .run(threading.Event(), watch=False, follow=False))
        self.assertEqual(sha256(self.cards / "mygame.img"), before)
        self.assertEqual([call for call in console.calls if call[0] != "shell?"], [("attach", "pv0-shape")])
        from mun_card import shape
        from mun_card.source import DebugfsSource
        source = DebugfsSource(self.cards / "pv0-shape.img", image.find_tool("debugfs"))
        self.assertEqual(shape.check_card(source, "content").shape["transition"]["in"], "tide",
                         "the copy carries the folder's package (sea), not the base card's (paper)")

    def test_at_the_end_the_card_leaves_safely_unless_a_game_is_played(self):
        (self.cards / "other.img").write_bytes(b"not the preview's")
        console = Console(cards=["pv1-shape"])
        (self.cards / "pv1-shape.img").write_bytes(b"in")
        self.run_with(console, lambda: self.preview().finish())
        self.assertEqual(console.calls, [("detach", "pv1-shape", False)])
        self.assertFalse((self.cards / "pv1-shape.img").exists())
        self.assertTrue((self.cards / "other.img").exists())
        console = Console(cards=["pv0-shape"], playing=True)
        (self.cards / "pv0-shape.img").write_bytes(b"in")
        self.run_with(console, lambda: self.preview().finish())
        self.assertEqual(console.calls, [])
        self.assertTrue((self.cards / "pv0-shape.img").exists(), "a card in use stays")


if __name__ == "__main__":
    unittest.main()
