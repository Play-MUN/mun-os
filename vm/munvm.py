#!/usr/bin/env python3
"""MUN virtual console laboratory: the QEMU lifecycle of MUN OS image guests (`./mun vm`).

Host-side automation only. It owns a guest's lifecycle (start/stop, commands
through qemu-ga, virtual Game Card attach/detach through QMP, keys, pointer,
screenshots) and nothing else. It is not a console service and must not become
one; the console runtime lives in the image. Guests and builds are made by
vm/mundev.py (`./mun dev`).

Everything generated lives under `.local/mun/` and `.local/gamecards/`, which
Git ignores.

Runs on macOS, Linux and Windows; what differs between them (accelerator,
firmware, window and sound, background start, the control channel, process
and lock primitives) is in vm/host.py. Requires Python 3.9+ and QEMU 8.2 or
later with its ARM64 UEFI firmware; building also needs what vm/mundev.py
lists. No third-party Python packages.
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CARD_ROOT = REPO_ROOT / ".local" / "gamecards"
# Where every instance records which card images it holds; the single-writer
# rule is enforced here before QEMU's own image lock gets a say (docs/saves.md).
ATTACH_REGISTRY = CARD_ROOT / "attached.json"

# Every instance is an image guest: a MUN OS development build booted by
# vm/mundev.py under .local/mun/guests/<name>. It has no network interface at
# all; the host drives it through QMP and reaches it only through qemu-ga,
# QEMU's guest-side control daemon, on a virtio-serial port, which the image's
# development profile installs and a release composition would not.
GUESTS_ROOT = REPO_ROOT / ".local" / "mun" / "guests"
DEFAULT_INSTANCE = "dev"   # the default guest of `./mun dev run`
INSTANCE = DEFAULT_INSTANCE

# Guest sizing; development settings, not console specifications.
VCPUS = int(os.environ.get("MUN_VM_VCPUS", "4"))
MEMORY = os.environ.get("MUN_VM_MEMORY", "8G")
# The display's preferred mode: fbcon and the shell's Automatic resolution use
# it, and the shell's Resolution setting can ask virtio-gpu for another
# (services/mun-shell/README.md, "Resolution").
DISPLAY_WIDTH = int(os.environ.get("MUN_VM_DISPLAY_WIDTH", "1920"))
DISPLAY_HEIGHT = int(os.environ.get("MUN_VM_DISPLAY_HEIGHT", "1080"))
GUEST_HOSTNAME = f"mun-{INSTANCE}"

_GUEST_ROOT = GUESTS_ROOT / INSTANCE
PATHS = {
    "system_disk": _GUEST_ROOT / "system.qcow2",
    "efivars": _GUEST_ROOT / "efivars.fd",
    "pidfile": _GUEST_ROOT / "qemu.pid",
    "qmp": _GUEST_ROOT / "qmp.sock",
    "qga": _GUEST_ROOT / "qga.sock",
    "qga_lock": _GUEST_ROOT / "qga.lock",
    "guest": _GUEST_ROOT / "guest.json",
    "console_log": _GUEST_ROOT / "console.log",
    "state": _GUEST_ROOT / "state.json",
    "screens": _GUEST_ROOT / "screens",
    "audio": _GUEST_ROOT / "audio.wav",
    "lifecycle_lock": _GUEST_ROOT / "lifecycle.lock",
    "control": _GUEST_ROOT / "control.json",
    "qemu_log": _GUEST_ROOT / "qemu.log",
}

# The machine these tools run on; tests replace it to build another host's
# command line.
HOST = host.detect()

def socket_path(root: Path, name: str) -> Path:
    """A UNIX socket path QEMU can bind. macOS limits sun_path to 104 bytes
    (Linux to 108); a checkout in a deep directory would exceed it, so a long
    path moves to a private per-user directory named after the instance
    directory. On Windows the path only names the loopback port recorded
    next to it (see control_address)."""
    path = root / name
    if len(str(path).encode()) <= 100 or not HOST.unix_sockets:
        return path
    short = Path(tempfile.gettempdir()) / f"mun-{os.getuid()}" / hashlib.sha1(str(root).encode()).hexdigest()[:12]
    short.mkdir(mode=0o700, parents=True, exist_ok=True)
    return short / name


def instance_paths(root: Path) -> Dict[str, Path]:
    return {
        "system_disk": root / "system.qcow2",
        "efivars": root / "efivars.fd",
        "pidfile": root / "qemu.pid",
        "qmp": socket_path(root, "qmp.sock"),
        "qga": socket_path(root, "qga.sock"),
        "qga_lock": root / "qga.lock",
        "guest": root / "guest.json",
        "console_log": root / "console.log",
        "state": root / "state.json",
        "screens": root / "screens",
        "audio": root / "audio.wav",
        "lifecycle_lock": root / "lifecycle.lock",
        "control": root / "control.json",
        "qemu_log": root / "qemu.log",
    }


IMAGE_GUEST_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,15}")


def select_instance(name: str) -> None:
    """Point every path and name at one image guest, which vm/mundev.py
    created under .local/mun/guests/<name>. Called once from main()."""
    global INSTANCE, GUEST_HOSTNAME
    root = GUESTS_ROOT / name
    if not IMAGE_GUEST_NAME.fullmatch(name) or not (root / "guest.json").is_file():
        raise LabError(f"unknown guest {name!r}: a guest is created by `./mun dev run --guest NAME`")
    INSTANCE = name
    PATHS.update(instance_paths(root))            # in place: tests and callers hold the same dict
    GUEST_HOSTNAME = f"mun-{name}"


# PCIe root ports reserved at boot so virtio-blk cards can be hot-plugged later.
# QEMU's `virt` machine has no hotplug-capable slots unless they are declared.
CARD_SLOTS = ("card-slot-1", "card-slot-2")


class LabError(Exception):
    """A recoverable, user-facing failure. Printed without a traceback."""


# --------------------------------------------------------------------------- utils

def run(cmd: List[str], check: bool = True, capture: bool = False, **kw: Any) -> subprocess.CompletedProcess:
    if capture:
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.STDOUT)
        kw.setdefault("text", True)
    return subprocess.run(cmd, check=check, **kw)


def which(name: str) -> str:
    try:
        return host.find_program(HOST, name)
    except host.HostError as exc:
        raise LabError(str(exc)) from exc


def firmware_path() -> Path:
    """The ARM64 UEFI firmware of the QEMU installation in use (vm/host.py)."""
    try:
        return host.firmware(HOST, which("qemu-system-aarch64"))
    except host.HostError as exc:
        raise LabError(str(exc)) from exc


def guest_timezone() -> str:
    try:
        return host.local_timezone(HOST)
    except host.HostError as exc:
        raise LabError(str(exc)) from exc


def accelerator() -> str:
    """HVF, KVM or TCG for this host; MUN_VM_ACCEL=tcg forces emulation."""
    try:
        return host.accelerator(HOST, os.environ.get("MUN_VM_ACCEL") or None)
    except host.HostError as exc:
        raise LabError(str(exc)) from exc


def read_pid() -> Optional[int]:
    try:
        pid = int(PATHS["pidfile"].read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


# ------------------------------------------------------------------- lab locks
#
# Card attach and detach are read-modify-write transactions over this
# instance's state.json and the shared attach registry, around QMP commands
# that can take seconds (a PCIe hot-unplug waits for the guest). They run from
# separate processes (terminal commands, the `card-watch` process) and from a
# thread inside `open`, so a thread lock is not enough: an flock(2) on a lock
# file serialises every transaction of one instance across processes, and a
# second one guards the registry that all instances share. Lock order is always
# lifecycle, then registry. A thread may re-enter a lock it already holds, so
# a transaction can call helpers that take the same lock.

LOCK_TIMEOUT = 120.0   # longer than any transaction: detach waits up to 30 s for the guest
_held_locks = threading.local()


@contextlib.contextmanager
def lab_lock(path: Path, what: str, timeout: Optional[float] = None) -> Any:
    held = getattr(_held_locks, "paths", None)
    if held is None:
        held = _held_locks.paths = {}
    key = str(path)
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        started = time.monotonic()
        limit = LOCK_TIMEOUT if timeout is None else timeout
        announced = False
        while True:
            if host.try_lock(HOST, fd):
                break
            else:
                waited = time.monotonic() - started
                if waited >= limit:
                    raise LabError(f"{what} is busy: another lab command still holds {path.name} after {limit:.0f}s")
                if waited >= 1.0 and not announced:
                    print(f"waiting for another lab command to finish with {what} ...", file=sys.stderr, flush=True)
                    announced = True
                time.sleep(0.1)
        held[key] = 1
        try:
            yield
        finally:
            del held[key]
            host.unlock(HOST, fd)
    finally:
        os.close(fd)


def lifecycle_lock(timeout: Optional[float] = None) -> Any:
    """Exclusive over this instance's card attachments, across processes."""
    return lab_lock(PATHS["lifecycle_lock"], f"instance {INSTANCE}'s cards", timeout)


