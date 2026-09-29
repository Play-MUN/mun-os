"""State-machine tests for mun-cardd with injected platform pieces (no udev, no root)."""

import sys
from unittest.mock import Mock, patch
import os
import json
import os
import stat as statmod
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from mun_card.errors import CardError  # noqa: E402


class FakeMounter:
    def __init__(self, fail_for=()):
        self.mounted = {}
        self.fail_for = set(fail_for)
        self.log = []

    def device_path(self, device):
        return f"/dev/{device}"

    def mount(self, card, path):
        if card.device in self.fail_for:
            raise CardError("image_needs_recovery", "El sistema de archivos necesita recuperación y no se monta")
        if card.device == "vanish":
            raise FileNotFoundError(2, "No such file or directory", path)
        target = f"/run/test/{card.slot}"
        self.mounted[card.slot] = target
        self.log.append(("mount", card.slot))
        return target

    fail_strict_unmount = False

    def unmount(self, card, strict=False):
        if card.mount:
            if strict and self.fail_strict_unmount:
                self.log.append(("umount-failed", card.slot))
                raise CardError("unmount_failed", "No se pudo desmontar la tarjeta de forma segura", "target is busy")
            self.mounted.pop(card.slot, None)
            self.log.append(("umount", card.slot))
            card.mount = None

    def remount(self, card, rw):
        if not card.mount:
            raise CardError("card_removed", "La tarjeta ya no está montada")
        self.log.append(("remount", card.slot, "rw" if rw else "ro"))


class Harness:
    """Drives CardManager synchronously: scheduled callbacks run when `pump()` is called."""

    def __init__(self, mounter=None, validator=None):
        self.events = []
        self.pending = []
        self.validation_gate = threading.Event()
        self.validation_gate.set()
        self.validator_calls = 0

        def default_validator(mount):
            self.validator_calls += 1
            self.validation_gate.wait(5)
            return {"id": "mun.testcard", "title": "MUN Test Card", "version": "0.1.0", "kind": "test",
                    "arch": "aarch64", "profile": "linux-arm64-v0", "root": "content", "entry": None,
                    "cover": "cover.png", "saves": "saves", "runnable": False, "schema": 1}
        self.mounter = mounter or FakeMounter()
        self.manager = cardd.CardManager(self.mounter, validator or default_validator,
                                         self.events.append, self.pending.append)

    def pump(self, timeout=5.0):
        """Wait for worker threads to schedule, then run scheduled callbacks."""
        deadline = threading.Event()
        for _ in range(int(timeout * 100)):
            if self.pending:
                break
            deadline.wait(0.01)
        while self.pending:
            fn = self.pending.pop(0)
            fn()

    def states(self):
        return [(e["card"]["slot"], e["card"]["state"], e["card"]["active"]) for e in self.events if e["type"] == "card"]


class CardManagerTests(unittest.TestCase):
    def test_insert_validate_remove(self):
        h = Harness()
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc")
        h.pump()
        self.assertEqual(h.states(), [("c1", "reading", True), ("c1", "valid", True)])
        valid = [e for e in h.events if e["type"] == "card"][-1]["card"]
        self.assertEqual(valid["info"]["title"], "MUN Test Card")
        self.assertNotIn("cover_path", valid, "mounts are private to the service; no paths cross the socket")
        h.manager.device_removed("vdc")
        self.assertEqual(h.events[-1]["type"], "removed")
        self.assertEqual(h.mounter.log, [("mount", "c1"), ("umount", "c1")])
        self.assertEqual(h.manager.snapshot()["cards"], [])

    def test_ignores_non_lab_devices(self):
        h = Harness()
        h.manager.device_added("vda", "NEPTUNE-SYSTEM", "/dev/vda")   # a system disk named before MUN
        h.manager.device_added("vdb", "MUN-SYSTEM", "/dev/vdb")       # a MUN OS image guest
        h.manager.device_added("vdd", "", "/dev/vdd")
        self.assertEqual(h.events, [])
        self.assertEqual(h.mounter.log, [])

    def test_reinsertion_gets_a_new_slot_and_fresh_validation(self):
        h = Harness()
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc"); h.pump()
        h.manager.device_removed("vdc")
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc"); h.pump()
        self.assertEqual(h.states()[-1], ("c2", "valid", True))
        self.assertEqual(h.validator_calls, 2)

    def test_removal_during_validation_discards_stale_result(self):
        h = Harness()
        h.validation_gate.clear()          # validator blocks
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc")
        self.assertEqual(h.states(), [("c1", "reading", True)])
        h.manager.device_removed("vdc")    # removed while the worker is still busy
        h.validation_gate.set()
        h.pump()
        self.assertEqual([e["type"] for e in h.events], ["card", "removed"], "no valid event after removal")
        self.assertEqual(h.mounter.mounted, {})

    def test_invalid_card_is_reported_and_unmounted(self):
        def bad_validator(mount):
            raise CardError("arch_unsupported", "La arquitectura del contenido no es compatible", "x86_64")
        h = Harness(validator=bad_validator)
        h.manager.device_added("vdc", "NPT-bad", "/dev/vdc"); h.pump()
        self.assertEqual(h.states()[-1], ("c1", "invalid", True))
        self.assertEqual(h.events[-1]["card"]["error"]["code"], "arch_unsupported")
        self.assertEqual(h.mounter.mounted, {})

    def test_unmountable_image_is_invalid_without_worker(self):
        h = Harness(mounter=FakeMounter(fail_for={"vdc"}))
        h.manager.device_added("vdc", "NPT-dirty", "/dev/vdc")
        self.assertEqual(h.states()[-1], ("c1", "invalid", True))
        self.assertEqual(h.events[-1]["card"]["error"]["code"], "image_needs_recovery")

    def test_device_vanishing_before_mount_is_an_invalid_card_not_a_crash(self):
        h = Harness()
        h.manager.device_added("vanish", "NPT-gone", "/dev/vanish")
        self.assertEqual(h.states()[-1], ("c1", "invalid", True))
        self.assertEqual(h.events[-1]["card"]["error"]["code"], "source_unreadable")

    def test_second_card_waits_then_is_promoted_on_removal(self):
        h = Harness()
        h.manager.device_added("vdc", "NPT-one", "/dev/vdc"); h.pump()
        h.manager.device_added("vdd", "NPT-two", "/dev/vdd")
        self.assertEqual(h.states()[-1], ("c2", "waiting", False))
        self.assertEqual(h.events[-1]["card"]["error"]["code"], "another_card_active")
        self.assertEqual(h.mounter.log, [("mount", "c1")], "waiting card must not be mounted")
        h.manager.device_removed("vdc")
        h.pump()
        self.assertEqual(h.states()[-2:], [("c2", "reading", True), ("c2", "valid", True)])
        snap = h.manager.snapshot()
        self.assertEqual([(c["slot"], c["active"]) for c in snap["cards"]], [("c2", True)])

    def test_removing_waiting_card_does_not_disturb_active(self):
        h = Harness()
        h.manager.device_added("vdc", "NPT-one", "/dev/vdc"); h.pump()
        h.manager.device_added("vdd", "NPT-two", "/dev/vdd")
        h.manager.device_removed("vdd")
        removed = dict(h.events[-1]); removed.pop("insertion")
        self.assertEqual(removed, {"type": "removed", "slot": "c2", "device": "vdd", "serial": "NPT-two"})
        self.assertEqual(h.manager.snapshot()["cards"][0]["state"], "valid")

    def test_snapshot_carries_protocol_and_reader_state(self):
        h = Harness()
        snap = h.manager.snapshot()
        self.assertEqual((snap["type"], snap["protocol"], snap["reader"], snap["cards"]), ("snapshot", 1, "ready", []))

    def test_internal_validator_crash_becomes_visible_error(self):
        def crash(mount):
            raise RuntimeError("boom")
        h = Harness(validator=crash)
        h.manager.device_added("vdc", "NPT-x", "/dev/vdc"); h.pump()
        self.assertEqual(h.events[-1]["card"]["error"]["code"], "internal_error")


