"""Mount, remount and umount within a bound: a card that stops answering
leaves its command in the kernel, where no signal reaches it. The service
reports the operation as failed after MOUNT_TIMEOUT, keeps the process
(never an orphan), starts nothing more on that mount point until it ends,
and never calls a card safe to remove while its unmount has not finished."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mun-cardd"))
sys.path.insert(0, str(ROOT / "tools" / "mun-card"))

import cardd  # noqa: E402
from mun_card.errors import CardError  # noqa: E402

REAL_POPEN = subprocess.Popen


class Card:
    def __init__(self, mount=None):
        self.slot, self.serial, self.mount = "c1", "NPT-test", mount


class MountTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.mounter = cardd.Mounter(self.root)
        self.started, self.lazy = [], []
        self.behaviour = {"mount": "exit 0", "umount": "exit 0"}
        for p in (patch.object(cardd, "MOUNT_TIMEOUT", 0.3),
                  patch.object(cardd.ext4, "check_mountable", lambda path: None),
                  patch.object(cardd.subprocess, "Popen", self.popen),
                  patch.object(cardd.subprocess, "run", self.run_lazy),
                  patch.object(cardd.Mounter, "mounted", lambda self, target: target in mounted)):
            p.start()
            self.addCleanup(p.stop)
        mounted = self.mounted = set()
        self.addCleanup(self.finish)

    def popen(self, argv, **kwargs):
        # The command's own name decides what the stand-in does.
        self.started.append(argv)
        return REAL_POPEN(["sh", "-c", self.behaviour[argv[0]]], **kwargs)

    def run_lazy(self, argv, **kwargs):
        self.lazy.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def finish(self):
        for proc in self.mounter.stalled.values():
            proc[1].kill()
            proc[1].wait()

    def test_a_mount_that_does_not_answer_fails_in_time_and_is_kept(self):
        self.behaviour["mount"] = "sleep 30"
        started = time.monotonic()
        with self.assertRaises(CardError) as failed:
            self.mounter.mount(Card(), "/dev/vdb")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(failed.exception.code, "mount_failed")
        target = str(self.root / "c1")
        self.assertIn(target, self.mounter.stalled, "the process is kept, not abandoned")
        self.assertIsNone(self.mounter.stalled[target][1].poll(), "and is still running")
        # Nothing more is started on that mount point while it runs.
        count = len(self.started)
        with self.assertRaises(CardError):
            self.mounter.mount(Card(), "/dev/vdb")
        self.assertEqual(len(self.started), count)

    def test_a_late_mount_is_detached_once_it_ends_since_no_card_uses_it(self):
        self.behaviour["mount"] = "sleep 0.6"
        with self.assertRaises(CardError):
            self.mounter.mount(Card(), "/dev/vdb")
        target = str(self.root / "c1")
        self.mounted.add(target)               # the kernel finished it after all
        time.sleep(0.8)
        self.mounter.reap()
        self.assertNotIn(target, self.mounter.stalled)
        self.assertIn(["umount", "-l", target], self.lazy)

    def test_an_unmount_that_does_not_answer_never_reads_as_safe_removal(self):
        target = str(self.root / "c1")
        Path(target).mkdir()
        self.mounted.add(target)
        card = Card(mount=target)
        self.behaviour["umount"] = "sleep 0.6"
        with self.assertRaises(CardError) as failed:
            self.mounter.unmount(card, strict=True)
        self.assertEqual(failed.exception.code, "unmount_failed")
        self.assertEqual(card.mount, target, "still tracked: the caller must not tell anyone to unplug")
        with self.assertRaises(CardError):     # a retry while it runs is refused, nothing new started
            self.mounter.unmount(card, strict=True)
        # The umount ends; the kernel no longer has the mount: the next
        # release finds that and does not run umount on a mount point that is gone.
        time.sleep(0.8)
        self.mounted.discard(target)
        count = len(self.started)
        self.mounter.unmount(card, strict=True)
        self.assertIsNone(card.mount)
        self.assertEqual(len(self.started), count)

    def test_an_ordinary_mount_and_unmount_work_as_before(self):
        card = Card()
        card.mount = self.mounter.mount(card, "/dev/vdb")
        self.assertEqual(card.mount, str(self.root / "c1"))
        self.mounted.add(card.mount)
        self.mounter.unmount(card, strict=True)
        self.assertIsNone(card.mount)
        self.assertEqual([argv[0] for argv in self.started], ["mount", "umount"])
        self.assertEqual(self.mounter.stalled, {})


if __name__ == "__main__":
    unittest.main()
