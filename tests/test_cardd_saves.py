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


if __name__ == "__main__":
    unittest.main()