class InsertionIdentityTests(unittest.TestCase):
    """R2: every insertion carries a token that a slot number or serial cannot replace."""

    def game_validator(self, mount):
        return {"id": "mun.collect", "title": "MUN Collect", "version": "0.1.0", "kind": "game", "arch": "aarch64",
                "profile": "linux-arm64-v0", "root": "content", "entry": "content/mun-collect", "cover": None,
                "saves": "saves", "runnable": True, "schema": 1}

    def test_insertion_token_is_published_and_changes_on_reinsertion(self):
        h = Harness()
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc"); h.pump()
        first = h.manager.snapshot()["cards"][0]
        self.assertEqual(len(first["insertion"]), 16)
        h.manager.device_removed("vdc")
        self.assertEqual(h.events[-1]["insertion"], first["insertion"], "removal names the insertion that ended")
        h.manager.device_added("vdc", "NPT-card01", "/dev/vdc"); h.pump()
        second = h.manager.snapshot()["cards"][0]
        self.assertEqual((first["serial"], second["serial"]), ("NPT-card01", "NPT-card01"))
        self.assertNotEqual(first["insertion"], second["insertion"])
        other = Harness(); other.manager.device_added("vdc", "NPT-card01", "/dev/vdc"); other.pump()
        self.assertNotEqual(other.manager.snapshot()["cards"][0]["insertion"], second["insertion"],
                            "a restarted service reuses c1 but never an insertion token")

    def test_stage_rejects_a_request_for_another_insertion(self):
        h = Harness(validator=self.game_validator)
        h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); h.pump()
        card = h.manager.snapshot()["cards"][0]
        replies = []
        base = {"type": "stage", "slot": "c1", "serial": "NPT-game", "version": "0.1.0", "session": "s1", "dest": "/nowhere"}
        h.manager.stage(dict(base, insertion="stale-token"), replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "card_mismatch")
        h.manager.stage(dict(base, insertion=card["insertion"]), replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "bad_destination", "the right insertion passes the identity check")


