"""Host tests of the laboratory's MUN Shape preview (vm/shapepreview.py,
`./mun dev shape`): its cards are its own, known by the files it made, not by
their names; a file it did not make keeps its bytes and its identity; one
preview per console; each change is a new insertion after the previous one
left by the safe path, once the shell is there to see it arrive; a game is
never interrupted; a card it starts from is only read; and a console whose
build records no MUN Shape is refused. The console is replaced by mocks; the
cards are real images (e2fsprogs)."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
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
from mun_card import cli as card_cli, image  # noqa: E402

vm = shapepreview.vm
SAMPLES = ROOT / "examples" / "shape"


def have_e2fsprogs() -> bool:
    try:
        image.find_tool("mke2fs")
        image.find_tool("debugfs")
        return True
    except Exception:
        return False


def fingerprint(path: Path):
    """A file's bytes and identity: what a file the preview did not make keeps."""
    info = os.lstat(path)
    return hashlib.sha256(path.read_bytes()).hexdigest(), info.st_dev, info.st_ino


class Console:
    """A stand-in for a running laboratory console: which cards are in, and
    whether a game is being played; records what the preview asks of it.
    `registry` stands for the attach registry all guests share."""

    def __init__(self, cards=(), playing=False, refuse=0, registry=None):
        self.cards = {name: {"slot": "card-slot-1"} for name in cards}
        self.playing = playing
        self.refuse = refuse           # releases to refuse before one succeeds
        self.shell_up = True
        self.registry = registry or {}
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
        if "ActiveEnterTimestampMonotonic" in script:
            self.calls.append(("shell?",))
            return SimpleNamespace(stdout="active 10.0\n" if self.shell_up else "inactive 0\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="running\n" if self.playing else "idle\n", stderr="", returncode=0)

    def patches(self):
        return [patch.object(vm, "read_pid", return_value=4242), patch.object(vm, "guest_ready", return_value=True),
                patch.object(vm, "load_state", side_effect=lambda: {"cards": dict(self.cards)}),
                patch.object(vm, "load_registry", side_effect=lambda: dict(self.registry)),
                patch.object(vm, "pid_alive", return_value=True),
                patch.object(vm, "cmd_card_attach", side_effect=self.attach),
                patch.object(vm, "detach_card", side_effect=self.detach),
                patch.object(vm, "guest_command", side_effect=self.command),
                patch.object(vm, "reconcile_released", return_value=[])]

    def visible(self):
        return [call for call in self.calls if call[0] != "shell?"]


class NamesAndFolderTests(unittest.TestCase):
    def test_two_cards_per_guest_within_the_laboratory_names(self):
        for guest in ("shape", "a1", "a-very-long-gues"):
            names = shapepreview.card_names(guest)
            self.assertEqual(len(set(names)), 2)
            for name in names:
                vm.card_serial(name)       # raises for a name the laboratory refuses
        self.assertNotEqual(shapepreview.card_names("one"), shapepreview.card_names("two"))
        self.assertEqual(shapepreview.card_names("shape"), ["pv0-shape", "pv1-shape"])

    def test_long_guest_names_that_share_their_start_get_different_cards(self):
        # Cut to 16 characters, both would be pv0-lab-console-; the digest keeps them apart.
        first, second = shapepreview.card_names("lab-console-one1"), shapepreview.card_names("lab-console-one2")
        self.assertFalse(set(first) & set(second))
        self.assertTrue(all(len(name) <= 16 for name in first + second))

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


class BuildTests(unittest.TestCase):
    """Which console the preview takes: one whose build records the MUN Shape
    this checkout writes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-builds-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def build(self, name, shape=None):
        build = self.tmp / name
        build.mkdir()
        info = {"source": {"describe": "abc1234"}, "built": {"finished": "2026-10-01T00:00:00Z"}}
        if shape:
            info["shape"] = {"format": shape}
        (build / "BUILD-INFO.json").write_text(json.dumps(info))
        return build

    def test_a_build_that_records_this_checkouts_shape_is_taken(self):
        new = self.build("new", "mun-shape/1")
        self.assertEqual(shapepreview.build_shape(new), "mun-shape/1")
        shapepreview.check_build("shape", new, new, recorded=True)

    def test_an_existing_guest_of_an_older_build_is_refused_with_the_way_to_a_new_one(self):
        old, new = self.build("s3u"), self.build("s4w", "mun-shape/1")
        with self.assertRaises(vm.LabError) as refused:
            shapepreview.check_build("consola", old, new, recorded=True)
        text = str(refused.exception)
        self.assertIn("guest consola runs build s3u", text)
        self.assertIn("records no MUN Shape", text)
        self.assertIn("build s4w", text)
        self.assertIn("./mun dev vm consola destroy --yes", text)

    def test_without_any_build_that_records_it_the_way_is_a_new_image(self):
        old = self.build("dev2")
        with self.assertRaises(vm.LabError) as refused:
            shapepreview.check_build("shape", old, old, recorded=False)
        self.assertIn("./mun dev build", str(refused.exception))
        self.assertNotIn("destroy", str(refused.exception), "no guest exists yet to remove")

    def test_another_major_version_is_refused(self):
        other = self.build("future", "mun-shape/2")
        with self.assertRaises(vm.LabError) as refused:
            shapepreview.check_build("shape", other, other, recorded=False)
        self.assertIn("reads mun-shape/2", str(refused.exception))


@unittest.skipUnless(have_e2fsprogs(), "e2fsprogs (mke2fs, debugfs) not installed")
class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shape-preview-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cards = self.tmp / "gamecards"
        self.cards.mkdir()
        card_root = patch.object(vm, "CARD_ROOT", self.cards)
        card_root.start()
        self.addCleanup(card_root.stop)
        self.build = self.tmp / "build"
        (self.build / "games" / "mun-collect").mkdir(parents=True)
        (self.build / "games" / "mun-collect" / "mun-collect").write_bytes(b"\x7fELF stand-in")
        self.folder = self.tmp / "pkg"
        shutil.copytree(SAMPLES / "sea", self.folder)
        self.out = io.StringIO()

    def preview(self, base=None, guest="shape"):
        preview = shapepreview.Preview(self.folder, guest, self.build, base, "Shape preview",
                                       say=lambda text: self.out.write(text + "\n"))
        self.addCleanup(preview.release)
        return preview

    def run_with(self, console: Console, action):
        with contextlib.ExitStack() as stack:
            for item in console.patches():
                stack.enter_context(item)
            stack.enter_context(patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0))
            return action()

    def made_and_inserted(self, preview, console, name):
        """A card the preview made, in the console."""
        self.run_with(console, lambda: preview.make_card(name))
        console.cards[name] = {"slot": "card-slot-1"}

    def foreign_file(self, name, data=b"a player's own card, named like the preview's"):
        path = self.cards / f"{name}.img"
        path.write_bytes(data)
        return path

    # ------------------------------------------------------------ the preview's way

    def test_the_first_card_is_made_from_the_folder_and_inserted(self):
        console = Console()
        self.run_with(console, lambda: self.preview().run(threading.Event(), watch=False, follow=False))
        self.assertEqual(console.visible(), [("attach", "pv0-shape")])
        self.assertTrue((self.cards / "pv0-shape.img").is_file())
        self.assertIn("LISTO", self.out.getvalue())
        record = json.loads((self.cards / ".shape-preview" / "pv0-shape.json").read_text())
        self.assertEqual(record["guest"], "shape")
        self.assertEqual(record["identity"]["inode"], os.lstat(self.cards / "pv0-shape.img").st_ino)
        self.assertEqual([p.name for p in (self.cards / ".shape-preview").glob("*.img")], [],
                         "nothing half-made is left beside the records")

    def test_the_first_card_waits_for_the_shell_so_that_it_arrives(self):
        console = Console()
        console.shell_up = False
        stop = threading.Event()
        with contextlib.ExitStack() as stack:
            for item in console.patches():
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
        mine = self.foreign_file("mine")
        before = fingerprint(mine)
        console = Console(cards=["mine"])
        self.run_with(console, lambda: self.preview().run(threading.Event(), watch=False, follow=False))
        self.assertEqual(console.visible(), [])
        self.assertEqual(fingerprint(mine), before)
        self.assertIn("no other card", self.out.getvalue())

    def test_a_change_waits_while_a_game_is_played(self):
        preview = self.preview()
        console = Console(playing=True)
        self.made_and_inserted(preview, console, "pv0-shape")
        changed = self.run_with(console, preview.change)
        self.assertFalse(changed)
        self.assertEqual(console.calls, [])
        self.assertIn("waits for it to end", self.out.getvalue())

    def test_after_a_game_the_new_card_waits_for_the_shell_to_be_back(self):
        # The launcher is idle again before it starts the shell: a card put
        # in then would be found at start, without its arrival.
        preview = self.preview()
        console = Console()
        self.made_and_inserted(preview, console, "pv0-shape")
        console.shell_up = False
        self.assertFalse(self.run_with(console, preview.change))
        self.assertFalse(self.run_with(console, preview.change))
        self.assertEqual(console.visible(), [])
        self.assertEqual(self.out.getvalue().count("the new card waits for it"), 1)
        console.shell_up = True
        self.assertTrue(self.run_with(console, preview.change))
        self.assertEqual(console.visible(), [("detach", "pv0-shape", False), ("attach", "pv1-shape")])

    def test_a_change_is_a_new_card_after_the_previous_left_safely(self):
        preview = self.preview()
        console = Console()
        self.made_and_inserted(preview, console, "pv0-shape")
        self.assertTrue(self.run_with(console, preview.change))
        self.assertEqual(console.visible(), [("detach", "pv0-shape", False), ("attach", "pv1-shape")])
        self.assertFalse((self.cards / "pv0-shape.img").exists(), "the card that left is deleted")
        self.assertFalse((self.cards / ".shape-preview" / "pv0-shape.json").exists(), "and its record")
        self.assertTrue((self.cards / "pv1-shape.img").is_file())

    def test_a_release_the_console_refuses_is_tried_again_and_nothing_is_forced(self):
        preview = self.preview()
        console = Console(refuse=1)
        self.made_and_inserted(preview, console, "pv0-shape")
        self.assertFalse(self.run_with(console, preview.change))
        self.assertEqual(console.visible(), [("detach", "pv0-shape", False)])
        self.assertTrue(self.run_with(console, preview.change))
        self.assertEqual(console.visible()[1:], [("detach", "pv0-shape", False), ("attach", "pv1-shape")])

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
            for item in console.patches():
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
        self.assertEqual(console.visible(),
                         [("attach", "pv0-shape"), ("detach", "pv0-shape", False), ("attach", "pv1-shape")])

    def test_a_console_that_stops_answering_ends_the_watch_without_a_trace(self):
        # Turned off from its menu, the console stops answering through
        # qemu-ga while its QEMU still runs, then QEMU ends.
        console = Console()
        stop = threading.Event()
        silent = {"count": 0}

        def reconcile(**_):
            silent["count"] += 1
            if silent["count"] >= 3:
                console.gone = True
            raise vm.LabError("qemu-ga connection closed")

        console.gone = False
        with contextlib.ExitStack() as stack:
            for item in console.patches():
                stack.enter_context(item)
            stack.enter_context(patch.object(shapepreview, "POLL_SECONDS", 0.05))
            stack.enter_context(patch.object(shapepreview, "SHELL_GRACE_SECONDS", 0))
            stack.enter_context(patch.object(vm, "reconcile_released", side_effect=reconcile))
            stack.enter_context(patch.object(vm, "read_pid", side_effect=lambda: None if console.gone else 4242))
            worker = threading.Thread(target=self.preview().run, args=(stop, True, True))
            worker.start()
            worker.join(30)
            self.assertFalse(worker.is_alive(), "the watch ends once the console is gone")
        self.assertEqual(self.out.getvalue().count("the console does not answer"), 1)

    def test_a_base_card_is_copied_and_never_written(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = card_cli.main(["create", str(self.cards / "mygame.img"), "--shape", str(SAMPLES / "paper")])
        self.assertEqual(code, 0)
        before = fingerprint(self.cards / "mygame.img")
        console = Console()
        self.run_with(console, lambda: self.preview(base=self.cards / "mygame.img")
                      .run(threading.Event(), watch=False, follow=False))
        self.assertEqual(fingerprint(self.cards / "mygame.img"), before)
        self.assertEqual(console.visible(), [("attach", "pv0-shape")])
        from mun_card import shape
        from mun_card.source import DebugfsSource
        source = DebugfsSource(self.cards / "pv0-shape.img", image.find_tool("debugfs"))
        self.assertEqual(shape.check_card(source, "content").shape["transition"]["in"], "tide",
                         "the copy carries the folder's package (sea), not the base card's (paper)")

    def test_at_the_end_the_card_leaves_safely_unless_a_game_is_played(self):
        other = self.foreign_file("other", b"not the preview's")
        before = fingerprint(other)
        preview = self.preview()
        console = Console()
        self.made_and_inserted(preview, console, "pv1-shape")
        self.run_with(console, preview.finish)
        self.assertEqual(console.visible(), [("detach", "pv1-shape", False)])
        self.assertFalse((self.cards / "pv1-shape.img").exists())
        self.assertEqual(fingerprint(other), before)
        preview = self.preview()
        console = Console(playing=True)
        self.made_and_inserted(preview, console, "pv0-shape")
        self.run_with(console, preview.finish)
        self.assertEqual(console.visible(), [])
        self.assertTrue((self.cards / "pv0-shape.img").exists(), "a card in use stays")

    # ------------------------------------------------------------ files the preview did not make

    def test_a_file_at_the_previews_name_it_did_not_make_is_kept_and_the_preview_does_not_start(self):
        for name in ("pv0-shape", "pv1-shape"):
            with self.subTest(name=name):
                path = self.foreign_file(name)
                before = fingerprint(path)
                console = Console()
                preview = self.preview()
                self.run_with(console, lambda: preview.run(threading.Event(), watch=False, follow=False))
                self.run_with(console, preview.finish)
                self.assertEqual(console.visible(), [], "nothing inserted, nothing taken out")
                self.assertEqual(fingerprint(path), before, "same bytes, same file")
                self.assertIn("was not made by this preview", self.out.getvalue())
                path.unlink()

    def test_making_a_card_never_replaces_a_file_it_did_not_make(self):
        path = self.foreign_file("pv1-shape")
        before = fingerprint(path)
        with self.assertRaises(shapepreview.NotOurs):
            self.run_with(Console(), lambda: self.preview().make_card("pv1-shape"))
        self.assertEqual(fingerprint(path), before)
        self.assertFalse((self.cards / ".shape-preview" / "pv1-shape.json").exists())

    def test_a_change_waits_while_another_file_holds_the_next_name_and_touches_neither(self):
        preview = self.preview()
        console = Console()
        self.made_and_inserted(preview, console, "pv0-shape")
        inserted = fingerprint(self.cards / "pv0-shape.img")
        path = self.foreign_file("pv1-shape")
        before = fingerprint(path)
        self.assertFalse(self.run_with(console, preview.change))
        self.assertFalse(self.run_with(console, preview.change))
        self.assertEqual(console.visible(), [], "the card in the console stays in")
        self.assertEqual(fingerprint(path), before)
        self.assertEqual(fingerprint(self.cards / "pv0-shape.img"), inserted)
        self.assertEqual(self.out.getvalue().count("the change waits"), 1, "said once")
        path.unlink()
        self.assertTrue(self.run_with(console, preview.change), "taken once the file is gone")
        self.assertEqual(console.visible(), [("detach", "pv0-shape", False), ("attach", "pv1-shape")])

    def test_a_card_replaced_after_the_preview_made_it_is_no_longer_its_own(self):
        preview = self.preview()
        console = Console()
        self.run_with(console, lambda: preview.make_card("pv1-shape"))
        replacement = self.tmp / "replacement.img"
        replacement.write_bytes(b"put in place of the preview's card")
        os.replace(replacement, self.cards / "pv1-shape.img")
        before = fingerprint(self.cards / "pv1-shape.img")
        self.run_with(console, preview.delete_unused)
        self.run_with(console, preview.finish)
        self.assertEqual(fingerprint(self.cards / "pv1-shape.img"), before)
        with self.assertRaises(shapepreview.NotOurs):
            self.run_with(console, lambda: self.preview().make_card("pv1-shape"))
        self.assertEqual(fingerprint(self.cards / "pv1-shape.img"), before)

    def test_a_card_moved_or_copied_over_the_previews_is_kept(self):
        with contextlib.redirect_stdout(io.StringIO()):
            card_cli.main(["create", str(self.tmp / "mine.img"), "--title", "Mine", "--id", "org.example.mine"])
        # Moved over the name: another inode.
        preview = self.preview()
        console = Console()
        self.run_with(console, lambda: preview.make_card("pv0-shape"))
        shutil.copyfile(self.tmp / "mine.img", self.tmp / "moved.img")
        os.rename(self.tmp / "moved.img", self.cards / "pv0-shape.img")
        before = fingerprint(self.cards / "pv0-shape.img")
        self.run_with(console, preview.finish)
        self.assertEqual(fingerprint(self.cards / "pv0-shape.img"), before)
        os.unlink(self.cards / "pv0-shape.img")
        # Copied into the preview's own file (`cp` over it keeps the inode):
        # the card in it is another, and it is kept too.
        preview = self.preview()
        self.run_with(console, lambda: preview.make_card("pv1-shape"))
        with open(self.tmp / "mine.img", "rb") as source, open(self.cards / "pv1-shape.img", "r+b") as target:
            shutil.copyfileobj(source, target)
        before = fingerprint(self.cards / "pv1-shape.img")
        self.run_with(console, preview.finish)
        self.assertEqual(fingerprint(self.cards / "pv1-shape.img"), before)

    def test_a_file_put_at_the_name_while_the_preview_removes_its_own_is_put_back(self):
        preview = self.preview()
        console = Console()
        self.run_with(console, lambda: preview.make_card("pv1-shape"))
        os.unlink(self.cards / "pv1-shape.img")
        path = self.foreign_file("pv1-shape", b"arrived between the check and the removal")
        before = fingerprint(path)
        # The first check is passed as if the file had not changed yet; the
        # second, on the file itself once moved aside, finds it is another.
        with patch.object(shapepreview.Preview, "own", return_value=True):
            self.assertFalse(self.run_with(console, lambda: preview.remove_own("pv1-shape")))
        self.assertEqual(fingerprint(path), before, "back at its name, same bytes, same inode")
        self.assertEqual(list((self.cards / ".shape-preview").glob("gone-*")), [])

    def test_a_file_that_appears_while_a_card_is_made_is_not_replaced(self):
        real_run = subprocess.run
        late = self.cards / "pv0-shape.img"

        def create_then_someone_writes(command, **kwargs):
            result = real_run(command, **kwargs)
            late.write_bytes(b"written while the preview made its card")
            return result

        with patch.object(shapepreview.subprocess, "run", side_effect=create_then_someone_writes):
            with self.assertRaises(shapepreview.NotOurs):
                self.run_with(Console(), lambda: self.preview().make_card("pv0-shape"))
        self.assertEqual(late.read_bytes(), b"written while the preview made its card")
        self.assertFalse((self.cards / ".shape-preview" / "pv0-shape.json").exists())
        self.assertEqual(list((self.cards / ".shape-preview").glob("*.img")), [])

    def test_a_card_another_console_holds_is_never_remade_or_deleted(self):
        preview = self.preview()
        self.run_with(Console(), lambda: preview.make_card("pv1-shape"))
        # Someone attached the preview's spare card to another guest.
        console = Console(registry={"pv1-shape": {"instance": "prueba", "pid": 777}})
        before = fingerprint(self.cards / "pv1-shape.img")
        self.run_with(console, preview.delete_unused)
        self.assertEqual(fingerprint(self.cards / "pv1-shape.img"), before)
        with self.assertRaises(shapepreview.NotOurs):
            self.run_with(console, lambda: preview.make_card("pv1-shape"))

    # ------------------------------------------------------------ two previews

    def test_a_second_preview_of_one_console_is_refused_and_takes_nothing(self):
        first = self.preview()
        console = Console()
        self.made_and_inserted(first, console, "pv0-shape")
        inserted = fingerprint(self.cards / "pv0-shape.img")
        with self.assertRaises(vm.LabError) as refused:
            self.preview()
        self.assertIn("another preview is running in guest shape", str(refused.exception))
        self.assertEqual(fingerprint(self.cards / "pv0-shape.img"), inserted)
        first.release()
        self.preview()                    # once the first has ended

    def test_a_preview_in_another_process_holds_the_console(self):
        (self.cards / ".shape-preview").mkdir(exist_ok=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644); "
             "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)",
             str(self.cards / ".shape-preview" / "shape.lock")],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(vm.LabError):
                self.preview()
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_previews_in_two_consoles_keep_to_their_own_cards(self):
        one, two = self.preview(guest="one"), self.preview(guest="two")
        console_one, console_two = Console(), Console()
        self.made_and_inserted(one, console_one, "pv0-one")
        self.run_with(console_one, lambda: one.make_card("pv1-one"))
        kept = {name: fingerprint(self.cards / f"{name}.img") for name in ("pv0-one", "pv1-one")}
        self.run_with(console_two, lambda: two.run(threading.Event(), watch=False, follow=False))
        self.run_with(console_two, two.change)
        self.run_with(console_two, two.finish)
        self.assertEqual(console_two.visible(),
                         [("attach", "pv0-two"), ("detach", "pv0-two", False), ("attach", "pv1-two"),
                          ("detach", "pv1-two", False)])
        self.assertEqual({name: fingerprint(self.cards / f"{name}.img") for name in kept}, kept)
        self.assertFalse(two.own("pv0-one"), "a record names the guest whose preview made the card")
        self.assertEqual(sorted(p.name for p in self.cards.glob("pv*.img")), ["pv0-one.img", "pv1-one.img"])


if __name__ == "__main__":
    unittest.main()
