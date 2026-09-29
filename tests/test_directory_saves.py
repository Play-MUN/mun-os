"""Directory saves in mun-launchd (docs/saves.md): the capture rule, carried units,
restore validation and the session lifecycle, with a fake freezer and a fake
inotify; the Linux-only pieces run where Linux is."""

import base64
import calendar
import hashlib
import json
import os
import sys
import tempfile
import time
import shutil
import unittest
import unittest.mock
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-launchd"))
sys.path.insert(0, str(ROOT / "tests"))

import launchd  # noqa: E402
from test_launchd import FakeRunner, Harness, game_card  # noqa: E402

SPEC = {"directory": ".Sample/save", "units": ["*.sav", "screen-*.zsc"], "checks": ["zlib-xml", "zlib"],
        "max_bytes": 1024 * 1024, "card_id": "sample.lab"}
# As one measured engine writes them: several top-level elements, the C string terminator last.
SLOT = zlib.compress(b"<?xml version='1.0'?>\n<Flag a='1'/><Pos x='2' y='3'/>\n\0")
SLOT2 = zlib.compress(b"<Flag a='2'/><Pos x='9' y='9'/>\n\0")
THUMB = zlib.compress(b"\x00\x10" * 5000)


class FakeInotify:
    def __init__(self):
        self.events, self.watched = [], []

    def add(self, path):
        self.watched.append(path)
        return 1

    def read(self):
        events, self.events = self.events, []
        return events

    def close(self):
        pass


class FakeHost:
    """The Runner's freezer and the read-lease probe; records the order of calls.
    `writing`: [] no writer, a non-empty list a writer, None leases unavailable."""

    def __init__(self):
        self.calls, self.writing, self.freezable = [], [], True

    def freeze(self, sid):
        self.calls.append("freeze")
        return self.freezable

    def thaw(self, sid):
        self.calls.append("thaw")

    def lease(self, fd):
        self.calls.append("lease")
        return None if self.writing is None else not self.writing


