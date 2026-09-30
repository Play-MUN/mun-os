#!/usr/bin/env python3
"""MUN OS development builds and image guests (host side of `./mun dev`).

A build runs in a disposable builder: a new QEMU guest made from the pinned
Debian cloud image (os/inputs.json), with its own disk, key and seed. The
host copies in the committed source tree, the builder provisions itself from
the pinned Debian snapshot and runs os/builder/build.sh, and the host takes
the results back into .local/mun/builds/<name>/ and deletes the builder. No
earlier builder or earlier build takes part.

An image guest boots a copy-on-write disk made from one build's image. It has
no network interface; vm/munvm.py drives it (cards, keys, screenshots,
commands through qemu-ga) as instance <name>.

Running a guest needs what vm/munvm.py needs (Python and QEMU). Building also
needs Git, OpenSSH, an ISO tool for the builder's seed (hdiutil on macOS,
xorriso or genisoimage on Linux) and, to be practical, an ARM64 host with
hardware acceleration: under TCG the builder is emulated and a build takes
hours. Building on Windows is not supported (use WSL 2).
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle  # noqa: E402
import sources as package_sources  # noqa: E402
import munvm as vm  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "os" / "builder"))
import recipes as game_recipes  # noqa: E402

REPO_ROOT = vm.REPO_ROOT
MUN_ROOT = REPO_ROOT / ".local" / "mun"
# Verified downloads may be shared between checkouts (reported in the build log).
CACHE_ROOT = Path(os.environ.get("MUN_CACHE_DIR", str(MUN_ROOT / "cache")))
BUILDERS_ROOT = MUN_ROOT / "builders"
BUILDS_ROOT = MUN_ROOT / "builds"
BUNDLES_ROOT = MUN_ROOT / "bundles"
SOURCES_ROOT = MUN_ROOT / "sources"
CARD_TOOL = REPO_ROOT / "tools" / "mun-card" / "mun-card"
GUESTS_ROOT = vm.GUESTS_ROOT
INPUTS_FILE = REPO_ROOT / "os" / "inputs.json"
PROFILES = ("qemu-dev",)
NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,15}")

BUILDER_VCPUS = int(os.environ.get("MUN_BUILDER_VCPUS", str(max(2, min(8, (os.cpu_count() or 4) - 2)))))
BUILDER_MEMORY = os.environ.get("MUN_BUILDER_MEMORY", "8G")
BUILDER_DISK = os.environ.get("MUN_BUILDER_DISK", "60G")
BUILDER_USER = "builder"
RECIPE_LIMIT = 16 * 1024 * 1024


def inputs() -> Dict[str, Any]:
    return json.loads(INPUTS_FILE.read_text())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


HOST_LOG: Optional[Path] = None   # the running build's logs/host.log


def log(text: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    if HOST_LOG is not None and HOST_LOG.parent.is_dir():
        with HOST_LOG.open("a") as handle:
            handle.write(line + "\n")


# ------------------------------------------------------------------ inputs

def builder_base_image() -> Path:
    """The pinned Debian cloud image, downloaded once and verified every time.
    The published SHA512SUMS must list the pinned digest before a download is
    trusted; a cached copy is re-hashed against the pin before each build."""
    spec = inputs()["builder"]
    image = CACHE_ROOT / spec["image"]
    if not image.exists():
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        sums = CACHE_ROOT / (spec["image"] + ".SHA512SUMS")
        vm.download(spec["sums_url"], sums)
        if vm.expected_sum(sums, spec["image"]) != spec["sha512"]:
            raise vm.LabError("the published SHA512SUMS does not list the pinned builder image digest")
        vm.download(spec["url"], image, spec["sha512"])
    log(f"verifying the builder image {image.name} against its pinned SHA-512 ...")
    if vm.sha512_file(image) != spec["sha512"]:
        raise vm.LabError(f"{image} does not match the pinned SHA-512; delete it to download it again")
    return image


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True).stdout.strip()


def source_state() -> Dict[str, Any]:
    """What the build is made from: the checkout's commit and whether the
    working tree matches it (ignored files never count; they are never sent)."""
    try:
        commit = git("rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise vm.LabError(f"not a git checkout: {exc}") from exc
    status = git("status", "--porcelain", "--untracked-files=normal")
    return {
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "commit_time": int(git("show", "-s", "--format=%ct", "HEAD")),
        "describe": git("describe", "--always", "--dirty"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "clean": status == "",
        "changes": status.splitlines()[:50],
    }


def source_archive(clean: bool, destination: Path) -> str:
    """A tar of exactly what is built: the commit itself for a clean checkout;
    tracked and untracked-but-not-ignored files for an explicit dirty build."""
    if clean:
        subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "-o", str(destination), "HEAD"],
                       check=True)
    else:
        names = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others",
                                "--exclude-standard"], check=True, capture_output=True).stdout.split(b"\0")
        with tarfile.open(destination, "w") as tar:
            for name in sorted(n.decode() for n in names if n):
                path = REPO_ROOT / name
                if path.is_file() or path.is_symlink():
                    tar.add(str(path), arcname=name, recursive=False)
    return sha256_file(destination)


# ----------------------------------------------------------------- builder

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Builder:
    """One disposable builder VM. Everything it has lives in its own directory."""

    def __init__(self, name: str):
        self.name = name
        self.dir = BUILDERS_ROOT / name
        self.disk = self.dir / "system.qcow2"
        self.efivars = self.dir / "efivars.fd"
        self.seed_dir = self.dir / "seed"
        self.seed_iso = self.dir / "seed.iso"
        self.key = self.dir / "id_ed25519"
        self.known_hosts = self.dir / "known_hosts"
        self.pidfile = self.dir / "qemu.pid"
        self.qmp = vm.socket_path(self.dir, "qmp.sock")
        self.console = self.dir / "console.log"
        self.port = 0

    def create(self, base: Path) -> None:
        if self.dir.exists():
            raise vm.LabError(f"builder {self.dir} exists: every build starts from a new builder")
        self.dir.mkdir(parents=True)
        vm.run([vm.which("qemu-img"), "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(base),
                str(self.disk), BUILDER_DISK])
        with self.efivars.open("wb") as handle:
            handle.truncate(64 * 1024 * 1024)
        # A key for this builder only; it dies with the builder and never enters an image.
        vm.run([vm.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-C", f"mun-builder-{self.name}",
                "-f", str(self.key)])
        self.seed_dir.mkdir()
        (self.seed_dir / "meta-data").write_text(
            f"instance-id: mun-builder-{self.name}\nlocal-hostname: mun-builder\n")
        (self.seed_dir / "user-data").write_text(f"""#cloud-config
