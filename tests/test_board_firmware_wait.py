"""The q6a-dev profile's firmware wait (extra/usr/lib/mun/board-firmware-wait):
writing the UEFI variable lifts efivarfs's immutable flag for the one write
and must put the file's original flags back, after a write that worked and
after one that failed. The variable is a temporary file and the flags live
in a stand-in for ioctl, so no firmware is touched and any host runs it."""

import importlib.machinery
import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "os/mkosi/mkosi.profiles/q6a-dev/extra/usr/lib/mun/board-firmware-wait"


def load():
    loader = importlib.machinery.SourceFileLoader("board_firmware_wait", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Flags:
    """efivarfs's inode flags as FS_IOC_GETFLAGS and FS_IOC_SETFLAGS see
    them, and every value set, in order."""

    def __init__(self, module, flags):
        self.module, self.flags, self.set = module, flags, []

    def __call__(self, fd, request, buffer, mutate):
        if request == self.module.FS_IOC_GETFLAGS:
            buffer[0] = self.flags
        elif request == self.module.FS_IOC_SETFLAGS:
            self.flags = buffer[0]
            self.set.append(buffer[0])
        else:
            raise AssertionError(f"unexpected ioctl {request:#x}")
        return 0


class BoardFirmwareWaitTests(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.work = Path(tempfile.mkdtemp(prefix="board-firmware-wait-test-"))
        self.addCleanup(self.cleanup)
        self.variable = self.work / "BootAskTimeoutSecs"
        self.variable.write_bytes(struct.pack("<II", 7, 5))

    def cleanup(self):
        self.variable.chmod(0o644)
        self.variable.unlink(missing_ok=True)
        self.work.rmdir()

    def test_a_write_puts_the_immutable_flag_back(self):
        flags = Flags(self.module, self.module.FS_IMMUTABLE_FL)
        self.module.write(1, path=str(self.variable), ioctl=flags)
        self.assertEqual(struct.unpack("<II", self.variable.read_bytes()), (7, 1), "attributes kept, value set")
        self.assertEqual(flags.set, [0, self.module.FS_IMMUTABLE_FL], "lifted for the write, then put back")
        self.assertEqual(flags.flags, self.module.FS_IMMUTABLE_FL)

    def test_other_flags_are_kept_as_they_were(self):
        other = 0x80  # FS_NOATIME_FL, say
        flags = Flags(self.module, self.module.FS_IMMUTABLE_FL | other)
        self.module.write(1, path=str(self.variable), ioctl=flags)
        self.assertEqual(flags.set, [other, self.module.FS_IMMUTABLE_FL | other])

    def test_a_file_that_was_not_immutable_is_left_alone(self):
        flags = Flags(self.module, 0)
        self.module.write(1, path=str(self.variable), ioctl=flags)
        self.assertEqual(flags.set, [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes a read-only file anyway")
    def test_a_failed_write_still_puts_the_immutable_flag_back(self):
        self.variable.chmod(0o444)  # the write is refused, as a firmware may refuse it
        flags = Flags(self.module, self.module.FS_IMMUTABLE_FL)
        with self.assertRaises(OSError):
            self.module.write(1, path=str(self.variable), ioctl=flags)
        self.assertEqual(flags.flags, self.module.FS_IMMUTABLE_FL, "the variable is protected again")
        self.assertEqual(flags.set, [0, self.module.FS_IMMUTABLE_FL])
        self.assertEqual(struct.unpack("<II", self.variable.read_bytes()), (7, 5), "the value untouched")

    def test_a_flag_that_cannot_be_put_back_is_an_error(self):
        flags = Flags(self.module, self.module.FS_IMMUTABLE_FL)

        def refusing(fd, request, buffer, mutate):
            if request == self.module.FS_IOC_SETFLAGS and buffer[0] & self.module.FS_IMMUTABLE_FL:
                raise PermissionError(1, "Operation not permitted")
            return flags(fd, request, buffer, mutate)

        with self.assertRaisesRegex(OSError, "could not be put back"):
            self.module.write(1, path=str(self.variable), ioctl=refusing)


if __name__ == "__main__":
    unittest.main()