def envelope(files, game="sample.lab", kind="files", recovered=False, **over):
    document = {"format": "neptune-save/1", "game": game, "schema": 1, "payload_kind": kind,
                "payload": {"files": [{"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                                       "data": base64.b64encode(d).decode()} for n, d in files.items()]}}
    if recovered:
        document["recovered_from"] = "save.json.prev"
    document.update(over)
    return json.dumps(document).encode()


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.dest = Path(tempfile.mkdtemp()) / "s1"
        (self.dest / "work").mkdir(parents=True)
        self.host, self.sent = FakeHost(), []
        self.watch = FakeInotify()
        self.clock = [1_790_000_000.0]
        self.sync = launchd.DirectorySync("s1", dict(SPEC), self.dest, os.getuid(), os.getgid(), self.host,
                                          lambda path, size, digest, done: self.sent.append((json.loads(Path(path).read_bytes()), done)),
                                          wall=lambda: self.clock[0], inotify_factory=lambda: self.watch,
                                          root_ids=(os.getuid(), os.getgid()), lease=self.host.lease)
        self.addCleanup(self.sync.close)

    def prepare(self, card_save=None, staged=None):
        path = self.dest / "work" / "save.json"
        if card_save is not None:
            path.write_bytes(card_save)
        self.sync.prepare(path, staged if staged is not None else ({"present": True, "copied": True} if card_save else {"present": False}))

    @property
    def savedir(self):
        return self.dest / "work" / ".Sample" / "save"

    def game_writes(self, name, data, close=True):
        (self.savedir / name).write_bytes(data)
        self.watch.events.append((launchd.IN_CREATE, name))
        if close:
            self.watch.events.append((launchd.IN_CLOSE_WRITE, name))

    def files_sent(self, index=-1):
        return {f["path"]: base64.b64decode(f["data"]) for f in self.sent[index][0]["files"]}

    def answer(self, ok=True, index=-1, **error):
        self.sent[index][1]({"ok": True, "bytes": 1000} if ok else {"ok": False, "error": error})


class CaptureRuleTests(SyncCase):
    def test_a_closed_unit_is_captured_with_the_game_frozen(self):
        self.prepare()
        self.assertEqual(self.watch.watched, [Path(f"/proc/self/fd/{self.sync.dir_fd}")],
                         "watch starts before the game, bound to the directory the launcher holds, not to a path")
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()
        self.assertEqual(self.host.calls, ["freeze", "lease", "thaw"], "leased and read only between freeze and thaw")
        self.assertEqual(self.files_sent(), {"save-0000.sav": SLOT})
        self.answer()
        summary = self.sync.summary("exited")
        self.assertTrue(summary["ok"]); self.assertIn("Partida guardada en la Game Card", summary["message"])

    def test_no_capture_without_a_close(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT, close=False)
        self.sync.poll()
        self.assertEqual((self.host.calls, self.sent), ([], []), "a created or modified file is no save point; no timer either")

    def test_a_writer_in_the_middle_means_no_snapshot(self):
        self.prepare()
        self.host.writing = ["pid 7 fd 12"]
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()
        self.assertEqual(self.sent, []); self.assertEqual(self.host.calls[-1], "thaw", "always thawed")
        self.sync.poll()
        self.assertEqual(self.sent, [], "no retry until the next close")
        self.host.writing = []
        self.watch.events.append((launchd.IN_CLOSE_WRITE, "screen-0000.zsc"))
        (self.savedir / "screen-0000.zsc").write_bytes(THUMB)
        self.sync.poll()
        self.assertEqual(set(self.files_sent()), {"save-0000.sav", "screen-0000.zsc"})

    def test_unlistable_open_files_or_a_failed_freeze_are_no_proof(self):
        self.prepare()
        self.host.writing = None
        self.game_writes("save-0000.sav", SLOT); self.sync.poll()
        self.assertEqual(self.sent, [])
        self.host.writing, self.host.freezable, self.host.calls = [], False, []
        self.game_writes("save-0000.sav", SLOT); self.sync.poll()
        self.assertEqual(self.sent, []); self.assertEqual(self.host.calls, ["freeze", "thaw"], "nothing read unfrozen")

    def test_a_torn_unit_keeps_its_last_valid_version(self):
        self.prepare(envelope({"save-0000.sav": SLOT, "save-0001.sav": SLOT}))
        self.game_writes("save-0000.sav", SLOT2[:len(SLOT2) // 2])     # crash mid-write
        self.game_writes("save-0001.sav", SLOT2)
        self.sync.poll()
        files = self.files_sent()
        self.assertEqual(files["save-0000.sav"], SLOT, "the torn slot is carried from the card")
        self.assertEqual(files["save-0001.sav"], SLOT2, "and does not cost the other slot its progress")
        self.assertEqual(self.sent[-1][0]["carried"], ["save-0000.sav"])
        self.answer()
        summary = self.sync.summary("exited")
        self.assertFalse(summary["ok"]); self.assertIn("se conservó su versión anterior", summary["message"])

    def test_a_torn_new_unit_is_left_out_and_reported(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)
        self.game_writes("save-0003.sav", b"\x78\x9c\x01\x02")
        self.sync.poll()
        self.assertEqual(set(self.files_sent()), {"save-0000.sav"})
        self.answer()
        self.assertIn("incompleta y no se guardó", self.sync.summary("exited")["message"])

    def test_a_deleted_unit_stays_on_the_card_and_an_identical_snapshot_is_not_rewritten(self):
        self.prepare(envelope({"save-0000.sav": SLOT}))
        (self.savedir / "save-0000.sav").unlink()
        self.watch.events.append((launchd.IN_DELETE, "save-0000.sav"))
        self.game_writes("screen-0000.zsc", THUMB)
        self.sync.poll()
        self.assertEqual(self.files_sent(), {"save-0000.sav": SLOT, "screen-0000.zsc": THUMB})
        self.answer()
        self.watch.events.append((launchd.IN_CLOSE_WRITE, "screen-0000.zsc"))
        self.sync.poll()
        self.assertEqual(len(self.sent), 1, "same bytes as the card holds: not written again")

    def test_links_special_files_and_hard_links_are_not_units(self):
        self.prepare(envelope({"save-0000.sav": SLOT, "save-0001.sav": SLOT}))
        outside = self.dest / "outside"; outside.write_bytes(SLOT2)
        (self.savedir / "save-0000.sav").unlink(); (self.savedir / "save-0000.sav").symlink_to(outside)
        (self.savedir / "save-0001.sav").unlink(); os.link(outside, self.savedir / "save-0001.sav")
        self.watch.events += [(launchd.IN_CLOSE_WRITE, "save-0000.sav"), (launchd.IN_CLOSE_WRITE, "save-0001.sav")]
        self.sync.poll()
        self.assertEqual(self.sent, [], "both carried from the card: the card already holds this")
        self.assertEqual(sorted(self.sync.carried), ["save-0000.sav", "save-0001.sav"])
        self.game_writes("screen-0000.zsc", THUMB); self.sync.poll()
        self.assertEqual(self.files_sent(), {"save-0000.sav": SLOT, "save-0001.sav": SLOT, "screen-0000.zsc": THUMB})

    def test_the_bound_is_enforced_before_anything_is_sent(self):
        self.prepare()
        self.game_writes("save-0000.sav", os.urandom(1024 * 1024 + 1))
        self.sync.poll()
        self.assertEqual(self.sent, [])
        summary = self.sync.summary("exited")
        self.assertFalse(summary["ok"]); self.assertIn("supera el tamaño", summary["message"])

    def test_a_failed_write_is_reported_and_retried_at_the_next_save_point(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT); self.sync.poll()
        self.answer(ok=False, code="no_space", message="La Game Card está llena")
        self.assertIn("La última partida no se guardó: La Game Card está llena", self.sync.summary("exited")["message"])
        self.game_writes("save-0000.sav", SLOT2); self.sync.poll()
        self.answer()
        self.assertTrue(self.sync.summary("exited")["ok"])

    def test_a_close_during_a_write_waits_for_the_answer(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT); self.sync.poll()
        self.game_writes("save-0000.sav", SLOT2); self.sync.poll()
        self.assertEqual(len(self.sent), 1, "one write at a time")
        self.answer(); self.sync.poll()
        self.assertEqual(self.files_sent(), {"save-0000.sav": SLOT2})

    def test_the_directory_itself_moved_is_reported(self):
        self.prepare()
        self.watch.events.append((launchd.IN_MOVE_SELF, ""))
        self.sync.poll()
        self.game_writes("save-0000.sav", SLOT); self.sync.poll()
        self.assertEqual(self.sent, [])
        self.assertIn("movió o borró su carpeta", self.sync.summary("exited")["message"])


class FinalCaptureTests(SyncCase):
    def test_after_the_stop_the_last_changes_are_captured_without_a_freeze(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)      # the game quit right after saving
        done = []
        self.sync.final(lambda: done.append(True))
        self.assertEqual(self.host.calls, [], "nothing runs: no freeze, no lease")
        self.assertEqual(done, [], "waits for the card's answer")
        self.answer()
        self.assertEqual(done, [True]); self.assertTrue(self.sync.summary("exited")["ok"])

    def test_a_crash_mid_write_carries_the_last_valid_version(self):
        self.prepare(envelope({"save-0000.sav": SLOT}))
        self.game_writes("save-0000.sav", SLOT2[:5], close=False)   # killed inside fwrite
        done = []
        self.sync.final(lambda: done.append(True))
        self.assertEqual(self.sent, [], "carrying the card's own version changes nothing: no write")
        self.assertEqual(done, [True])
        self.assertIn("se conservó su versión anterior", self.sync.summary("crashed")["message"])

    def test_nothing_changed_nothing_written(self):
        self.prepare(envelope({"save-0000.sav": SLOT}))
        done = []
        self.sync.final(lambda: done.append(True))
        self.assertEqual((self.sent, done), ([], [True]))
        self.assertEqual(self.sync.summary("exited")["message"], "")

    def test_card_gone_reports_what_was_lost(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT); self.sync.poll(); self.answer()
        self.clock[0] += 600
        self.game_writes("save-0000.sav", SLOT2)
        done = []
        self.sync.final(lambda: done.append(True), write=False)
        self.assertEqual((len(self.sent), done), (1, [True]))
        summary = self.sync.summary("card_removed")
        self.assertFalse(summary["ok"]); self.assertIn("Se perdieron los cambios de la partida posteriores a las", summary["message"])

    def test_card_gone_before_any_write_names_the_save_the_card_holds(self):
        self.prepare(envelope({"save-0000.sav": SLOT}, saved_at="2026-09-26T07:02:00Z"))
        self.game_writes("save-0000.sav", SLOT2)
        self.sync.final(lambda: None, write=False)
        expected = time.strftime("%H:%M", time.localtime(calendar.timegm((2026, 9, 26, 7, 2, 0, 0, 0, 0))))
        self.assertIn(f"posteriores a las {expected}", self.sync.summary("card_removed")["message"])

    def test_an_unanswered_write_is_reported_as_not_saved(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)
        self.sync.final(lambda: None)
        self.sync.abandon()
        self.assertIn("no respondió a tiempo", self.sync.summary("exited")["message"])
        self.answer()                                  # a late answer changes nothing
        self.assertFalse(self.sync.summary("exited")["ok"])

    # A game that saves and quits while an earlier
    # write is still in flight. Its close events are queued after the last
    # poll; the final step must account for them before deciding it is done.
    def test_final_after_an_outstanding_write_captures_what_was_queued_behind_it(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()                                # the first version is being written
        self.game_writes("save-0000.sav", SLOT2)        # the game saves again and stops before the next poll
        done = []
        self.sync.final(lambda: done.append(True))
        self.answer(index=0)
        self.assertEqual(len(self.sent), 2, "the newest slot gets its own write")
        self.assertEqual(self.files_sent()["save-0000.sav"], SLOT2)
        self.assertEqual(done, [], "not done until that write is answered")
        self.assertEqual(self.watch.events, [], "every queued event was accounted for")
        self.answer()
        self.assertEqual(done, [True])
        summary = self.sync.summary("exited")
        self.assertTrue(summary["ok"]); self.assertIn("Partida guardada en la Game Card", summary["message"])
        self.assertEqual(self.sync.last_synced["save-0000.sav"], SLOT2)

    def test_final_behind_an_outstanding_write_to_a_card_that_is_gone_reports_the_loss(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()
        self.game_writes("save-0000.sav", SLOT2)
        done = []
        self.sync.final(lambda: done.append(True), write=False)
        self.answer(index=0)                            # the older write still made it
        self.assertEqual((len(self.sent), done), (1, [True]), "nothing more is sent to a card that is gone")
        summary = self.sync.summary("card_removed")
        self.assertFalse(summary["ok"])
        self.assertIn("Se perdieron los cambios de la partida posteriores a las", summary["message"])

    def test_final_behind_a_failed_write_tries_once_more_and_reports(self):
        self.prepare()
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()
        self.game_writes("save-0000.sav", SLOT2)
        done = []
        self.sync.final(lambda: done.append(True))
        self.answer(ok=False, index=0, code="no_space", message="No hay espacio en la Game Card para guardar la partida")
        self.assertEqual(self.files_sent()["save-0000.sav"], SLOT2, "the retry carries the newest bytes")
        self.answer(ok=False, code="no_space", message="No hay espacio en la Game Card para guardar la partida")
        self.assertEqual((len(self.sent), done), (2, [True]), "bounded: one final capture")
        self.assertIn("La última partida no se guardó: No hay espacio", self.sync.summary("exited")["message"])


class AdoptionPathTests(SyncCase):
    """The game owns work/ and everything below it; while
    no launcher runs it may replace any component of the save directory. The
    adopting launcher must never follow one outside the session."""

    def outside(self):
        outside = self.dest.parent / "outside"
        (outside / "save").mkdir(parents=True)
        (outside / "save" / "canary.sav").write_bytes(SLOT2)
        return outside

    def assert_refused(self):
        with self.assertRaises(OSError):
            self.sync.resume()
        self.sync.poll()
        self.assertEqual(self.sent, [], "nothing from outside the session is read or sent")
        self.assertEqual(self.host.calls, [], "no capture is even attempted")

    def test_an_intermediate_link_is_refused(self):
        self.prepare()
        self.sync.close()
        outside = self.outside()
        parent = self.savedir.parent
        parent.rename(parent.with_name(".Sample-original"))
        parent.symlink_to(outside, target_is_directory=True)
        self.assert_refused()

    def test_a_final_link_is_refused(self):
        self.prepare()
        self.sync.close()
        outside = self.outside()
        self.savedir.rename(self.savedir.with_name("save-original"))
        self.savedir.symlink_to(outside / "save", target_is_directory=True)
        self.assert_refused()

    def test_a_component_replaced_by_a_file_is_refused(self):
        self.prepare()
        self.sync.close()
        parent = self.savedir.parent
        parent.rename(parent.with_name(".Sample-original"))
        parent.write_bytes(b"not a directory")
        self.assert_refused()

    def test_a_component_replaced_during_the_walk_is_refused(self):
        self.prepare()
        self.sync.close()
        outside = self.outside()
        parent = self.savedir.parent
        real_open = os.open

        def racing_open(path, flags, *args, **kwargs):
            if path == ".Sample" and kwargs.get("dir_fd") is not None and not parent.is_symlink():
                parent.rename(parent.with_name(".Sample-original"))   # swapped after work/ was opened
                parent.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)
        with unittest.mock.patch.object(launchd.os, "open", racing_open):
            self.assert_refused()

    def test_ordinary_adoption_still_captures(self):
        self.prepare()
        self.sync.close()
        self.sync.resume()
        self.game_writes("save-0000.sav", SLOT)
        self.sync.poll()
        self.assertEqual(self.files_sent(), {"save-0000.sav": SLOT})


class RestoreTests(SyncCase):
    def test_restore_writes_the_units_owned_by_the_game_and_removes_the_envelope(self):
        self.prepare(envelope({"save-0000.sav": SLOT, "screen-0000.zsc": THUMB}))
        self.assertEqual((self.savedir / "save-0000.sav").read_bytes(), SLOT)
        self.assertEqual((self.savedir / "screen-0000.zsc").read_bytes(), THUMB)
        self.assertFalse((self.dest / "work" / "save.json").exists())
        self.assertEqual(self.sync.restore["state"], "restored")

    def test_an_envelope_of_the_other_generation_is_not_restored(self):
        # The console checks the naming generation itself; the card
        # service already falls back, this is the launcher's own check.
        cases = ((None, "neptune-save/1", "restored"), ("earlier", "neptune-save/1", "restored"),
                 ("mun", "mun-save/1", "restored"), ("mun", "neptune-save/1", "damaged"),
                 ("earlier", "mun-save/1", "damaged"))
        for naming, fmt, state in cases:
            with self.subTest(card=naming, envelope=fmt):
                self.setUp()
                spec = dict(SPEC, **({"naming": naming} if naming else {}))
                self.sync.spec = spec
                self.prepare(envelope({"save-0000.sav": SLOT}, format=fmt))
                self.assertEqual(self.sync.restore["state"], state)
                self.assertEqual((self.savedir / "save-0000.sav").exists(), state == "restored")

    def test_the_card_tool_judges_an_envelope_as_this_restore_does(self):
        # The converter (mun_card.saves) must not certify what this refuses,
        # nor refuse what this restores: the same cases, the same verdicts.
        sys.path.insert(0, str(ROOT / "tools" / "mun-card"))
        from mun_card.saves import envelope_problem
        info = {"id": SPEC["card_id"], "saves_directory": SPEC["directory"], "saves_units": SPEC["units"],
                "saves_checks": SPEC["checks"], "saves_max_bytes": SPEC["max_bytes"]}
        cases = {
            "valid": envelope({"save-0000.sav": SLOT, "screen-0000.zsc": THUMB}),
            "no kind": envelope({"save-0000.sav": SLOT}, kind=None),
            "other schema": envelope({"save-0000.sav": SLOT}, schema=2),
            "undeclared name": envelope({"notes.txt": b"x"}),
            "unit failing its check": envelope({"save-0000.sav": b"not zlib"}),
            "torn zlib": envelope({"save-0000.sav": SLOT[:-4]}),
            "another game": envelope({"save-0000.sav": SLOT}, game="other.game"),
            "other format": envelope({"save-0000.sav": SLOT}, format="mun-save/1"),
            "hash mismatch": envelope({"save-0000.sav": SLOT}).replace(b'"sha256": "', b'"sha256": "0'),
            "over the declared size": envelope({"screen-0000.zsc": zlib.compress(os.urandom(SPEC["max_bytes"] + 1))}),
        }
        self.sync.max_bytes = SPEC["max_bytes"]
        for what, raw in cases.items():
            with self.subTest(what):
                launcher = self.sync._check_envelope(raw)[0]
                try:
                    tool = envelope_problem(json.loads(raw), info, "neptune-save/1")
                except ValueError:
                    tool = "not JSON"
                self.assertEqual(launcher is None, tool is None, f"launcher: {launcher!r}; card tool: {tool!r}")
        self.assertIsNone(self.sync._check_envelope(cases["valid"])[0])

    def test_recovered_copy_is_reported(self):
        self.prepare(envelope({"save-0000.sav": SLOT}, recovered=True))
        self.assertEqual(self.sync.summary("exited")["message"], "Partida recuperada de la copia anterior")

    def test_any_flaw_refuses_the_whole_envelope(self):
        good = {"save-0000.sav": SLOT}
        cases = {
            "escape": envelope({"../save-0000.sav": SLOT}),
            "subdir": envelope({"x/save-0000.sav": SLOT}),
            "undeclared": envelope({"save-0000.sav": SLOT, "evil.sh": b"#!/bin/sh"}),
            "hash": envelope(good).replace(hashlib.sha256(SLOT).hexdigest().encode(), b"0" * 64),
            "torn": envelope({"save-0000.sav": SLOT[:-3]}),
            "other game": envelope(good, game="mun.collect"),
            "single object": envelope(good, kind=None),
            "not json": b"{",
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.setUp()
                self.prepare(raw)
                self.assertEqual(sorted(os.listdir(self.savedir)), [], "nothing unpacked")
                summary = self.sync.summary("exited")
                self.assertFalse(summary["ok"]); self.assertIn("no se pudo cargar", summary["message"])

    def test_the_card_service_could_not_read_it(self):
        self.prepare(None, staged={"present": True, "copied": False, "error": {"code": "save_damaged", "detail": "save.json dañado"}})
        self.assertIn("La partida de la Game Card no se pudo cargar: save.json dañado", self.sync.summary("exited")["message"])


class UnitCheckTests(unittest.TestCase):
    def test_checks(self):
        self.assertIsNone(launchd.unit_check("zlib-xml", SLOT))
        self.assertIsNone(launchd.unit_check("zlib", THUMB))
        self.assertIn("incompleto", launchd.unit_check("zlib", THUMB[:-4]))
        self.assertIn("después", launchd.unit_check("zlib", THUMB + b"junk"))
        self.assertIsNotNone(launchd.unit_check("zlib", b"not zlib"))
        self.assertIsNotNone(launchd.unit_check("zlib-xml", zlib.compress(b"<a><b></a>")))
        self.assertIsNotNone(launchd.unit_check("xml", b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>"))
        self.assertIsNone(launchd.unit_check("xml", b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?><a/><b/>"))
        self.assertIsNone(launchd.unit_check("any", b""))
        self.assertIsNotNone(launchd.unit_check("zlib-xml", zlib.compress(b"<a/>\0<b/>\0")), "a NUL inside is not a terminator")
        self.assertIsNotNone(launchd.unit_check("zlib-xml", zlib.compress(b"<a/><b/>\0\0")), "only one terminator")

    def test_inflation_is_bounded(self):
        bomb = zlib.compress(b"\0" * (launchd.INFLATE_LIMIT + 1))
        self.assertIn("límite", launchd.unit_check("zlib", bomb))

    def test_manifest_declaration_is_rechecked(self):
        info = {"id": "g", "saves_directory": ".Sample/save", "saves_units": ["*.sav"], "saves_checks": ["zlib-xml"],
                "saves_max_bytes": 4096}
        self.assertEqual(launchd.directory_saves_spec(info)["directory"], ".Sample/save")
        self.assertIsNone(launchd.directory_saves_spec({"id": "g"}))
        for over in ({"saves_directory": "../x"}, {"saves_directory": "/abs"}, {"saves_units": ["a/b"]},
                     {"saves_checks": ["exec"]}, {"saves_units": ["*.sav", "*.x"]}, {"saves_max_bytes": 10 ** 9},
                     {"saves_units": ["[a]"]}):
            with self.subTest(over):
                with self.assertRaises(ValueError):
                    launchd.directory_saves_spec(dict(info, **over))


def sample_card():
    return {"slot": "c1", "insertion": "ins-1", "serial": "NPT-aq", "state": "valid", "active": True,
            "info": {"id": "sample.lab", "title": "Sample (lab)", "version": "1.1.3", "kind": "game",
                     "entry": "content/sample", "profile": "linux-arm64-gl-v0",
                     "saves_directory": ".Sample/save", "saves_units": ["*.sav"], "saves_checks": ["zlib-xml"],
                     "saves_max_bytes": 1024 * 1024}}


class TempRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.root = Path(tempfile.mkdtemp())
        self.uid, self.gid = os.getuid(), os.getgid()
        self.host = FakeHost()
        self.cleanup_ran = []

    def prepare_dir(self, sid):
        self.dirs.add(sid)
        (self.root / sid / "work").mkdir(parents=True)
        return self.root / sid

    def freeze(self, sid):
        return self.host.freeze(sid)

    def thaw(self, sid):
        self.host.thaw(sid)



class SessionTests(unittest.TestCase):
    """The session waits for its last capture and reports it with the result."""

    def start(self, card_save=None):
        h = Harness(sample_card())
        h.runner = h.m.runner = TempRunner()
        h.watch, h.saves = FakeInotify(), []
        h.m.saver = lambda request, reply: h.saves.append((request, reply))
        h.m.sync_factory = lambda *args: launchd.DirectorySync(*args, inotify_factory=lambda: h.watch,
                                                               root_ids=(os.getuid(), os.getgid()), lease=h.runner.host.lease)
        h.auto_stage = False
        sid = h.m.launch({"slot": "c1", "serial": "NPT-aq", "version": "1.1.3"})["session"]
        if card_save is not None:
            (h.runner.root / sid / "work" / "save.json").write_bytes(card_save)
        request = h.stage_requests[-1]
        h.m._staged(sid, {"ok": True, "size": 10, "sha256": "ab" * 32,
                          "save": {"present": card_save is not None, "copied": card_save is not None}})
        h.advance(0.5)
        self.assertEqual(h.m.state, "running")
        h.savedir = h.runner.root / sid / "work" / ".Sample" / "save"
        return h, sid

    def write(self, h, name, data):
        (h.savedir / name).write_bytes(data)
        h.watch.events.append((launchd.IN_CLOSE_WRITE, name))

    def test_saved_during_play_and_at_exit(self):
        h, sid = self.start(envelope({"save-0000.sav": SLOT}))
        self.assertEqual((h.savedir / "save-0000.sav").read_bytes(), SLOT, "restored before the unit started")
        self.write(h, "save-0001.sav", SLOT2); h.advance(0.5)
        request, reply = h.saves[-1]
        self.assertEqual((request["payload_kind"], request["session"], request["insertion"], request["game"]),
                         ("files", sid, "ins-1", "sample.lab"))
        self.assertTrue(request["payload_file"].startswith(str(h.runner.root / sid / "sync")))
        reply({"ok": True, "bytes": 900}); h.pump()
        self.write(h, "save-0001.sav", SLOT)            # saves again, then quits at once
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "saving", "the session waits for its last capture")
        self.assertEqual(h.shell.log, ["stop"], "the shell is not back yet")
        self.assertEqual(h.m.release_request("NPT-aq")["error"]["code"], "in_use")
        h.saves[-1][1]({"ok": True, "bytes": 900}); h.pump()
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.shell.log, ["stop", "start"])
        result = h.result()
        self.assertEqual(result["reason"], "exited")
        self.assertTrue(result["saves"]["ok"]); self.assertIn("Partida guardada en la Game Card", result["saves"]["message"])

    def test_card_pulled_reports_the_lost_changes(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT); h.advance(0.5)
        h.saves[-1][1]({"ok": True, "bytes": 900}); h.pump()
        self.write(h, "save-0000.sav", SLOT2)
        h.cards.apply({"type": "removed", "slot": "c1"}); h.m.card_removed("c1", "ins-1")
        h.advance(0.5); h.pump()
        self.assertEqual(h.m.state, "idle")
        self.assertEqual(len(h.saves), 1, "nothing is sent to a card that is gone")
        result = h.result()
        self.assertEqual(result["reason"], "card_removed")
        self.assertFalse(result["saves"]["ok"]); self.assertIn("Se perdieron los cambios", result["saves"]["message"])

    def test_final_write_failure_is_the_result_line(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT)
        h.runner.finish(sid, code=0); h.advance(0.5)
        h.saves[-1][1]({"ok": False, "error": {"code": "card_unavailable", "message": "La tarjeta ya no está disponible"}}); h.pump()
        self.assertEqual((h.m.state, len(h.saves)), ("saving", 2), "a failed write is tried once more after the stop")
        h.saves[-1][1]({"ok": False, "error": {"code": "card_unavailable", "message": "La tarjeta ya no está disponible"}}); h.pump()
        self.assertEqual((h.m.state, len(h.saves)), ("idle", 2), "and only once")
        self.assertIn("La última partida no se guardó: La tarjeta ya no está disponible", h.result()["saves"]["message"])

    def test_an_unanswered_final_write_ends_at_the_deadline(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT)
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "saving")
        h.advance(launchd.SAVE_TIMEOUT + 6)
        self.assertEqual(h.m.state, "idle")
        self.assertIn("no respondió a tiempo", h.result()["saves"]["message"])

    def test_the_socket_save_contract_is_closed_to_a_directory_game(self):
        h, sid = self.start()
        replies = []
        h.m.save_request(sid, {"type": "save", "schema": 1, "data": {"x": 1}}, replies.append)
        self.assertEqual(replies[-1]["error"]["code"], "save_invalid")
        environment = launchd.unit_environment("linux-arm64-gl-v0", sid, h.m.session, h.runner.root / sid)
        for name in ("MUN_SAVE_FILE", "MUN_SAVE_SOCKET", "NEPTUNE_SAVE_FILE", "NEPTUNE_SAVE_SOCKET"):
            self.assertNotIn(name, environment)

    def test_a_save_and_exit_between_the_last_poll_and_the_stop_is_written_before_the_shell(self):
        # Saving and quitting, through the session: the game writes its newest slot and
        # quits after the tick's poll and before unit_status sees it gone.
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT); h.advance(0.5)
        self.assertEqual(len(h.saves), 1)
        original_status = h.runner.unit_status

        def late_exit(session_id):
            self.write(h, "save-0000.sav", SLOT2)
            h.runner.finish(sid, code=0)
            return original_status(session_id)
        with unittest.mock.patch.object(h.runner, "unit_status", side_effect=late_exit):
            h.advance(0.5)
        self.assertEqual(h.m.state, "saving")
        h.saves[0][1]({"ok": True, "bytes": 1000}); h.pump()
        self.assertEqual((h.m.state, len(h.saves)), ("saving", 2), "the newest slot is written before the session ends")
        request = h.saves[1][0]
        payload = json.loads(Path(request["payload_file"]).read_bytes())
        self.assertEqual(base64.b64decode(payload["files"][0]["data"]), SLOT2)
        self.assertEqual(h.shell.log, ["stop"], "no shell until that write is answered")
        h.saves[1][1]({"ok": True, "bytes": 1000}); h.pump()
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.shell.log, ["stop", "start"])
        self.assertTrue(h.result()["saves"]["ok"])

    def test_adoption_through_a_replaced_component_is_a_reported_save_failure(self):
        # Replaced components, through the session.
        h, sid = self.start()
        session = json.loads(json.dumps({k: v for k, v in h.m.session.items() if k != "saving"}))
        h.m.sync.close()
        outside = h.runner.root / "outside"
        (outside / "save").mkdir(parents=True)
        (outside / "save" / "canary.sav").write_bytes(SLOT2)
        parent = h.savedir.parent
        parent.rename(parent.with_name(".Sample-original"))
        parent.symlink_to(outside, target_is_directory=True)
        h2 = Harness(sample_card())
        h2.runner = h2.m.runner = h.runner
        h2.saves = []
        h2.m.saver = lambda request, reply: h2.saves.append((request, reply))
        h2.m.sync_factory = lambda *args: launchd.DirectorySync(*args, inotify_factory=FakeInotify,
                                                                root_ids=(os.getuid(), os.getgid()), lease=h.runner.host.lease)
        h2.m.adopt(sid, session)
        self.assertIsNone(h2.m.sync)
        h2.advance(0.5)
        h.runner.finish(sid, code=0); h2.advance(0.5); h2.pump()
        self.assertEqual((h2.m.state, h2.saves), ("idle", []), "nothing from outside the session reached the card")
        saves = h2.result()["saves"]
        self.assertFalse(saves["ok"]); self.assertIn("no se pudo guardar", saves["message"])

    # N1 follow-up (2026-09-26) on the directory-save path: the cleanup runs
    # while the last write is still in flight; the session waits for the write,
    # not for the cleanup, and a cleanup outcome that comes later still goes
    # with this session's result.
    def ended_with_a_pending_cleanup(self):
        from test_launchd import RUNNING
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT)
        h.runner.cleanup = {sid: dict(RUNNING)}
        h.runner.finish(sid, code=0); h.advance(0.5)
        self.assertEqual(h.m.state, "saving", "the last write is still awaited")
        h.saves[-1][1]({"ok": True, "bytes": 900}); h.pump()
        self.assertEqual(h.m.state, "idle"); self.assertEqual(h.shell.log[-1], "start")
        self.assertTrue(h.result()["saves"]["ok"])
        self.assertNotIn("platform", h.result())
        self.assertIn(sid, h.m.cleanups)
        return h, sid

    def test_directory_session_cleanup_pending_then_success(self):
        from test_launchd import FakeRunner as Base
        h, sid = self.ended_with_a_pending_cleanup()
        h.runner.cleanup[sid] = dict(Base.DONE); h.advance(0.5)
        self.assertEqual((h.m.cleanups, h.runner.released), ({}, [(sid, "succeeded")]))
        self.assertNotIn("platform", h.result())

    def test_directory_session_cleanup_pending_then_failure(self):
        from test_launchd import FAILED
        h, sid = self.ended_with_a_pending_cleanup()
        h.runner.cleanup[sid] = dict(FAILED); h.advance(0.5)
        result = h.result()
        self.assertTrue(result["saves"]["ok"], "the save line is kept")
        self.assertIn("La limpieza de la sesión falló (systemd: exit-code)", result["platform"]["message"])
        self.assertEqual(h.runner.released, [(sid, "failed")])

    def test_directory_session_cleanup_pending_then_timeout(self):
        h, sid = self.ended_with_a_pending_cleanup()
        h.advance(launchd.CLEANUP_TIMEOUT + 1)
        self.assertIn("no terminó en", h.result()["platform"]["message"])
        self.assertTrue(h.result()["saves"]["ok"])
        self.assertEqual(h.runner.released, [(sid, "timeout")])

    def test_adopted_session_thaws_and_keeps_saving(self):
        h, sid = self.start()
        self.write(h, "save-0000.sav", SLOT); h.advance(0.5)
        h.saves[-1][1]({"ok": True, "bytes": 900}); h.pump()
        session = json.loads(json.dumps({k: v for k, v in h.m.session.items() if k != "saving"}))
        # A new launcher adopts the unit that outlived the old one.
        h2 = Harness(sample_card())
        h2.runner = h2.m.runner = h.runner
        h2.watch, h2.saves = FakeInotify(), []
        h2.m.saver = lambda request, reply: h2.saves.append((request, reply))
        h2.m.sync_factory = lambda *args: launchd.DirectorySync(*args, inotify_factory=lambda: h2.watch,
                                                                root_ids=(os.getuid(), os.getgid()), lease=h.runner.host.lease)
        h.runner.host.calls = []
        h2.m.adopt(sid, session)
        self.assertEqual(h.runner.host.calls, ["thaw"], "a capture cut short between freeze and thaw is undone")
        (h.savedir / "save-0000.sav").write_bytes(SLOT2)    # closed while no launcher watched
        h2.advance(0.5)
        self.assertEqual(len(h2.saves), 1, "the first tick captures: the proof does not depend on history")
        h2.saves[-1][1]({"ok": True, "bytes": 900}); h2.pump()
        (h.savedir / "save-0000.sav").write_bytes(SLOT2[:4])   # killed mid-write: the kernel still reports the close
        h2.watch.events.append((launchd.IN_MODIFY, "save-0000.sav"))
        h.runner.finish(sid, code=0); h2.advance(0.5); h2.pump()
        self.assertEqual(h2.m.state, "idle")
        self.assertIn("se conservó su versión anterior", h2.result()["saves"]["message"],
                      "the version carried is the one the previous launcher wrote")


class StartupReconcileTests(unittest.TestCase):
    """A session that ended while no launcher ran is
    recorded before anything of it is removed, whether its unit is still
    listed (failed and kept for its result, or inactive) or gone."""

    def session_dir(self, root, sid, saves=True):
        dest = root / sid
        (dest / "work" / ".Sample" / "save").mkdir(parents=True)
        (dest / "sync").mkdir()
        (dest / "work" / ".Sample" / "save" / "save-0000.sav").write_bytes(SLOT)
        (dest / "session.json").write_text(json.dumps({"id": sid, "card_id": "sample.lab", "title": "Sample (lab)",
                                                       "version": "1.1.3", **({"saves": SPEC} if saves else {})}))
        return dest

    def runner(self, root, units):
        runner = unittest.mock.Mock()
        runner.list_game_units.return_value = [f"{launchd.UNIT_PREFIX}{sid}.service" for sid in units]
        runner.unit_status.side_effect = lambda sid: units[sid]
        runner.list_cleanup_units.return_value = []
        return runner

    def test_a_listed_stopped_unit_is_recorded_before_its_directory_goes(self):
        # The review's reproduction, through main()'s real startup path.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "launch"; root.mkdir()
            dest = self.session_dir(root, "sstopped")
            runner = self.runner(root, {"sstopped": {"active": False, "finished": True, "exit_code": None, "signal": 9}})
            store = launchd.ResultStore(None)
            seen_at_removal = []

            def remove(name):
                seen_at_removal.append(store.current())
                shutil.rmtree(root / name)
            runner.remove_dir.side_effect = remove

            class StopBeforeLoop(Exception):
                pass
            shell = unittest.mock.Mock()
            shell.start.side_effect = StopBeforeLoop
            with unittest.mock.patch.object(launchd, "LAUNCH_ROOT", root), \
                    unittest.mock.patch.object(launchd, "CONTENT_MOUNT", Path(td) / "content"), \
                    unittest.mock.patch.object(launchd, "Runner", return_value=runner), \
                    unittest.mock.patch.object(launchd, "ResultStore", return_value=store), \
                    unittest.mock.patch.object(launchd, "Server", return_value=unittest.mock.Mock()), \
                    unittest.mock.patch.object(launchd, "ShellControl", return_value=shell), \
                    unittest.mock.patch.object(launchd.socket, "socketpair", return_value=[unittest.mock.Mock(), unittest.mock.Mock()]), \
                    unittest.mock.patch.object(launchd.subprocess, "run", return_value=unittest.mock.Mock(stdout="rw,exec")):
                with self.assertRaises(StopBeforeLoop):
                    launchd.main([])
            self.assertFalse(dest.exists())
            self.assertIsNotNone(seen_at_removal[0], "the result was stored before the directory was removed")
            result = store.current()
            self.assertEqual((result["session"], result["reason"], result["card_id"]), ("sstopped", "interrupted", "sample.lab"))
            self.assertIn("señal 9", result["detail"])
            self.assertFalse(result["saves"]["ok"])
            self.assertIn("No se pudo comprobar si la última partida llegó a la Game Card", result["saves"]["message"])
            runner.forget_unit.assert_called_once_with("sstopped")

    def test_a_failed_unit_and_an_orphaned_directory_are_both_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "launch"; root.mkdir()
            self.session_dir(root, "sfailed")
            self.session_dir(root, "sorphan", saves=False)
            runner = self.runner(root, {"sfailed": {"active": False, "finished": True, "exit_code": 3, "signal": None}})
            runner.remove_dir.side_effect = lambda name: shutil.rmtree(root / name)
            h = Harness(None)
            stored = []
            h.m.results.store = stored.append
            self.assertFalse(launchd.reconcile_sessions(runner, h.m, root))
            self.assertEqual([r["session"] for r in stored], ["sfailed", "sorphan"])
            self.assertIn("código 3", stored[0]["detail"]); self.assertIn("saves", stored[0])
            self.assertNotIn("saves", stored[1], "a socket-save game gets no directory-save line")
            self.assertEqual(list(root.iterdir()), [])

    def test_the_active_unit_is_still_adopted_and_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "launch"; root.mkdir()
            dest = self.session_dir(root, "slive", saves=False)
            runner = self.runner(root, {"slive": {"active": True, "finished": False}})
            h = Harness(game_card())
            stored = []
            h.m.results.store = stored.append
            self.assertTrue(launchd.reconcile_sessions(runner, h.m, root))
            self.assertEqual((h.m.state, h.m.session["id"], stored), ("running", "slive", []))
            self.assertTrue(dest.exists())
            runner.remove_dir.assert_not_called()


@unittest.skipUnless(sys.platform.startswith("linux"), "inotify and /proc are Linux")
class LinuxPieceTests(unittest.TestCase):
    def test_inotify_reports_a_close_after_write(self):
        directory = Path(tempfile.mkdtemp())
        watch = launchd.Inotify()
        self.addCleanup(watch.close)
        watch.add(directory)
        (directory / "save-0000.sav").write_bytes(b"x")
        events = watch.read()
        self.assertIn(launchd.IN_CLOSE_WRITE, [mask & launchd.IN_CLOSE_WRITE for mask, name in events if name == "save-0000.sav"])

    def test_the_watch_follows_the_held_directory_not_its_path(self):
        # After the walk, the watch is bound through /proc/self/fd.
        base = Path(tempfile.mkdtemp())
        (base / "home" / "save").mkdir(parents=True)
        outside = base / "outside" / "save"; outside.mkdir(parents=True)
        fd = os.open(base / "home" / "save", os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        watch = launchd.Inotify()
        self.addCleanup(watch.close)
        watch.add(Path(f"/proc/self/fd/{fd}"))
        (base / "home").rename(base / "moved")
        (base / "home").symlink_to(base / "outside", target_is_directory=True)
        (base / "home" / "save" / "canary.sav").write_bytes(b"x")     # lands in outside/
        (base / "moved" / "save" / "save-0000.sav").write_bytes(b"y")  # the held directory
        names = {name for mask, name in watch.read() if mask & launchd.IN_CLOSE_WRITE}
        self.assertEqual(names, {"save-0000.sav"})

    def test_read_lease_refuses_while_a_writer_or_writable_mapping_exists(self):
        import mmap
        import signal
        signal.signal(signal.SIGIO, signal.SIG_IGN)
        path = Path(tempfile.mkdtemp()) / "save-0000.sav"
        path.write_bytes(b"\0" * 4096)

        def probe():
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                return launchd.read_lease(fd)
            finally:
                os.close(fd)
        self.assertTrue(probe())
        with open(path, "rb"):
            self.assertTrue(probe(), "a reader is not a writer")
        handle = open(path, "r+b")
        self.assertFalse(probe(), "open for writing")
        mapping = mmap.mmap(handle.fileno(), 4096)
        handle.close()
        self.assertFalse(probe(), "a shared writable mapping still writes with its descriptor closed")
        mapping.close()
        self.assertTrue(probe())


if __name__ == "__main__":
    unittest.main()
