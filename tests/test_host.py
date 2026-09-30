"""Host tests of vm/host.py and the command lines vm/munvm.py builds for each
host the laboratory supports (macOS, Linux, Windows; ARM64 and x86_64). They
run on any machine: no QEMU, guest or network is used."""

import argparse
import contextlib
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm"))
import host  # noqa: E402
import munvm as vm  # noqa: E402
import mundev  # noqa: E402

MAC = host.Host("macos", "arm64", hypervisor=True)
MAC_VM = host.Host("macos", "arm64", hypervisor=False)   # a macOS virtual machine, such as a CI runner
LINUX_KVM = host.Host("linux", "arm64", hypervisor=True)
LINUX_ARM_NO_KVM = host.Host("linux", "arm64", hypervisor=False)
LINUX_PC = host.Host("linux", "x86_64", hypervisor=True)   # KVM on x86_64 cannot run an ARM64 guest
WINDOWS = host.Host("windows", "x86_64")
WINDOWS_ARM = host.Host("windows", "arm64")
THIS = host.detect()
REAL_TRY_LOCK, REAL_UNLOCK = host.try_lock, host.unlock


class Fixture(unittest.TestCase):
    def enterContext(self, context):
        # Keep tests runnable with the macOS system Python 3.9.
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def temporary(self) -> Path:
        directory = tempfile.TemporaryDirectory(prefix="mun-host-")
        self.addCleanup(directory.cleanup)
        return Path(directory.name).resolve()

    def guest_paths(self, on: host.Host):
        """A guest directory with the real file names, laid out as `on` would."""
        root = self.temporary()
        with patch.object(vm, "HOST", on):
            paths = vm.instance_paths(root)
        for key in ("system_disk", "efivars"):
            paths[key].touch()
        return paths

    def simulate_locks(self):
        # Another host's lock primitive cannot run here; this machine's stands in.
        self.enterContext(patch.object(vm.host, "try_lock", side_effect=lambda on, fd: REAL_TRY_LOCK(THIS, fd)))
        self.enterContext(patch.object(vm.host, "unlock", side_effect=lambda on, fd: REAL_UNLOCK(THIS, fd)))


class AcceleratorTests(Fixture):
    def test_each_host_gets_its_accelerator_and_emulation_elsewhere(self):
        self.assertEqual(host.accelerator(MAC), "hvf")
        self.assertEqual(host.accelerator(LINUX_KVM), "kvm")
        for emulated in (MAC_VM, LINUX_ARM_NO_KVM, LINUX_PC, WINDOWS, WINDOWS_ARM, host.Host("macos", "x86_64")):
            self.assertEqual(host.accelerator(emulated), "tcg", emulated.label)

    def test_emulation_can_be_forced_and_nothing_foreign_can(self):
        self.assertEqual(host.accelerator(MAC, "tcg"), "tcg")
        self.assertEqual(host.accelerator(LINUX_KVM, "kvm"), "kvm")
        for requested, on in (("kvm", MAC), ("hvf", LINUX_KVM), ("kvm", LINUX_ARM_NO_KVM), ("kvm", LINUX_PC),
                              ("hvf", MAC_VM), ("whpx", WINDOWS)):
            with self.assertRaises(host.HostError, msg=f"{requested} on {on.label}"):
                host.accelerator(on, requested)

    def test_hardware_acceleration_uses_the_host_cpu_and_emulation_qemus_own(self):
        self.assertEqual(host.machine_arguments("hvf"), ["-machine", "virt,accel=hvf,gic-version=max", "-cpu", "host"])
        self.assertEqual(host.machine_arguments("tcg")[-1], "max,pauth-impdef=on")

    def test_machine_names_are_normalised(self):
        for raw, expected in (("arm64", "arm64"), ("aarch64", "arm64"), ("ARM64", "arm64"),
                              ("x86_64", "x86_64"), ("AMD64", "x86_64")):
            self.assertEqual(host.normalise_machine(raw), expected)


