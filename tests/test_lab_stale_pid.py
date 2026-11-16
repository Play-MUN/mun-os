"""A qemu.pid left behind by a host that went down does not block its guest.

After a crash the pid file survives, and its number is soon another
process's. The guest is running only if that process is this guest's QEMU;
anything else is a stale file. No QEMU is started here: a sleeping process
whose command line carries the guest's `-pidfile` stands in for one.
"""

import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("munvm_stale_pid", Path(__file__).resolve().parents[1] / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)


@unittest.skipIf(sys.platform == "win32", "a process's command line is read on macOS and Linux only")
class StalePidTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-pid-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / "guests" / "aa").mkdir(parents=True)
        (self.root / "guests" / "aa" / "guest.json").write_text('{"build": "b1"}')
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("GUESTS_ROOT", self.root / "guests"), ("CARD_ROOT", self.root / "cards"),
                            ("ATTACH_REGISTRY", self.root / "cards" / "attached.json"),
                            ("PATHS", vm.PATHS.copy()), ("INSTANCE", vm.INSTANCE), ("GUEST_HOSTNAME", vm.GUEST_HOSTNAME)):
            stack.enter_context(patch.object(vm, name, value))
        vm.select_instance("aa")
        for key in ("system_disk", "efivars"):
            vm.PATHS[key].touch()

    def lab(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = vm.main(["--instance", "aa", *arguments])
        return status, out.getvalue(), err.getvalue()

    def left_by_a_crash(self):
        """A pid file naming a live process that is not QEMU (this one), and
        the control socket's file with nothing listening."""
        vm.PATHS["pidfile"].write_text(f"{os.getpid()}\n")
        vm.PATHS["qmp"].parent.mkdir(parents=True, exist_ok=True)
        with socket.socket(socket.AF_UNIX) as left:
            left.bind(str(vm.PATHS["qmp"]))

    def stand_in(self, spelled=str):
        """A live process with this guest's -pidfile on its command line."""
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)",
                                    "-pidfile", spelled(str(vm.PATHS["pidfile"]))])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        vm.PATHS["pidfile"].write_text(f"{process.pid}\n")
        # The interpreter starts through a launcher that replaces itself:
        # wait until the process is the one that stays, as a QEMU is.
        deadline = time.monotonic() + 20
        while "-pidfile" not in (vm.host.process_command(vm.HOST, process.pid) or "") and time.monotonic() < deadline:
            time.sleep(0.05)
        return process

    def test_a_pid_that_is_now_another_process_is_not_this_guest(self):
        self.left_by_a_crash()
        self.assertTrue(vm.pid_alive(os.getpid()))
        self.assertIsNone(vm.read_pid())

    def test_the_guests_own_qemu_is_still_recognised(self):
        process = self.stand_in()
        self.assertEqual(vm.read_pid(), process.pid)
        process.kill()
        process.wait()
        self.assertIsNone(vm.read_pid())

    def test_a_guest_started_from_the_checkout_spelled_in_another_case_is_recognised(self):
        # On a file system that ignores case the same checkout can be entered
        # as /Applications/... or /applications/...: the running guest is the
        # same one, and taking it for a stale file would clear its cards.
        process = self.stand_in(spelled=str.swapcase)
        self.assertEqual(vm.read_pid(), process.pid)
        status, out, _err = self.lab("status")
        self.assertEqual(status, 1, "it is running: its control socket is asked, and here nothing answers")
        self.assertTrue(vm.PATHS["pidfile"].exists())

    def test_a_command_line_the_system_does_not_show_is_not_another_process(self):
        # ps prints the bare program name in parentheses when it cannot read
        # a process's arguments: that says nothing of whose process it is.
        mac = vm.host.Host("macos", "arm64", hypervisor=True)
        listed = lambda text: patch.object(vm.host.subprocess, "run",  # noqa: E731
                                           return_value=subprocess.CompletedProcess([], 0, text, ""))
        with listed("(qemu-system-aarch64)\n"):
            self.assertIsNone(vm.host.process_command(mac, 4242))
        with listed("\n"):
            self.assertIsNone(vm.host.process_command(mac, 4242))
        with listed("/opt/homebrew/bin/qemu-system-aarch64 -name mun-aa -pidfile /x/qemu.pid\n"):
            self.assertEqual(vm.host.process_command(mac, 4242),
                             "/opt/homebrew/bin/qemu-system-aarch64 -name mun-aa -pidfile /x/qemu.pid")
        vm.PATHS["pidfile"].write_text(f"{os.getpid()}\n")
        with patch.object(vm, "HOST", mac), listed("(qemu-system-aarch64)\n"):
            self.assertEqual(vm.read_pid(), os.getpid(), "not known to be another: it counts as the guest")

    def test_stop_and_status_say_the_guest_is_off_and_the_file_is_removed(self):
        self.left_by_a_crash()
        status, out, err = self.lab("status")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(json.loads(out)["running"], False)
        status, out, err = self.lab("stop", "--hard")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("VM is not running", out)
        self.assertIn("qemu.pid", out, "and says what it removed")
        self.assertFalse(vm.PATHS["pidfile"].exists())

    def test_start_is_not_refused_as_already_running(self):
        self.left_by_a_crash()
        with patch.object(vm, "which", side_effect=lambda name: name), \
                patch.object(vm, "firmware_path", return_value=Path("/firmware.fd")):
            status, out, err = self.lab("start", "--print-command")
        self.assertEqual((status, err), (0, ""))
        self.assertIn("-pidfile", out)

    def test_a_running_guest_is_still_refused_a_second_start(self):
        self.stand_in()
        status, _out, err = self.lab("start", "--print-command")
        self.assertEqual(status, 1)
        self.assertIn("already running", err)

    def test_a_control_socket_that_does_not_answer_is_an_error_message(self):
        self.stand_in()
        with socket.socket(socket.AF_UNIX) as left:
            left.bind(str(vm.PATHS["qmp"]))
        for arguments in (("stop", "--hard"), ("status",), ("screenshot",)):
            with self.subTest(arguments=arguments):
                status, _out, err = self.lab(*arguments)
                self.assertEqual(status, 1)
                self.assertTrue(err.startswith("error: "), err)
                self.assertIn("control socket", err)


if __name__ == "__main__":
    unittest.main()
