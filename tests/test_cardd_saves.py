"""Saves as the card holds them, read back by mun-cardd: files a card's
author can write under a save's name, and the sizes the console accepts and
reads (docs/saves.md)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from test_cardd import FakeMounter, Harness, RealDirMounter, game_info  # noqa: E402

DEEP = b"[" * 70000                      # valid JSON so far, nested deeper than any parser goes
LONE = b'{"format": "neptune-save/1", "game": "mun.collect", "schema": 1, "payload": {"name": "\\ud800"}}\n'
GOOD = b'{"format": "neptune-save/1", "game": "mun.collect", "schema": 1, "payload": {"n": 1}}\n'
DIRECTORY = {"saves_directory": ".Sample/save", "saves_units": ["*.sav"], "saves_checks": ["any"],
             "saves_max_bytes": 1024 * 1024}


def files_envelope(data=b"<Save/>"):
    import base64, hashlib
    payload = {"files": [{"path": "save-0000.sav", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "data": base64.b64encode(data).decode()}]}
    return (json.dumps({"format": "neptune-save/1", "game": "mun.collect", "schema": 1, "payload_kind": "files",
                        "payload": payload}) + "\n").encode()


class HostileSaveFileTests(unittest.TestCase):
    """What a card's author left under a save's name never ends the thread
    that reads it: it is handed over, set aside or ignored like any other
    file that is not a save."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.game_dir = self.tmp / "saves" / "mun.collect"
        self.game_dir.mkdir(parents=True)
        (self.tmp / "content").mkdir(); (self.tmp / "work").mkdir()
        silenced = redirect_stdout(io.StringIO()); silenced.__enter__(); self.addCleanup(silenced.__exit__, None, None, None)

    def handout(self, info=None):
        target = self.tmp / "work" / "save.json"
        if target.exists():
            target.unlink()
        return cardd._copy_save_out(self.tmp, info or game_info(), target), target

    def test_a_deeply_nested_save_is_handed_to_a_single_object_game_as_any_damaged_file(self):
        (self.game_dir / "save.json").write_bytes(DEEP)
        out, target = self.handout()
        self.assertEqual((out["present"], out["copied"], out["recovered"]), (True, True, False))
        self.assertEqual(target.read_bytes(), DEEP, "the game is handed the file and says it is damaged")

    def test_a_deeply_nested_previous_copy_is_not_a_recovery_source(self):
        (self.game_dir / "save.json.prev").write_bytes(DEEP)
        out, target = self.handout()
        self.assertEqual(out, {"present": False}); self.assertFalse(target.exists())

    def test_a_deeply_nested_directory_save_falls_back_or_is_reported_damaged(self):
        info = game_info(**DIRECTORY)
        (self.game_dir / "save.json").write_bytes(DEEP)
        out, target = self.handout(info)
        self.assertEqual((out["copied"], out["error"]["code"]), (False, "save_damaged"))
        (self.game_dir / "save.json.prev").write_bytes(DEEP)
        out, target = self.handout(info)
        self.assertEqual((out["copied"], out["error"]["code"]), (False, "save_damaged"))
        (self.game_dir / "save.json.prev").write_bytes(files_envelope())
        out, target = self.handout(info)
        self.assertEqual((out["copied"], out["recovered"]), (True, True))
        self.assertEqual(json.loads(target.read_bytes())["recovered_from"], "save.json.prev")

    def test_a_previous_copy_with_text_utf8_cannot_carry_is_not_a_recovery_source(self):
        # The console refuses to store such text, so it never wrote this file.
        (self.game_dir / "save.json.prev").write_bytes(LONE)
        out, target = self.handout()
        self.assertEqual(out, {"present": False}); self.assertFalse(target.exists())

    def test_a_current_save_with_such_text_is_handed_over_byte_for_byte(self):
        (self.game_dir / "save.json").write_bytes(LONE)
        out, target = self.handout()
        self.assertEqual((out["copied"], out["recovered"]), (True, False))
        self.assertEqual(target.read_bytes(), LONE)

    def test_an_ordinary_previous_copy_is_still_recovered_and_marked(self):
        (self.game_dir / "save.json.prev").write_bytes(GOOD)
        out, target = self.handout()
        self.assertEqual((out["present"], out["copied"], out["recovered"]), (True, True, True))
        document = json.loads(target.read_bytes())
        self.assertEqual((document["payload"], document["recovered_from"]), ({"n": 1}, "save.json.prev"))

    def test_saving_over_a_deeply_nested_file_sets_it_aside_and_ends_read_only(self):
        (self.game_dir / "save.json").write_bytes(DEEP)
        info = game_info()
        card = cardd.Card(slot="c1", device="vdc", serial="NPT-game", state="valid", active=True, info=info, mount=str(self.tmp))
        mounter = FakeMounter()
        outcome = cardd._write_save(mounter, card, info, GOOD, 0, lambda: 0)
        self.assertTrue(outcome["ok"], outcome)
        names = sorted(p.name for p in self.game_dir.iterdir())
        aside = [n for n in names if n.startswith("save.json.damaged-")]
        self.assertEqual(len(aside), 1); self.assertEqual((self.game_dir / aside[0]).read_bytes(), DEEP)
        self.assertEqual([n for n in names if n not in aside], ["save.json"], "never the previous copy, no temporary")
        self.assertEqual(mounter.log[-1], ("remount", "c1", "ro"))

    def test_a_session_is_staged_from_a_card_with_such_files(self):
        # Through the manager and its worker thread, as a launch does.
        root = Path(tempfile.mkdtemp()); self.addCleanup(__import__("shutil").rmtree, root, True)
        h = Harness(mounter=RealDirMounter(root), validator=lambda mount: game_info())
        h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); h.pump()
        card = h.manager.snapshot()["cards"][0]
        game_dir = root / "c1" / "saves" / "mun.collect"; game_dir.mkdir()
        for current, previous in ((DEEP, None), (None, DEEP), (None, LONE), (DEEP, LONE)):
            with self.subTest(current=current and current[:20], previous=previous and previous[:20]):
                for name, data in (("save.json", current), ("save.json.prev", previous)):
                    (game_dir / name).unlink(missing_ok=True) if data is None else (game_dir / name).write_bytes(data)
                dest = Path(tempfile.mkdtemp(dir=root)); (dest / "work").mkdir()
                replies = []
                with patch.object(cardd, "LAUNCH_ROOT_PREFIX", str(root) + "/"):
                    h.manager.stage({"type": "stage", "slot": "c1", "insertion": card["insertion"], "serial": "NPT-game",
                                     "version": "0.1.0", "session": "s1", "dest": str(dest)}, replies.append)
                    h.pump()
                self.assertTrue(replies and replies[-1]["ok"], replies)
                self.assertEqual(replies[-1]["save"].get("present"), current is not None)