hostname: mun-builder
ssh_pwauth: false
disable_root: true
users:
  - name: {BUILDER_USER}
    shell: /bin/bash
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    lock_passwd: true
    ssh_authorized_keys:
      - {self.key.with_suffix('.pub').read_text().strip()}
package_update: false
package_upgrade: false
growpart:
  mode: auto
  devices: ["/"]
""")
        vm.make_seed_iso(self.seed_dir, self.seed_iso)

    def qemu_command(self) -> List[str]:
        return [
            vm.which("qemu-system-aarch64"),
            "-name", f"mun-builder-{self.name}",
            *vm.host.machine_arguments(vm.accelerator()),
            "-smp", str(BUILDER_VCPUS),
            "-m", BUILDER_MEMORY,
            "-rtc", "base=utc",
            "-drive", f"if=pflash,format=raw,readonly=on,file={vm.firmware_path()}",
            "-drive", f"if=pflash,format=raw,file={self.efivars}",
            "-blockdev", json.dumps({"driver": "qcow2", "node-name": "sys",
                                     "file": {"driver": "file", "filename": str(self.disk)}}),
            "-device", "virtio-blk-pci,drive=sys,bootindex=0",
            "-blockdev", json.dumps({"driver": "raw", "node-name": "seed", "read-only": True,
                                     "file": {"driver": "file", "filename": str(self.seed_iso)}}),
            "-device", "virtio-blk-pci,drive=seed",
            "-device", "virtio-rng-pci",
            "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{self.port}-:22",
            "-device", "virtio-net-pci,netdev=net0",
            "-display", "none",
            "-serial", f"file:{self.console}",
            "-monitor", "none",
            "-qmp", f"unix:{self.qmp},server,nowait",
            "-pidfile", str(self.pidfile),
            "-daemonize",
        ]

    def start(self) -> None:
        self.port = free_port()
        vm.run(self.qemu_command())
        log(f"builder {self.name} started ({BUILDER_VCPUS} vCPU, {BUILDER_MEMORY}); console -> {self.console}")

    def pid(self) -> Optional[int]:
        try:
            pid = int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if vm.pid_alive(pid) else None

    def ssh_base(self) -> List[str]:
        return [vm.which("ssh"), "-i", str(self.key), "-p", str(self.port),
                "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", "-o", "BatchMode=yes",
                "-o", "ServerAliveInterval=30", f"{BUILDER_USER}@127.0.0.1"]

    def wait(self, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        while time.monotonic() < deadline:
            if self.pid() is None:
                raise vm.LabError(f"builder QEMU exited during boot; see {self.console}")
            if subprocess.run(self.ssh_base() + ["true"], capture_output=True).returncode == 0:
                log(f"builder reachable over SSH after {int(time.monotonic() - started)} s")
                return
            time.sleep(3)
        raise vm.LabError(f"builder not reachable after {timeout} s; see {self.console}")

    def run(self, command: str, log_file: Optional[Path] = None, stdin: Optional[Any] = None) -> None:
        """Run a command in the builder, streaming its output (and into log_file)."""
        proc = subprocess.Popen(self.ssh_base() + [command], stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        assert proc.stdout is not None
        sink = log_file.open("ab") if log_file else None
        try:
            for line in iter(proc.stdout.readline, b""):
                sys.stdout.buffer.write(line)
                sys.stdout.flush()
                if sink:
                    sink.write(line)
        finally:
            if sink:
                sink.close()
        if proc.wait() != 0:
            raise vm.LabError(f"builder command failed (exit {proc.returncode}): {command}")

    def fetch(self, remote_dir: str, destination: Path) -> None:
        destination.mkdir(parents=True)
        pull = subprocess.Popen(self.ssh_base() + [f"sudo tar -C {remote_dir} -cf - ."], stdout=subprocess.PIPE)
        unpack = subprocess.run([vm.which("tar"), "-xf", "-", "-C", str(destination)], stdin=pull.stdout)
        pull.wait()
        if pull.returncode != 0 or unpack.returncode != 0:
            raise vm.LabError("copying the build results from the builder failed")

    def stop(self) -> None:
        if self.pid() is None:
            return
        try:
            qmp = vm.Qmp(self.qmp)
            try:
                qmp.execute("system_powerdown")
            finally:
                qmp.close()
        except (vm.LabError, OSError):
            pass
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and self.pid() is not None:
            time.sleep(1)
        if self.pid() is not None:
            qmp = vm.Qmp(self.qmp)
            try:
                qmp.execute("quit")
            finally:
                qmp.close()
            time.sleep(2)

    def destroy(self) -> None:
        if self.pid() is not None:
            raise vm.LabError(f"builder {self.name} still running")
        shutil.rmtree(self.dir)
        if self.qmp.parent != self.dir:
            shutil.rmtree(self.qmp.parent, ignore_errors=True)


# ------------------------------------------------------------------- build

def cmd_build(args: argparse.Namespace) -> None:
    if not vm.HOST.daemonizes:
        raise vm.LabError(f"building is not supported on {vm.HOST.label}; build inside WSL 2 (Linux), "
                          "or download an image with `./mun get`")
    if args.profile not in PROFILES:
        raise vm.LabError(f"unknown profile {args.profile}; available: {', '.join(PROFILES)}")
    name = args.name or time.strftime("b%m%d-%H%M%S")
    if not NAME.fullmatch(name):
        raise vm.LabError("build name: 2-16 lowercase letters, digits or '-', starting with a letter or digit")
    final = BUILDS_ROOT / name
    if final.exists() or (BUILDS_ROOT / f"{name}.partial").exists():
        raise vm.LabError(f"build {name} exists; choose another --name")
    recipes = []
    for directory in args.recipe or []:
        try:
            recipes.append((Path(directory).resolve(), game_recipes.load(Path(directory))))
        except game_recipes.RecipeError as exc:
            raise vm.LabError(str(exc)) from exc
        # A recipe holds a manifest, a build script and patches; game data
        # goes on the card its owner makes, never into a build.
        size = sum(f.stat().st_size for f in Path(directory).rglob("*") if f.is_file())
        if size > RECIPE_LIMIT:
            raise vm.LabError(f"recipe {directory} holds {size / 1e6:.0f} MB: a recipe is instructions and patches, "
                              "not game data (that goes on the card)")
    if len({data["name"] for _path, data in recipes}) != len(recipes):
        raise vm.LabError("two recipes with the same name")
    source = source_state()
    if not source["clean"] and not args.allow_dirty:
        raise vm.LabError("the checkout has uncommitted changes; commit them, or pass --allow-dirty to "
                          "build the working tree (recorded as clean: false in BUILD-INFO)")
    started = time.monotonic()
    partial = BUILDS_ROOT / f"{name}.partial"
    (partial / "logs").mkdir(parents=True)
    global HOST_LOG
    HOST_LOG = partial / "logs" / "host.log"
    log(f"build {name}: {source['describe']} on {source['branch']}, clean={source['clean']}, profile {args.profile}")
    if vm.accelerator() == "tcg":
        log(f"{vm.HOST.label}: the builder runs emulated (TCG); expect a build to take hours")
    cache_reused = (CACHE_ROOT / inputs()["builder"]["image"]).exists()
    base = builder_base_image()
    with tempfile.TemporaryDirectory(prefix="mun-source-") as tmp:
        archive = Path(tmp) / "source.tar"
        source["archive_sha256"] = source_archive(source["clean"], archive)
        source["builder_image_cache"] = "reused a verified download" if cache_reused else "downloaded"
        source_info = Path(tmp) / "source.json"
        source_info.write_text(json.dumps(source, indent=2) + "\n")
        builder = Builder(name)
        builder.create(base)
        try:
            builder.start()
            builder.wait()
            log("copying the source tree into the builder ...")
            with archive.open("rb") as tar:
                builder.run("sudo mkdir -p /srv/mun/src /srv/mun/out && sudo tar -xf - -C /srv/mun/src", stdin=tar)
            with source_info.open("rb") as info:
                builder.run("sudo tee /srv/mun/source.json >/dev/null", stdin=info)
            for path, data in recipes:
                log(f"copying recipe {data['name']} into the builder ...")
                packed = Path(tmp) / f"recipe-{data['name']}.tar"
                with tarfile.open(packed, "w") as tar:
                    tar.add(str(path), arcname=data["name"])
                with packed.open("rb") as recipe_tar:
                    builder.run("sudo mkdir -p /srv/mun/recipes && sudo tar -xf - -C /srv/mun/recipes", stdin=recipe_tar)
            log("provisioning the builder from the pinned Debian snapshot ...")
            builder.run("sudo /srv/mun/src/os/builder/provision.sh /srv/mun/out", log_file=partial / "logs" / "provision.log")
            log("building the image ...")
            builder.run(f"sudo /srv/mun/src/os/builder/build.sh --profile {args.profile} --out /srv/mun/out "
                        f"--source-info /srv/mun/source.json" + (" --recipes /srv/mun/recipes" if recipes else ""),
                        log_file=partial / "logs" / "build.log")
            log("copying the results back ...")
            results = partial / "results"
            builder.fetch("/srv/mun/out", results)
        finally:
            builder.stop()
            if builder.console.exists():
                shutil.copy2(builder.console, partial / "logs" / "builder-console.log")
            if args.keep_builder:
                log(f"builder kept in {builder.dir} (--keep-builder)")
            else:
                builder.destroy()
    elapsed = int(time.monotonic() - started)
    info = publish_build(partial, final, f"build {name} finished in {elapsed} s; "
                                         f"builder image cache: {source['builder_image_cache']}")
    HOST_LOG = final / "logs" / "host.log"
    log(f"build {name} done in {elapsed // 60} min {elapsed % 60} s: {info['name']} {info['version']} "
        f"({info['environment']}, release={str(info['release']).lower()}), build id {info['build_id']}")
    log(f"image: {final / image_name(info)}")


def publish_build(partial: Path, final: Path, note: str) -> Dict[str, Any]:
    """Move what the builder produced (partial/results) next to the host's
    logs, check it against its SHA256SUMS, then make it the finished build
    in one rename. A build that fails a check stays `.partial`."""
    results = partial / "results"
    for item in results.iterdir():
        if item.name == "logs":
            for entry in item.iterdir():
                shutil.move(str(entry), str(partial / "logs" / entry.name))
            item.rmdir()
        else:
            shutil.move(str(item), str(partial / item.name))
    results.rmdir()
    verify_sums(partial)
    with (partial / "logs" / "host.log").open("a") as handle:
        handle.write(note + "\n")
    partial.rename(final)
    return json.loads((final / "BUILD-INFO.json").read_text())


def image_name(info: Dict[str, Any]) -> str:
    names = [name for name in info["artifacts"] if name.endswith(".qcow2")]
    if len(names) != 1:
        raise vm.LabError("BUILD-INFO does not name exactly one image")
    return names[0]


def verify_sums(directory: Path) -> None:
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        raise vm.LabError(f"{sums} missing")
    for line in sums.read_text().splitlines():
        digest, _, name = line.partition("  ")
        path = directory / name
        if not path.is_file() or sha256_file(path) != digest:
            raise vm.LabError(f"{path} does not match SHA256SUMS")


def builds() -> List[Path]:
    if not BUILDS_ROOT.is_dir():
        return []
    done = [p for p in BUILDS_ROOT.iterdir() if p.is_dir() and (p / "BUILD-INFO.json").is_file()
            and not p.name.endswith(".partial")]

    def finished(build: Path) -> str:
        try:
            return json.loads((build / "BUILD-INFO.json").read_text())["built"]["finished"]
        except (OSError, ValueError, KeyError, TypeError):
            return ""
    return sorted(done, key=lambda p: (finished(p), p.name))


# ------------------------------------------------------------------ guests

def create_guest(name: str, build: Path) -> None:
    """A new guest disk: copy-on-write over the build's image, which stays untouched."""
    info = json.loads((build / "BUILD-INFO.json").read_text())
    image = build / image_name(info)
    root = GUESTS_ROOT / name
    if (root / "guest.json").exists():
        raise vm.LabError(f"guest {name} exists")
    root.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(image)
    if digest != info["artifacts"][image.name]["sha256"]:
        raise vm.LabError(f"{image} does not match its BUILD-INFO hash")
    vm.run([vm.which("qemu-img"), "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(image),
            str(root / "system.qcow2")])
    with (root / "efivars.fd").open("wb") as handle:
        handle.truncate(64 * 1024 * 1024)
    vm.write_json_atomically(root / "state.json", {"cards": {}})
    vm.write_json_atomically(root / "guest.json", {
        "build": build.name, "build_id": info["build_id"], "image": str(image), "image_sha256": digest,
        "version": info["version"], "environment": info["environment"], "release": info["release"],
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    log(f"guest {name}: new disk over build {build.name} ({info['build_id']})")


def cmd_run(args: argparse.Namespace) -> int:
    name = args.guest
    if not NAME.fullmatch(name):
        raise vm.LabError("guest name: 2-16 lowercase letters, digits or '-', starting with a letter or digit")
    guest_file = GUESTS_ROOT / name / "guest.json"
    if guest_file.exists():
        recorded = json.loads(guest_file.read_text())["build"]
        if args.build and args.build != recorded:
            raise vm.LabError(f"guest {name} runs build {recorded}; use a new --guest name for build {args.build}")
    else:
        available = builds()
        if args.build:
            build = BUILDS_ROOT / args.build
            if build not in available:
                raise vm.LabError(f"no finished build {args.build} in {BUILDS_ROOT}")
        elif available:
            build = available[-1]
        else:
            raise vm.LabError("no build yet: run `./mun dev build` first")
        create_guest(name, build)
    if args.window:
        extra = (["--audio", args.audio] if args.audio else []) + (["--card", args.card] if getattr(args, "card", None) else [])
        return vm.main(["--instance", name, "open"] + extra)
    return vm.main(["--instance", name, "start", "--audio", args.audio or "none", "--wait", str(args.wait)])


def cmd_vm(args: argparse.Namespace) -> int:
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    return vm.main(["--instance", args.guest] + rest)


def cmd_list(args: argparse.Namespace) -> None:
    for build in builds():
        info = json.loads((build / "BUILD-INFO.json").read_text())
        origin = ""
        if (build / "ORIGIN.json").is_file():
            with contextlib.suppress(OSError, ValueError, KeyError):
                origin = f" downloaded from {json.loads((build / 'ORIGIN.json').read_text())['source']}"
        print(f"build {build.name:16} {info['version']} {info['environment']} release={str(info['release']).lower()} "
              f"id {info['build_id']} source {info['source']['describe']}{origin}")
    if GUESTS_ROOT.is_dir():
        for guest in sorted(GUESTS_ROOT.glob("*/guest.json")):
            data = json.loads(guest.read_text())
            print(f"guest {guest.parent.name:16} build {data['build']} created {data['created']}")


# ------------------------------------------------------------ downloads

# A download carries MUN's own software only: a build that also built a
# third-party game (from a recipe, `--recipe`) is not bundled, whatever that
# game's terms.
OWN_GAMES = {"mun-collect", "mun-gl-probe"}
# The cards a bundle carries, made from the build's own example game.
BUNDLED_CARDS = {
    "demo": ("MUN Test Card", []),
    "collect": ("MUN Collect", ["--variant", "game", "--game", "{games}/mun-collect/mun-collect"]),
}


def cmd_bundle(args: argparse.Namespace) -> None:
    """A downloadable bundle of a finished build (vm/bundle.py), with its cards."""
    build = BUILDS_ROOT / args.build
    if build not in builds():
        raise vm.LabError(f"no finished build {args.build} in {BUILDS_ROOT}")
    if (build / "ORIGIN.json").exists():
        raise vm.LabError(f"build {args.build} was downloaded; bundle the build it came from instead")
    info = json.loads((build / "BUILD-INFO.json").read_text())
    others = sorted(set(info.get("games", {})) - OWN_GAMES)
    if others:
        raise vm.LabError(f"build {args.build} also built {', '.join(others)}: a download carries MUN's own software "
                          "only; bundle a build made without it")
    if not info.get("source", {}).get("clean", False):
        log(f"warning: build {args.build} is of uncommitted changes (clean: false in BUILD-INFO); fine for a "
            "local test, not for publication")
    destination = Path(args.out) if args.out else BUNDLES_ROOT / args.build
    with tempfile.TemporaryDirectory(prefix="mun-bundle-") as tmp:
        cards = {}
        for name, (title, extra) in BUNDLED_CARDS.items():
            image = Path(tmp) / f"{name}.img"
            command = [sys.executable, str(CARD_TOOL), "create", str(image), "--title", title]
            command += [part.format(games=build / "games") for part in extra]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise vm.LabError(f"making the {name} card failed: {(result.stderr or result.stdout).strip()}")
            cards[name] = (image, title)
        licences = (REPO_ROOT / "LICENSE", REPO_ROOT / "NOTICE",
                    *sorted((REPO_ROOT / "services" / "mun-shell" / "fonts").glob("*-OFL.txt")))
        try:
            manifest = bundle.make(build, destination, cards, licences)
        except bundle.BundleError as exc:
            raise vm.LabError(str(exc)) from exc
    total = sum(entry["size"] for entry in manifest["files"])
    log(f"bundle of build {args.build} ({manifest['build_id']}): {destination} "
        f"({len(manifest['files'])} files, {total / 1e6:.0f} MB); serve the directory, then `./mun get <URL>`")


def cmd_sources(args: argparse.Namespace) -> None:
    """The corresponding source of a build's Debian packages (vm/sources.py)."""
    build = BUILDS_ROOT / args.build
    if build not in builds():
        raise vm.LabError(f"no finished build {args.build} in {BUILDS_ROOT}")
    info = json.loads((build / "BUILD-INFO.json").read_text())
    out = Path(args.out) if args.out else SOURCES_ROOT / info["build_id"]
    try:
        manifest = package_sources.collect(info, out, fetch_files=not args.list, report=log)
    except package_sources.SourcesError as exc:
        raise vm.LabError(f"{exc}; run the same command again to resume") from exc
    size = sum(int(f["size"]) for s in manifest["sources"] for f in s["files"])
    log(f"{'listed' if args.list else 'sources in'} {out}: {len(manifest['sources'])} source packages, "
        f"{size / 1e6:.0f} MB; publish the directory beside the release (docs/licensing.md)")


def install(source: str, name: Optional[str] = None) -> Path:
    """Download a bundle, verify it and install it as a build; its path."""
    import urllib.error
    try:
        # One download at a time: two would share the staging directory and
        # its partial files.
        with vm.lab_lock(BUILDS_ROOT / ".downloads.lock", "the downloads", timeout=5):
            build, cards = bundle.get(source, BUILDS_ROOT, vm.CARD_ROOT, name, report=log)
    except bundle.BundleError as exc:
        raise vm.LabError(str(exc)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise vm.LabError(f"download failed: {exc}; run the same command again to resume") from exc
    for card in cards:
        log(f"card {card.stem}: {card}")
    return build


def cmd_get(args: argparse.Namespace) -> None:
    build = install(args.source, args.name)
    log(f"build {build.name} ready. Play: ./mun play" + (" --card collect" if (vm.CARD_ROOT / "collect.img").exists() else ""))


def cmd_play(args: argparse.Namespace) -> int:
    """The console in a window: guest `play` over the latest (or given)
    build, created the first time, with a card inserted once it is up."""
    if args.card:
        vm.card_path(args.card)                      # refuses a bad name before anything starts
        if not (vm.CARD_ROOT / f"{args.card}.img").is_file():
            raise vm.LabError(f"no card {args.card} in {vm.CARD_ROOT}")
    run_args = argparse.Namespace(guest=args.guest, build=args.build, window=True, audio=args.audio, wait=0,
                                  card=args.card)
    return cmd_run(run_args)


# --------------------------------------------------------------- smoke check

# Run in the guest as root through qemu-ga: wait for the card service to show
# a valid game card, then ask the launcher to start it, as the shell's Play
# does (the launcher's own protocol, so the check does not depend on the
# shell's navigation). Prints one JSON line.
GUEST_LAUNCH = r"""python3 - <<'EOF'
import json, socket, time
def first_message(path, wanted):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(20)
        sock.connect(path)
        buffer = b""
        while True:
            data = sock.recv(65536)
            if not data:
                return {}
            buffer += data
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                message = json.loads(line)
                if message.get("type") == wanted:
                    return message
deadline = time.time() + SECONDS
card = None
while time.time() < deadline and card is None:
    snapshot = first_message("/run/mun/cardd.sock", "snapshot")
    card = next((c for c in snapshot.get("cards", []) if c.get("serial") == "SERIAL" and c.get("state") == "valid"
                 and (c.get("info") or {}).get("kind") == "game"), None)
    if card is None:
        time.sleep(2)
if card is None:
    print(json.dumps({"ok": False, "why": "the card never became a valid game card"}))
    raise SystemExit(1)
def launch(card):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(30)
        sock.connect("/run/mun/launchd.sock")
        sock.sendall((json.dumps({"type": "launch", "slot": card["slot"], "serial": card["serial"],
                                  "version": card["info"].get("version")}) + "\n").encode())
        buffer = b""
        while True:
            data = sock.recv(65536)
            if not data:
                return {}
            buffer += data
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                message = json.loads(line)
                if message.get("type") == "launch_result":
                    return message
# A slow guest can list the card before the launcher has its own view of the
# reader; those refusals mean "not yet", so ask again until the deadline.
NOT_YET = ("reader_unavailable", "card_mismatch", "busy")
message = {}
while time.time() < deadline:
    message = launch(card)
    if message.get("accepted") or (message.get("error") or {}).get("code") not in NOT_YET:
        break
    time.sleep(2)
print(json.dumps({"ok": bool(message.get("accepted")), "result": message}))
raise SystemExit(0 if message.get("accepted") else 1)
EOF"""


def screen_drawn(guest: str) -> bool:
    """Whether the guest's display shows anything but black: a QMP
    screendump in PPM (read with the standard library), sampled."""
    target = GUESTS_ROOT / guest / "screens" / "smoke-probe.ppm"
    target.parent.mkdir(parents=True, exist_ok=True)
    qmp = vm.Qmp(vm.PATHS["qmp"])
    try:
        qmp.execute("screendump", filename=str(target.resolve()), format="ppm")
    finally:
        qmp.close()
    data = target.read_bytes()
    # P6 header: magic, width, height, maximum value, each followed by whitespace.
    fields = data.split(maxsplit=4)
    if len(fields) < 5 or fields[0] != b"P6":
        return False
    pixels = fields[4]
    return any(value > 24 for value in pixels[::997])


def cmd_smoke(args: argparse.Namespace) -> int:
    """Boot a build (a bundle installed first, if given) and play its MUN
    Collect card: start, card, game, exit, clean power-off. The check each
    host runs on a real QEMU; it writes smoke.json next to the guest."""
    started = time.monotonic()
    steps: Dict[str, Any] = {}

    def step(name: str, detail: str = "") -> None:
        steps[name] = round(time.monotonic() - started, 1)
        log(f"smoke: {name}" + (f" ({detail})" if detail else "") + f" at {steps[name]} s")

    if args.bundle:
        build: Optional[str] = install(args.bundle).name
    else:
        build = args.build or (builds()[-1].name if builds() else None)
    if not build:
        raise vm.LabError("no build to check: give --build or --bundle")
    card = args.card
    if not (vm.CARD_ROOT / f"{card}.img").is_file():
        raise vm.LabError(f"no card {card} in {vm.CARD_ROOT} (a bundle brings one)")
    guest = args.guest
    if (GUESTS_ROOT / guest / "guest.json").exists():
        recorded = json.loads((GUESTS_ROOT / guest / "guest.json").read_text())["build"]
        if recorded != build:
            raise vm.LabError(f"guest {guest} runs build {recorded}; use another --guest for build {build}")
        if vm.main(["--instance", guest, "stop", "--hard"]) != 0:
            raise vm.LabError(f"could not stop guest {guest}")
    else:
        create_guest(guest, BUILDS_ROOT / build)
    report = {"host": vm.HOST.label, "accelerator": vm.accelerator(), "build": build,
              "qemu": ".".join(map(str, vm.host.qemu_version(vm.which("qemu-system-aarch64")))), "steps": steps}
    step("start", f"{report['host']}, {report['accelerator']}, QEMU {report['qemu']}")
    ok = False
    try:
        if vm.main(["--instance", guest, "start", "--audio", "none", "--wait", str(args.wait)]) != 0:
            raise vm.LabError("the guest did not come up")
        step("qemu-ga answered")
        vm.select_instance(guest)
        services = vm.guest_command("systemctl is-active mun-shell mun-cardd mun-launchd", timeout=60)
        if services.returncode != 0:
            raise vm.LabError(f"a console service is not active: {services.stdout.split()}")
        step("services active")
        if vm.main(["--instance", guest, "card-attach", card]) != 0:
            raise vm.LabError(f"card {card} could not be inserted")
        step("card inserted")
        launch = vm.guest_command(GUEST_LAUNCH.replace("SECONDS", str(args.card_timeout))
                                  .replace("SERIAL", vm.card_serial(card)), timeout=args.card_timeout + 60)
        if launch.returncode != 0:
            raise vm.LabError(f"the game did not start: {launch.stdout.strip()[-300:]}")
        step("game started")
        time.sleep(args.play)
        vm.main(["--instance", guest, "screenshot", "--name", "smoke-game.png"])
        vm.main(["--instance", guest, "send-key", "--hold", "300", "esc"])
        deadline = time.monotonic() + args.card_timeout
        result = ""
        while time.monotonic() < deadline:
            journal = vm.guest_command("journalctl -u mun-launchd -b --no-pager -o cat | grep ': result ' | tail -1",
                                       timeout=60).stdout.strip()
            if journal:
                result = journal
                break
            time.sleep(3)
        if "result exited (code=0" not in result:
            raise vm.LabError(f"the game did not end cleanly: {result or 'no result'}")
        step("game ended", result.split(": ", 1)[-1])
        # The launcher hands the screen back: the shell runs again, the card
        # service still sees the card.
        deadline = time.monotonic() + args.card_timeout
        while time.monotonic() < deadline:
            if vm.guest_command("systemctl is-active mun-shell", timeout=60).returncode == 0:
                break
            time.sleep(3)
        else:
            raise vm.LabError("MUN Shell did not come back after the game")
        step("shell back")
        while time.monotonic() < deadline and not screen_drawn(guest):
            time.sleep(3)
        if not screen_drawn(guest):
            raise vm.LabError("MUN Shell runs but the screen stayed black after the game")
        step("shell drawn")
        vm.main(["--instance", guest, "screenshot", "--name", "smoke-after.png"])
        if vm.main(["--instance", guest, "card-detach", card]) != 0:
            raise vm.LabError(f"card {card} was not released safely")
        step("card released")
        ok = True
    finally:
        stopped = vm.main(["--instance", guest, "stop", "--timeout", "300"]) == 0
        if not stopped:
            vm.main(["--instance", guest, "stop", "--hard"])
        step("powered off" if stopped else "forced off")
        report["ok"] = ok and stopped
        vm.write_json_atomically(GUESTS_ROOT / guest / "smoke.json", report)
    log(f"smoke: {'passed' if report['ok'] else 'failed'}; report {GUESTS_ROOT / guest / 'smoke.json'}")
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./mun dev", description="MUN OS development image (QEMU)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="build a development image in a new disposable builder")
    p.add_argument("--name", help="build name (default: time-based)")
    p.add_argument("--profile", default="qemu-dev", help="composition profile (only qemu-dev exists)")
    p.add_argument("--recipe", action="append", metavar="DIR",
                   help="also compile a game from a recipe kept outside this repository (os/builder/recipes.py); "
                        "repeatable")
    p.add_argument("--allow-dirty", action="store_true", help="build uncommitted changes (recorded in BUILD-INFO)")
    p.add_argument("--keep-builder", action="store_true", help="leave the builder VM's disk for inspection")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("run", help="boot an image guest (a new disk from the latest or given build the first time)")
    p.add_argument("--guest", default="dev", help="guest name (default dev)")
    p.add_argument("--build", help="build to create the guest from (default: the latest)")
    p.add_argument("--window", action="store_true", help="open a window on this computer (foreground, sound on)")
    p.add_argument("--audio", choices=vm.AUDIO_BACKENDS,
                   help="sound backend (default none; with --window, this host's audible one)")
    p.add_argument("--wait", type=int, default=240, help="seconds to wait for qemu-ga in the guest (background start)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("vm", help="any ./mun vm command for a guest: ./mun dev vm GUEST COMMAND ...")
    p.add_argument("guest")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_vm)
    sub.add_parser("list", help="list builds and guests").set_defaults(func=cmd_list)
    p = sub.add_parser("smoke", help="boot a build (or a bundle) and play its MUN Collect card: the check each host runs")
    p.add_argument("--build", help="build to check (default: the latest, or the bundle's)")
    p.add_argument("--bundle", help="install this bundle first (address as for ./mun get)")
    p.add_argument("--guest", default="smoke", help="guest name (default smoke)")
    p.add_argument("--card", default="collect", help="game card to play (default collect)")
    p.add_argument("--wait", type=int, default=1500, help="seconds to wait for the guest (emulated hosts are slow)")
    p.add_argument("--card-timeout", type=int, default=240, help="seconds for the card and for the game's end")
    p.add_argument("--play", type=int, default=15, help="seconds the game runs before Esc")
    p.set_defaults(func=cmd_smoke)
    p = sub.add_parser("bundle", help="make a downloadable bundle of a build (for ./mun get)")
    p.add_argument("--build", required=True)
    p.add_argument("--out", help="directory to create (default .local/mun/bundles/<build>)")
    p.set_defaults(func=cmd_bundle)
    p = sub.add_parser("sources", help="fetch the source of a build's Debian packages, to publish beside its release")
    p.add_argument("--build", required=True)
    p.add_argument("--out", help="directory (default .local/mun/sources/<build id>)")
    p.add_argument("--list", action="store_true", help="only resolve and list them (SOURCES.json), fetch nothing")
    p.set_defaults(func=cmd_sources)
    return parser


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./mun get", description="download and verify a MUN OS image, ready to play")
    parser.add_argument("source", help="a bundle: its URL (https) or a local directory or release.json")
    parser.add_argument("--name", help="build name to install it as (default d<month><day>-<time> of its build)")
    parser.set_defaults(func=cmd_get)
    return parser


def play_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="./mun play", description="the MUN console in a window")
    parser.add_argument("--build", help="build or download to play (default: the latest)")
    parser.add_argument("--guest", default="play", help="guest name (default play)")
    parser.add_argument("--card", help="a card in .local/gamecards/ to insert once the console is up, e.g. collect")
    parser.add_argument("--audio", choices=vm.AUDIO_BACKENDS, help="sound backend (default: this host's audible one)")
    parser.set_defaults(func=cmd_play)
    return parser


def main(argv: Optional[List[str]] = None, parser: Optional[argparse.ArgumentParser] = None) -> int:
    args = (parser or build_parser()).parse_args(argv)
    try:
        result = args.func(args)
    except vm.LabError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        return exc.returncode or 1
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