def registry_lock(timeout: Optional[float] = None) -> Any:
    """Exclusive over the attach registry that every instance shares."""
    return lab_lock(ATTACH_REGISTRY.with_name(ATTACH_REGISTRY.name + ".lock"), "the attach registry", timeout)


def write_json_atomically(path: Path, data: Dict[str, Any]) -> None:
    """Replace `path` in one rename from a temporary unique to this writer:
    a shared `.tmp` name would let two writers interleave into one file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def load_state() -> Dict[str, Any]:
    try:
        return json.loads(PATHS["state"].read_text())
    except (OSError, ValueError):
        return {"cards": {}}


def save_state(state: Dict[str, Any]) -> None:
    write_json_atomically(PATHS["state"], state)


def pid_alive(pid: int) -> bool:
    return host.process_alive(HOST, pid)


def load_registry() -> Dict[str, Any]:
    try:
        return json.loads(ATTACH_REGISTRY.read_text())
    except (OSError, ValueError):
        return {}


def save_registry(registry: Dict[str, Any]) -> None:
    write_json_atomically(ATTACH_REGISTRY, registry)


def registry_claim(name: str, attachment: Optional[str] = None) -> None:
    """Record that this instance's guest holds `name`; refuse if a live guest elsewhere does.

    A card image is a writable medium once saves exist, so two guests must
    never hold it at once. Entries whose QEMU process is gone are stale and
    are dropped here. The whole read-modify-write runs under the registry lock.
    """
    with registry_lock():
        registry = load_registry()
        holder = registry.get(name)
        if holder and holder.get("instance") != INSTANCE:
            if pid_alive(int(holder.get("pid", 0) or 0)):
                if holder.get("purpose") == "convert":
                    # mun-card convert holds its source and destination here
                    # for the whole conversion (docs/game-cards.md).
                    raise LabError(f"{name} is reserved by a card conversion (pid {holder['pid']}); "
                                   "attach it when the conversion has finished")
                raise LabError(f"{name} is attached to instance {holder['instance']} (QEMU pid {holder['pid']}); "
                               f"detach it there first: a card is never a writable medium in two guests")
            del registry[name]
        registry[name] = {"instance": INSTANCE, "pid": read_pid(), "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                          "attachment": attachment}
        save_registry(registry)


def registry_release(name: str, attachment: Optional[str] = None) -> None:
    """Drop this instance's claim on `name`; with `attachment`, only that attachment's
    claim, so cleanup for an old insertion never frees a newer one."""
    with registry_lock():
        registry = load_registry()
        holder = registry.get(name, {})
        if holder.get("instance") != INSTANCE:
            return
        if attachment is not None and holder.get("attachment") not in (None, attachment):
            return
        del registry[name]
        save_registry(registry)


def clear_cards() -> None:
    """A fresh QEMU process starts with empty slots; card image files survive."""
    with lifecycle_lock():
        state = load_state()
        state["cards"] = {}
        if PATHS["state"].exists():
            save_state(state)
        with registry_lock():
            registry = load_registry()
            mine = [name for name, holder in registry.items() if holder.get("instance") == INSTANCE]
            if mine:
                for name in mine:
                    del registry[name]
                save_registry(registry)


# ----------------------------------------------------------------------------- QMP

def control_address(path: Path) -> Optional["host.Address"]:
    """Where QEMU listens for the channel `path` names (qmp.sock or
    qga.sock): the UNIX socket itself, or on Windows the loopback port it was
    started with, recorded next to it in control.json. None while nothing
    listens there."""
    if HOST.unix_sockets:
        return path if path.exists() else None
    try:
        ports = json.loads((path.parent / "control.json").read_text())
        return (host.LOOPBACK, int(ports[path.stem]))
    except (OSError, ValueError, KeyError):
        return None


class Qmp:
    """Minimal QMP client over the guest's control socket (JSON lines, no deps)."""

    def __init__(self, path: Path, timeout: float = 30.0):
        address = control_address(path)
        if address is None:
            raise LabError("QMP socket missing; is the VM running? (./mun vm status)")
        self.sock = host.connect(address, timeout)
        self.buf = b""
        self.events: List[Dict[str, Any]] = []
        self._read()  # greeting
        self.execute("qmp_capabilities")

    def _read(self) -> Dict[str, Any]:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise LabError("QMP connection closed by QEMU")
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line)

    def execute(self, command: str, **arguments: Any) -> Any:
        message: Dict[str, Any] = {"execute": command}
        if arguments:
            message["arguments"] = arguments
        self.sock.sendall(json.dumps(message).encode() + b"\n")
        while True:
            reply = self._read()
            if "event" in reply:
                self.events.append(reply)
                continue
            if "error" in reply:
                raise LabError(f"QMP {command}: {reply['error'].get('desc')}")
            return reply.get("return")

    def wait_event(self, name: str, timeout: float,
                   data_match: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Keep unrelated events queued and accept either event/response ordering."""
        def matches(event: Dict[str, Any]) -> bool:
            return event.get("event") == name and all(
                event.get("data", {}).get(key) == value
                for key, value in (data_match or {}).items()
            )

        for index, event in enumerate(self.events):
            if matches(event):
                return self.events.pop(index)
        deadline = time.monotonic() + timeout
        previous_timeout = self.sock.gettimeout()
        try:
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    reply = self._read()
                except socket.timeout:
                    return None
                if matches(reply):
                    return reply
                if "event" in reply:
                    self.events.append(reply)
        finally:
            self.sock.settimeout(previous_timeout)
        return None

    def close(self) -> None:
        self.sock.close()


# ----------------------------------------------------------------------- downloads
#
# Used by vm/mundev.py for the builder's pinned base image.

def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path, expected_digest: Optional[str] = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    if expected_digest is not None:
        if sha512_file(partial) != expected_digest:
            partial.unlink()
            raise LabError("downloaded image does not match the pinned SHA-512")
        # Publish a verified image without replacing a backing file, even if
        # another process created the destination while the download was running.
        os.link(partial, destination)
        partial.unlink()
    else:
        partial.replace(destination)


def expected_sum(sums_file: Path, name: str) -> str:
    for line in sums_file.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0]
    raise LabError(f"{name} not listed in {sums_file}")


# ---------------------------------------------------------------------- seed

def make_seed_iso(seed_dir: Path, iso: Path) -> None:
    """NoCloud seed ISO (volume label cidata, long names) from a directory,
    for the builder's first boot (vm/mundev.py): hdiutil on macOS; on Linux
    xorriso, genisoimage or mkisofs, whichever is installed."""
    if iso.exists():
        iso.unlink()
    if HOST.system == "macos":
        run([which("hdiutil"), "makehybrid", "-quiet", "-iso", "-joliet", "-default-volume-name", "cidata",
             "-o", str(iso), str(seed_dir)])
        return
    if HOST.system == "linux":
        if shutil.which("xorriso"):
            run([which("xorriso"), "-as", "mkisofs", "-quiet", "-V", "cidata", "-J", "-r", "-o", str(iso),
                 str(seed_dir)])
            return
        for tool in ("genisoimage", "mkisofs"):
            if shutil.which(tool):
                run([which(tool), "-quiet", "-V", "cidata", "-J", "-r", "-o", str(iso), str(seed_dir)])
                return
        raise LabError("building needs an ISO tool for the builder's seed: install xorriso "
                       "(or genisoimage)")
    raise LabError(f"building is not supported on {HOST.label}; build inside WSL 2 (Linux) instead")


# ---------------------------------------------------------------------------- start

# Guest sound device. virtio-sound is the paravirtual device the
# Debian kernel ships as virtio_snd (QEMU 8.2 or later); the backend decides
# where the samples go: an audible one of the host's (coreaudio on macOS,
# pipewire, pa or alsa on Linux, dsound on Windows, sdl where QEMU has it),
# none (device present, silent) or wav (a capture file next to the guest
# state, for evidence). The console promises a sound device to GL-profile
# games (docs/runtime.md), and some engines crash without one, so a guest has
# one by default (`none`); `off` removes it on purpose.
AUDIBLE_BACKENDS = ("coreaudio", "pipewire", "pa", "alsa", "dsound", "sdl")
AUDIO_BACKENDS = ("none", "wav", "off") + AUDIBLE_BACKENDS
SOUND_DEVICE_QEMU = (8, 2)


def audio_arguments(backend: Optional[str]) -> List[str]:
    if backend is None or backend == "off":
        return []
    if backend not in AUDIO_BACKENDS:
        raise LabError(f"unknown audio backend {backend!r}; one of {', '.join(AUDIO_BACKENDS)}")
    if backend in AUDIBLE_BACKENDS:
        offered = host.qemu_offers(which("qemu-system-aarch64"), "audiodev")
        if backend not in offered:
            raise LabError(f"this QEMU has no {backend} sound backend; it offers {', '.join(offered)}")
    audiodev = f"{backend},id=snd0"
    if backend == "wav":
        audiodev += f",path={PATHS['audio']}"
    # streams=1: playback only. The lab has no use for guest capture and the
    # wav/none backends have no input side to offer anyway.
    return ["-audiodev", audiodev, "-device", "virtio-sound-pci,audiodev=snd0,streams=1"]


def finalize_wav(path: Path) -> bool:
    """Make the RIFF/data sizes match the file. QEMU's wav backend writes them
    when a stream closes, not when QEMU exits after a guest power-off, so the
    header describes an earlier moment (or nothing) and readers stop early.
    Returns True if the header was patched."""
    try:
        with open(path, "r+b") as f:
            header = f.read(44)
            if len(header) < 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE" or header[36:40] != b"data":
                return False
            size = path.stat().st_size
            if int.from_bytes(header[40:44], "little") == size - 44:
                return False
            f.seek(4); f.write((size - 8).to_bytes(4, "little"))
            f.seek(40); f.write((size - 44).to_bytes(4, "little"))
            return True
    except OSError:
        return False


def finalize_capture() -> None:
    """Repair the wav header once no QEMU of this instance is alive. Called
    from every path that observes the guest gone: stop after a power-down,
    stop on an already stopped guest, and the foreground (windowed) start
    returning. Never while the process still exists: QEMU is still writing."""
    if read_pid() is not None:
        return
    if PATHS["audio"].exists() and finalize_wav(PATHS["audio"]):
        print(f"audio capture finalised: {PATHS['audio']}")


def default_window_audio() -> str:
    """The audible sound backend a windowed console uses on this host."""
    return host.default_audio(HOST, host.qemu_offers(which("qemu-system-aarch64"), "audiodev"))


def control_ports() -> Dict[str, int]:
    """Loopback ports for QMP and qemu-ga where there are no UNIX sockets:
    those recorded for this guest if any, otherwise new ones."""
    try:
        ports = json.loads(PATHS["control"].read_text())
        return {"qmp": int(ports["qmp"]), "qga": int(ports["qga"])}
    except (OSError, ValueError, KeyError):
        return {"qmp": host.free_port(), "qga": host.free_port()}


def qemu_command(display: str, audio: Optional[str] = None, ports: Optional[Dict[str, int]] = None) -> List[str]:
    """The guest's machine: display, input, card slots and sound device, with
    no network interface (`-nic none`: the guest sees only `lo`) and qemu-ga's
    virtio-serial port as the host's only way in besides QMP. `display` is
    `none` or `window` (the host's window backend); `ports` are the loopback
    ports of a host without UNIX sockets."""
    qemu = which("qemu-system-aarch64")
    if HOST.unix_sockets:
        qmp: "host.Address" = PATHS["qmp"]
        qga: "host.Address" = PATHS["qga"]
    else:
        ports = ports or control_ports()
        qmp, qga = (host.LOOPBACK, ports["qmp"]), (host.LOOPBACK, ports["qga"])
    if display == "window":
        try:
            display = host.window_display(HOST, host.qemu_offers(qemu, "display"))
        except host.HostError as exc:
            raise LabError(str(exc)) from exc
    elif display != "none":
        raise LabError(f"unknown display {display!r}; none or window")
    cmd = [
        qemu,
        "-name", GUEST_HOSTNAME,
        *host.machine_arguments(accelerator()),
        "-smp", str(VCPUS),
        "-m", MEMORY,
        "-rtc", "base=utc",
        # The host's time zone for the development image's clock and light
        # (its unit mun-lab-timezone reads it; os/mkosi, profile qemu-dev).
        "-fw_cfg", f"name=opt/mun/timezone,string={guest_timezone()}",
        "-drive", f"if=pflash,format=raw,readonly=on,file={firmware_path()}",
        "-drive", f"if=pflash,format=raw,file={PATHS['efivars']}",
        "-blockdev", json.dumps({"driver": "qcow2", "node-name": "sys",
                                 "file": {"driver": "file", "filename": str(PATHS["system_disk"])}}),
        # Not NPT-: the card service only ever considers NPT- serials.
        "-device", "virtio-blk-pci,drive=sys,bootindex=0,serial=MUN-SYSTEM",
        # Display and input path under test. virtio-gpu gives the guest a DRM/KMS
        # device; the USB HID pair is what a controller-less laboratory can exercise.
        "-device", f"virtio-gpu-pci,xres={DISPLAY_WIDTH},yres={DISPLAY_HEIGHT}",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-kbd,bus=xhci.0",
        "-device", "usb-tablet,bus=xhci.0",
        "-display", display,
        "-nic", "none",
        "-device", "virtio-serial-pci,id=vser0",
        "-chardev", host.chardev_socket("qga0", qga),
        "-device", "virtserialport,bus=vser0.0,chardev=qga0,name=org.qemu.guest_agent.0",
        "-serial", f"file:{PATHS['console_log']}",
        "-monitor", "none",
        "-qmp", host.qmp_option(qmp),
        "-pidfile", str(PATHS["pidfile"]),
    ]
    for index, slot in enumerate(CARD_SLOTS, start=1):
        cmd += ["-device", f"pcie-root-port,id={slot},chassis={index}"]
    cmd += audio_arguments(audio)
    return cmd


def check_sound_device(audio: Optional[str]) -> None:
    if audio in (None, "off"):
        return
    try:
        version = host.qemu_version(which("qemu-system-aarch64"))
    except host.HostError as exc:
        raise LabError(str(exc)) from exc
    if version < SOUND_DEVICE_QEMU:
        raise LabError(f"QEMU {'.'.join(map(str, version))} has no virtio-sound device (QEMU 8.2 or later); "
                       "upgrade QEMU, or start with --audio off (games that need a sound device then fail)")


def cmd_start(args: argparse.Namespace) -> None:
    if read_pid():
        raise LabError("VM already running")
    for key in ("system_disk", "efivars"):
        if not PATHS[key].exists():
            raise LabError(f"{PATHS[key]} missing; recreate the guest with `./mun dev run --guest {INSTANCE}`")
    audio = getattr(args, "audio", None)
    ports = None if HOST.unix_sockets else {"qmp": host.free_port(), "qga": host.free_port()}
    cmd = qemu_command(args.display, audio, ports)
    if args.print_command:
        print(" ".join(repr(part) if " " in part or "{" in part else part for part in cmd))
        return
    check_sound_device(audio)
    for stale in (PATHS["qmp"], PATHS["qga"], PATHS["pidfile"], PATHS["control"]):
        if stale.exists():
            stale.unlink()
    clear_cards()
    if ports is not None:
        write_json_atomically(PATHS["control"], ports)
    accel = accelerator()
    if accel == "tcg":
        print(f"{HOST.label}: the guest's ARM64 processor is emulated (TCG); it runs, more slowly than with "
              "hardware acceleration")
    if args.display == "none":
        if HOST.daemonizes:
            cmd.append("-daemonize")
            run(cmd)
        else:
            start_in_background(cmd)
        print(f"started {GUEST_HOSTNAME} in background (pid {read_pid()}); console -> {PATHS['console_log']}")
        if args.wait:
            wait_for_guest(args.wait)
    else:
        # A window needs a foreground process (Cocoa its main thread); Ctrl-C
        # or the window close button ends the guest without a graceful shutdown.
        print("starting with a display window; this process stays in the foreground")
        try:
            run(cmd)
        finally:
            finalize_capture()


def card_backend(path: Path) -> Dict[str, Any]:
    """The file node of a card's image. locking=on: QEMU also refuses an
    image another process holds for writing. Its Windows file driver has no
    locking; there the attach registry is the only guard."""
    backend: Dict[str, Any] = {"driver": "file", "filename": str(path)}
    if HOST.image_locking:
        backend["locking"] = "on"
    return backend


def start_in_background(cmd: List[str]) -> None:
    """Where QEMU cannot daemonize (Windows): start it detached, then give it
    a moment to fail on its arguments, as -daemonize would have reported."""
    process = host.start_detached(cmd, PATHS["qemu_log"])
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = process.poll()
        if status is not None:
            try:
                reason = PATHS["qemu_log"].read_text(errors="replace").strip().splitlines()[-1]
            except (OSError, IndexError):
                reason = "no output"
            raise LabError(f"QEMU exited at once with status {host.exit_status(status)}: {reason} "
                           f"(see {PATHS['qemu_log']})")
        time.sleep(0.2)
    if read_pid() is None:
        PATHS["pidfile"].write_text(f"{process.pid}\n")


def wait_for_guest(timeout: int) -> None:
    """Until qemu-ga in the guest answers."""
    print(f"waiting up to {timeout}s for qemu-ga in {INSTANCE} ...")
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if read_pid() is None:
            raise LabError(f"QEMU exited during boot; see {PATHS['console_log']}")
        if guest_ready():
            print(f"qemu-ga answered after {int(time.monotonic() - started)}s")
            return
        time.sleep(2)
    raise LabError(f"qemu-ga did not answer after {timeout}s; see {PATHS['console_log']}")


def cmd_wait(args: argparse.Namespace) -> None:
    if read_pid() is None:
        raise LabError("VM is not running")
    wait_for_guest(args.timeout)


# ----------------------------------------------------------------- qemu-ga

class Qga:
    """Minimal client of qemu-ga, QEMU's guest-side control daemon (JSON lines over the virtio-serial
    chardev socket). QEMU accepts one client at a time on that socket, so
    every use holds this instance's qga.lock; a client that left mid-reply
    can leave bytes behind, which guest-sync-delimited skips (qemu-ga
    prefixes its answer with 0xFF, and a 0xFF from us resets its parser)."""

    def __init__(self, path: Path, timeout: float = 10.0):
        address = control_address(path)
        if address is None:
            raise LabError("qemu-ga socket missing; is the guest running?")
        self.sock = host.connect(address, timeout)
        self.buf = b""
        token = secrets.randbelow(1 << 31)
        self.sock.sendall(b"\xff" + json.dumps({"execute": "guest-sync-delimited",
                                                "arguments": {"id": token}}).encode() + b"\n")
        while True:
            reply = self._line(delimited=True)
            if reply.get("return") == token:
                break

    def _line(self, delimited: bool = False) -> Dict[str, Any]:
        while True:
            if delimited:
                marker = self.buf.find(b"\xff")
                if marker < 0:
                    self.buf = b""
                else:
                    self.buf = self.buf[marker + 1:]
                    delimited = False
                    continue
            elif b"\n" in self.buf:
                line, _, self.buf = self.buf.partition(b"\n")
                if not line.strip():
                    continue
                try:
                    return json.loads(line)
                except ValueError:
                    continue
            chunk = self.sock.recv(65536)
            if not chunk:
                raise LabError("qemu-ga connection closed")
            self.buf += chunk

    def execute(self, command: str, **arguments: Any) -> Any:
        message: Dict[str, Any] = {"execute": command}
        if arguments:
            message["arguments"] = arguments
        self.sock.sendall(json.dumps(message).encode() + b"\n")
        reply = self._line()
        if "error" in reply:
            raise LabError(f"qemu-ga {command}: {reply['error'].get('desc')}")
        return reply.get("return")

    def run(self, argv: List[str], timeout: float) -> subprocess.CompletedProcess:
        """Run a program in the guest as root and collect its exit status and output."""
        pid = self.execute("guest-exec", path=argv[0], arg=argv[1:], **{"capture-output": True},
                           env=["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                                "LANG=C.UTF-8"])["pid"]
        deadline = time.monotonic() + timeout
        while True:
            status = self.execute("guest-exec-status", pid=pid)
            if status.get("exited"):
                break
            if time.monotonic() >= deadline:
                raise LabError(f"guest command still running after {timeout:.0f}s: {' '.join(argv)[:120]}")
            time.sleep(0.1)
        out = base64.b64decode(status.get("out-data", "")).decode("utf-8", "replace")
        err = base64.b64decode(status.get("err-data", "")).decode("utf-8", "replace")
        code = status.get("exitcode")
        if code is None:
            code = 128 + int(status.get("signal", 0))
        return subprocess.CompletedProcess(argv, code, out, err)

    def close(self) -> None:
        self.sock.close()


def qga_run(argv: List[str], timeout: float = 120.0, connect_timeout: float = 10.0) -> subprocess.CompletedProcess:
    with lab_lock(PATHS["qga_lock"], "qemu-ga", timeout=LOCK_TIMEOUT):
        try:
            qga = Qga(PATHS["qga"], timeout=connect_timeout)
        except OSError as exc:
            raise LabError(f"qemu-ga unreachable: {exc}") from exc
        try:
            qga.sock.settimeout(max(connect_timeout, 30.0))
            return qga.run(argv, timeout)
        except OSError as exc:
            raise LabError(f"qemu-ga failed: {exc}") from exc
        finally:
            qga.close()


def guest_ready() -> bool:
    """True when the guest can be asked to run a command right now."""
    try:
        return qga_run(["/bin/true"], timeout=10, connect_timeout=3).returncode == 0
    except LabError:
        return False


def guest_command(command: str, capture: bool = True, timeout: float = 120.0,
                  connect_timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a shell command in the guest as root through qemu-ga. With
    capture=False the output goes to this terminal, after the fact."""
    result = qga_run(["/bin/sh", "-c", command], timeout=timeout, connect_timeout=connect_timeout)
    if not capture:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result


def cmd_run(args: argparse.Namespace) -> None:
    """Run one command in the guest as root and exit with its status."""
    if read_pid() is None:
        raise LabError("VM is not running")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise LabError("nothing to run: `run -- COMMAND ...`")
    result = guest_command(" ".join(shlex.quote(part) for part in command), capture=False, timeout=args.timeout)
    if result.returncode:
        raise SystemExit(result.returncode)


# ----------------------------------------------------------------------- stop/status

def cmd_stop(args: argparse.Namespace) -> None:
    pid = read_pid()
    if pid is None:
        clear_cards()
        finalize_capture()
        print("VM is not running")
        return
    qmp = Qmp(PATHS["qmp"])
    try:
        qmp.execute("quit" if args.hard else "system_powerdown")
    finally:
        qmp.close()
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if read_pid() is None:
            clear_cards()
            finalize_capture()
            print("VM stopped" + (" (hard)" if args.hard else " after guest shutdown"))
            return
        time.sleep(1)
    raise LabError(f"guest still running after {args.timeout}s; retry with --hard to force")


def cmd_status(args: argparse.Namespace) -> None:
    pid = read_pid()
    state = load_state()
    info = {
        "instance": INSTANCE,
        "running": pid is not None,
        "pid": pid,
        "system_disk": str(PATHS["system_disk"]) if PATHS["system_disk"].exists() else None,
        "attached_cards": state.get("cards", {}) if pid is not None else {},
        "cards_held_by_instances": load_registry(),
    }
    try:
        info["guest"] = json.loads(PATHS["guest"].read_text())
    except (OSError, ValueError):
        info["guest"] = None
    info["access"] = "no network interface; qemu-ga through `run`"
    if pid is not None:
        qmp = Qmp(PATHS["qmp"])
        try:
            info["qemu_status"] = qmp.execute("query-status")
            version = qmp.execute("query-version").get("qemu", {})
            info["qemu_version"] = ".".join(str(version.get(k, "?")) for k in ("major", "minor", "micro"))
        finally:
            qmp.close()
    print(json.dumps(info, indent=2))


def cmd_screenshot(args: argparse.Namespace) -> None:
    """Dump the guest framebuffer (virtio-gpu console) to a PNG for evidence."""
    if read_pid() is None:
        raise LabError("VM is not running")
    PATHS["screens"].mkdir(parents=True, exist_ok=True)
    target = PATHS["screens"] / (args.name or time.strftime("screen-%Y%m%d-%H%M%S.png"))
    qmp = Qmp(PATHS["qmp"])
    try:
        qmp.execute("screendump", filename=str(target), format="png")
    finally:
        qmp.close()
    print(target)


def cmd_destroy(args: argparse.Namespace) -> None:
    if read_pid():
        raise LabError("VM is running; stop it first")
    if not args.yes:
        raise LabError("destroy discards the guest disk and EFI vars; pass --yes to confirm")
    # The guest's disk and identity; its build and the cards stay.
    for key in ("system_disk", "efivars", "state", "console_log", "guest"):
        if PATHS[key].exists():
            PATHS[key].unlink()
    print("guest state removed; its build and the card images kept")


# ---------------------------------------------------------------------------- cards

def card_path(name: str) -> Path:
    card_serial(name)
    path = CARD_ROOT / f"{name}.img"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LabError("card image must be a regular file, not a link or device")
    return path


def card_serial(name: str) -> str:
    """A short, injective serial fits virtio-blk's 20-byte limit without truncation."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,15}", name, flags=re.ASCII):
        raise LabError("card name must be 1-16 lowercase ASCII letters/digits, '-' or '_', "
                       "starting with a letter or digit")
    return f"NPT-{name}"


def device_present(qmp: Qmp, device_id: str) -> bool:
    return any(item["name"] == device_id
               for item in qmp.execute("qom-list", path="/machine/peripheral"))


def cmd_card_create(args: argparse.Namespace) -> None:
    """Create an empty raw card image; raw mirrors removable media most closely."""
    path = card_path(args.name)
    if read_pid() and args.name in load_state().get("cards", {}):
        raise LabError("card is attached; detach it before replacing the image")
    if path.exists() and not args.force:
        raise LabError(f"{path} exists; use --force to overwrite")
    CARD_ROOT.mkdir(parents=True, exist_ok=True)
    run([which("qemu-img"), "create", "-q", "-f", "raw", str(path), args.size])
    print(f"created {path} ({args.size}, unformatted)")


# PCIe hot-unplug in the guest takes about five seconds after the console
# releases a card (the attention-button countdown); a re-attach that arrives
# inside that window waits for it rather than failing.
UNPLUG_GRACE = 20.0

# The launcher's command line tool in the guest.
LAUNCHD_CLI = "python3 /opt/mun/launchd/launchd.py"
RELEASE_CLI = f"{LAUNCHD_CLI} release"
CARDS_CLI = f"{LAUNCHD_CLI} cards"


def guest_card_records() -> List[Dict[str, Any]]:
    """The card service's current records (slot, serial, state, insertion),
    read live through the launcher's `cards` command. Raises LabError when the
    guest cannot be asked; callers decide what not knowing means."""
    result = guest_command(CARDS_CLI, timeout=30, connect_timeout=5)
    out = (result.stdout or "").strip()
    try:
        reply = json.loads(out.splitlines()[-1]) if out else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or not reply.get("ok") or not isinstance(reply.get("cards"), list):
        detail = (reply or {}).get("error", {}).get("message") if isinstance(reply, dict) else (result.stderr or "").strip()[:200]
        raise LabError(f"could not read the guest's card records: {detail or 'no answer'}")
    return [card for card in reply["cards"] if isinstance(card, dict)]


def _insertions_of(records: List[Dict[str, Any]], serial: str) -> List[str]:
    return sorted({str(r["insertion"]) for r in records if r.get("serial") == serial and r.get("insertion")})


def cmd_card_attach(args: argparse.Namespace) -> None:
    if read_pid() is None:
        raise LabError("VM is not running")
    path = card_path(args.name)
    if not path.exists():
        raise LabError(f"{path} missing; run card-create")
    # Each attempt is one transaction under the lifecycle lock: state
    # is read under it and the new attachment is recorded before it is let go.
    # When an earlier attachment of this card is still plugged in and nothing
    # proves it may be pulled, the attempt ends and the lock is released before
    # waiting, so whoever is removing it (the watcher, a pending device_del)
    # can finish. Every retry revalidates from scratch.
    deadline = time.monotonic() + UNPLUG_GRACE
    announced = False
    while True:
        with lifecycle_lock():
            previous = load_state().get("cards", {}).get(args.name)
            if previous is None or _clear_previous_attachment(args.name, previous, deadline):
                _attach_locked(args.name, path)
                return
            slot = previous.get("slot")
        if time.monotonic() >= deadline:
            raise LabError(f"{args.name} is still plugged in at {slot} after {UNPLUG_GRACE:.0f}s and the guest "
                           "has not shown that its insertion was released: run card-detach first")
        if not announced:
            print(f"{args.name} is still plugged in at {slot}; waiting for the removal to finish ...", flush=True)
            announced = True
        time.sleep(0.5)


def _clear_previous_attachment(name: str, previous: Dict[str, Any], deadline: float) -> bool:
    """With the lifecycle lock held: get rid of an earlier attachment of `name`
    so it can be attached again. True when it is gone; False when the caller
    must release the lock and retry; LabError when it must not be pulled.

    - Device already gone (the unplug finished, or the record outlived its
      QEMU): drop the record, its claim and any leftover backend.
    - Guest shows the insertion bound to this attachment released: this
      transaction pulls it itself, exactly as the watcher would, so the lock
      it holds cannot keep the removal from happening.
    - Guest shows it valid, reading or playing: refuse now; it was not ejected.
    - Nothing provable (guest unreachable, record unbound, or an unplug already
      in progress): False, and the caller waits without the lock."""
    qmp = Qmp(PATHS["qmp"])
    try:
        plugged = device_present(qmp, f"{previous['node']}-dev")
        if not plugged:
            nodes = qmp.execute("query-named-block-nodes")
            if any(item.get("node-name") == previous["node"] for item in nodes):
                qmp.execute("blockdev-del", **{"node-name": previous["node"]})
    finally:
        qmp.close()
    if not plugged:
        state = load_state()
        if state.get("cards", {}).get(name, {}).get("attachment") == previous.get("attachment"):
            del state["cards"][name]
            save_state(state)
        registry_release(name, previous.get("attachment"))
        print(f"{name} had been pulled; reconciled the lab state")
        return True
    try:
        record = current_guest_record(previous, guest_card_records())
    except LabError:
        return False
    if record is None:
        return False
    if record.get("state") != "released":
        raise LabError(f"{name} is still plugged in at {previous.get('slot')} and the guest shows it "
                       f"{record.get('state')}{' with a game running' if record.get('active') and record.get('state') == 'valid' else ''}; "
                       "eject it first (Eject safely) or run card-detach")
    try:
        text = detach_card(name, timeout=max(1.0, deadline - time.monotonic()), verified_released=True,
                           expected_attachment=previous.get("attachment"))
    except LabError:
        return False     # e.g. an unplug already in progress: wait for it without the lock
    print(text.replace("after the console released it", "after the console released it, to insert it again"))
    return True


def _attach_locked(name: str, path: Path) -> None:
    """The attach proper; the caller holds the lifecycle lock and has checked
    that no attachment of `name` is recorded."""
    state = load_state()
    cards = state.setdefault("cards", {})
    used = {card["slot"] for card in cards.values()}
    free = [slot for slot in CARD_SLOTS if slot not in used]
    if not free:
        raise LabError("no free card slot; detach a card first")
    slot = free[0]
    node = f"card-{name}"
    serial = card_serial(name)
    attachment = secrets.token_hex(6)
    # Insertions the guest still reports for this serial belong to earlier
    # plugs of the card, never to this one: they can never authorise
    # unplugging it. None when the guest cannot be asked.
    try:
        stale: Optional[List[str]] = _insertions_of(guest_card_records(), serial)
    except LabError:
        stale = None
    registry_claim(name, attachment)
    qmp = Qmp(PATHS["qmp"])
    try:
        qmp.execute("blockdev-add", driver="raw", **{"node-name": node}, file=card_backend(path))
        try:
            qmp.execute("device_add", driver="virtio-blk-pci", drive=node, id=f"{node}-dev", bus=slot,
                        serial=serial)
        except LabError as failure:
            try:
                qmp.execute("blockdev-del", **{"node-name": node})
            except LabError as cleanup_failure:
                raise LabError(f"{failure}; block cleanup also failed: {cleanup_failure}") from failure
            raise
    except LabError:
        registry_release(name, attachment)
        raise
    finally:
        qmp.close()
    state = load_state()
    state.setdefault("cards", {})[name] = {
        "slot": slot, "node": node, "path": str(path), "serial": serial,
        "attachment": attachment, "stale_insertions": stale, "insertion": None}
    save_state(state)
    print(f"attached {path.name} to {slot} as virtio-blk (serial {serial}); "
          f"guest sees /dev/disk/by-id/virtio-{serial}")


def release_in_guest(serial: str) -> None:
    """Safe removal (docs/saves.md): the launcher refuses while a session uses the card,
    otherwise the card service stops writes, finishes the one in flight and unmounts."""
    result = guest_command(f"{RELEASE_CLI} {shlex.quote(serial)}", timeout=60)
    line = (result.stdout or "").strip().splitlines()[-1] if (result.stdout or "").strip() else ""
    try:
        reply = json.loads(line)
    except ValueError:
        raise LabError("the guest did not answer the release request; is the launcher installed? "
                       "Use --abrupt only to simulate an unexpected removal")
    if not reply.get("ok"):
        error = reply.get("error", {})
        hint = " Exit the game first." if error.get("code") == "in_use" else ""
        raise LabError(f"guest refused to release {serial}: {error.get('code')} - {error.get('message')}.{hint} "
                       f"(--abrupt unplugs anyway, as a fault experiment)")


def detach_card(name: str, timeout: float = 30.0, abrupt: bool = False,
                verified_released: bool = False, expected_attachment: Optional[str] = None) -> str:
    """Unplug a virtual card. Safe by default: the guest releases it first
    (docs/saves.md). `abrupt` skips that as a fault experiment. `verified_released`
    is only for reconcile_released(), which has just read, under the same lock,
    that the guest released this attachment's current insertion; it then names
    the attachment it checked in `expected_attachment`.

    The whole transaction holds this instance's lifecycle lock, and the final
    state write removes only the attachment this call started with."""
    card_serial(name)
    if read_pid() is None:
        raise LabError("VM is not running")
    with lifecycle_lock():
        card = load_state().get("cards", {}).get(name)
        if card is None:
            raise LabError(f"{name} is not attached")
        attachment = card.get("attachment")
        if expected_attachment is not None and attachment != expected_attachment:
            raise LabError(f"{name} was re-attached meanwhile; the release does not apply to the new insertion")
        if not abrupt and not verified_released:
            release_in_guest(card.get("serial") or card_serial(name))
        qmp = Qmp(PATHS["qmp"])
        try:
            # device_del asks the guest to release the device; DEVICE_DELETED confirms
            # it actually happened. Only then may the block node be dropped.
            device_id = f"{card['node']}-dev"
            if device_present(qmp, device_id):
                qmp.execute("device_del", id=device_id)
                event = qmp.wait_event("DEVICE_DELETED", timeout=timeout,
                                       data_match={"device": device_id})
                if event is None and device_present(qmp, device_id):
                    raise LabError("card removal is still pending; retry card-detach to reconcile it")
            # A previous timeout may have outlived the connection that received the
            # deletion event. Query actual device/node state so retry can finish.
            nodes = qmp.execute("query-named-block-nodes")
            if any(item.get("node-name") == card["node"] for item in nodes):
                qmp.execute("blockdev-del", **{"node-name": card["node"]})
        finally:
            qmp.close()
        state = load_state()
        if state.get("cards", {}).get(name, {}).get("attachment") == attachment:
            del state["cards"][name]
            save_state(state)
        registry_release(name, attachment)
    how = " (abrupt)" if abrupt else (" after the console released it" if verified_released else " after a safe release")
    return f"detached {name} from {card['slot']}{how}"


def cmd_card_detach(args: argparse.Namespace) -> None:
    print(detach_card(args.name, args.timeout, abrupt=getattr(args, "abrupt", False)))


def current_guest_record(card: Dict[str, Any], records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The guest record that belongs to this host attachment, or None.

    Insertion tokens are unique per plug. Any insertion the guest reported for
    the serial before this attachment's device_add is an earlier plug's; any
    other one appeared after it, while the lifecycle lock kept every other
    attach out, so it is this attachment's. When the guest could not be asked
    at attach time, only an insertion learned since then is trusted."""
    serial = card.get("serial")
    mine = [r for r in records if r.get("serial") == serial and r.get("insertion")]
    stale = card.get("stale_insertions")
    if stale is None:
        known = card.get("insertion")
        return next((r for r in mine if r.get("insertion") == known), None) if known else None
    fresh = [r for r in mine if r.get("insertion") not in stale]
    return fresh[0] if len(fresh) == 1 else None


def reconcile_released(announce: Callable[[str], None] = print) -> List[str]:
    """Unplug every attached card whose current insertion the guest reports as
    released. The authority is the card service's live state, read while this
    instance's lifecycle lock is held, so no attach or detach can slip between
    the check and the unplug. A card that is valid, reading or playing is left
    alone whatever an old log line said."""
    unplugged: List[str] = []
    with lifecycle_lock():
        names = sorted(load_state().get("cards", {}))
        if not names:
            return unplugged
        records = guest_card_records()
        for name in names:
            state = load_state()
            card = state.get("cards", {}).get(name)
            if card is None:
                continue
            changed = False
            if card.get("stale_insertions") is None and not card.get("insertion"):
                # Attached while the guest could not be asked: learn the insertion
                # from a record that is not released. Worst case a stale one is
                # learned and this card is never unplugged automatically.
                candidates = [r for r in records if r.get("serial") == card.get("serial") and r.get("insertion")]
                if len(candidates) == 1 and candidates[0].get("state") != "released":
                    card["insertion"] = candidates[0]["insertion"]
                    changed = True
                else:
                    # A released record never seen valid proves nothing about
                    # this attachment: say so once, and name the safe way out.
                    released = [r["insertion"] for r in candidates if r.get("state") == "released"]
                    if released and card.get("unbound_notice") != released[0]:
                        card["unbound_notice"] = released[0]
                        changed = True
                        announce(f"{name} was released in the guest, but this attachment never saw that insertion "
                                 f"before its release; not unplugging it automatically: run card-detach {name}")
            record = current_guest_record(card, records)
            if record is not None and card.get("insertion") != record.get("insertion"):
                card["insertion"] = record.get("insertion")
                changed = True
            if changed:
                save_state(state)
            if record is None or record.get("state") != "released":
                continue
            announce(detach_card(name, verified_released=True, expected_attachment=card.get("attachment")))
            unplugged.append(name)
    return unplugged


# How often the watcher reconciles. qemu-ga answers one command at a time and
# there is no journal to follow from the host, so the guest is polled; every
# poll can also bind an attachment made while the guest could not be asked.
RELEASE_POLL_INTERVAL = 2.0


def watch_released(stop: "threading.Event", announce: Callable[[str], None] = print) -> None:
    """Unplug every card the console releases. Each poll reconciles the guest's
    current state, so a release is found however long qemu-ga was unreachable;
    the card service's live records are the only authority, and a
    released record can never bind an attachment. Returns when `stop` is set or
    the VM is gone."""
    last_error: List[str] = []

    def reconcile() -> None:
        # The poll runs every few seconds while the card service may be down:
        # report a failure once, and again only when it changes.
        try:
            reconcile_released(announce)
            last_error.clear()
        except LabError as exc:
            text = f"could not reconcile released cards: {exc}"
            if last_error != [text]:
                last_error[:] = [text]
                announce(text)

    while not stop.is_set():
        if read_pid() is None:
            return
        reconcile()
        stop.wait(RELEASE_POLL_INTERVAL)


def cmd_card_watch(args: argparse.Namespace) -> None:
    """Foreground watcher for a background console: Ctrl-C ends it."""
    if read_pid() is None:
        raise LabError("VM is not running")
    print("watching the card service: a card the console releases is unplugged here (Ctrl-C to stop)")
    stop = threading.Event()
    try:
        watch_released(stop)
    except KeyboardInterrupt:
        stop.set()


# ---------------------------------------------------------------------------- probe

PROBE_COMMANDS = [
    ("kernel", "uname -a"),
    ("os", "cat /etc/os-release | head -3"),
    ("cpu", "nproc; lscpu | grep -E 'Model name|Architecture|Vendor'"),
    ("memory", "free -m | head -2"),
    ("block", "lsblk -o NAME,SIZE,TYPE,SERIAL,MOUNTPOINTS"),
    ("root_fs", "df -h / | tail -1"),
    ("drm", "ls -l /dev/dri 2>&1; cat /sys/class/drm/card*/device/uevent 2>/dev/null | grep -E 'DRIVER|PCI_ID'"),
    ("framebuffer", "cat /sys/class/graphics/fb0/name 2>&1; cat /sys/class/graphics/fb0/virtual_size 2>&1"),
    ("input", "grep -E '^N:|^H:' /proc/bus/input/devices"),
    ("pci", "lspci 2>/dev/null || cat /sys/bus/pci/devices/*/uevent | grep -E 'DRIVER|PCI_ID' | sort | uniq -c"),
    ("hotplug_slots", "ls /sys/bus/pci/slots/ 2>&1"),
    ("network", "ip -brief addr; ip route | head -2"),
]


def cmd_probe(args: argparse.Namespace) -> None:
    """Collect guest capability evidence into a dated report (through qemu-ga)."""
    if read_pid() is None:
        raise LabError("VM is not running")
    folder = PATHS["state"].parent
    folder.mkdir(parents=True, exist_ok=True)
    report = folder / time.strftime("probe-%Y%m%d-%H%M%S.txt")
    lines = [f"# {GUEST_HOSTNAME} probe {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", ""]
    for title, command in PROBE_COMMANDS:
        result = guest_command(command, timeout=60)
        lines += [f"## {title}", f"$ {command}", ((result.stdout or "") + (result.stderr or "")).rstrip(), ""]
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"-> {report}")