class BackendTests(Fixture):
    def test_the_window_backend_follows_the_host_and_what_qemu_offers(self):
        self.assertEqual(host.window_display(MAC, ["none", "cocoa", "sdl"]), "cocoa")
        self.assertEqual(host.window_display(LINUX_KVM, ["none", "sdl", "gtk"]), "gtk")
        self.assertEqual(host.window_display(WINDOWS, ["none", "sdl"]), "sdl")
        with self.assertRaises(host.HostError) as refused:
            host.window_display(LINUX_PC, ["none", "egl-headless"])
        self.assertIn("qemu-system-gui", str(refused.exception), "the Debian package that adds the window")

    def test_sound_takes_the_hosts_first_audible_backend_or_stays_silent(self):
        self.assertEqual(host.default_audio(MAC, ["none", "coreaudio", "wav"]), "coreaudio")
        self.assertEqual(host.default_audio(LINUX_KVM, ["none", "alsa", "pa", "pipewire"]), "pipewire")
        self.assertEqual(host.default_audio(LINUX_PC, ["none", "alsa"]), "alsa")
        self.assertEqual(host.default_audio(WINDOWS, ["none", "dsound", "sdl"]), "dsound")
        self.assertEqual(host.default_audio(LINUX_PC, ["none", "wav"]), "none")

    def test_the_backends_qemu_lists_are_read_from_its_help(self):
        listing = "Available audio drivers:\nnone\nalsa\npa\npipewire\nwav\n"
        with patch.object(host.subprocess, "run") as run:
            run.return_value.stdout = listing
            self.assertEqual(host.qemu_offers("/fake/qemu-a", "audiodev"), ["none", "alsa", "pa", "pipewire", "wav"])
            run.return_value.stdout = "Available display backend types:\nnone\ngtk\negl-headless\n"
            self.assertEqual(host.qemu_offers("/fake/qemu-b", "display"), ["none", "gtk", "egl-headless"])

    def test_the_version_is_read_from_qemu(self):
        with patch.object(host.subprocess, "run") as run:
            run.return_value.stdout = "QEMU emulator version 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1)\n"
            self.assertEqual(host.qemu_version("qemu"), (8, 2, 2))
            run.return_value.stdout = "nothing"
            with self.assertRaises(host.HostError):
                host.qemu_version("qemu")


class FirmwareTests(Fixture):
    def descriptor(self, directory: Path, name: str, executable: Path, features=(), machines=("virt-*",)):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(json.dumps({
            "interface-types": ["uefi"],
            "mapping": {"device": "flash", "mode": "split",
                        "executable": {"filename": str(executable), "format": "raw"}},
            "targets": [{"architecture": "aarch64", "machines": list(machines)}],
            "features": list(features)}))

    def test_qemus_own_copy_is_found_next_to_it_on_every_system(self):
        root = self.temporary()
        (root / "bin").mkdir()
        qemu = root / "bin" / "qemu-system-aarch64"
        qemu.touch()
        (root / "share" / "qemu").mkdir(parents=True)
        (root / "share" / "qemu" / "edk2-aarch64-code.fd").touch()
        self.assertEqual(host.firmware(MAC, str(qemu), {}), root / "share" / "qemu" / "edk2-aarch64-code.fd")
        windows = root / "Program Files" / "qemu"
        (windows / "share").mkdir(parents=True)
        (windows / "qemu-system-aarch64.exe").touch()
        (windows / "share" / "edk2-aarch64-code.fd").touch()
        self.assertEqual(host.firmware(WINDOWS, str(windows / "qemu-system-aarch64.exe"), {}),
                         windows / "share" / "edk2-aarch64-code.fd")

    def test_a_distributions_descriptor_names_it_and_secure_boot_is_passed_over(self):
        root = self.temporary()
        (root / "bin").mkdir()
        qemu = root / "bin" / "qemu-system-aarch64"
        qemu.touch()
        plain, secure = root / "AAVMF_CODE.fd", root / "AAVMF_CODE.secboot.fd"
        plain.touch()
        secure.touch()
        firmware_dir = root / "share" / "qemu" / "firmware"
        self.descriptor(firmware_dir, "10-secure.json", secure, features=["secure-boot", "enrolled-keys"])
        self.descriptor(firmware_dir, "20-x86.json", plain, machines=("pc-q35-*",))
        self.descriptor(firmware_dir, "60-aarch64.json", plain)
        with patch.object(host, "KNOWN_FIRMWARE", ()):
            self.assertEqual(host.firmware(LINUX_KVM, str(qemu), {}), plain)

    def test_an_explicit_file_wins_and_a_missing_one_is_explained(self):
        root = self.temporary()
        chosen = root / "mine.fd"
        chosen.touch()
        self.assertEqual(host.firmware(LINUX_PC, "/usr/bin/qemu-system-aarch64", {"MUN_VM_FIRMWARE": str(chosen)}),
                         chosen)
        with self.assertRaises(host.HostError):
            host.firmware(LINUX_PC, "/usr/bin/qemu-system-aarch64", {"MUN_VM_FIRMWARE": str(root / "none.fd")})
        (root / "bin").mkdir()
        (root / "bin" / "qemu-system-aarch64").touch()
        with patch.object(host, "KNOWN_FIRMWARE", ()), \
                patch.object(host, "_descriptor_firmware", return_value=None), \
                self.assertRaises(host.HostError) as missing:
            host.firmware(LINUX_PC, str(root / "bin" / "qemu-system-aarch64"), {})
        self.assertIn("qemu-efi-aarch64", str(missing.exception))


