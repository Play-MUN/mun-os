"""The card service's MUN Shape export (docs/shape.md): after `valid`, off the
event loop, bound to one insertion, published whole or not at all, cancelled
by removal, replacement and safe release, with Play and Eject kept available.

Workers are real threads over real temporary directories; the moments that
matter (between chunks, inside a read that does not return, just after
publication) are held with gates, so each race is exercised in a fixed order.
"""

import json
import os
import shutil
import stat as statmod
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "services" / "mun-launchd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from mun_card import shapetools  # noqa: E402
from test_cardd import FakeMounter, game_info  # noqa: E402

SEA = ROOT / "examples" / "shape" / "sea"


def _copy_entry(source, destination):
    """copytree's copy for a card tree: a FIFO is made again, not read."""
    if statmod.S_ISFIFO(os.lstat(source).st_mode):
        os.mkfifo(destination)
    else:
        shutil.copy2(source, destination)


class PackageMounter(FakeMounter):
    """Mounts a real directory holding a game card whose content carries
    `package` (a folder copied as content/mun-shape, or None)."""

    def __init__(self, root, packages):
        super().__init__()
        self.root = root
        self.packages = packages      # device -> package folder or None

    def mount(self, card, path):
        target = self.root / "cards" / card.slot
        (target / "content").mkdir(parents=True)
        (target / "saves").mkdir()
        elf = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8 + (2).to_bytes(2, "little") + (183).to_bytes(2, "little") + b"\0" * 100
        (target / "content" / "mun-collect").write_bytes(elf)
        package = self.packages.get(card.device)
        if package is not None:
            shutil.copytree(package, target / "content" / "mun-shape", symlinks=True, copy_function=_copy_entry)
        self.mounted[card.slot] = str(target)
        self.log.append(("mount", card.slot))
        return str(target)


class ShapeHarness:
    """CardManager with the Shape export on, fake timers and synchronous
    export removal; scheduled callbacks run on `pump()`."""

    def __init__(self, root, packages):
        self.events, self.pending, self.timers = [], [], []
        self.shape_root = root / "shape"
        self.mounter = PackageMounter(root, packages)
        self.manager = cardd.CardManager(self.mounter, lambda mount: game_info(), self.events.append,
                                         self.pending.append, shape_root=self.shape_root,
                                         later=lambda seconds, fn: self.timers.append(fn),
                                         remove_export=cardd._remove_tree)

    def pump(self, until=None, timeout=10.0):
        """Run scheduled callbacks as they arrive, until `until()` holds (or,
        without it, until one batch has run)."""
        waited = threading.Event()
        for _ in range(int(timeout * 100)):
            while self.pending:
                self.pending.pop(0)()
            if until is None and self.events and not self.pending:
                if until is None:
                    return
            if until is not None and until():
                return
            waited.wait(0.01)
        raise AssertionError("timed out waiting for the card service")

    def shape_states(self, slot="c1"):
        return [e["shape"]["state"] if e["shape"] else None for e in self.events
                if e["type"] == "shape" and e["slot"] == slot]

    def card(self, slot="c1"):
        return next(c for c in self.manager.snapshot()["cards"] if c["slot"] == slot)

    def settled(self, slot="c1"):
        return lambda: any(state not in (None, "preparing") for state in self.shape_states(slot))

    def exports(self):
        return sorted(os.listdir(self.shape_root)) if self.shape_root.exists() else []

    def owners(self):
        """The insertion each published export belongs to (`<insertion>.<attempt>`)."""
        return [name.rsplit(".", 1)[0] for name in self.exports() if not name.startswith(".")]


class Gate:
    """Holds a worker at one point until opened; `reached` says it got there."""

    def __init__(self):
        self.reached, self.opened = threading.Event(), threading.Event()

    def hold(self):
        self.reached.set()
        self.opened.wait(10)

    def wait_reached(self, test):
        test.assertTrue(self.reached.wait(10), "the worker reached the gate")


class ShapeExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardd-shape-"))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for directory, folders, _ in os.walk(self.tmp):
            os.chmod(directory, 0o700)
        shutil.rmtree(self.tmp, True)

    def harness(self, **packages):
        return ShapeHarness(self.tmp, packages or {"vdc": SEA})

    def insert(self, h, device="vdc", serial="NPT-game"):
        """Insert a card and run the loop until it is valid and its copy has started."""
        h.manager.device_added(device, serial, f"/dev/{device}")
        slot = h.manager.cards[device].slot
        h.pump(until=lambda: "preparing" in h.shape_states(slot))

    def fixture(self, name):
        return shapetools.write_fixture(self.tmp / "fixtures", name)

    # --- the ordinary path --------------------------------------------------------
    def test_valid_is_published_first_then_the_export_whole_and_immutable(self):
        h = self.harness()
        self.insert(h)
        h.pump(until=h.settled())
        kinds = [(e["type"], e["card"]["state"] if e["type"] == "card" else e["shape"]["state"]) for e in h.events]
        self.assertEqual(kinds, [("card", "reading"), ("card", "valid"), ("shape", "preparing"), ("shape", "ready")])
        self.assertIsNone(h.events[1]["card"]["shape"], "valid carries no Shape state: nothing about Shape delays it")
        card = h.card()
        record = card["shape"]
        self.assertEqual((record["state"], record["insertion"], record["version"]), ("ready", card["insertion"], "0.1.0"))
        export = Path(record["path"])
        self.assertEqual(h.exports(), [export.name], "published under its own name, no .part left")
        self.assertEqual(h.owners(), [card["insertion"]])
        files = {p.relative_to(export).as_posix() for p in export.rglob("*") if p.is_file()}
        expected = {p.relative_to(SEA).as_posix() for p in SEA.rglob("*") if p.is_file()}
        self.assertEqual(files, expected)
        document = json.loads((export / "shape.json").read_text())
        self.assertEqual((document["insertion"], document["version"], document["format"]),
                         (card["insertion"], "0.1.0", "mun-shape/1"))
        self.assertIn("surfaces", document, "the normalised document, not the card's own")
        self.assertEqual((export / "world" / "fish.png").read_bytes(), (SEA / "world" / "fish.png").read_bytes())
        for path in [export, *export.rglob("*")]:
            mode = statmod.S_IMODE(path.lstat().st_mode)
            self.assertEqual(mode, 0o550 if path.is_dir() else 0o440, path)
        self.assertEqual((record["files"], record["bytes"]), (len(expected) - 1,
                                                              sum((SEA / f).stat().st_size for f in expected if f != "shape.json")))

    def test_a_card_without_a_package_has_none(self):
        h = ShapeHarness(self.tmp, {"vdc": None})
        self.insert(h)
        h.pump(until=h.settled())
        self.assertEqual(h.shape_states(), ["preparing", "none"])
        self.assertEqual(h.exports(), [])

    def test_a_defective_package_exports_only_what_the_checker_accepted(self):
        h = ShapeHarness(self.tmp, {"vdc": self.fixture("png-bomb")})
        self.insert(h)
        h.pump(until=h.settled())
        record = h.card()["shape"]
        self.assertEqual(record["state"], "partial")
        self.assertIn(("shape_png_dimensions", "world"), [(n["code"], n["block"]) for n in record["notes"]])
        export = Path(record["path"])
        self.assertFalse((export / "world").exists(), "a dropped block's files never reach the shell")
        self.assertNotIn("world", json.loads((export / "shape.json").read_text()))

    def test_an_unusable_package_publishes_nothing(self):
        h = ShapeHarness(self.tmp, {"vdc": self.fixture("not-json")})
        self.insert(h)
        h.pump(until=h.settled())
        record = h.card()["shape"]
        self.assertEqual((record["state"], record["notes"][0]["code"]), ("unused", "shape_syntax"))
        self.assertNotIn("path", record)
        self.assertEqual(h.exports(), [])
        self.assertEqual(h.card()["state"], "valid", "the card is unaffected")

    def test_links_and_fifos_on_the_card_are_never_followed_or_waited_on(self):
        package = self.tmp / "tricky"
        shutil.copytree(SEA, package)
        (package / "world" / "backdrop.png").unlink()
        (package / "world" / "backdrop.png").symlink_to("/etc/passwd")
        (package / "sfx" / "move.wav").unlink()
        os.mkfifo(package / "sfx" / "move.wav")
        h = ShapeHarness(self.tmp, {"vdc": package})
        self.insert(h)
        h.pump(until=h.settled())
        record = h.card()["shape"]
        self.assertEqual(record["state"], "partial")
        codes = {(n["code"], n["block"]) for n in record["notes"]}
        self.assertTrue({("shape_path_symlink", "world"), ("shape_path_type", "sounds")} <= codes, codes)
        export = Path(record["path"])
        self.assertFalse(any(p.is_symlink() for p in export.rglob("*")))
        self.assertFalse((export / "sfx").exists() or (export / "world").exists())

    # --- Play and Eject while the copy runs ----------------------------------------
    def hold_between_chunks(self, after=3):
        """Patch the copy's cancellation check so the worker stops at its
        `after`-th check until the gate opens; returns the gate."""
        gate, calls, original = Gate(), [0], cardd.ShapeExport.check

        def check(export):
            calls[0] += 1
            if calls[0] == after:
                gate.hold()
            original(export)
        patcher = patch.object(cardd.ShapeExport, "check", check)
        patcher.start()
        self.addCleanup(patcher.stop)
        return gate

    def test_play_and_a_save_are_served_while_the_copy_is_held(self):
        gate = self.hold_between_chunks(after=4)
        h = self.harness()
        self.insert(h)
        h.pump(until=lambda: "preparing" in h.shape_states())
        gate.wait_reached(self)
        card = h.card()
        dest = self.tmp / "launch" / "s1"
        (dest / "work").mkdir(parents=True)
        staged, saved = [], []
        with patch.object(cardd, "_copy_bounded", lambda *a, **k: {"ok": True, "path": "x", "size": 1, "sha256": "ab"}), \
                patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.tmp) + "/"):
            h.manager.stage({"type": "stage", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                             "version": "0.1.0", "session": "s1", "dest": str(dest)}, staged.append)
            h.pump(until=lambda: staged)
        self.assertTrue(staged[0]["ok"], staged)
        h.manager.save({"type": "save", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                        "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                        "payload": {"n": 1}}, saved.append)
        h.pump(until=lambda: saved)
        self.assertTrue(saved[0]["ok"], saved)
        self.assertEqual(h.shape_states(), ["preparing"], "the copy is still held")
        gate.opened.set()
        h.pump(until=h.settled())
        self.assertEqual(h.shape_states()[-1], "ready")

    def test_eject_during_the_copy_cancels_it_within_a_chunk_then_unmounts(self):
        gate = self.hold_between_chunks(after=4)
        h = self.harness()
        self.insert(h)
        gate.wait_reached(self)
        released = []
        h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
        self.assertEqual(released, [], "the release waits for the copy to close its files")
        self.assertNotIn(("umount", "c1"), h.mounter.log, "not unmounted under an open reader")
        gate.opened.set()      # the next check sees the cancellation
        h.pump(until=lambda: released)
        self.assertTrue(released[0]["ok"], released)
        self.assertEqual(h.mounter.log[-1], ("umount", "c1"))
        self.assertEqual(h.card()["state"], "released")
        self.assertIsNone(h.card()["shape"])
        self.assertEqual(h.exports(), [], "no staging copy and no export remain")
        self.assertEqual(len(h.timers), 1, "the release had armed its deadline")
        events = len(h.events)
        h.timers.pop()()
        self.assertEqual(len(h.events), events, "a deadline that fires after the release does nothing")

    def test_eject_with_a_read_that_does_not_return_says_still_in_use_and_stays_pending(self):
        gate, original = Gate(), cardd._open_package_file

        def stuck_open(mount, relative):
            if relative.endswith(".wav"):
                gate.hold()      # a read on a failing card: no cancellation check runs here
            return original(mount, relative)
        with patch.object(cardd, "_open_package_file", stuck_open):
            h = self.harness()
            self.insert(h)
            gate.wait_reached(self)
            card = h.card()
            released = []
            h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
            self.assertEqual(len(h.timers), 1, "a bounded, asynchronous wait: a timer, no join")
            h.timers.pop()()                 # the deadline passes
            self.assertEqual(len(released), 1)
            self.assertFalse(released[0]["ok"])
            self.assertEqual(released[0]["error"]["code"], "card_busy")
            self.assertNotIn(("umount", "c1"), h.mounter.log)
            staged = []
            h.manager.stage({"type": "stage", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                             "version": "0.1.0", "session": "s2", "dest": "/run/mun/launch/s2"}, staged.append)
            self.assertEqual(staged[0]["error"]["code"], "card_unavailable", "the release stays pending: no Play")
            gate.opened.set()                # the read finally returns
            h.pump(until=lambda: h.card()["state"] == "released")
        self.assertEqual(h.mounter.log[-1], ("umount", "c1"), "unmounted only once the reader closed")
        self.assertEqual(len(released), 1, "the caller was answered once; the card event says released")
        self.assertEqual(h.exports(), [])

    def test_a_release_that_cannot_unmount_brings_the_copy_back(self):
        gate = self.hold_between_chunks(after=4)
        h = self.harness()
        self.insert(h)
        gate.wait_reached(self)
        h.mounter.fail_strict_unmount = True
        released = []
        h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
        gate.opened.set()
        h.pump(until=lambda: released)
        self.assertEqual(released[0]["error"]["code"], "unmount_failed")
        h.pump(until=lambda: h.shape_states()[-1] == "ready")
        self.assertEqual(h.card()["state"], "valid")
        self.assertEqual(h.owners(), [h.card()["insertion"]])
        self.assertEqual(h.exports(), [Path(h.card()["shape"]["path"]).name])

    # --- removal, replacement and late completions --------------------------------
    def test_removal_during_the_copy_leaves_nothing_and_no_late_state(self):
        gate = self.hold_between_chunks(after=5)
        h = self.harness()
        self.insert(h)
        gate.wait_reached(self)
        h.manager.device_removed("vdc")
        removed_at = len(h.events)
        gate.opened.set()
        h.pump(until=lambda: h.manager._shape_running is None)
        self.assertEqual(h.events[removed_at - 1]["type"], "removed")
        self.assertEqual(h.events[removed_at:], [], "a late completion publishes nothing")
        self.assertEqual(h.exports(), [])

    def test_a_completion_that_arrives_after_removal_deletes_its_export(self):
        gate, original = Gate(), os.rename

        def rename(source, destination, *args, **kwargs):
            original(source, destination, *args, **kwargs)
            if str(source).endswith(".part"):
                gate.hold()       # published, the loop not yet told
        with patch.object(cardd.os, "rename", rename):
            h = self.harness()
            self.insert(h)
            gate.wait_reached(self)
            insertion = h.card()["insertion"]
            self.assertEqual(h.owners(), [insertion])
            h.manager.device_removed("vdc")
            gate.opened.set()
            h.pump(until=lambda: h.manager._shape_running is None)
        self.assertEqual(h.exports(), [], "the stale export is deleted")
        self.assertNotIn("shape", [e["type"] for e in h.events[[e["type"] for e in h.events].index("removed"):]])

    def test_a_replacement_waits_for_a_stuck_copy_and_never_runs_two(self):
        gate, original = Gate(), cardd._open_package_file
        first_mount = []

        def stuck_open(mount, relative):
            if not first_mount:
                first_mount.append(mount)
            if mount == first_mount[0] and relative.endswith(".wav"):
                gate.hold()
            return original(mount, relative)
        with patch.object(cardd, "_open_package_file", stuck_open):
            h = ShapeHarness(self.tmp, {"vdc": SEA, "vdd": SEA})
            self.insert(h, "vdc", "NPT-first")
            gate.wait_reached(self)
            h.manager.device_removed("vdc")        # the copy is stuck in a read of the removed card
            self.insert(h, "vdd", "NPT-second")
            h.pump(until=lambda: "preparing" in h.shape_states("c2"))
            self.assertEqual([t.name for t in threading.enumerate() if t.name.startswith("shape-")],
                             [f"shape-{h.manager._shape_running.insertion}"], "one copy at a time")
            self.assertEqual(h.card("c2")["state"], "valid", "the new card is valid and playable meanwhile")
            gate.opened.set()
            h.pump(until=h.settled("c2"))
        second = h.card("c2")
        self.assertEqual(second["shape"]["state"], "ready")
        self.assertEqual(h.owners(), [second["insertion"]], "only the current insertion's export exists")

    def test_a_quick_reinsertion_of_the_same_card_gets_only_its_own_export(self):
        gate = self.hold_between_chunks(after=6)
        h = self.harness()
        self.insert(h)
        gate.wait_reached(self)
        first = h.card()["insertion"]
        h.manager.device_removed("vdc")
        self.insert(h)                             # the same device again: a new insertion
        gate.opened.set()
        h.pump(until=h.settled("c2"))
        second = h.card("c2")
        self.assertNotEqual(first, second["insertion"])
        self.assertEqual(h.owners(), [second["insertion"]])
        self.assertTrue(all(e["insertion"] == second["insertion"] for e in h.events
                            if e["type"] == "shape" and e["shape"] and e["shape"]["state"] != "preparing"))

    def test_eject_and_removal_after_ready_delete_the_export(self):
        h = self.harness()
        self.insert(h)
        h.pump(until=h.settled())
        released = []
        h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
        self.assertTrue(released[0]["ok"])
        self.assertEqual(h.exports(), [])
        self.assertIsNone(h.events[-1]["card"]["shape"])
        h2 = ShapeHarness(self.tmp / "second", {"vdc": SEA})
        h2.manager.device_added("vdc", "NPT-game", "/dev/vdc")
        h2.pump(until=h2.settled())
        h2.manager.device_removed("vdc")
        self.assertEqual(h2.exports(), [])

    # --- the service's own lifetime and the wire ------------------------------------
    def test_start_and_stop_clear_every_export_and_staging_copy(self):
        root = self.tmp / "shape"
        (root / "abc" / "world").mkdir(parents=True)
        (root / "abc" / "world" / "a.png").write_bytes(b"x")
        (root / ".def.part").mkdir()
        for path in (root / "abc" / "world" / "a.png",):
            os.chmod(path, 0o440)
        os.chmod(root / "abc" / "world", 0o550)
        os.chmod(root / "abc", 0o550)
        cardd.clear_shape_root(root)
        self.assertEqual(os.listdir(root), [])
        gate = self.hold_between_chunks(after=4)
        h = self.harness()
        self.insert(h)
        gate.wait_reached(self)
        running = h.manager._shape_running
        stopper = threading.Thread(target=h.manager.stop_shape, args=(5,))
        stopper.start()
        gate.opened.set()
        stopper.join(10)
        self.assertTrue(running.done.is_set() and running.cancel.is_set())

    def test_the_new_message_and_field_leave_the_launcher_unchanged(self):
        import launchd
        view = launchd.CardView()
        h = self.harness()
        self.insert(h)
        h.pump(until=h.settled())
        for event in [h.manager.snapshot(), *h.events]:
            view.apply(event)
        self.assertEqual(view.active()["state"], "valid")
        self.assertIsNone(view.apply({"type": "shape", "slot": "c1", "insertion": "x", "shape": {"state": "ready"}}))
        frame = cardd.encode_frame(h.events[-1])
        self.assertLess(len(frame), 16 * 1024, "a Shape state change never resends the cover")



class SaveReadAndEjectTests(unittest.TestCase):
    """A release meets a save in flight and a Shape copy with an open
    descriptor: the card is unmounted only once both have ended, in either
    order; the caller is answered once; nothing is enabled meanwhile."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardd-shape-save-"))
        self.addCleanup(lambda: ShapeExportTests.cleanup(self))
        self.reader, self.writer = Gate(), Gate()
        real_open, held = cardd._open_package_file, []

        def hold_open(mount, relative):
            fd = real_open(mount, relative)          # a real descriptor on the card, kept open
            if relative.endswith(".wav") and not held:
                held.append(fd)
                self.reader.hold()
            return fd

        def hold_write(*args, **kwargs):
            self.writer.hold()
            return {"ok": True, "bytes": 10, "sha256": "ab", "path": "saves/x"}
        for patcher in (patch.object(cardd, "_open_package_file", hold_open),
                        patch.object(cardd, "_write_save", hold_write)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = ShapeHarness(self.tmp, {"vdc": SEA})
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc")
        self.h.pump(until=lambda: "preparing" in self.h.shape_states())
        self.reader.wait_reached(self)
        self.card = self.h.manager.cards["vdc"]
        self.saves, self.releases = [], []
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": self.card.insertion, "serial": "NPT-game",
                             "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                             "payload": {"n": 1}}, self.saves.append)
        self.writer.wait_reached(self)
        self.h.manager.release({"type": "release", "serial": "NPT-game"}, self.releases.append)

    def unmounted(self):
        return ("umount", "c1") in self.h.mounter.log

    def test_the_save_ends_first_then_the_copy(self):
        self.assertEqual((self.releases, self.h.timers), ([], []), "waiting for the save: no deadline yet")
        self.writer.opened.set()
        self.h.pump(until=lambda: self.saves)
        self.assertTrue(self.saves[0]["ok"])
        self.assertEqual(self.releases, [])
        self.assertFalse(self.unmounted(), "the Shape reader is still open")
        self.assertEqual(len(self.h.timers), 1, "the wait for the copy is bounded from here")
        self.reader.opened.set()
        self.h.pump(until=lambda: self.releases)
        self.assertEqual([r["ok"] for r in self.releases], [True])
        self.assertTrue(self.unmounted())
        self.assertEqual(self.h.card()["state"], "released")
        self.assertEqual(self.h.exports(), [])
        self.h.timers.pop()()                        # the deadline, late: nothing more
        self.assertEqual(len(self.releases), 1)

    def test_the_copy_ends_first_then_the_save(self):
        self.reader.opened.set()
        self.h.pump(until=lambda: self.h.manager._shape_running is None)
        self.assertEqual(self.releases, [])
        self.assertFalse(self.unmounted(), "the save is still in flight")
        self.assertEqual(self.h.timers, [])
        self.writer.opened.set()
        self.h.pump(until=lambda: self.releases)
        self.assertTrue(self.saves[0]["ok"])
        self.assertEqual([r["ok"] for r in self.releases], [True])
        self.assertTrue(self.unmounted())
        self.assertEqual(self.h.exports(), [])

    def test_past_the_deadline_the_release_stays_pending_until_both_end(self):
        self.writer.opened.set()
        self.h.pump(until=lambda: self.saves)
        self.h.timers.pop()()                        # the copy has not closed its files in time
        self.assertEqual([r["error"]["code"] for r in self.releases], ["card_busy"])
        self.assertTrue(self.card.releasing, "still pending")
        refused = []
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": self.card.insertion, "serial": "NPT-game",
                             "version": "0.1.0", "game": "mun.collect", "session": "s2", "schema": 1,
                             "payload": {"n": 2}}, refused.append)
        self.h.manager.stage({"type": "stage", "slot": "c1", "insertion": self.card.insertion, "serial": "NPT-game",
                              "version": "0.1.0", "session": "s3", "dest": "/run/mun/launch/s3"}, refused.append)
        self.assertEqual([r["error"]["code"] for r in refused], ["card_unavailable", "card_unavailable"])
        self.assertFalse(self.unmounted())
        self.reader.opened.set()
        self.h.pump(until=lambda: self.h.card()["state"] == "released")
        self.assertTrue(self.unmounted())
        self.assertEqual(len(self.releases), 1, "answered once; the card event says released")

    def test_a_failed_unmount_after_both_is_answered_once_and_the_copy_comes_back(self):
        self.h.mounter.fail_strict_unmount = True
        self.writer.opened.set()
        self.reader.opened.set()
        self.h.pump(until=lambda: self.releases)
        self.assertEqual([r["error"]["code"] for r in self.releases], ["unmount_failed"])
        self.assertFalse(self.card.releasing, "usable again, as after any failed release")
        self.h.pump(until=lambda: self.h.shape_states()[-1] == "ready")
        self.assertEqual(self.h.owners(), [self.card.insertion])
        self.assertEqual(len(self.releases), 1)

    def test_a_card_pulled_while_its_release_waits_is_answered_and_left_alone(self):
        self.h.manager.device_removed("vdc")
        self.assertEqual([r["error"]["code"] for r in self.releases], ["card_removed"])
        removed_at = len(self.h.events)
        self.writer.opened.set()
        self.reader.opened.set()
        self.h.pump(until=lambda: self.saves and self.h.manager._shape_running is None)
        self.assertEqual([e for e in self.h.events[removed_at:] if e["type"] == "card"], [],
                         "no released state for a card that is gone")
        self.assertEqual(len(self.releases), 1)
        self.assertEqual(self.h.exports(), [])


class RetryAndCleanupTests(unittest.TestCase):
    """A copy started again after a failed release has names of its own: the
    previous attempt's cleanup, deferred and asynchronous, never touches it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardd-shape-retry-"))
        self.addCleanup(lambda: ShapeExportTests.cleanup(self))
        self.h = ShapeHarness(self.tmp, {"vdc": SEA})
        self.deferred = []
        self.h.manager.remove_export = self.deferred.append      # cleanup that has not run yet

    def publish_then_fail_release(self, during_retry=None):
        """Attempt 1 publishes and is held before its result reaches the loop;
        a release cancels it and its unmount fails; attempt 2 starts while the
        cleanup of attempt 1 is still pending. `during_retry(export)` runs at
        attempt 2's first check."""
        real_copy, real_check, held = cardd.ShapeExport._copy, cardd.ShapeExport.check, Gate()

        def copy(export):
            result = real_copy(export)
            if export.final.name.endswith(".1"):
                held.hold()                           # published; the loop not told yet
            return result

        def check(export):
            if during_retry and export.final.name.endswith(".2") and not getattr(export, "_ran", False):
                export._ran = True
                during_retry(export)
            real_check(export)
        with patch.object(cardd.ShapeExport, "_copy", copy), patch.object(cardd.ShapeExport, "check", check):
            self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc")
            self.h.pump(until=lambda: "preparing" in self.h.shape_states())
            held.wait_reached(self)
            first = self.h.exports()
            self.h.mounter.fail_strict_unmount = True
            replies = []
            self.h.manager.release({"type": "release", "serial": "NPT-game"}, replies.append)
            held.opened.set()
            self.h.pump(until=lambda: replies and self.h.shape_states()[-1] in ("ready", "partial", "unused"))
        self.assertEqual([r["error"]["code"] for r in replies], ["unmount_failed"])
        return first

    def test_the_retry_is_ready_beside_a_cleanup_that_has_not_run(self):
        first = self.publish_then_fail_release()
        record = self.h.card()["shape"]
        self.assertEqual(record["state"], "ready")
        self.assertEqual(sorted(self.h.exports()), sorted(first + [Path(record["path"]).name]))
        self.assertEqual([p.name for p in self.deferred], first, "attempt 1's export awaits its cleanup")
        for path in self.deferred:
            cardd._remove_tree(path)                  # the late cleanup runs now
        self.assertEqual(self.h.exports(), [Path(record["path"]).name], "only the current attempt remains")
        self.assertEqual((Path(record["path"]) / "world" / "fish.png").read_bytes(),
                         (SEA / "world" / "fish.png").read_bytes())

    def test_a_cleanup_that_runs_during_the_retry_leaves_it_whole(self):
        def clean_meanwhile(export):
            workers = [threading.Thread(target=cardd._remove_tree, args=(path,)) for path in self.deferred]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
        self.publish_then_fail_release(during_retry=clean_meanwhile)
        record = self.h.card()["shape"]
        self.assertEqual(record["state"], "ready")
        self.assertEqual(self.h.exports(), [Path(record["path"]).name])
        files = {p.relative_to(record["path"]).as_posix() for p in Path(record["path"]).rglob("*") if p.is_file()}
        self.assertEqual(files, {p.relative_to(SEA).as_posix() for p in SEA.rglob("*") if p.is_file()})


class PublicationFailureTests(unittest.TestCase):
    """A copy that fails on its way to publication leaves neither its staging
    copy nor a result, and never touches another attempt's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardd-shape-fail-"))
        self.addCleanup(lambda: ShapeExportTests.cleanup(self))
        self.h = ShapeHarness(self.tmp, {"vdc": SEA})
        self.other = self.h.shape_root / "0123456789abcdef.9"     # another attempt's result
        self.other.mkdir(parents=True)
        (self.other / "shape.json").write_text("{}")

    def run_card(self):
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc")
        self.h.pump(until=self.h.settled())
        return self.h.card()["shape"]

    def assert_failed_cleanly(self, record, detail):
        self.assertEqual((record["state"], record["notes"][0]["code"]), ("unused", "shape_export_failed"))
        self.assertIn(detail, record["notes"][0]["detail"])
        self.assertNotIn("path", record)
        self.assertEqual(self.h.exports(), [self.other.name], "no .part, no result; the other attempt kept")
        self.assertEqual((self.other / "shape.json").read_text(), "{}")

    def test_sealing_fails(self):
        real_chmod = os.chmod

        def chmod(path, mode, *args, **kwargs):
            if Path(path).name.endswith(".part") and mode == 0o550:
                raise OSError(5, "injected failure sealing the copy")
            return real_chmod(path, mode, *args, **kwargs)
        with patch.object(cardd.os, "chmod", chmod):
            self.assert_failed_cleanly(self.run_card(), "sealing")

    def test_the_publishing_rename_fails(self):
        real_rename = os.rename

        def rename(source, destination, *args, **kwargs):
            if str(source).endswith(".part"):
                raise OSError(5, "injected failure publishing")
            return real_rename(source, destination, *args, **kwargs)
        with patch.object(cardd.os, "rename", rename):
            self.assert_failed_cleanly(self.run_card(), "publishing")

    def test_a_failure_after_publication_removes_that_result_only(self):
        real_copy = cardd.ShapeExport._copy

        def copy(export):
            real_copy(export)
            raise OSError(5, "injected failure after publication")
        with patch.object(cardd.ShapeExport, "_copy", copy):
            self.assert_failed_cleanly(self.run_card(), "after publication")


if __name__ == "__main__":
    unittest.main()
