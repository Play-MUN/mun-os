"""Mount, remount and umount within a bound: a card that stops answering
leaves its command in the kernel, where no signal reaches it. The service
reports the operation as failed after MOUNT_TIMEOUT, keeps the process
(never an orphan), starts nothing more on that mount point until it ends,
and never calls a card safe to remove while its unmount has not finished.

The commands are stand-in `mount` and `umount` programs put first on PATH,
so the service runs them exactly as it runs the real ones."""
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cardd  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from test_cardd import Harness, RealDirMounter, game_info  # noqa: E402


class Card:
    def __init__(self, mount=None):
        self.slot, self.serial, self.mount = "c1", "NPT-test", mount


class MountTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.bin = Path(tempfile.mkdtemp())
        self.log = self.bin / "calls.log"
        for name in ("mount", "umount"):
            (self.bin / f"{name}.does").write_text("exit 0")
            program = self.bin / name
            program.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n'
                               f'exec /bin/sh -c "$(cat "{self.bin}/{name}.does")"\n')
            program.chmod(program.stat().st_mode | stat.S_IXUSR)
        mounted = self.mounted = set()
        for p in (patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}),
                  patch.object(cardd, "MOUNT_TIMEOUT", 0.3, create=True),
                  patch.object(cardd.ext4, "check_mountable", lambda path: None),
                  patch.object(cardd.Mounter, "mounted", lambda self, target: target in mounted, create=True)):
            p.start()
            self.addCleanup(p.stop)
        self.mounter = cardd.Mounter(self.root)
        self.addCleanup(self.finish)

    def does(self, name, script):
        (self.bin / f"{name}.does").write_text(script)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def finish(self):
        for _, proc in getattr(self.mounter, "stalled", {}).values():
            proc.kill()
            proc.wait()

    def test_a_mount_that_does_not_answer_fails_in_time_and_is_kept(self):
        self.does("mount", "sleep 5")
        started = time.monotonic()
        with self.assertRaises(CardError) as failed:
            self.mounter.mount(Card(), "/dev/vdb")
        self.assertLess(time.monotonic() - started, 2.0, "the service waited on the card")
        self.assertEqual(failed.exception.code, "mount_failed")
        target = str(self.root / "c1")
        self.assertIn(target, self.mounter.stalled, "the process is kept, not abandoned")
        self.assertIsNone(self.mounter.stalled[target][1].poll(), "and is still running")
        count = len(self.calls())
        with self.assertRaises(CardError):          # nothing more is started on that mount point
            self.mounter.mount(Card(), "/dev/vdb")
        self.assertEqual(len(self.calls()), count)

    def test_a_late_mount_is_detached_once_it_ends_since_no_card_uses_it(self):
        self.does("mount", "sleep 0.6")
        with self.assertRaises(CardError):
            self.mounter.mount(Card(), "/dev/vdb")
        target = str(self.root / "c1")
        self.mounted.add(target)                     # the kernel finished it after all
        time.sleep(0.8)
        self.mounter.reap()
        self.assertNotIn(target, self.mounter.stalled)
        self.assertIn(f"umount -l {target}", self.calls())

    def test_a_late_remount_never_detaches_the_card_in_use(self):
        target = str(self.root / "c1")
        Path(target).mkdir()
        self.mounted.add(target)
        card = Card(mount=target)
        self.does("mount", "sleep 0.6")
        with self.assertRaises(CardError) as failed:
            self.mounter.remount(card, rw=False)
        self.assertEqual(failed.exception.code, "sync_failed")
        time.sleep(0.8)
        self.mounter.reap()
        self.assertEqual(self.mounter.stalled, {})
        self.assertNotIn(f"umount -l {target}", self.calls(), "the card in use was detached")
        self.assertEqual(card.mount, target)
        self.does("mount", "exit 0")
        self.mounter.remount(card, rw=False)         # and it can be remounted again

    def test_a_mount_point_stays_taken_until_the_detach_of_a_late_mount_ends(self):
        # The late mount ends; its detach takes a while. A card that comes in
        # meanwhile must not be mounted there, or the detach would take it.
        self.does("mount", "sleep 0.4")
        with self.assertRaises(CardError):
            self.mounter.mount(Card(), "/dev/vdb")
        target = str(self.root / "c1")
        self.mounted.add(target)
        self.does("umount", "sleep 0.5")
        time.sleep(0.6)
        with patch.object(cardd, "MOUNT_TIMEOUT", 3.0):    # this detach is slow, not stalled
            reaper = threading.Thread(target=self.mounter.reap)
            reaper.start()
            time.sleep(0.15)
            self.does("mount", "exit 0")
            with self.assertRaises(CardError):       # the new card waits for the detach
                self.mounter.mount(Card(), "/dev/vdc")
            reaper.join()
        self.assertEqual(self.mounter.stalled, {})
        self.assertEqual(self.mounter.mount(Card(), "/dev/vdc"), target)   # then it is mounted

    def test_a_detach_that_does_not_end_is_bounded_and_keeps_its_mount_point(self):
        self.does("mount", "sleep 0.4")
        with self.assertRaises(CardError):
            self.mounter.mount(Card(), "/dev/vdb")
        target = str(self.root / "c1")
        self.does("umount", "sleep 5")
        time.sleep(0.6)
        started = time.monotonic()
        self.mounter.reap()
        self.assertLess(time.monotonic() - started, 2.0, "the service waited on the detach")
        self.assertEqual(self.mounter.stalled[target][0], "detach")
        self.assertIsNone(self.mounter.stalled[target][1].poll(), "kept running, not killed and waited on")

    def test_an_unmount_that_does_not_answer_never_reads_as_safe_removal(self):
        target = str(self.root / "c1")
        Path(target).mkdir()
        self.mounted.add(target)
        card = Card(mount=target)
        self.does("umount", "sleep 0.6")
        started = time.monotonic()
        with self.assertRaises(CardError) as failed:
            self.mounter.unmount(card, strict=True)
        self.assertLess(time.monotonic() - started, 0.55, "the service waited on the card")
        self.assertEqual(failed.exception.code, "unmount_failed")
        self.assertEqual(card.mount, target, "still tracked: the caller must not tell anyone to unplug")
        count = len(self.calls())
        with self.assertRaises(CardError):           # a retry while it runs is refused, nothing new started
            self.mounter.unmount(card, strict=True)
        self.assertEqual(len(self.calls()), count)
        # The umount ends; the kernel no longer has the mount: the next
        # release finds that and does not run umount on a mount point that is gone.
        time.sleep(0.8)
        self.mounted.discard(target)
        self.mounter.unmount(card, strict=True)
        self.assertIsNone(card.mount)
        self.assertEqual(len(self.calls()), count)

    def test_an_ordinary_mount_and_unmount_work_as_before(self):
        card = Card()
        card.mount = self.mounter.mount(card, "/dev/vdb")
        self.assertEqual(card.mount, str(self.root / "c1"))
        self.mounted.add(card.mount)
        self.mounter.unmount(card, strict=True)
        self.assertIsNone(card.mount)
        self.assertEqual([call.split()[0] for call in self.calls()], ["mount", "umount"])
        self.assertEqual(getattr(self.mounter, "stalled", {}), {})


