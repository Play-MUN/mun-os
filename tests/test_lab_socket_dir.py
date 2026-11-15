"""The short directory QEMU's control sockets move to is the user's alone.

A guest directory too deep for a UNIX socket path gets its sockets in
<temporary directory>/mun-<uid>/<hash>. On a shared temporary directory
anyone can make that path first; QMP there runs commands as the user, so a
directory that is not this user's, or that others can enter, is refused.
"""

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("munvm_socket_dir", Path(__file__).resolve().parents[1] / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)

DEEP = Path("/" + "d" * 120)


@unittest.skipIf(sys.platform == "win32", "UNIX sockets and POSIX modes")
class SocketDirectoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-sock-")
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name).resolve()
        patcher = patch.object(tempfile, "tempdir", str(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.parent = self.tmp / f"mun-{os.getuid()}"
        self.leaf = self.parent / hashlib.sha1(str(DEEP).encode()).hexdigest()[:12]

    def test_a_new_directory_is_made_for_this_user_alone(self):
        path = vm.socket_path(DEEP, "qmp.sock")
        self.assertEqual(path, self.leaf / "qmp.sock")
        for directory in (self.parent, self.leaf):
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700, directory)
        self.assertEqual(vm.socket_path(DEEP, "qga.sock").parent, self.leaf, "and it is found again")

    def test_a_directory_others_can_enter_is_refused(self):
        # The parent as the tool makes it, whatever this user's umask: the
        # directory under test is the one inside it.
        self.parent.mkdir(mode=0o700)
        self.leaf.mkdir()
        for mode in (0o777, 0o750, 0o705):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.leaf, mode)
                with self.assertRaises(vm.LabError) as refused:
                    vm.socket_path(DEEP, "qmp.sock")
                self.assertIn(str(self.leaf), str(refused.exception))
        os.chmod(self.leaf, 0o700)
        self.assertEqual(vm.socket_path(DEEP, "qmp.sock"), self.leaf / "qmp.sock")

    def test_a_parent_others_can_write_is_refused(self):
        # Whoever can write the parent can put another directory at the name.
        self.leaf.mkdir(parents=True, mode=0o700)
        os.chmod(self.parent, 0o777)
        with self.assertRaises(vm.LabError) as refused:
            vm.socket_path(DEEP, "qmp.sock")
        self.assertIn(str(self.parent), str(refused.exception))
        os.chmod(self.parent, 0o755)            # as earlier versions made it: listed by others, not written
        self.assertEqual(vm.socket_path(DEEP, "qmp.sock"), self.leaf / "qmp.sock")

    def test_a_directory_of_another_user_is_refused(self):
        # Made here, by this user, at the name another user's lab would use:
        # to that user it is somebody else's directory.
        other = os.getuid() + 1
        with patch.object(vm.os, "getuid", return_value=other):
            (self.tmp / f"mun-{other}").mkdir(mode=0o700)
            with self.assertRaises(vm.LabError) as refused:
                vm.socket_path(DEEP, "qmp.sock")
        self.assertIn("owner", str(refused.exception))

    def test_a_link_in_its_place_is_refused(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        self.parent.mkdir(mode=0o700)
        os.symlink(elsewhere, self.leaf)
        with self.assertRaises(vm.LabError):
            vm.socket_path(DEEP, "qmp.sock")

    def test_a_short_path_stays_in_the_guest_directory(self):
        self.assertEqual(vm.socket_path(Path("/tmp/x"), "qmp.sock"), Path("/tmp/x/qmp.sock"))
        self.assertFalse(self.parent.exists())


if __name__ == "__main__":
    unittest.main()