# ---------------------------------------------------------------------------- logs

def cmd_launchd_log(args: argparse.Namespace) -> None:
    if read_pid() is None:
        raise LabError("VM is not running")
    guest_command(f"journalctl -u mun-launchd -u 'mun-game-*' -u 'mun-launch-cleanup@*' -b --no-pager "
                  f"-n {args.lines}", capture=False)


def cmd_cardd_log(args: argparse.Namespace) -> None:
    if read_pid() is None:
        raise LabError("VM is not running")
    guest_command(f"journalctl -u mun-cardd -b --no-pager -n {args.lines}", capture=False)


def cmd_shell_log(args: argparse.Namespace) -> None:
    if read_pid() is None:
        raise LabError("VM is not running")
    guest_command(f"journalctl -u mun-shell -b --no-pager -n {args.lines}", capture=False)


def cmd_open(args: argparse.Namespace) -> None:
    """Boot the console with a window on this computer. Foreground until the guest powers off.
    A watcher thread unplugs every card the console releases, so Eject safely in
    the shell ends with the card gone, as a player's hand would."""
    args.display = "window"
    args.audio = args.audio or default_window_audio()
    args.wait = 0
    args.print_command = False
    stop = threading.Event()
    watcher = threading.Thread(target=_watch_when_up, args=(stop, getattr(args, "card", None)), name="card-watch",
                               daemon=True)
    watcher.start()
    try:
        cmd_start(args)
    finally:
        stop.set()