class TimeZoneTests(Fixture):
    def test_the_zone_is_read_from_the_localtime_link_or_given(self):
        root = self.temporary()
        link = root / "localtime"
        link.symlink_to("/var/db/timezone/zoneinfo/Europe/Madrid")
        self.assertEqual(host.local_timezone(MAC, {}, link), "Europe/Madrid")
        other = root / "linux-localtime"
        other.symlink_to("../usr/share/zoneinfo/America/Mexico_City")
        self.assertEqual(host.local_timezone(LINUX_PC, {}, other), "America/Mexico_City")
        self.assertEqual(host.local_timezone(MAC, {"MUN_VM_TIMEZONE": "Asia/Tokyo"}, link), "Asia/Tokyo")
        with self.assertRaises(host.HostError):
            host.local_timezone(MAC, {"MUN_VM_TIMEZONE": "../../etc/passwd"}, link)

    def test_without_a_zone_name_the_offset_stands_in(self):
        missing = self.temporary() / "none"
        with patch.object(host.Path, "read_text", side_effect=OSError):
            self.assertEqual(host.local_timezone(LINUX_PC, {}, missing, gmtoff=7200), "Etc/GMT-2")
        self.assertEqual(host.local_timezone(WINDOWS, {}, missing, gmtoff=-5 * 3600), "Etc/GMT+5")
        self.assertEqual(host.local_timezone(WINDOWS, {}, missing, gmtoff=5 * 3600 + 1800), "UTC", "half hours: UTC")
        self.assertEqual(host.local_timezone(WINDOWS, {}, missing, gmtoff=0), "UTC")


