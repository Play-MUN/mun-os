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


class CommaTests(Fixture):
    """QEMU reads a comma as the end of a field of an option, so a path with
    one would be cut there and the rest read as further fields."""

    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(vm, "which", side_effect=lambda name: name))
        self.stack.enter_context(patch.object(vm, "firmware_path", return_value=Path("/firmware.fd")))
        self.stack.enter_context(patch.object(vm, "accelerator", return_value="tcg"))
        self.stack.enter_context(patch.object(vm, "guest_timezone", return_value="UTC"))
        self.stack.enter_context(patch.object(vm, "HOST", vm.host.Host("linux", "arm64")))

    def test_a_guest_under_a_path_with_a_comma_is_refused_with_the_reason(self):
        self.guest(self.root / "my,checkout" / "guests" / "one")
        with self.assertRaises(vm.LabError) as refused:
            vm.qemu_command("none")
        self.assertIn("comma", str(refused.exception))
        self.assertIn("my,checkout", str(refused.exception))

    def test_a_firmware_or_capture_path_with_a_comma_is_refused(self):
        self.guest(self.root / "guests" / "one")
        with patch.object(vm, "firmware_path", return_value=Path("/opt/qemu,11/edk2-aarch64-code.fd")), \
                self.assertRaises(vm.LabError) as refused:
            vm.qemu_command("none")
        self.assertIn("comma", str(refused.exception))
        vm.PATHS["audio"] = self.root / "a,b" / "audio.wav"
        with self.assertRaises(vm.LabError):
            vm.qemu_command("none", audio="wav")

    def test_a_path_without_one_goes_into_the_options_as_it_is(self):
        self.guest(self.root / "guests" / "one")
        command = vm.qemu_command("none", audio="wav")
        self.assertIn(f"if=pflash,format=raw,file={vm.PATHS['efivars']}", command)
        self.assertIn(f"unix:{vm.PATHS['qmp']},server,nowait", command)
        self.assertIn(f"wav,id=snd0,path={vm.PATHS['audio']}", command)

    def test_the_builder_is_held_to_the_same_rule(self):
        sys.path.insert(0, str(ROOT / "vm"))
        import mundev
        for where, refused in (("my,checkout", True), ("checkout", False)):
            with self.subTest(where=where), patch.object(mundev, "BUILDERS_ROOT", self.root / where / "builders"), \
                    patch.object(mundev.vm, "which", side_effect=lambda name: name), \
                    patch.object(mundev.vm, "firmware_path", return_value=Path("/firmware.fd")), \
                    patch.object(mundev.vm, "accelerator", return_value="tcg"):
                builder = mundev.Builder("b1")
                if refused:
                    with self.assertRaises(mundev.vm.LabError) as error:
                        builder.qemu_command()
                    self.assertIn("comma", str(error.exception))
                else:
                    self.assertIn(f"if=pflash,format=raw,file={builder.efivars}", builder.qemu_command())


if __name__ == "__main__":
    unittest.main()
