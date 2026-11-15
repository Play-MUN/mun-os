"""What the lab hands to QEMU as a file name or inside an option is checked first."""

import argparse
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("munvm_qemu_options", ROOT / "vm" / "munvm.py")
vm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vm)


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mun-options-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        # A deep guest directory moves its sockets under the temporary directory.
        self.stack.enter_context(patch.object(tempfile, "tempdir", str(self.root)))

    def guest(self, directory):
        directory.mkdir(parents=True)
        self.stack.enter_context(patch.object(vm, "PATHS", vm.instance_paths(directory)))


class ScreenshotNameTests(Fixture):
    def setUp(self):
        super().setUp()
        self.guest(self.root / "guest")
        self.qmp = Mock()
        self.stack.enter_context(patch.object(vm, "Qmp", return_value=self.qmp))
        self.stack.enter_context(patch.object(vm, "read_pid", return_value=123))

    def test_a_screenshot_name_is_a_file_name_in_the_guests_screens(self):
        for name in ("../../../x.png", "/tmp/x.png", "a/b.png", "..", ".hidden.png", "-o.png", "a b.png", "x" * 101):
            with self.subTest(name=name):
                with self.assertRaises(vm.LabError) as refused:
                    vm.cmd_screenshot(argparse.Namespace(name=name))
                self.assertIn("screenshot name", str(refused.exception))
        self.qmp.execute.assert_not_called()

    def test_an_ordinary_name_and_the_default_one_are_dumped_there(self):
        vm.cmd_screenshot(argparse.Namespace(name="smoke-game.png"))
        self.assertEqual(self.qmp.execute.call_args.kwargs["filename"], str(vm.PATHS["screens"] / "smoke-game.png"))
        vm.cmd_screenshot(argparse.Namespace(name=None))
        dumped = Path(self.qmp.execute.call_args.kwargs["filename"])
        self.assertEqual(dumped.parent, vm.PATHS["screens"])
        self.assertRegex(dumped.name, r"^screen-\d{8}-\d{6}\.png$")


if __name__ == "__main__":
    unittest.main()