def _watch_when_up(stop: "threading.Event", card: Optional[str] = None) -> None:
    # The window starts before QEMU has a pid file or qemu-ga; wait, insert
    # the card asked for, then follow. Under TCG the guest takes longer.
    deadline = time.monotonic() + 600
    while not stop.is_set() and time.monotonic() < deadline:
        if read_pid() is not None and guest_ready():
            if card:
                try:
                    cmd_card_attach(argparse.Namespace(name=card))
                except LabError as exc:
                    print(f"[card-watch] card {card} not inserted: {exc}", flush=True)
            watch_released(stop, announce=lambda text: print(f"[card-watch] {text}", flush=True))
            return
        time.sleep(3)


def cmd_send_key(args: argparse.Namespace) -> None:
    """Inject key presses through the virtual USB keyboard (QEMU qcode names)."""
    if read_pid() is None:
        raise LabError("VM is not running")
    qmp = Qmp(PATHS["qmp"])
    try:
        for chord in args.keys:
            keys = [{"type": "qcode", "data": name} for name in chord.split("+")]
            qmp.execute("send-key", keys=keys, **{"hold-time": args.hold})
            time.sleep(args.gap)
    finally:
        qmp.close()
    print(f"sent {len(args.keys)} key event(s)")


def mouse_events(x: float, y: float, click: bool) -> List[dict]:
    """QMP input events for the virtual USB tablet: an absolute move to (x, y)
    given as fractions of the display (0..1), then a left click if asked.
    The tablet reports 0..32767 on each axis whatever the guest's mode, so a
    game that changed the mode (800x600 fullscreen) is addressed the same way."""
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise LabError("mouse position must be fractions of the display, 0..1")
    move = [{"type": "abs", "data": {"axis": "x", "value": int(round(x * 32767))}},
            {"type": "abs", "data": {"axis": "y", "value": int(round(y * 32767))}}]
    if not click:
        return [move]
    return [move,
            [{"type": "btn", "data": {"down": True, "button": "left"}}],
            [{"type": "btn", "data": {"down": False, "button": "left"}}]]


