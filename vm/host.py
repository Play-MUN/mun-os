"""The machine the laboratory runs on: what differs between macOS, Linux and
Windows for vm/munvm.py and vm/mundev.py.

The guest is the same everywhere: an ARM64 QEMU `virt` machine booting a MUN
OS development image. What the host changes is how QEMU runs it and how
these tools reach it:

- the accelerator: HVF on an Apple Silicon Mac that offers it, KVM on an
  ARM64 Linux with a usable /dev/kvm, TCG everywhere else (a virtual machine
  usually offers neither). TCG emulates the ARM64 processor (an
  x86_64 PC, Windows, an ARM64 Linux without KVM): the same guest, slower.
- the UEFI firmware QEMU boots, which each packaging puts somewhere else;
- the window and sound backends QEMU was built with;
- starting QEMU in the background (`-daemonize` does not exist on Windows);
- the channel to QMP and qemu-ga: UNIX sockets, or loopback TCP on Windows,
  where Python has no AF_UNIX (a local port has no file permissions: any
  program of the machine could connect while the guest runs);
- whether a process is alive (on Windows `os.kill(pid, 0)` would terminate
  it) and advisory file locks.

`Host` is plain data and every choice is a function of it, so tests can build
any host's command line on any machine. No third-party packages.
"""

import contextlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

# A control address: a UNIX socket path, or a loopback (host, port).
Address = Union[Path, Tuple[str, int]]

LOOPBACK = "127.0.0.1"


class HostError(Exception):
    """The host lacks something the laboratory needs; the message says what."""


@dataclass(frozen=True)
class Host:
    system: str             # "macos", "linux" or "windows"
    machine: str            # "arm64" or "x86_64"
    # The host's hypervisor is usable: macOS reports kern.hv_support, Linux
    # has a /dev/kvm this user can open. Windows' is not used (no WHPX for an
    # ARM64 guest in QEMU).
    hypervisor: bool = False

    @property
    def label(self) -> str:
        names = {"macos": "macOS", "linux": "Linux", "windows": "Windows"}
        return f"{names.get(self.system, self.system)} {'ARM64' if self.machine == 'arm64' else 'x86_64'}"

    @property
    def daemonizes(self) -> bool:
        return self.system != "windows"

    @property
    def unix_sockets(self) -> bool:
        return self.system != "windows"

    # The QEMU window backends to try, in order.
    @property
    def window_displays(self) -> Tuple[str, ...]:
        return ("cocoa",) if self.system == "macos" else ("gtk", "sdl")

    # Audible sound backends to try, in order, when none is asked for.
    @property
    def audio_backends(self) -> Tuple[str, ...]:
        if self.system == "macos":
            return ("coreaudio",)
        if self.system == "windows":
            return ("dsound", "sdl")
        return ("pipewire", "pa", "alsa", "sdl")


def normalise_machine(name: str) -> str:
    name = name.lower()
    if name in ("arm64", "aarch64", "armv8", "armv8l"):
        return "arm64"
    if name in ("x86_64", "amd64", "x64"):
        return "x86_64"
    return name


def detect() -> Host:
    systems = {"darwin": "macos", "linux": "linux", "win32": "windows"}
    system = systems.get(sys.platform)
    if system is None:
        raise HostError(f"the laboratory runs on macOS, Linux or Windows, not {sys.platform}")
    if system == "linux":
        hypervisor = os.access("/dev/kvm", os.R_OK | os.W_OK)
    elif system == "macos":
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.hv_support"], capture_output=True, text=True,
                                check=False)
        hypervisor = result.stdout.strip() == "1"
    else:
        hypervisor = False
    return Host(system, normalise_machine(platform.machine()), hypervisor)


# ------------------------------------------------------------------ the machine

ACCELERATORS = ("hvf", "kvm", "tcg")


def accelerator(host: Host, requested: Optional[str] = None) -> str:
    """The accelerator for an ARM64 guest on `host`; `requested` (MUN_VM_ACCEL)
    may ask for TCG anywhere, or for the host's own accelerator."""
    native = None
    if host.machine == "arm64" and host.hypervisor:
        native = {"macos": "hvf", "linux": "kvm"}.get(host.system)
    if requested:
        if requested not in ACCELERATORS:
            raise HostError(f"unknown accelerator {requested!r}; one of {', '.join(ACCELERATORS)}")
        if requested != "tcg" and requested != native:
            reason = {"linux": " (no usable /dev/kvm)", "macos": " (the Hypervisor framework is not available)"}
            raise HostError(f"{requested} cannot run an ARM64 guest on {host.label}"
                            + (reason.get(host.system, "") if host.machine == "arm64" else ""))
        return requested
    return native or "tcg"


