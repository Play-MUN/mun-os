"""A card is writable only for the length of a save (docs/saves.md).

The kernel refuses to make a filesystem read-only while a file removed from
it is still open, and a save removes one: the previous copy it replaces. A
game that reads its card through its own mount can hold that file open. The
service must then not confirm the save, not leave the card writable for good
and not call it safe to remove while it is."""

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
from test_cardd import Harness, RealDirMounter, game_info  # noqa: E402


class WritableCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.mounter = RealDirMounter(self.tmp)
        self.h = Harness(mounter=self.mounter, validator=lambda mount: game_info())
        self.timers = []                       # (seconds, callback) the manager asked for, run by hand
        self.h.manager.later = lambda seconds, fn: self.timers.append((seconds, fn))
        out = io.StringIO()
        silenced = redirect_stdout(out); silenced.__enter__(); self.addCleanup(silenced.__exit__, None, None, None)
        self.journal = out
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc"); self.h.pump()
        self.card = self.h.manager.snapshot()["cards"][0]
        self.replies = []

    def save(self, n=1):
        before = len(self.replies)
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                             "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                             "payload": {"n": n}}, self.replies.append)
        if len(self.replies) == before:
            self.h.pump()
        return self.replies[-1]

    def release(self):
        answers = []
        self.h.manager.release({"type": "release", "serial": "NPT-game"}, answers.append)
        return answers[-1]

    def on_card(self):
        return json.loads((self.tmp / "c1" / "saves" / "mun.collect" / "save.json").read_text())["payload"]

    def run_timer(self):
        seconds, fn = self.timers.pop(0)
        fn()
        return seconds

    def remounts(self):
        return [entry[0] + ":" + entry[2] for entry in self.mounter.log if entry[0].startswith("remount")]

    def writable(self):
        return next(iter(self.h.manager.cards.values())).writable

    # --- the ordinary case ---------------------------------------------------------------
    def test_an_ordinary_save_ends_read_only_and_asks_for_no_retry(self):
        self.assertTrue(self.save()["ok"])
        self.assertEqual(self.remounts(), ["remount:rw", "remount:ro"])
        self.assertFalse(self.writable()); self.assertEqual(self.timers, [])
        self.assertTrue(self.release()["ok"])
        self.assertEqual(self.mounter.log[-1], ("umount", "c1"))

    # --- the remount to read-only is refused -----------------------------------------------
    def test_a_save_the_card_could_not_close_is_not_confirmed_and_says_why(self):
        self.mounter.refuse_read_only = True
        reply = self.save(7)
        self.assertEqual((reply["ok"], reply["error"]["code"]), (False, "sync_failed"))
        self.assertIn("mount point is busy", reply["error"]["detail"])
        self.assertIn("no la retires", reply["error"]["message"])
        self.assertEqual(self.on_card(), {"n": 7}, "the save is written; it is the card that is not closed")
        self.assertTrue(self.writable())

    def test_the_remount_is_retried_until_it_succeeds_and_then_no_more(self):
        self.mounter.refuse_read_only = True
        self.save()
        self.assertEqual(len(self.timers), 1, "one retry asked for")
        for _ in range(3):                       # the file is still held open
            self.assertEqual(self.run_timer(), cardd.READ_ONLY_RETRY)
            self.assertTrue(self.writable()); self.assertEqual(len(self.timers), 1, "and asked for again, once")
        self.assertEqual(self.remounts(), ["remount:rw"] + ["remount-refused:ro"] * 4)
        self.mounter.refuse_read_only = False    # the file was closed
        self.run_timer()
        self.assertFalse(self.writable())
        self.assertEqual(self.remounts()[-1], "remount:ro")
        self.assertEqual(self.timers, [], "converged: nothing left to retry")
        self.assertIn("read-only again", self.journal.getvalue())
        self.assertEqual(self.journal.getvalue().count("may still be writable after the save"), 1, "said once, not at every attempt")

    def test_two_refused_saves_keep_a_single_retry_running(self):
        self.mounter.refuse_read_only = True
        self.assertEqual(self.save(1)["error"]["code"], "sync_failed")
        self.assertEqual(self.save(2)["error"]["code"], "sync_failed")
        self.assertEqual(self.on_card(), {"n": 2})
        self.assertEqual(len(self.timers), 1)

    def test_the_next_save_that_succeeds_closes_the_card_and_the_retry_finds_nothing_to_do(self):
        self.mounter.refuse_read_only = True
        self.save(1)
        self.mounter.refuse_read_only = False
        self.assertTrue(self.save(2)["ok"])
        self.assertFalse(self.writable())
        attempts = len(self.remounts())
        self.run_timer()
        self.assertEqual(len(self.remounts()), attempts, "no remount of a card that is already read-only")
        self.assertEqual(self.timers, [])

    def test_a_retry_never_runs_under_a_save_in_flight(self):
        self.mounter.refuse_read_only = True
        self.save(1)
        card = next(iter(self.h.manager.cards.values()))
        card.saving = True                       # a second save is writing
        attempts = len(self.remounts())
        self.run_timer()
        self.assertEqual(len(self.remounts()), attempts, "the save in flight closes the card itself, or asks again")
        self.assertEqual(self.timers, [])
        card.saving = False

    def test_a_failed_write_that_cannot_close_the_card_is_retried_too(self):
        import errno
        self.mounter.refuse_read_only = True
        with patch.object(cardd.os, "fsync", side_effect=OSError(errno.EIO, "Input/output error")):
            reply = self.save()
        self.assertEqual(reply["error"]["code"], "sync_failed")
        self.assertTrue(self.writable()); self.assertEqual(len(self.timers), 1)
        self.mounter.refuse_read_only = False
        self.run_timer()
        self.assertFalse(self.writable())

    # --- safe removal ------------------------------------------------------------------------
    def test_a_writable_card_is_not_released_and_a_card_that_closed_is(self):
        self.mounter.refuse_read_only = True
        self.save()
        answer = self.release()
        self.assertEqual((answer["ok"], answer["error"]["code"]), (False, "card_busy"))
        self.assertIn("no la retires", answer["error"]["message"])
        self.assertNotIn(("umount", "c1"), self.mounter.log, "never unmounted while it could not be closed")
        self.assertEqual(self.remounts()[-1], "remount-refused:ro", "the release tried to close it first")
        card = self.h.manager.snapshot()["cards"][0]
        self.assertEqual(card["state"], "valid", "still in use; the caller may ask again")
        self.assertNotIn("safe to unplug", self.journal.getvalue())
        # A save is still taken (it is how the game would retry), and a release asked again is answered.
        self.assertEqual(self.save(2)["error"]["code"], "sync_failed")
        self.assertEqual(self.release()["error"]["code"], "card_busy")
        self.mounter.refuse_read_only = False    # the game ended: its files are closed
        answer = self.release()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.mounter.log[-2:], [("remount", "c1", "ro"), ("umount", "c1")])
        self.assertEqual(self.h.manager.snapshot()["cards"][0]["state"], "released")

    def test_a_release_that_waited_for_the_save_is_refused_when_the_save_leaves_the_card_writable(self):
        self.mounter.refuse_read_only = True
        answers = []
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": self.card["insertion"], "serial": "NPT-game",
                             "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                             "payload": {"n": 1}}, self.replies.append)
        self.h.manager.release({"type": "release", "serial": "NPT-game"}, answers.append)
        self.h.pump()
        self.assertEqual(self.replies[-1]["error"]["code"], "sync_failed")
        if not answers:                          # the worker had not finished when the release was asked
            self.h.pump()
        self.assertEqual((answers[-1]["ok"], answers[-1]["error"]["code"]), (False, "card_busy"))
        self.assertNotIn(("umount", "c1"), self.mounter.log)

    def test_a_card_pulled_while_writable_ends_the_retries(self):
        self.mounter.refuse_read_only = True
        self.save()
        self.h.manager.device_removed("vdc")
        attempts = len(self.remounts())
        self.run_timer()
        self.assertEqual(len(self.remounts()), attempts)
        self.assertEqual(self.timers, [])


if __name__ == "__main__":
    unittest.main()