class LateRemountTests(unittest.TestCase):
    """A save's remount that ends after its bound may still take effect: the
    card service then follows what the kernel did, never what the failed
    save believed. The stand-in `mount` keeps the card's read-only or
    read-write state in a file, as the kernel's table would."""

    def setUp(self):
        self.bin = Path(tempfile.mkdtemp())
        self.state = self.bin / "state"
        self.state.write_text("ro")
        self.log = self.bin / "calls.log"
        self.delay = self.bin / "delay"          # "rw" or "ro": that remount takes 0.6 s
        program = self.bin / "mount"
        program.write_text(f'''#!/bin/sh
echo "mount $*" >> "{self.log}"
case "$*" in
  *remount,rw*) [ "$(cat "{self.delay}" 2>/dev/null)" = rw ] && sleep 0.6; echo rw > "{self.state}" ;;
  *remount,ro*) [ "$(cat "{self.delay}" 2>/dev/null)" = ro ] && sleep 0.6; echo ro > "{self.state}" ;;
esac
exit 0
''')
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
        state = self.state
        real_dir = RealDirMounter(Path(tempfile.mkdtemp()))

        class Mounter(cardd.Mounter):
            def mount(self, card, path):
                return real_dir.mount(card, path)

            def unmount(self, card, strict=False):
                card.mount = None

            def mounted(self, target):
                return True

            def read_only(self, target):
                return state.read_text().strip() == "ro"

        for p in (patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}),
                  patch.object(cardd, "MOUNT_TIMEOUT", 0.3, create=True)):
            p.start()
            self.addCleanup(p.stop)
        info = game_info(naming="mun")
        self.h = Harness(mounter=Mounter(), validator=lambda mount: dict(info))
        self.h.manager.device_added("vdc", "NPT-game", "/dev/vdc")
        self.h.pump()
        self.card = self.h.manager.cards["vdc"]
        self.addCleanup(self.finish)

    def finish(self):
        for _, proc in self.h.manager.mounter.stalled.values():
            proc.kill()
            proc.wait()

    def save(self):
        replies = []
        insertion = self.h.manager.snapshot()["cards"][0]["insertion"]
        self.h.manager.save({"type": "save", "slot": "c1", "insertion": insertion, "serial": "NPT-game",
                             "version": "0.1.0", "game": "mun.collect", "session": "s1", "schema": 1,
                             "payload": {"x": 1}}, replies.append)
        self.h.pump()
        return replies[-1]

    def remounts(self):
        return [line.split(",")[0].split()[-1] for line in self.log.read_text().splitlines()]

    def test_a_late_remount_to_read_write_is_returned_to_read_only(self):
        self.delay.write_text("rw")
        reply = self.save()
        self.assertEqual((reply["ok"], reply["error"]["code"]), (False, "write_failed"))
        self.assertTrue(self.card.writable, "the card may become writable: it counts as such")
        self.assertTrue(self.card.closing, "and its return to read-only is due")
        self.assertFalse(self.h.manager._close_card(self.card), "nothing is decided while the remount runs")
        time.sleep(0.8)                              # the remount to read-write ends after all
        self.assertEqual(self.state.read_text().strip(), "rw")
        self.assertTrue(self.h.manager._close_card(self.card))
        self.assertEqual(self.state.read_text().strip(), "ro", "the card was left writable")
        self.assertFalse(self.card.writable)

    def test_a_late_remount_to_read_only_is_not_done_twice(self):
        self.delay.write_text("ro")
        reply = self.save()
        self.assertEqual((reply["ok"], reply["error"]["code"]), (False, "sync_failed"))
        self.assertTrue(self.card.writable)
        time.sleep(0.8)                              # the remount to read-only ends after all
        self.assertEqual(self.state.read_text().strip(), "ro")
        before = self.remounts()
        self.assertTrue(self.h.manager._close_card(self.card))
        self.assertFalse(self.card.writable)
        self.assertEqual(self.remounts(), before, "a card already read-only was remounted again")


if __name__ == "__main__":
    unittest.main()