def machine_arguments(accel: str) -> List[str]:
    """`-machine` and `-cpu` for the guest. Under TCG the CPU is QEMU's own
    `max` with the implementation-defined pointer authentication, which QEMU
    emulates much faster than the architected algorithm."""
    cpu = "host" if accel in ("hvf", "kvm") else "max,pauth-impdef=on"
    return ["-machine", f"virt,accel={accel},gic-version=max", "-cpu", cpu]


# ------------------------------------------------------------------ programs

def find_program(host: Host, name: str, environ: Optional[Dict[str, str]] = None) -> str:
    """A QEMU program (or any tool) on PATH; on Windows also QEMU's default
    installation directory, which its installer does not add to PATH."""
    environ = os.environ if environ is None else environ
    found = shutil.which(name)
    if found:
        return found
    if host.system == "windows":
        directories = [Path(environ["MUN_QEMU_DIR"])] if environ.get("MUN_QEMU_DIR") else []
        directories += [Path(environ[key]) / "qemu" for key in ("ProgramFiles", "ProgramW6432") if environ.get(key)]
        for directory in directories:
            candidate = directory / f"{name}.exe"
            if candidate.is_file():
                return str(candidate)
    raise HostError(f"required program not found: {name}")


def qemu_version(qemu: str) -> Tuple[int, int, int]:
    output = subprocess.run([qemu, "--version"], capture_output=True, text=True, check=False).stdout
    match = re.search(r"version (\d+)\.(\d+)(?:\.(\d+))?", output)
    if not match:
        raise HostError(f"cannot tell the version of {qemu}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


_help_cache: Dict[Tuple[str, str], List[str]] = {}


def qemu_offers(qemu: str, option: str) -> List[str]:
    """The backends `qemu -<option> help` lists (display or audiodev), as this
    QEMU was built: distributions split them into optional packages."""
    key = (qemu, option)
    if key not in _help_cache:
        output = subprocess.run([qemu, f"-{option}", "help"], capture_output=True, text=True, check=False).stdout
        names = []
        for line in output.splitlines():
            word = line.strip().split(" ")[0]
            if word and not word.endswith(":") and re.fullmatch(r"[a-z][a-z0-9-]*", word):
                names.append(word)
        _help_cache[key] = names
    return _help_cache[key]


def window_display(host: Host, offered: Iterable[str]) -> str:
    offered = list(offered)
    for name in host.window_displays:
        if name in offered:
            return name
    hint = {"linux": " (on Debian and Ubuntu, the package qemu-system-gui)"}.get(host.system, "")
    raise HostError(f"this QEMU has no window backend for {host.label}: it offers {', '.join(offered) or 'none'}; "
                    f"one of {', '.join(host.window_displays)} is needed{hint}")


def default_audio(host: Host, offered: Iterable[str]) -> str:
    """The host's audible backend this QEMU has, else `none` (a silent device)."""
    offered = list(offered)
    for name in host.audio_backends:
        if name in offered:
            return name
    return "none"


# ------------------------------------------------------------------ firmware

# Where packagings keep the ARM64 UEFI code as a 64 MiB flash image, after
# QEMU's own copy and its firmware descriptors.
KNOWN_FIRMWARE = (
    Path("/usr/share/AAVMF/AAVMF_CODE.fd"),                  # Debian, Ubuntu
    Path("/usr/share/edk2/aarch64/QEMU_EFI-pflash.raw"),     # Fedora
    Path("/usr/share/edk2/aarch64/QEMU_CODE.fd"),            # Arch Linux
)


def _descriptor_firmware(directory: Path) -> Optional[Path]:
    """The first ARM64 UEFI flash image a QEMU firmware descriptor names
    (docs/interop/firmware.json in QEMU), without Secure Boot."""
    if not directory.is_dir():
        return None
    for descriptor in sorted(directory.glob("*.json")):
        try:
            data = json.loads(descriptor.read_text())
        except (OSError, ValueError):
            continue
        mapping = data.get("mapping", {})
        features = data.get("features", [])
        targets = data.get("targets", [])
        if ("uefi" not in data.get("interface-types", []) or mapping.get("device") != "flash"
                or mapping.get("mode", "split") != "split" or "secure-boot" in features
                or "enrolled-keys" in features):
            continue
        if not any(t.get("architecture") == "aarch64" and any(str(m).startswith("virt") for m in t.get("machines", []))
                   for t in targets):
            continue
        executable = Path(mapping.get("executable", {}).get("filename", ""))
        if executable.is_file():
            return executable
    return None


def firmware(host: Host, qemu: str, environ: Optional[Dict[str, str]] = None) -> Path:
    environ = os.environ if environ is None else environ
    if environ.get("MUN_VM_FIRMWARE"):
        chosen = Path(environ["MUN_VM_FIRMWARE"])
        if not chosen.is_file():
            raise HostError(f"MUN_VM_FIRMWARE names no file: {chosen}")
        return chosen
    binary = Path(qemu).resolve()
    # A bin/ next to share/qemu (Homebrew, Linux, MSYS2), or the QEMU for
    # Windows installer's single directory with share/ inside it.
    prefix = binary.parent if host.system == "windows" and not binary.parent.name.lower() == "bin" else binary.parents[1]
    looked = [prefix / "share" / "qemu" / "edk2-aarch64-code.fd", prefix / "share" / "edk2-aarch64-code.fd"]
    for candidate in looked:
        if candidate.is_file():
            return candidate
    descriptor_dirs = [prefix / "share" / "qemu" / "firmware", Path("/usr/share/qemu/firmware"),
                       Path("/etc/qemu/firmware")]
    for directory in descriptor_dirs:
        found = _descriptor_firmware(directory)
        if found:
            return found
    for candidate in KNOWN_FIRMWARE:
        if candidate.is_file():
            return candidate
    hint = {"linux": "; install it with QEMU (Debian and Ubuntu: qemu-efi-aarch64; Fedora: edk2-aarch64)"}.get(host.system, "")
    raise HostError("no ARM64 UEFI firmware found next to QEMU or in its firmware descriptors" + hint
                    + "; MUN_VM_FIRMWARE may name the file")


# ------------------------------------------------------------------ time zone

ZONE_NAME = re.compile(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}")


def local_timezone(host: Host, environ: Optional[Dict[str, str]] = None,
                   localtime: Path = Path("/etc/localtime"), gmtoff: Optional[int] = None) -> str:
    """The host computer's time zone as an IANA name, for the guest's clock
    and time-of-day light: MUN_VM_TIMEZONE if set; on macOS and Linux the
    zone /etc/localtime links to (or /etc/timezone); otherwise, and on
    Windows, the current offset from UTC as an Etc/GMT zone (whole hours;
    it does not follow a later change of summer time), or UTC."""
    environ = os.environ if environ is None else environ
    requested = environ.get("MUN_VM_TIMEZONE", "")
    if requested:
        if not ZONE_NAME.fullmatch(requested):
            raise HostError(f"MUN_VM_TIMEZONE is not a time zone name: {requested!r}")
        return requested
    if host.system in ("macos", "linux"):
        try:
            target = os.readlink(localtime)
        except OSError:
            target = ""
        if "zoneinfo/" in target:
            zone = target.split("zoneinfo/", 1)[1]
            if ZONE_NAME.fullmatch(zone):
                return zone
        with contextlib.suppress(OSError):
            zone = Path("/etc/timezone").read_text().strip()
            if ZONE_NAME.fullmatch(zone):
                return zone
    offset = time.localtime().tm_gmtoff if gmtoff is None else gmtoff
    if offset and offset % 3600 == 0 and abs(offset) <= 14 * 3600:
        hours = offset // 3600
        return f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}"   # the Etc zones' sign is inverted
    return "UTC"