def compact(payload):
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class SaveSizeTests(unittest.TestCase):
    """docs/saves.md: a game's object of at most 64 KiB is saved, and what is
    saved is what the next launch reads. The bound a save is accepted under
    and the bound it is read back with are one."""

    # A plausible save: a map of single-digit tiles. Few bytes per separator,
    # so the envelope, which is written with a space after each, is half as
    # large again as the object the game sent.
    TILES = {"level": 3, "tiles": [i % 10 for i in range(30000)]}
    # The largest object the contract allows, of the shape that grows most.
    LARGEST = {"t": [0] * 32761, "p": ""}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.mounter = RealDirMounter(self.tmp)
        self.h = Harness(mounter=self.mounter, validator=lambda mount: game_info())
        silenced = redirect_stdout(io.StringIO()); silenced.__enter__(); self.addCleanup(silenced.__exit__, None, None, None)
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); self.h.pump()
        self.card = self.h.manager.snapshot()["cards"][0]
        self.game_dir = self.tmp / "c1" / "saves" / "mun.collect"
        self.replies = []

    def save(self, payload, **over):
        request = {"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                   "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1, "payload": payload}
        request.update(over)
        before = len(self.replies)
        self.h.manager.save(request, self.replies.append)
        if len(self.replies) == before:
            self.h.pump()
        return self.replies[-1]

    def next_launch(self):
        """What the next launch hands the game as its save file."""
        work = Path(tempfile.mkdtemp(dir=self.tmp))
        out = cardd._copy_save_out(self.tmp / "c1", game_info(), work / "save.json")
        return out, (work / "save.json").read_bytes() if out.get("copied") else None

    def names(self):
        return sorted(p.name for p in self.game_dir.iterdir()) if self.game_dir.exists() else []

    def assert_round_trip(self, payload, follow):
        reply = self.save(payload)
        self.assertTrue(reply["ok"], reply)
        on_card = (self.game_dir / "save.json").read_bytes()
        self.assertEqual(reply["bytes"], len(on_card))
        out, handed = self.next_launch()
        self.assertEqual((out["present"], out["copied"], out["recovered"]), (True, True, False))
        self.assertEqual(handed, on_card, "the game is handed the whole envelope")
        self.assertEqual(json.loads(handed)["payload"], payload)
        # The following save keeps it as the previous copy; nothing is set aside as damaged.
        self.assertTrue(self.save(follow)["ok"])
        self.assertEqual(self.names(), ["save.json", "save.json.prev"])
        self.assertEqual((self.game_dir / "save.json.prev").read_bytes(), on_card)
        self.assertEqual(json.loads(self.next_launch()[1])["payload"], follow)
        (self.game_dir / "save.json").unlink()            # and that previous copy is a recovery source
        out, handed = self.next_launch()
        self.assertEqual(out["recovered"], True); self.assertEqual(json.loads(handed)["payload"], payload)

    def test_a_60021_byte_save_is_read_back_at_the_next_launch(self):
        self.assertEqual(compact(self.TILES), 60021)
        self.assert_round_trip(self.TILES, dict(self.TILES, level=4))

    def test_the_largest_save_the_contract_allows_is_read_back(self):
        self.assertEqual(compact(self.LARGEST), cardd.SAVE_MAX_BYTES)
        self.assert_round_trip(self.LARGEST, {"t": [1] + [0] * 32760, "p": ""})

    def test_an_ordinary_small_save_is_read_back(self):
        self.assert_round_trip({"level": 1, "name": "ñ 収集", "taken": [0, 1, 2]}, {"level": 2, "taken": []})

    def test_a_save_over_the_limit_is_refused_before_anything_is_written(self):
        over = {"t": [0] * 32761, "p": "x"}
        self.assertEqual(compact(over), cardd.SAVE_MAX_BYTES + 1)
        reply = self.save(over)
        self.assertEqual((reply["ok"], reply["error"]["code"]), (False, "save_too_large"))
        self.assertEqual(self.names(), []); self.assertFalse(self.game_dir.exists())
        self.assertNotIn(("remount", "c1", "rw"), self.mounter.log)

    def test_an_envelope_the_next_launch_would_not_read_is_never_written(self):
        # Whatever makes the envelope large, here the session's name: the
        # bytes to be written are measured against the bound they are read with.
        payload_max, file_max = cardd.save_bounds(game_info())
        reply = self.save(self.LARGEST, session="s" * (file_max - payload_max * 3 // 2))
        self.assertEqual((reply["ok"], reply["error"]["code"]), (False, "save_too_large"))
        self.assertEqual(self.names(), [])
        self.assertNotIn(("remount", "c1", "rw"), self.mounter.log)
        self.assertTrue(self.save(self.LARGEST)["ok"], "the same payload under an ordinary session is saved")

    def test_no_accepted_payload_can_outgrow_the_bound_it_is_read_with(self):
        payload_max, file_max = cardd.save_bounds(game_info())
        self.assertEqual(payload_max, cardd.SAVE_MAX_BYTES)
        shapes = ([0] * 32761, [[]] * 21000, [{"a": 0}] * 8000, {str(i): 0 for i in range(7000)},
                  [True, None, 1.5, "ñ, : 収"] * 2000, "x" * 60000, [[[[0, 0], [0, 0]], [[0, 0], [0, 0]]]] * 1500)
        for shape in shapes:
            # Each lengthened by a string to exactly the largest size accepted.
            payload = {"t": shape, "p": "x" * (payload_max - compact({"t": shape, "p": ""}))}
            size = compact(payload)
            self.assertEqual(size, payload_max)
            written = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            self.assertLessEqual(written, size * 3 // 2, "a space per separator: at most half as much again")
            self.assertLessEqual(written + 4096, file_max, "with room for the envelope's own fields")

    def test_a_card_with_an_envelope_written_before_the_bounds_agreed_is_restored(self):
        # As the published version left it: a confirmed 90 KiB envelope it could
        # not read back, and a copy set aside as damaged by each later save.
        envelope = (json.dumps({"format": "neptune-save/1", "game": "mun.collect", "content_version": "0.1.0",
                                "schema": 1, "saved_at": "2026-11-14T10:00:00Z", "session": "s0",
                                "insertion": "0123456789abcdef", "payload": self.TILES}, ensure_ascii=False) + "\n").encode()
        self.assertGreater(len(envelope), 90000)
        self.game_dir.mkdir()
        (self.game_dir / "save.json").write_bytes(envelope)
        for stamp in ("20261114T100100Z", "20261114T100200Z"):
            (self.game_dir / f"save.json.damaged-{stamp}").write_bytes(envelope)
        out, handed = self.next_launch()
        self.assertEqual((out["copied"], out["recovered"], out["bytes"]), (True, False, len(envelope)))
        self.assertEqual(json.loads(handed)["payload"], self.TILES, "loadable again")
        self.assertTrue(self.save(dict(self.TILES, level=4))["ok"])
        self.assertEqual(self.names(), ["save.json", "save.json.damaged-20261114T100100Z",
                                        "save.json.damaged-20261114T100200Z", "save.json.prev"],
                         "kept as the previous copy: no further damaged copy")
        self.assertEqual((self.game_dir / "save.json.prev").read_bytes(), envelope)
        self.assertTrue(self.save(dict(self.TILES, level=5))["ok"])
        self.assertEqual(len([n for n in self.names() if ".damaged-" in n]), 2)

    def test_the_card_tool_judges_such_an_envelope_as_the_console_does(self):
        from mun_card import convert, saves
        from mun_card.source import DirectorySource
        self.assertEqual(saves.save_bounds(game_info()), cardd.save_bounds(game_info()))
        self.assertTrue(self.save(self.LARGEST)["ok"])
        self.assertTrue(self.save({"t": [1] + [0] * 32760, "p": ""})["ok"])       # save.json and save.json.prev
        documents = convert._card_saves(DirectorySource(self.tmp / "c1"), game_info(), "saves/mun.collect")
        self.assertEqual(sorted(documents), ["save.json", "save.json.prev"])
        self.assertEqual(documents["save.json.prev"]["payload"], self.LARGEST)

    def test_a_request_as_large_as_the_contract_allows_may_arrive_in_pieces(self):
        # The launcher's line for the largest save, read in two parts as a socket may deliver it.
        import socket
        request = {"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                   "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1, "payload": self.LARGEST}
        line = (json.dumps(request) + "\n").encode("utf-8")
        control = cardd.ControlServer(self.tmp / "unused.sock", self.h.manager)
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close); self.addCleanup(theirs.close)
        for sock in (ours, theirs):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        ours.setblocking(False); theirs.settimeout(5)
        control.buffers[ours] = b""
        theirs.sendall(line[:60000]); self.assertTrue(control.handle(ours))
        theirs.sendall(line[60000:90000]); self.assertTrue(control.handle(ours), "not refused for its size")
        theirs.sendall(line[90000:])
        while ours in control.buffers and b"\n" not in control.buffers[ours] and control.handle(ours):
            if not control.buffers.get(ours):
                break
        self.h.pump()
        reply = json.loads(theirs.recv(65536))
        self.assertEqual((reply["type"], reply.get("ok")), ("saved", True), reply)


if __name__ == "__main__":
    unittest.main()