class ProgramTests(Fixture):
    def test_windows_finds_qemu_in_its_installation_directory(self):
        root = self.temporary()
        (root / "qemu").mkdir()
        (root / "qemu" / "qemu-img.exe").touch()
        with patch.object(host.shutil, "which", return_value=None):
            self.assertEqual(host.find_program(WINDOWS, "qemu-img", {"ProgramFiles": str(root)}),
                             str(root / "qemu" / "qemu-img.exe"))
            with self.assertRaises(host.HostError):
                host.find_program(LINUX_PC, "qemu-img", {"ProgramFiles": str(root)})

    def test_windows_arm64_also_looks_where_msys2_puts_its_arm64_qemu(self):
        drive = self.temporary()
        msys2 = drive / "msys64" / "clangarm64" / "bin"
        msys2.mkdir(parents=True)
        (msys2 / "qemu-system-aarch64.exe").touch()
        with patch.object(host.shutil, "which", return_value=None):
            self.assertEqual(host.find_program(WINDOWS_ARM, "qemu-system-aarch64", {"SystemDrive": str(drive)}),
                             str(msys2 / "qemu-system-aarch64.exe"))
            with self.assertRaises(host.HostError, msg="an x86_64 computer keeps QEMU for Windows"):
                host.find_program(WINDOWS, "qemu-system-aarch64", {"SystemDrive": str(drive)})

    @staticmethod
    def program(path, machine):
        # The smallest PE header: "MZ", the offset of "PE\0\0" at 0x3C, then
        # the Machine field.
        head = bytearray(0x80)
        head[:2] = b"MZ"
        head[0x3C:0x40] = (0x40).to_bytes(4, "little")
        head[0x40:0x44] = b"PE\0\0"
        head[0x44:0x46] = machine.to_bytes(2, "little")
        path.write_bytes(bytes(head))
        return str(path)

    def test_a_windows_program_says_which_processor_it_is_built_for(self):
        root = self.temporary()
        self.assertEqual(host.program_machine(self.program(root / "x64.exe", 0x8664)), "x86_64")
        self.assertEqual(host.program_machine(self.program(root / "arm.exe", 0xAA64)), "arm64")
        (root / "script").write_text("#!/bin/sh\n")
        self.assertIsNone(host.program_machine(str(root / "script")))
        self.assertIsNone(host.program_machine(str(root / "missing.exe")))

    def test_the_x86_64_qemu_is_refused_on_windows_arm64_with_what_to_install(self):
        # The second CI run: that build ended at once with 0xC00000FF there.
        root = self.temporary()
        intel, arm = self.program(root / "intel.exe", 0x8664), self.program(root / "arm.exe", 0xAA64)
        with self.assertRaises(host.HostError) as refused:
            host.check_qemu_build(WINDOWS_ARM, intel)
        self.assertIn("mingw-w64-clang-aarch64-qemu", str(refused.exception))
        host.check_qemu_build(WINDOWS_ARM, arm)
        host.check_qemu_build(WINDOWS, intel)
        host.check_qemu_build(MAC, "/opt/homebrew/bin/qemu-system-aarch64")


class ProcessAndLockTests(Fixture):
    @unittest.skipIf(sys.platform == "win32", "POSIX liveness")
    def test_liveness_of_this_process_and_of_none(self):
        this = THIS
        self.assertTrue(host.process_alive(this, os.getpid()))
        self.assertFalse(host.process_alive(this, 0))

    @unittest.skipIf(sys.platform == "win32", "POSIX locks")
    def test_a_lock_excludes_a_second_holder_until_released(self):
        this = THIS
        path = self.temporary() / "lock"
        first = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        second = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, first)
        self.addCleanup(os.close, second)
        self.assertTrue(host.try_lock(this, first))
        self.assertFalse(host.try_lock(this, second))
        host.unlock(this, first)
        self.assertTrue(host.try_lock(this, second))
        host.unlock(this, second)