# --------------------------------------------------------------- control channel

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


def qmp_option(address: Address) -> str:
    if isinstance(address, Path):
        return f"unix:{address},server,nowait"
    return f"tcp:{address[0]}:{address[1]},server=on,wait=off"


def chardev_socket(chardev_id: str, address: Address) -> str:
    if isinstance(address, Path):
        return f"socket,id={chardev_id},path={address},server=on,wait=off"
    return f"socket,id={chardev_id},host={address[0]},port={address[1]},server=on,wait=off"


def connect(address: Address, timeout: float) -> socket.socket:
    if isinstance(address, Path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target: Union[str, Tuple[str, int]] = str(address)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = address
    sock.settimeout(timeout)
    try:
        sock.connect(target)
    except OSError:
        sock.close()
        raise
    return sock


# ------------------------------------------------------------------ processes

def process_alive(host: Host, pid: int) -> bool:
    if pid <= 0:
        return False
    if host.system == "windows":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5             # ERROR_ACCESS_DENIED: it exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259                        # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def start_detached(command: List[str], log: Path) -> int:
    """Start QEMU in the background where it cannot daemonize itself
    (Windows): detached from this console, its output into `log`. Returns
    its pid; QEMU writes its own pid file too."""
    flags = 0x00000008 | 0x00000200                     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with log.open("ab") as sink:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                                   creationflags=flags, close_fds=True)
    return process.pid


# ------------------------------------------------------------------ file locks

def try_lock(host: Host, fd: int) -> bool:
    """Take an exclusive advisory lock on the open file `fd` without waiting."""
    if host.system == "windows":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def unlock(host: Host, fd: int) -> None:
    if host.system == "windows":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)