class TransportTests(unittest.TestCase):
    """R3: large legal frames survive slow readers; oversize lines are handled explicitly."""

    def big_snapshot(self, cover_bytes=800 * 1024):
        import base64
        cover = base64.b64encode(b"\x89PNG" + bytes(range(256)) * (cover_bytes // 256)).decode("ascii")
        return {"type": "snapshot", "protocol": 1, "reader": "ready",
                "cards": [{"slot": "c1", "insertion": "abc", "serial": "NPT-big", "state": "valid", "active": True,
                           "info": {"id": "mun.big", "title": "Big", "version": "0.1.0", "kind": "test", "cover_data": cover},
                           "error": None}]}

    def pair(self):
        import socket
        a, b = socket.socketpair()
        a.setblocking(False)
        a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        self.addCleanup(a.close); self.addCleanup(b.close)
        return a, b

    def test_partial_write_is_queued_not_dropped_and_completes_when_drained(self):
        import json, selectors
        server = cardd.Server(Path("/nonexistent.sock"), "nobody")
        sel = selectors.DefaultSelector(); server.selector = sel
        a, b = self.pair()
        server.clients.append(a); sel.register(a, selectors.EVENT_READ, "client")
        server._send(a, self.big_snapshot())          # peer is not reading yet
        self.assertIn(a, server.clients, "backpressure is not a dead client")
        self.assertGreater(len(server.outbox.pending[a]), 0)
        self.assertTrue(sel.get_key(a).events & selectors.EVENT_WRITE, "asks to be woken when writable")
        received = bytearray()
        for _ in range(100000):
            try:
                chunk = b.recv(65536)
            except BlockingIOError:
                chunk = b""
            received += chunk
            server.on_event(a, selectors.EVENT_WRITE)
            if not server.outbox.pending.get(a) and received.endswith(b"\n"):
                break
        self.assertEqual(sel.get_key(a).events, selectors.EVENT_READ, "write interest cleared once drained")
        line, _, rest = bytes(received).partition(b"\n")
        self.assertEqual(rest, b"")
        self.assertEqual(json.loads(line)["cards"][0]["info"]["cover_data"], self.big_snapshot()["cards"][0]["info"]["cover_data"])
        self.assertGreater(len(line), 1 << 20, "a legal cover produces a line over 1 MiB")
        self.assertLessEqual(len(line) + 1, cardd.MAX_FRAME_BYTES)

    def test_client_that_never_reads_is_dropped_at_the_outbox_limit(self):
        server = cardd.Server(Path("/nonexistent.sock"), "nobody")
        server.outbox.limit = 3 * 1024 * 1024
        a, b = self.pair(); server.clients.append(a)
        for _ in range(3):
            server._send(a, self.big_snapshot())
        self.assertNotIn(a, server.clients, "over the limit: dropped, memory bounded")
        self.assertNotIn(a, server.outbox.pending)

    def test_frame_over_budget_keeps_state_and_omits_cover(self):
        import json
        frame = cardd.encode_frame(self.big_snapshot(cover_bytes=2 * 1024 * 1024))
        self.assertLessEqual(len(frame), cardd.MAX_FRAME_BYTES)
        card = json.loads(frame)["cards"][0]
        self.assertEqual((card["state"], card["info"]["id"], card["info"].get("cover_omitted")), ("valid", "mun.big", True))
        self.assertNotIn("cover_data", card["info"])
        self.assertEqual(json.loads(cardd.encode_frame({"type": "removed", "slot": "c1"})), {"type": "removed", "slot": "c1"})

    def test_two_clients_one_slow_do_not_disturb_each_other(self):
        import json
        server = cardd.Server(Path("/nonexistent.sock"), "nobody")
        slow_a, slow_b = self.pair(); fast_a, fast_b = self.pair()
        fast_a.setsockopt(__import__("socket").SOL_SOCKET, __import__("socket").SO_SNDBUF, 4 * 1024 * 1024)
        fast_b.setsockopt(__import__("socket").SOL_SOCKET, __import__("socket").SO_RCVBUF, 4 * 1024 * 1024)
        server.clients += [slow_a, fast_a]
        server.broadcast(self.big_snapshot(cover_bytes=64 * 1024))
        self.assertEqual(server.clients, [slow_a, fast_a])
        fast_b.setblocking(False)
        got = fast_b.recv(4 * 1024 * 1024)
        self.assertTrue(got.endswith(b"\n") and json.loads(got)["type"] == "snapshot")
        self.assertGreater(len(server.outbox.pending[slow_a]), 0, "the slow one still has bytes queued")


class SpecialFileTests(unittest.TestCase):
    """R4: objects that are not regular files under a save name are rejected without
    blocking. Each case runs in its own process with a timeout, because the defect was a
    hang: a FIFO opened for reading with no writer never returns."""

    SCRIPT = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import cardd
mount, mode = sys.argv[3], sys.argv[4]
info = {"id": "mun.collect", "saves": "saves", "version": "0.1.0", "kind": "game", "entry": "content/mun-collect"}
if mode == "restore":
    os.makedirs(os.path.join(mount, "work"), exist_ok=True)
    out = cardd._copy_save_out(__import__("pathlib").Path(mount), info, __import__("pathlib").Path(mount, "work", "save.json"))
    print(json.dumps(out))
else:
    class Mounter:
        log = []
        def remount(self, card, rw):
            self.log.append("rw" if rw else "ro")
    card = cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True, info=info, mount=mount)
    m = Mounter()
    try:
        out = cardd._write_save(m, card, info, b'{"format": "neptune-save/1", "schema": 1, "payload": {"n": 9}}\n', 0, lambda: 0)
    except cardd.CardError as exc:
        out = {"ok": False, "error": exc.to_dict()}
    out["remounts"] = m.log
    print(json.dumps(out))
'''

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "content").mkdir(); (self.tmp / "saves" / "mun.collect").mkdir(parents=True)
        self.game_dir = self.tmp / "saves" / "mun.collect"

    def run_isolated(self, mode):
        import subprocess, sys
        result = subprocess.run([sys.executable, "-c", self.SCRIPT, str(ROOT / "services" / "mun-cardd"),
                                 str(ROOT / "tools" / "mun-card"), str(self.tmp), mode],
                                capture_output=True, text=True, timeout=10)   # the old code never returned
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def names(self):
        return sorted(p.name for p in self.game_dir.iterdir())

    def test_fifo_under_the_canonical_name_is_an_empty_slot_for_restore(self):
        os.mkfifo(self.game_dir / "save.json")
        self.assertEqual(self.run_isolated("restore"), {"present": False})
        self.assertFalse((self.tmp / "work" / "save.json").exists())

    def test_fifo_under_the_previous_name_is_not_a_recovery_source(self):
        os.mkfifo(self.game_dir / "save.json.prev")
        self.assertEqual(self.run_isolated("restore"), {"present": False})

    def test_fifo_under_the_canonical_name_falls_back_to_a_valid_previous(self):
        os.mkfifo(self.game_dir / "save.json")
        (self.game_dir / "save.json.prev").write_bytes(b'{"format": "neptune-save/1", "schema": 1, "payload": {"n": 1}}\n')
        out = self.run_isolated("restore")
        self.assertEqual((out["present"], out["copied"], out["recovered"]), (True, True, True))

    def test_writer_moves_a_canonical_fifo_aside_and_returns_read_only(self):
        os.mkfifo(self.game_dir / "save.json")
        out = self.run_isolated("write")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["remounts"], ["rw", "ro"], "the card is read-only again: cleanup completed")
        names = self.names()
        self.assertIn("save.json", names); self.assertTrue((self.game_dir / "save.json").is_file())
        aside = [n for n in names if n.startswith("save.json.damaged-")]
        self.assertEqual(len(aside), 1); self.assertTrue(statmod.S_ISFIFO(os.lstat(self.game_dir / aside[0]).st_mode))
        self.assertNotIn("save.json.tmp", names)

    def test_writer_replaces_a_fifo_left_under_the_temporary_name(self):
        os.mkfifo(self.game_dir / "save.json.tmp")
        out = self.run_isolated("write")
        self.assertTrue(out["ok"], out); self.assertEqual(out["remounts"], ["rw", "ro"])
        self.assertEqual(self.names(), ["save.json"]); self.assertTrue((self.game_dir / "save.json").is_file())

    def test_special_files_are_never_opened_blocking(self):
        # Direct check of the helper: a FIFO is answered as absent without opening it.
        os.mkfifo(self.game_dir / "save.json")
        dir_fd = os.open(self.game_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            import signal
            def alarm(*_):
                raise TimeoutError("blocked on a special file")
            signal.signal(signal.SIGALRM, alarm); signal.alarm(3)
            try:
                self.assertIsNone(cardd._read_regular(dir_fd, "save.json", 1024))
            finally:
                signal.alarm(0)
        finally:
            os.close(dir_fd)


class StagingCopyTests(unittest.TestCase):
    """_copy_bounded: the only path by which card bytes reach an executable location."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "content"; self.src.mkdir()
        self.dest = self.tmp / "session"; self.dest.mkdir()

    def elf(self, machine=183, cls=2, data=1):
        return b"\x7fELF" + bytes([cls, data, 1, 0]) + b"\0" * 8 + (2).to_bytes(2, "little") + machine.to_bytes(2, "little") + b"\0" * 100

    def test_aarch64_elf_is_copied_and_published_read_only(self):
        (self.src / "game").write_bytes(self.elf())
        reply = cardd._copy_bounded(self.src / "game", self.dest, 1, lambda: 1)
        self.assertTrue(reply["ok"]); self.assertEqual(reply["size"], 120)
        self.assertEqual(oct((self.dest / "game").stat().st_mode & 0o777), "0o555")
        self.assertFalse((self.dest / "game.part").exists())

    def test_non_elf_and_foreign_elf_entries_are_rejected_before_copy(self):
        (self.src / "text").write_text("this is text, not an ELF executable\n")
        (self.src / "x86").write_bytes(self.elf(machine=62))
        (self.src / "elf32").write_bytes(self.elf(cls=1))
        for name in ("text", "x86", "elf32"):
            with self.assertRaises(CardError) as ctx:
                cardd._copy_bounded(self.src / name, self.dest, 1, lambda: 1)
            self.assertEqual(ctx.exception.code, "entry_not_executable", name)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [])

    def test_symlink_and_removal_during_copy_leave_nothing_behind(self):
        (self.src / "game").write_bytes(self.elf())
        (self.src / "link").symlink_to(self.src / "game")
        with self.assertRaises(OSError):
            cardd._copy_bounded(self.src / "link", self.dest, 1, lambda: 1)
        with self.assertRaises(CardError) as ctx:
            cardd._copy_bounded(self.src / "game", self.dest, 1, lambda: 2)   # card generation moved on
        self.assertEqual(ctx.exception.code, "card_removed")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [])


def game_info(**over):
    info = {"id": "mun.collect", "title": "MUN Collect", "version": "0.1.0", "kind": "game", "arch": "aarch64",
            "profile": "linux-arm64-v0", "root": "content", "entry": "content/mun-collect", "cover": None,
            "saves": "saves", "runnable": True, "schema": 1}
    info.update(over)
    return info


class RealDirMounter(FakeMounter):
    """A mounter whose mount point is a real temporary directory, for write tests."""

    def __init__(self, root):
        super().__init__()
        self.root = root

    def mount(self, card, path):
        target = self.root / card.slot
        target.mkdir(parents=True, exist_ok=True)
        (target / "content").mkdir(exist_ok=True)
        (target / "saves").mkdir(exist_ok=True)
        elf = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8 + (2).to_bytes(2, "little") + (183).to_bytes(2, "little") + b"\0" * 100
        (target / "content" / "mun-collect").write_bytes(elf)
        self.mounted[card.slot] = str(target)
        self.log.append(("mount", card.slot))
        return str(target)


class SaveWriteTests(unittest.TestCase):
    """The save write protocol (docs/saves.md): link-safe paths, exclusive temporaries, the current
    save kept until its replacement is published, errors reported, card read-only again."""

    ENTRY = b"\x7fELF the game executable, must never change"

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "content").mkdir(); (self.tmp / "saves").mkdir()
        self.entry = self.tmp / "content" / "mun-collect"; self.entry.write_bytes(self.ENTRY)
        self.info = game_info()
        self.card = cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True,
                               info=self.info, mount=str(self.tmp))
        self.mounter = FakeMounter()
        self.game_dir = self.tmp / "saves" / "mun.collect"
        self.target = self.game_dir / "save.json"

    def envelope(self, n):
        return ('{"format": "neptune-save/1", "schema": 1, "payload": {"n": %d}}\n' % n).encode()

    def write(self, payload=None, generation=0, current=None):
        return cardd._write_save(self.mounter, self.card, self.info, payload or self.envelope(1), generation, current or (lambda: 0))

    def handout(self):
        work = self.tmp / "work"; work.mkdir(exist_ok=True)
        return cardd._copy_save_out(self.tmp, self.info, work / "save.json"), work / "save.json"

    def names(self):
        return sorted(p.name for p in self.game_dir.iterdir()) if self.game_dir.exists() else []

    # --- the protocol -----------------------------------------------------------------
    def test_first_save_creates_dir_and_file_and_ends_read_only(self):
        outcome = self.write()
        self.assertTrue(outcome["ok"]); self.assertEqual(outcome["bytes"], len(self.envelope(1)))
        self.assertEqual(self.names(), ["save.json"])
        self.assertEqual(self.mounter.log, [("remount", "c1", "rw"), ("remount", "c1", "ro")])

    def test_second_save_keeps_the_previous_envelope(self):
        self.write(self.envelope(1)); self.write(self.envelope(2))
        self.assertIn(b'"n": 2', self.target.read_bytes())
        self.assertIn(b'"n": 1', self.target.with_name("save.json.prev").read_bytes())
        self.assertEqual(self.names(), ["save.json", "save.json.prev"])

    def test_damaged_current_file_is_moved_aside_not_deleted(self):
        self.game_dir.mkdir(); self.target.write_bytes(b"garbage that is not an envelope")
        self.write()
        aside = [n for n in self.names() if n.startswith("save.json.damaged-")]
        self.assertEqual(len(aside), 1); self.assertEqual((self.game_dir / aside[0]).read_bytes(), b"garbage that is not an envelope")
        self.assertNotIn("save.json.prev", self.names())

    def test_no_space_is_reported_and_leaves_no_temporary(self):
        import errno
        def failing_write(fd, data):
            raise OSError(errno.ENOSPC, "No space left on device")
        with patch.object(os, "write", failing_write):
            with self.assertRaises(CardError) as ctx:
                self.write()
        self.assertEqual(ctx.exception.code, "no_space")
        self.assertEqual(self.names(), [])
        self.assertEqual(self.mounter.log[-1], ("remount", "c1", "ro"), "read-only again even after a failure")

    def test_card_removed_mid_write_is_reported(self):
        calls = {"n": 0}
        def generation():
            calls["n"] += 1
            return 0 if calls["n"] < 2 else 1
        with self.assertRaises(CardError) as ctx:
            self.write(generation=0, current=generation)
        self.assertEqual(ctx.exception.code, "card_removed"); self.assertEqual(self.names(), [])

    # --- R1: links can never redirect a write -----------------------------------------------
    def test_symlinked_temporary_cannot_redirect_the_write(self):
        self.game_dir.mkdir(); (self.game_dir / "save.json.tmp").symlink_to(self.entry)
        self.assertTrue(self.write()["ok"])
        self.assertEqual(self.entry.read_bytes(), self.ENTRY, "the executable is untouched")
        self.assertEqual(self.names(), ["save.json"]); self.assertFalse(self.target.is_symlink())

    def test_hard_linked_temporary_cannot_redirect_the_write(self):
        self.game_dir.mkdir(); os.link(self.entry, self.game_dir / "save.json.tmp")
        self.assertTrue(self.write()["ok"])
        self.assertEqual(self.entry.read_bytes(), self.ENTRY); self.assertEqual(self.entry.stat().st_nlink, 1)
        self.assertEqual(self.names(), ["save.json"])

    def test_symlinked_canonical_name_is_moved_aside_never_written_through(self):
        self.game_dir.mkdir(); self.target.symlink_to(self.entry)
        self.assertTrue(self.write()["ok"])
        self.assertEqual(self.entry.read_bytes(), self.ENTRY)
        aside = [n for n in self.names() if n.startswith("save.json.damaged-")]
        self.assertEqual(len(aside), 1); self.assertTrue((self.game_dir / aside[0]).is_symlink())
        self.assertFalse(self.target.is_symlink()); self.assertIn(b'"n": 1', self.target.read_bytes())

    def test_symlinked_game_or_saves_directory_is_refused(self):
        (self.tmp / "saves" / "mun.collect").symlink_to(self.tmp / "content", target_is_directory=True)
        with self.assertRaises(CardError) as ctx:
            self.write()
        self.assertEqual(ctx.exception.code, "save_path_unsafe")
        self.assertEqual(sorted(p.name for p in (self.tmp / "content").iterdir()), ["mun-collect"], "nothing written into content")
        self.assertEqual(self.mounter.log[-1], ("remount", "c1", "ro"))
        (self.tmp / "saves" / "mun.collect").unlink(); (self.tmp / "saves").rmdir()
        (self.tmp / "saves").symlink_to(self.tmp / "content", target_is_directory=True)
        with self.assertRaises(CardError) as ctx:
            self.write()
        self.assertEqual(ctx.exception.code, "save_path_unsafe")
        self.assertEqual(sorted(p.name for p in (self.tmp / "content").iterdir()), ["mun-collect"])

    def test_restore_never_follows_links(self):
        (self.tmp / "saves" / "mun.collect").symlink_to(self.tmp / "content", target_is_directory=True)
        outcome, copy = self.handout()
        self.assertEqual((outcome["present"], outcome["copied"], outcome["error"]["code"]), (True, False, "save_path_unsafe"))
        self.assertFalse(copy.exists())
        (self.tmp / "saves" / "mun.collect").unlink(); self.game_dir.mkdir(); self.target.symlink_to(self.entry)
        outcome, copy = self.handout()
        self.assertEqual(outcome, {"present": False}, "a linked save.json is not a save; the entry is never handed out")

    # --- R2: the current save survives a failure at every boundary ----------------------------
    def failing(self, func_name, when):
        """Patch os.<func_name> to raise EIO on the call for which `when(args) is True`."""
        import errno
        original = getattr(os, func_name)
        def wrapper(*args, **kwargs):
            if when(args, kwargs):
                raise OSError(errno.EIO, "Input/output error")
            return original(*args, **kwargs)
        return patch.object(os, func_name, wrapper)

    def assert_loadable_old_save(self):
        outcome, copy = self.handout()
        self.assertTrue(outcome["present"] and outcome["copied"], outcome)
        self.assertIn(b'"n": 1', copy.read_bytes(), "relaunch loads the prior progress")

    def test_failure_writing_the_temporary_keeps_the_current_save(self):
        self.write(self.envelope(1))
        with self.failing("fsync", lambda a, k: True):
            with self.assertRaises(CardError) as ctx:
                self.write(self.envelope(2))
        self.assertEqual(ctx.exception.code, "sync_failed")
        self.assertEqual(self.names(), ["save.json"]); self.assertIn(b'"n": 1', self.target.read_bytes())
        self.assert_loadable_old_save()

    def test_failure_preserving_the_backup_keeps_the_current_save(self):
        self.write(self.envelope(1))
        with self.failing("link", lambda a, k: True):
            with self.assertRaises(CardError) as ctx:
                self.write(self.envelope(2))
        self.assertEqual(ctx.exception.code, "sync_failed")
        self.assertEqual(self.names(), ["save.json"]); self.assertIn(b'"n": 1', self.target.read_bytes())
        self.assert_loadable_old_save()

    def test_failure_at_the_final_rename_keeps_the_current_save(self):
        self.write(self.envelope(1))
        with self.failing("replace", lambda a, k: a[1] == "save.json"):
            with self.assertRaises(CardError) as ctx:
                self.write(self.envelope(2))
        self.assertEqual(ctx.exception.code, "sync_failed")
        self.assertEqual(self.names(), ["save.json", "save.json.prev"])
        self.assertIn(b'"n": 1', self.target.read_bytes(), "canonical name still the old save")
        self.assertIn(b'"n": 1', self.target.with_name("save.json.prev").read_bytes())
        self.assert_loadable_old_save()

    def test_failure_syncing_the_directory_is_reported_but_the_slot_is_loadable(self):
        self.write(self.envelope(1))
        calls = {"n": 0}
        def second_fsync(a, k):
            calls["n"] += 1
            return calls["n"] == 2          # the directory fsync, after the rename
        with self.failing("fsync", second_fsync):
            with self.assertRaises(CardError) as ctx:
                self.write(self.envelope(2))
        self.assertEqual(ctx.exception.code, "sync_failed")
        self.assertEqual(self.names(), ["save.json", "save.json.prev"])
        outcome, copy = self.handout()
        self.assertTrue(outcome["copied"]); self.assertTrue(b'"n": 2' in copy.read_bytes() or b'"n": 1' in copy.read_bytes())

    def test_stale_temporary_from_an_earlier_crash_is_replaced(self):
        self.write(self.envelope(1))
        (self.game_dir / "save.json.tmp").write_bytes(b"half written")
        self.assertTrue(self.write(self.envelope(2))["ok"])
        self.assertEqual(self.names(), ["save.json", "save.json.prev"]); self.assertIn(b'"n": 2', self.target.read_bytes())

    def test_missing_current_with_a_valid_previous_is_recovered_and_said_so(self):
        self.write(self.envelope(1)); self.write(self.envelope(2))
        self.target.unlink()                                   # an older writer or a manual edit left only .prev
        outcome, copy = self.handout()
        self.assertEqual((outcome["present"], outcome["copied"], outcome["recovered"]), (True, True, True))
        document = json.loads(copy.read_text())
        self.assertEqual(document["payload"], {"n": 1}); self.assertEqual(document["recovered_from"], "save.json.prev")

    def test_missing_current_with_a_damaged_previous_is_an_empty_slot(self):
        self.game_dir.mkdir(); (self.game_dir / "save.json.prev").write_bytes(b"not an envelope")
        outcome, copy = self.handout()
        self.assertEqual(outcome, {"present": False}); self.assertFalse(copy.exists())


class ReleaseUnmountTests(unittest.TestCase):
    """R3: safe release confirms only a strict unmount; removed-device cleanup stays best effort."""

    def card(self):
        return cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True, info=game_info(), mount="/run/mun/cards/c1")

    def test_strict_unmount_failure_is_raised_and_the_mount_kept(self):
        mounter = cardd.Mounter(Path("/run/mun/cards"))
        card = self.card(); calls = []
        def run(cmd, **kwargs):
            calls.append(cmd); return Mock(returncode=32, stderr="umount: target is busy.", stdout="")
        with patch.object(cardd.subprocess, "run", run):
            with self.assertRaises(CardError) as ctx:
                mounter.unmount(card, strict=True)
        self.assertEqual(ctx.exception.code, "unmount_failed"); self.assertIn("busy", ctx.exception.detail)
        self.assertEqual(card.mount, "/run/mun/cards/c1", "still tracked: the caller can retry")
        self.assertEqual(calls, [["umount", "/run/mun/cards/c1"]], "no lazy detach on the safe path")

    def test_best_effort_unmount_after_removal_falls_back_to_lazy_and_forgets(self):
        mounter = cardd.Mounter(Path("/run/mun/cards"))
        card = self.card(); calls = []
        def run(cmd, **kwargs):
            calls.append(cmd); return Mock(returncode=32, stderr="umount: target is busy.", stdout="")
        with patch.object(cardd.subprocess, "run", run), patch.object(Path, "rmdir", lambda self: None):
            mounter.unmount(card)
        self.assertIsNone(card.mount)
        self.assertEqual(calls, [["umount", "/run/mun/cards/c1"], ["umount", "-l", "/run/mun/cards/c1"]])

    def test_release_reports_a_failed_unmount_and_allows_retry(self):
        h = Harness(validator=lambda mount: game_info())
        h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); h.pump()
        h.mounter.fail_strict_unmount = True
        replies = []
        h.manager.release({"type": "release", "serial": "NPT-game"}, replies.append)
        self.assertFalse(replies[-1]["ok"]); self.assertEqual(replies[-1]["error"]["code"], "unmount_failed")
        card = h.manager.snapshot()["cards"][0]
        self.assertEqual(card["state"], "valid", "not released: still usable, host must not unplug")
        self.assertIn("c1", h.mounter.mounted)
        stage_replies = []
        h.manager.stage({"type": "stage", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                         "version": "0.1.0", "session": "s1", "dest": "/nowhere"}, stage_replies.append)
        self.assertEqual(stage_replies[-1]["error"]["code"], "bad_destination", "writes/stages are allowed again, not refused as releasing")
        h.mounter.fail_strict_unmount = False
        h.manager.release({"type": "release", "serial": "NPT-game"}, replies.append)
        self.assertTrue(replies[-1]["ok"]); self.assertEqual(h.manager.snapshot()["cards"][0]["state"], "released")
        self.assertEqual(h.mounter.log[-1], ("umount", "c1"))


class SaveRequestTests(unittest.TestCase):
    """CardManager.save/release: authority, identity binding, one writer, safe release."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.mounter = RealDirMounter(self.tmp)
        self.h = Harness(mounter=self.mounter, validator=lambda mount: game_info())
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); self.h.pump()
        self.card = self.h.manager.snapshot()["cards"][0]
        self.replies = []

    def request(self, **over):
        base = {"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1, "payload": {"x": 1, "taken": [0, 1]}}
        base.update(over)
        return base

    def save(self, **over):
        before = len(self.replies)
        self.h.manager.save(self.request(**over), self.replies.append)
        if len(self.replies) == before:      # accepted: the worker answers later
            self.h.pump()
        return self.replies[-1]

    def test_save_writes_envelope_bound_to_the_session(self):
        reply = self.save()
        self.assertTrue(reply["ok"], reply)
        saved = json.loads((self.tmp / "c1" / "saves" / "mun.collect" / "save.json").read_text())
        self.assertEqual((saved["format"], saved["game"], saved["content_version"], saved["schema"]), ("neptune-save/1", "mun.collect", "0.1.0", 1))
        self.assertEqual(saved["session"], "s1"); self.assertEqual(saved["insertion"], self.card["insertion"])
        self.assertEqual(saved["payload"], {"x": 1, "taken": [0, 1]})
        self.assertEqual(self.mounter.log[-2:], [("remount", "c1", "rw"), ("remount", "c1", "ro")])

    def test_wrong_insertion_serial_version_or_game_is_refused_before_any_write(self):
        for over in ({"insertion": "stale"}, {"serial": "NPT-other"}, {"version": "0.2.0"}, {"game": "mun.other"}):
            self.assertEqual(self.save(**over)["error"]["code"], "card_mismatch", over)
        self.assertFalse((self.tmp / "c1" / "saves" / "mun.collect").exists())
        self.assertNotIn(("remount", "c1", "rw"), self.mounter.log)

    def test_bad_payloads_are_refused(self):
        self.assertEqual(self.save(payload="text")["error"]["code"], "save_invalid")
        self.assertEqual(self.save(schema=0)["error"]["code"], "save_invalid")
        self.assertEqual(self.save(schema=True)["error"]["code"], "save_invalid")
        self.assertEqual(self.save(payload={"big": "x" * (64 * 1024)})["error"]["code"], "save_too_large")

    def test_one_save_at_a_time_and_release_waits_for_it(self):
        gate = threading.Event()
        original = cardd._write_save
        def slow_write(*args, **kwargs):
            gate.wait(5)
            return original(*args, **kwargs)
        with patch.object(cardd, "_write_save", slow_write):
            self.h.manager.save(self.request(), self.replies.append)
            self.h.manager.save(self.request(session="s2"), self.replies.append)
            self.assertEqual(self.replies[-1]["error"]["code"], "save_busy")
            released = []
            self.h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
            self.assertEqual(released, [], "release waits for the write in flight")
            self.h.manager.save(self.request(session="s3"), self.replies.append)
            self.assertEqual(self.replies[-1]["error"]["code"], "card_unavailable", "no new writes once releasing")
            gate.set()
            self.h.pump()
        self.assertTrue(any(r.get("ok") for r in self.replies if r.get("session") == "s1"))
        self.assertTrue(released and released[0]["ok"])
        self.assertEqual(self.h.manager.snapshot()["cards"][0]["state"], "released")
        self.assertEqual(self.mounter.log[-1], ("umount", "c1"))

    def test_release_of_idle_card_unmounts_at_once(self):
        released = []
        self.h.manager.release({"type": "release", "serial": "NPT-game"}, released.append)
        self.assertTrue(released[0]["ok"]); self.assertEqual(self.mounter.log[-1], ("umount", "c1"))
        self.assertEqual(self.h.events[-1]["card"]["state"], "released")
        stage_replies = []
        self.h.manager.stage({"type": "stage", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                              "version": "0.1.0", "session": "s9", "dest": "/run/mun/launch/s9"}, stage_replies.append)
        self.assertEqual(stage_replies[-1]["error"]["code"], "card_unavailable")

    def test_stage_hands_the_current_save_to_the_session(self):
        self.save()
        dest = self.tmp / "dest"; (dest / "work").mkdir(parents=True)
        replies = []
        with patch.object(cardd, "_copy_bounded", lambda *a, **k: {"ok": True, "path": "x", "size": 1, "sha256": "ab"}), \
                patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.tmp) + "/"):
            self.h.manager.stage({"type": "stage", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                                  "version": "0.1.0", "session": "s2", "dest": str(dest)}, replies.append)
            self.h.pump()
        self.assertTrue(replies[-1]["ok"]); self.assertEqual(replies[-1]["save"]["copied"], True)
        self.assertEqual(json.loads((dest / "work" / "save.json").read_text())["payload"], {"x": 1, "taken": [0, 1]})
        self.assertEqual(oct((dest / "work" / "save.json").stat().st_mode & 0o777), "0o600")

    def test_stage_without_a_save_reports_none(self):
        dest = self.tmp / "dest"; (dest / "work").mkdir(parents=True)
        replies = []
        with patch.object(cardd, "_copy_bounded", lambda *a, **k: {"ok": True, "path": "x", "size": 1, "sha256": "ab"}), \
                patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.tmp) + "/"):
            self.h.manager.stage({"type": "stage", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                                  "version": "0.1.0", "session": "s2", "dest": str(dest)}, replies.append)
            self.h.pump()
        self.assertEqual(replies[-1]["save"], {"present": False}); self.assertFalse((dest / "work" / "save.json").exists())

    def test_stage_names_the_content_directory_only_for_mount_access(self):
        dest = self.tmp / "dest"; (dest / "work").mkdir(parents=True)
        replies = []
        with patch.object(cardd, "_copy_bounded", lambda *a, **k: {"ok": True, "path": "x", "size": 1, "sha256": "ab"}), \
                patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.tmp) + "/"):
            request = {"type": "stage", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                       "version": "0.1.0", "session": "s3", "dest": str(dest)}
            self.h.manager.stage(request, replies.append); self.h.pump()
            self.assertNotIn("content", replies[-1], "copy access: nothing to bind")
            card = next(iter(self.h.manager.cards.values()))
            card.info["access"] = "mount"
            self.h.manager.stage(request, replies.append); self.h.pump()
        self.assertTrue(replies[-1]["ok"])
        self.assertEqual(replies[-1]["content"], {"device": self.mounter.device_path(card.device), "root": "content"})

    def test_mount_options_load_the_journal_read_only(self):
        self.assertEqual(cardd.MOUNT_OPTIONS, "ro,nosuid,nodev,noexec", "no noload: a later rw remount must be journaled")


if __name__ == "__main__":
    unittest.main()


class DirectorySaveCardTests(unittest.TestCase):
    """Directory saves on the card side: the payload file, per-card bounds, integrity,
    a damaged envelope never becomes the previous copy, restore falls back."""

    SPEC = {"saves_directory": ".Sample/save", "saves_units": ["*.sav"], "saves_checks": ["zlib-xml"],
            "saves_max_bytes": 1024 * 1024}

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.launch = self.tmp / "launch"
        (self.launch / "s1" / "sync").mkdir(parents=True)
        self.mounter = RealDirMounter(self.tmp / "cards")
        self.info = game_info(id="sample.lab", **self.SPEC)
        self.h = Harness(mounter=self.mounter, validator=lambda mount: dict(self.info))
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); self.h.pump()
        self.card = self.h.manager.snapshot()["cards"][0]
        self.replies = []
        self.sequence = 0
        patches = [patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(self.launch) + "/"),
                   patch.object(cardd, "PAYLOAD_OWNER_UID", os.getuid())]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def payload(self, files):
        import base64, hashlib
        return {"captured_at": "2026-09-26T10:00:00Z", "carried": [],
                "files": [{"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                           "data": base64.b64encode(d).decode()} for n, d in files.items()]}

    def save_files(self, files=None, payload=None, **over):
        import hashlib
        self.sequence += 1
        raw = json.dumps(payload if payload is not None else self.payload(files)).encode()
        path = self.launch / "s1" / "sync" / f"payload-{self.sequence}.json"
        path.write_bytes(raw)
        request = {"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                   "version": "0.1.0", "game": "sample.lab", "session": "s1", "schema": 1, "payload_kind": "files",
                   "payload_file": str(path), "payload_size": len(raw), "payload_sha256": hashlib.sha256(raw).hexdigest()}
        request.update(over)
        before = len(self.replies)
        self.h.manager.save(request, self.replies.append)
        if len(self.replies) == before:
            self.h.pump()
        return self.replies[-1]

    def save_dir(self):
        return self.tmp / "cards" / "c1" / "saves" / "sample.lab"

    def test_files_payload_is_written_as_an_envelope_with_its_kind(self):
        import zlib
        unit = zlib.compress(b"<Flag a='1'/><Pos x='2'/>")
        reply = self.save_files({"save-0000.sav": unit})
        self.assertTrue(reply["ok"], reply)
        saved = json.loads((self.save_dir() / "save.json").read_text())
        self.assertEqual((saved["payload_kind"], saved["game"]), ("files", "sample.lab"))
        self.assertEqual(saved["payload"]["files"][0]["path"], "save-0000.sav")

    def test_payload_file_outside_the_launch_root_link_or_mismatch_is_refused(self):
        import hashlib
        outside = self.tmp / "elsewhere.json"; outside.write_text("{}")
        self.assertEqual(self.save_files({}, payload_file=str(outside))["error"]["code"], "save_invalid")
        self.assertEqual(self.save_files({}, payload_file=str(self.launch / "s1" / ".." / "x"))["error"]["code"], "save_invalid")
        link = self.launch / "s1" / "sync" / "link.json"; link.symlink_to(outside)
        self.assertEqual(self.save_files({}, payload_file=str(link), payload_size=2,
                                         payload_sha256=hashlib.sha256(b"{}").hexdigest())["error"]["code"], "save_invalid")
        self.assertEqual(self.save_files({"a.sav": b"x"}, payload_sha256="0" * 64)["error"]["code"], "save_invalid")
        self.assertEqual(self.save_files({"a.sav": b"x"}, payload_size=10 ** 9)["error"]["code"], "save_too_large")
        self.assertFalse((self.save_dir() / "save.json").exists())

    def test_payload_whose_files_do_not_match_or_escape_is_refused(self):
        bad = self.payload({"a.sav": b"abc"}); bad["files"][0]["sha256"] = "0" * 64
        self.assertEqual(self.save_files(payload=bad)["error"]["code"], "save_invalid")
        escape = self.payload({"a.sav": b"abc"}); escape["files"][0]["path"] = "../a.sav"
        self.assertEqual(self.save_files(payload=escape)["error"]["code"], "save_invalid")
        big = self.payload({"a.sav": b"x" * (1024 * 1024 + 1)})
        self.assertIn(self.save_files(payload=big)["error"]["code"], ("save_invalid", "save_too_large"))
        self.assertFalse((self.save_dir() / "save.json").exists())

    def test_single_object_saves_are_refused_on_a_directory_card(self):
        before = len(self.replies)
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                             "version": "0.1.0", "game": "sample.lab", "session": "s1", "schema": 1,
                             "payload": {"x": 1}}, self.replies.append)
        self.assertEqual(len(self.replies), before + 1)
        self.assertEqual(self.replies[-1]["error"]["code"], "save_invalid")

    def test_a_damaged_envelope_never_replaces_the_previous_copy(self):
        good = self.save_files({"save-0000.sav": b"one"}); self.assertTrue(good["ok"])
        first = (self.save_dir() / "save.json").read_bytes()
        # The current envelope is damaged on the card (its file no longer matches its hash).
        document = json.loads(first); document["payload"]["files"][0]["sha256"] = "0" * 64
        (self.save_dir() / "save.json").write_text(json.dumps(document))
        (self.save_dir() / "save.json.prev").write_bytes(first)
        self.assertTrue(self.save_files({"save-0000.sav": b"two"})["ok"])
        self.assertEqual((self.save_dir() / "save.json.prev").read_bytes(), first, "the valid previous copy stays")
        self.assertTrue(list(self.save_dir().glob("save.json.damaged-*")), "the damaged one is kept aside")

    def test_restore_falls_back_to_the_previous_copy_or_reports_damage(self):
        self.assertTrue(self.save_files({"save-0000.sav": b"one"})["ok"])
        self.assertTrue(self.save_files({"save-0000.sav": b"two"})["ok"])
        target = self.tmp / "work"; target.mkdir()
        (self.save_dir() / "save.json").write_text('{"format": "neptune-save/1", "payload_kind": "files", "payload": {"files": 3}}')
        out = cardd._copy_save_out(self.tmp / "cards" / "c1", self.info, target / "save.json")
        self.assertEqual((out["copied"], out["recovered"]), (True, True))
        restored = json.loads((target / "save.json").read_text())
        self.assertEqual(restored["recovered_from"], "save.json.prev")
        (self.save_dir() / "save.json.prev").write_text("not json")
        out = cardd._copy_save_out(self.tmp / "cards" / "c1", self.info, target / "save2.json")
        self.assertEqual((out["copied"], out["error"]["code"]), (False, "save_damaged"))

    def test_bounds_follow_the_card(self):
        self.assertEqual(cardd.save_bounds({}), (cardd.SAVE_MAX_BYTES, cardd.SAVE_FILE_MAX_BYTES))
        payload, file_bound = cardd.save_bounds({"saves_max_bytes": 3 * 1024 * 1024})
        self.assertEqual(payload, 4 * 1024 * 1024 + 64 * 1024); self.assertEqual(file_bound, payload + 8 * 1024)


class NamingGenerationSaveTests(unittest.TestCase):
    """Naming generations on the card side: a card's saves are written in its own
    generation's format, and the console, not the game, refuses to hand out
    an envelope of the other generation, for single-object and directory saves."""

    FORMATS = {"mun": "mun-save/1", "earlier": "neptune-save/1"}
    DIRECTORY = {"saves_directory": ".Sample/save", "saves_units": ["*.sav"], "saves_checks": ["zlib-xml"],
                 "saves_max_bytes": 1024 * 1024}

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.game_dir = self.tmp / "saves" / "mun.collect"
        self.game_dir.mkdir(parents=True)
        (self.tmp / "work").mkdir()

    def envelope(self, fmt, n=1, **extra):
        return (json.dumps({"format": fmt, "game": "mun.collect", "schema": 1, "payload": {"n": n}, **extra}) + "\n").encode()

    def files_envelope(self, fmt, data=b"<Save/>"):
        import base64, hashlib
        payload = {"files": [{"path": "save-0000.sav", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "data": base64.b64encode(data).decode()}]}
        return self.envelope(fmt, payload_kind="files").replace(b'{"n": 1}', json.dumps(payload).encode())

    def handout(self, info):
        target = self.tmp / "work" / "save.json"
        if target.exists():
            target.unlink()
        return cardd._copy_save_out(self.tmp, info, target), target

    def test_each_generation_writes_its_own_format(self):
        for naming, expected in (("mun", "mun-save/1"), ("earlier", "neptune-save/1"), (None, "neptune-save/1")):
            with self.subTest(naming=naming):
                import tempfile
                root = Path(tempfile.mkdtemp())
                info = game_info(**({"naming": naming} if naming else {}))
                h = Harness(mounter=RealDirMounter(root), validator=lambda mount, info=info: dict(info))
                h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); h.pump()
                card = h.manager.snapshot()["cards"][0]
                replies = []
                h.manager.save({"type": "save", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                                "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                                "payload": {"x": 1}}, replies.append)
                h.pump()
                self.assertTrue(replies[-1]["ok"], replies)
                saved = json.loads((root / "c1" / "saves" / "mun.collect" / "save.json").read_text())
                self.assertEqual(saved["format"], expected)

    def test_single_object_save_of_the_other_generation_uses_the_previous_one(self):
        for naming, other in (("mun", "earlier"), ("earlier", "mun")):
            with self.subTest(card=naming):
                (self.game_dir / "save.json").write_bytes(self.envelope(self.FORMATS[other], 3))
                (self.game_dir / "save.json.prev").write_bytes(self.envelope(self.FORMATS[naming], 2))
                out, target = self.handout(game_info(naming=naming))
                self.assertEqual((out["copied"], out["recovered"]), (True, True))
                handed = json.loads(target.read_bytes())
                self.assertEqual((handed["format"], handed["payload"]), (self.FORMATS[naming], {"n": 2}))
                self.assertEqual(handed["recovered_from"], "save.json.prev")

    def test_single_object_save_of_the_other_generation_without_a_previous_one_is_not_handed_out(self):
        (self.game_dir / "save.json").write_bytes(self.envelope("neptune-save/1", 3))
        for previous in (None, self.envelope("neptune-save/1", 2), b"not json"):
            with self.subTest(previous=previous):
                prev = self.game_dir / "save.json.prev"
                if previous is None:
                    prev.unlink(missing_ok=True)
                else:
                    prev.write_bytes(previous)
                out, target = self.handout(game_info(naming="mun"))
                self.assertEqual((out["copied"], out["error"]["code"]), (False, "save_other_generation"))
                self.assertFalse(target.exists(), "a game that reads both formats must not see it")
                self.assertIn(b"neptune-save/1", (self.game_dir / "save.json").read_bytes(), "left on the card")

    def test_a_single_object_file_that_is_not_an_envelope_is_still_handed_to_the_game(self):
        (self.game_dir / "save.json").write_bytes(b"garbage {")
        out, target = self.handout(game_info(naming="mun"))
        self.assertTrue(out["copied"])
        self.assertEqual(target.read_bytes(), b"garbage {")

    def test_a_current_save_of_the_other_generation_is_moved_aside_when_the_game_saves(self):
        info = game_info(naming="mun")
        card = cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True, info=info, mount=str(self.tmp))
        (self.game_dir / "save.json").write_bytes(self.envelope("neptune-save/1", 3))
        outcome = cardd._write_save(FakeMounter(), card, info, self.envelope("mun-save/1", 4), 0, lambda: 0)
        self.assertTrue(outcome["ok"], outcome)
        aside = list(self.game_dir.glob("save.json.damaged-*"))
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_bytes(), self.envelope("neptune-save/1", 3), "kept byte for byte")
        self.assertFalse((self.game_dir / "save.json.prev").exists(), "never the previous copy of this card")

    def test_directory_save_of_the_other_generation_falls_back_or_is_refused(self):
        info = game_info(naming="mun", **self.DIRECTORY)
        (self.game_dir / "save.json").write_bytes(self.files_envelope("neptune-save/1", b"<old/>"))
        (self.game_dir / "save.json.prev").write_bytes(self.files_envelope("mun-save/1", b"<prev/>"))
        out, target = self.handout(info)
        self.assertEqual((out["copied"], out["recovered"]), (True, True))
        self.assertEqual(json.loads(target.read_bytes())["format"], "mun-save/1")
        (self.game_dir / "save.json.prev").write_bytes(self.files_envelope("neptune-save/1", b"<prev/>"))
        out, target = self.handout(info)
        self.assertEqual((out["copied"], out["error"]["code"]), (False, "save_other_generation"))
        self.assertFalse(target.exists())
        # The same envelopes are valid on a card of the earlier generation.
        out, target = self.handout(game_info(naming="earlier", **self.DIRECTORY))
        self.assertEqual((out["copied"], out["recovered"]), (True, False))