class CommandLineTests(Fixture):
    """The whole guest command, as each host would run it."""

    def setUp(self):
        self.paths = self.guest_paths(MAC)
        self.enterContext(patch.object(vm, "PATHS", self.paths))
        self.enterContext(patch.object(vm, "which", side_effect=lambda name: name))
        self.enterContext(patch.object(vm, "firmware_path", return_value=Path("/firmware.fd")))
        self.enterContext(patch.object(vm.host, "qemu_offers",
                                       return_value=["none", "cocoa", "gtk", "sdl", "coreaudio", "pipewire", "dsound", "wav"]))
        self.enterContext(patch.dict(os.environ, {"MUN_VM_ACCEL": ""}))

    def command(self, on, display="none", audio=None, ports=None):
        with patch.object(vm, "HOST", on):
            return vm.qemu_command(display, audio, ports)

    def test_the_guest_is_given_the_hosts_time_zone(self):
        with patch.object(vm.host, "local_timezone", return_value="Europe/Madrid"):
            cmd = self.command(LINUX_PC)
        self.assertIn("name=opt/mun/timezone,string=Europe/Madrid", cmd)

    def test_the_mac_keeps_its_machine(self):
        cmd = self.command(MAC, "window", "coreaudio")
        joined = " ".join(cmd)
        for part in ("virt,accel=hvf,gic-version=max", "-cpu host", "-display cocoa", "-nic none",
                     f"-qmp unix:{self.paths['qmp']},server,nowait",
                     f"socket,id=qga0,path={self.paths['qga']},server=on,wait=off", "coreaudio,id=snd0"):
            self.assertIn(part, joined)

    def test_linux_uses_kvm_where_it_can_and_emulates_elsewhere(self):
        self.assertIn("virt,accel=kvm,gic-version=max", self.command(LINUX_KVM))
        pc = self.command(LINUX_PC, "window", "pipewire")
        self.assertIn("virt,accel=tcg,gic-version=max", pc)
        self.assertIn("max,pauth-impdef=on", pc)
        self.assertEqual(pc[pc.index("-display") + 1], "gtk")
        self.assertIn(f"unix:{self.paths['qmp']},server,nowait", pc)

    def test_windows_talks_over_loopback_ports(self):
        cmd = self.command(WINDOWS, "none", "dsound", {"qmp": 40001, "qga": 40002})
        self.assertIn("tcp:127.0.0.1:40001,server=on,wait=off", cmd)
        self.assertIn("socket,id=qga0,host=127.0.0.1,port=40002,server=on,wait=off", cmd)
        self.assertIn("virt,accel=tcg,gic-version=max", cmd)
        self.assertNotIn("-daemonize", cmd)
        self.assertFalse(any("unix:" in part for part in cmd))

    def test_the_rest_of_the_machine_is_the_same_everywhere(self):
        def devices(cmd):
            return [cmd[i + 1] for i, part in enumerate(cmd) if part == "-device"]
        reference = devices(self.command(MAC, audio="none"))
        for on in (LINUX_KVM, LINUX_PC, WINDOWS_ARM):
            self.assertEqual(devices(self.command(on, audio="none", ports={"qmp": 1, "qga": 2})), reference, on.label)

    def test_an_unknown_display_or_an_absent_backend_is_refused(self):
        with self.assertRaises(vm.LabError):
            self.command(MAC, "cocoa")
        with patch.object(vm.host, "qemu_offers", return_value=["none", "wav"]), self.assertRaises(vm.LabError):
            self.command(LINUX_PC, audio="pipewire")

    def test_the_windows_channel_is_read_back_from_the_guest_directory(self):
        with patch.object(vm, "HOST", WINDOWS):
            self.assertIsNone(vm.control_address(self.paths["qmp"]))
            self.paths["control"].write_text(json.dumps({"qmp": 40001, "qga": 40002}))
            self.assertEqual(vm.control_address(self.paths["qga"]), ("127.0.0.1", 40002))
        with patch.object(vm, "HOST", MAC):
            self.assertIsNone(vm.control_address(self.paths["qmp"]), "a UNIX socket that is not there")
            self.paths["qmp"].touch()
            self.assertEqual(vm.control_address(self.paths["qmp"]), self.paths["qmp"])


class Started:
    """What host.start_detached returns, as far as the start can tell: a
    pid, and the exit status once the process has ended (None while it runs)."""

    def __init__(self, status=None, pid=4242):
        self.pid, self.status = pid, status

    def poll(self):
        return self.status


