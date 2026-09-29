#!/usr/bin/env python3
"""Write BUILD-INFO.json for one MUN OS image build (standard library only).

Every field comes from something the build used or produced: os/inputs.json,
the host's description of the source tree, the builder's package list, the
mkosi manifest of the image, the build overlay's toolchain record and the
files themselves. A value that is not known is recorded as absent with the
reason, never guessed.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

FORMAT = "mun-build-info/1"
# What the architecture plan asks BUILD-INFO to name explicitly.
KERNEL_PACKAGE = re.compile(r"linux-image-\d.*")
MESA_PACKAGES = ("libgl1-mesa-dri", "libegl-mesa0", "libglx-mesa0", "libgbm1", "mesa-libgallium")
FIRMWARE_PREFIXES = ("firmware-", "linux-firmware", "raspi-firmware")
TOOLCHAIN_PACKAGES = ("gcc", "g++", "cpp", "binutils", "libc6-dev", "make", "cmake", "ninja-build", "pkgconf",
                      "qt6-base-dev", "qt6-declarative-dev", "libsdl2-dev", "libopenal-dev")
BUILDER_TOOLS = ("mkosi", "systemd", "systemd-repart", "systemd-ukify", "systemd-boot-efi", "apt", "dpkg",
                 "qemu-utils", "python3", "e2fsprogs", "dosfstools", "mtools", "git")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    """Hash of a configuration tree: sorted relative paths and file hashes."""
    files = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        files[path.relative_to(root).as_posix()] = sha256(path)
    combined = hashlib.sha256("".join(f"{name}\0{digest}\n" for name, digest in files.items()).encode()).hexdigest()
    return {"sha256": combined, "files": files}


def read_tsv(path: Path) -> dict:
    packages = {}
    if not path.is_file():
        return packages
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            packages[parts[0]] = parts[1]
    return packages


def main(argv: list) -> int:
    parser = argparse.ArgumentParser()
    for name in ("inputs", "source-info", "profile", "build-id", "started", "image", "raw", "manifest",
                 "initrd-manifest", "initrd", "builder-packages", "toolchain", "config", "out"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.inputs).read_text())
    source = json.loads(Path(args.source_info).read_text())
    def package_list(manifest_path: str) -> list:
        manifest = json.loads(Path(manifest_path).read_text())
        return sorted(({"name": p["name"], "version": p["version"], "architecture": p["architecture"]}
                       for p in manifest.get("packages", [])), key=lambda p: p["name"])

    packages = package_list(args.manifest)
    initrd_packages = package_list(args.initrd_manifest)
    installed = {p["name"]: p["version"] for p in packages}
    builder = read_tsv(Path(args.builder_packages))
    toolchain = read_tsv(Path(args.toolchain))
    release = Path(args.config) / "mkosi.profiles" / args.profile / "mkosi.conf"
    environment = dict(re.findall(r"^\s*(MUN_[A-Z_]+)=(\S+)", release.read_text(), flags=re.MULTILINE))

    kernels = sorted(name for name in installed if KERNEL_PACKAGE.fullmatch(name))
    firmware = sorted(name for name in installed if name.startswith(FIRMWARE_PREFIXES))
    out = Path(args.out)
    games = {}
    for binary in sorted((out / "games").glob("*/*")):
        if binary.is_file() and os.access(binary, os.X_OK):
            games[binary.parent.name] = {"file": binary.relative_to(out).as_posix(), "size": binary.stat().st_size,
                                        "sha256": sha256(binary)}
    # Games compiled from recipes: which recipe, its sources and licence.
    recipes = json.loads((out / "recipes.json").read_text()) if (out / "recipes.json").is_file() else []
    for recipe in recipes:
        if recipe["name"] in games:
            games[recipe["name"]]["recipe"] = recipe

    info = {
        "format": FORMAT,
        "name": inputs["name"],
        "version": inputs["version"],
        "environment": environment.get("MUN_ENVIRONMENT"),
        "release": environment.get("MUN_RELEASE") == "true",
        "profile": args.profile,
        "build_id": args.build_id,
        "built": {"started": args.started, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "source": source,
        "debian": {
            "suite": inputs["debian"]["suite"],
            "snapshot": inputs["debian"]["snapshot"],
            "archives": inputs["debian"]["archives"],
            "components": inputs["debian"]["components"],
            "keyring": inputs["debian"]["keyring"],
        },
        "kernel": {"packages": {name: installed[name] for name in kernels}
                   or "absent: no linux-image package in this profile"},
        "mesa": {name: installed[name] for name in MESA_PACKAGES if name in installed},
        "firmware": {name: installed[name] for name in firmware}
                    or "not installed: QEMU's virtio devices need no device firmware",
        "toolchain": {name: toolchain[name] for name in TOOLCHAIN_PACKAGES if name in toolchain}
                     or "absent: no build scripts ran",
        "builder": {
            "image": inputs["builder"]["image"],
            "image_sha512": inputs["builder"]["sha512"],
            "tools": {name: builder[name] for name in BUILDER_TOOLS if name in builder},
            "packages_sha256": hashlib.sha256(Path(args.builder_packages).read_bytes()).hexdigest(),
            "package_count": len(builder),
            "kernel": os.uname().release,
        },
        "config": tree_hash(Path(args.config)),
        "inputs_sha256": sha256(Path(args.inputs)),
        "packages": packages,
        "initrd": {
            "configuration": "mkosi built-in mkosi-initrd, as subimage os/mkosi/mkosi.images/initrd",
            "sha256": sha256(Path(args.initrd)),
            "note": "part of the unified kernel image on the ESP, with a kernel-modules initrd mkosi adds",
            "packages": initrd_packages,
        },
        "games": games,
        "artifacts": {
            Path(args.image).name: {"sha256": sha256(Path(args.image)), "size": Path(args.image).stat().st_size},
            "raw_image": {"sha256": sha256(Path(args.raw)), "size": Path(args.raw).stat().st_size,
                          "note": "the uncompressed disk the qcow2 was converted from; not shipped"},
        },
    }
    json.dump(info, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