def cmd_send_mouse(args: argparse.Namespace) -> None:
    """Move the virtual tablet pointer and optionally click, or hold the left
    button for a while (games that move toward a held pointer)."""
    if read_pid() is None:
        raise LabError("VM is not running")
    hold = getattr(args, "hold", 0) or 0
    qmp = Qmp(PATHS["qmp"])
    try:
        events = mouse_events(args.x, args.y, args.click or hold > 0)
        qmp.execute("input-send-event", events=events[0])
        time.sleep(0.15)
        if hold > 0:
            qmp.execute("input-send-event", events=events[1])
            time.sleep(hold / 1000.0)
            qmp.execute("input-send-event", events=events[2])
        elif args.click:
            for step in events[1:]:
                qmp.execute("input-send-event", events=step)
                time.sleep(0.15)
    finally:
        qmp.close()
    print(f"pointer at ({args.x:.3f}, {args.y:.3f})"
          + (f", left button held {hold} ms" if hold > 0 else (" and clicked" if args.click else "")))


# ------------------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./mun vm", description=__doc__.splitlines()[0])
    parser.add_argument("--instance", default=DEFAULT_INSTANCE,
                        help=f"an image guest created by ./mun dev run (default {DEFAULT_INSTANCE})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="boot the guest")
    p.add_argument("--display", default="none", choices=["none", "window"],
                   help="none: background + screendump evidence; window: foreground window")
    p.add_argument("--audio", choices=AUDIO_BACKENDS, default="none",
                   help="sound device backend: none (default: device present, silent), wav (capture file), "
                        "off (no device), or an audible backend of this host (coreaudio, pipewire, pa, alsa, dsound, sdl)")
    p.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="wait for qemu-ga after starting")
    p.add_argument("--print-command", action="store_true", help="print the QEMU command line and exit")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("wait", help="wait until qemu-ga in the guest answers")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("run", help="run one command in the guest as root (through qemu-ga)")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("stop", help="ACPI power-down (or --hard to quit QEMU)")
    p.add_argument("--hard", action="store_true")
    p.add_argument("--timeout", type=int, default=90)
    p.set_defaults(func=cmd_stop)

    sub.add_parser("status", help="show process, QEMU and card state").set_defaults(func=cmd_status)

    p = sub.add_parser("screenshot", help="save the guest display as PNG")
    p.add_argument("--name")
    p.set_defaults(func=cmd_screenshot)

    sub.add_parser("probe", help="record guest capabilities").set_defaults(func=cmd_probe)

    p = sub.add_parser("destroy", help="remove the guest's disk and state (keeps its build and the cards)")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_destroy)

    p = sub.add_parser("card-create", help="create an empty raw virtual Game Card image")
    p.add_argument("name")
    p.add_argument("--size", default="1G")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_card_create)

    p = sub.add_parser("card-attach", help="hot-plug a card image into the running guest")
    p.add_argument("name")
    p.set_defaults(func=cmd_card_attach)

    p = sub.add_parser("card-detach", help="release the card in the guest (safe removal), then hot-unplug it")
    p.add_argument("--abrupt", action="store_true",
                   help="unplug without asking the guest: simulates pulling the card (fault experiment)")
    p.add_argument("name")
    p.add_argument("--timeout", type=float, default=30.0)
    p.set_defaults(func=cmd_card_detach)

    p = sub.add_parser("open", help="boot the console with a window on this computer (foreground)")
    p.add_argument("--card", help="insert this card once the console is up")
    p.add_argument("--audio", choices=AUDIO_BACKENDS,
                   help="sound device backend for the windowed console (default: this host's audible one; none for silence)")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("launchd-log", help="show the launcher and game session journal from the guest")
    p.add_argument("--lines", type=int, default=60)
    p.set_defaults(func=cmd_launchd_log)

    p = sub.add_parser("cardd-log", help="show the card service journal from the guest")
    p.add_argument("--lines", type=int, default=60)
    p.set_defaults(func=cmd_cardd_log)

    p = sub.add_parser("shell-log", help="show the shell service journal from the guest")
    p.add_argument("--lines", type=int, default=60)
    p.set_defaults(func=cmd_shell_log)

    p = sub.add_parser("card-watch", help="unplug every card the console releases (Eject safely in the shell); for a background start")
    p.set_defaults(func=cmd_card_watch)
    p = sub.add_parser("send-mouse", help="move the virtual tablet pointer to X Y (fractions of the display) and optionally click")
    p.add_argument("x", type=float); p.add_argument("y", type=float)
    p.add_argument("--click", action="store_true", help="left click after moving")
    p.add_argument("--hold", type=int, default=0, metavar="MS", help="hold the left button this long after moving")
    p.set_defaults(func=cmd_send_mouse)
    p = sub.add_parser("send-key", help="inject keys via the virtual keyboard, e.g. right ret esc ctrl+alt+f2")
    p.add_argument("keys", nargs="+")
    p.add_argument("--hold", type=int, default=120, metavar="MS")
    p.add_argument("--gap", type=float, default=0.35, metavar="SECONDS")
    p.set_defaults(func=cmd_send_key)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        select_instance(args.instance)
        args.func(args)
    except LabError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