class StartTests(Fixture):
    def setUp(self):
        self.paths = self.guest_paths(WINDOWS)
        root = self.paths["state"].parent
        self.enterContext(patch.object(vm, "PATHS", self.paths))
        self.enterContext(patch.object(vm, "CARD_ROOT", root / "cards"))
        self.enterContext(patch.object(vm, "ATTACH_REGISTRY", root / "cards" / "attached.json"))
        self.simulate_locks()
        self.enterContext(patch.object(vm, "which", side_effect=lambda name: name))
        self.enterContext(patch.object(vm, "firmware_path", return_value=Path("/firmware.fd")))
        self.enterContext(patch.object(vm.host, "qemu_offers", return_value=["none", "dsound", "wav"]))
        self.enterContext(patch.object(vm.host, "qemu_version", return_value=(9, 2, 0)))
        self.enterContext(patch.dict(os.environ, {"MUN_VM_ACCEL": ""}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_windows_starts_detached_records_its_ports_and_says_it_emulates(self):
        started = []
        out = io.StringIO()
        with patch.object(vm, "HOST", WINDOWS), \
                patch.object(vm.host, "start_detached", side_effect=lambda cmd, log: started.append(cmd) or Started()), \
                patch.object(vm, "pid_alive", return_value=True), patch.object(vm.time, "sleep"), \
                patch.object(vm.time, "monotonic", side_effect=itertools.count(0, 5)), \
                contextlib.redirect_stdout(out):
            vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0, audio="none"))
        ports = json.loads(self.paths["control"].read_text())
        self.assertIn(f"tcp:127.0.0.1:{ports['qmp']},server=on,wait=off", started[0])
        self.assertNotIn("-daemonize", started[0])
        self.assertEqual(self.paths["pidfile"].read_text().strip(), "4242")
        self.assertIn("emulated (TCG)", out.getvalue())

    def test_a_qemu_that_exits_at_once_is_reported_with_its_reason(self):
        self.paths["qemu_log"].write_text("qemu-system-aarch64.exe: -device virtio-sound-pci: not found\n")
        with patch.object(vm, "HOST", WINDOWS), patch.object(vm.host, "start_detached", return_value=Started(1)), \
                self.assertRaises(vm.LabError) as failed:
            vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0, audio="none"))
        self.assertIn("virtio-sound-pci: not found", str(failed.exception))
        self.assertIn("status 1:", str(failed.exception))

    def test_a_silent_crash_is_reported_by_its_windows_status(self):
        # Windows ARM64 in CI: QEMU ended at once and wrote nothing; its
        # NTSTATUS is the only clue (0xC0000135: a library is missing).
        self.paths["qemu_log"].write_text("")
        with patch.object(vm, "HOST", WINDOWS_ARM), \
                patch.object(vm.host, "start_detached", return_value=Started(0xC0000135)), \
                self.assertRaises(vm.LabError) as failed:
            vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0, audio="none"))
        self.assertIn("status 3221225781 (0xC0000135): no output", str(failed.exception))

    def test_card_images_are_locked_where_qemu_can_and_not_on_windows(self):
        # QEMU's Windows file driver refuses locking=on (the first CI run on
        # Windows); elsewhere the lock is the registry's second line.
        for on, locked in ((MAC, True), (LINUX_ARM_NO_KVM, True), (WINDOWS, False), (WINDOWS_ARM, False)):
            with patch.object(vm, "HOST", on):
                backend = vm.card_backend(Path("/cards/demo.img"))
            self.assertEqual(backend.get("locking"), "on" if locked else None, on.label)
            self.assertEqual((backend["driver"], backend["filename"]), ("file", str(Path("/cards/demo.img"))))

    def test_a_qemu_without_the_sound_device_is_refused_unless_sound_is_off(self):
        with patch.object(vm, "HOST", LINUX_PC), patch.object(vm.host, "qemu_version", return_value=(7, 2, 0)), \
                patch.object(vm, "run") as run:
            with self.assertRaises(vm.LabError):
                vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0, audio="none"))
            run.assert_not_called()
            with patch.object(vm, "read_pid", side_effect=[None, 99]):
                vm.cmd_start(argparse.Namespace(display="none", print_command=False, wait=0, audio="off"))
            self.assertIn("-daemonize", run.call_args.args[0])


class BuildHostTests(Fixture):
    def test_the_builders_seed_uses_each_hosts_iso_tool(self):
        root = self.temporary()
        with patch.object(vm, "run") as run, patch.object(vm, "which", side_effect=lambda name: name):
            with patch.object(vm, "HOST", MAC):
                vm.make_seed_iso(root, root / "seed.iso")
                self.assertEqual(run.call_args.args[0][:2], ["hdiutil", "makehybrid"])
            with patch.object(vm, "HOST", LINUX_KVM), \
                    patch.object(vm.shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None):
                vm.make_seed_iso(root, root / "seed.iso")
                self.assertEqual(run.call_args.args[0][:3], ["xorriso", "-as", "mkisofs"])
                self.assertIn("cidata", run.call_args.args[0])
            with patch.object(vm, "HOST", LINUX_KVM), patch.object(vm.shutil, "which", return_value=None), \
                    self.assertRaises(vm.LabError):
                vm.make_seed_iso(root, root / "seed.iso")
            with patch.object(vm, "HOST", WINDOWS), self.assertRaises(vm.LabError):
                vm.make_seed_iso(root, root / "seed.iso")

    def test_building_on_windows_is_refused_with_the_alternatives(self):
        with patch.object(vm, "HOST", WINDOWS), self.assertRaises(vm.LabError) as refused:
            mundev.cmd_build(argparse.Namespace(profile="qemu-dev", name="one", allow_dirty=True, recipe=None,
                                                keep_builder=False))
        self.assertIn("WSL 2", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
