"""The check that another account cannot reach a guest's control socket
(tests/windows_other_account.py, run on Windows by the Hosts workflow),
run here as the workflow runs it: a separate process, its exit status the
verdict, against real sockets. Only a refusal for want of rights may pass;
nobody listening or a path that is not there must not read as one."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "windows_other_account.py"


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs AF_UNIX sockets this Python can listen on")
class OtherAccountCheckTests(unittest.TestCase):
    def setUp(self):
        # Short: a socket's path has a small bound (104 bytes on macOS).
        self.dir = Path(tempfile.mkdtemp(prefix="oa", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(os.chmod, self.dir, 0o700)
        self.path = self.dir / "qmp.sock"

    def listen(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        server.listen(4)
        self.addCleanup(server.close)
        return server

    def check(self, *args):
        done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout.strip()

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root is not refused by a folder's rights")
    def test_access_denied_while_the_socket_listens_passes(self):
        self.listen()
        os.chmod(self.dir, 0)                    # this account may not reach it
        status, said = self.check(str(self.path))
        self.assertEqual(status, 0, said)
        self.assertIn("access denied", said)

    def test_nobody_listening_does_not_pass_for_a_refusal(self):
        # The false positive: a socket file that nothing listens on was
        # taken for another account being refused.
        self.listen().close()
        status, said = self.check(str(self.path))
        self.assertEqual(status, 2, said)
        self.assertIn("proves nothing", said)

    def test_a_path_that_is_not_there_does_not_pass(self):
        status, said = self.check(str(self.dir / "absent.sock"))
        self.assertEqual(status, 2, said)

    def test_a_connection_is_reported(self):
        self.listen()
        status, said = self.check(str(self.path))
        self.assertEqual(status, 1, said)

    def test_a_check_that_cannot_run_does_not_pass(self):
        self.assertEqual(self.check()[0], 3)


if __name__ == "__main__":
    unittest.main()
