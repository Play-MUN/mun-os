#!/usr/bin/env python3
"""The corresponding source of a MUN OS image's Debian packages.

An image is composed of Debian packages at the exact versions its
BUILD-INFO.json records, from one dated Debian snapshot (os/inputs.json).
Whoever distributes the image must be able to hand over the source of the
packages whose licences ask for it (docs/licensing.md), so a release carries
the source of every package in the image, its initrd and its kernel: this
tool fetches them, verified, from snapshot.debian.org, into one directory
that is published beside the release, never in Git.

    ./mun dev sources --build NAME [--out DIR] [--list]

For each binary package and version, snapshot.debian.org names its source
package and version (/mr/binary/NAME/); for each source, its files and their
SHA-1 (/mr/package/SOURCE/VERSION/srcfiles?fileinfo=1), each file fetched by
digest (/file/SHA1) and checked. DIR/SOURCES.json lists what was fetched and
which binaries each source built. Running it again fetches only what is
missing; nothing that does not match its digest is kept.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SNAPSHOT = "https://snapshot.debian.org"
FORMAT = "mun-sources/1"
NAME = re.compile(r"[a-z0-9][a-z0-9.+-]*")
VERSION = re.compile(r"[0-9A-Za-z.+~:-]+")
DIGEST = re.compile(r"[0-9a-f]{40}")
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]*")
CHUNK = 1 << 20


class SourcesError(Exception):
    """What cannot be resolved or fetched; the message says which."""


def fetch_json(url: str, opener: Callable = urllib.request.urlopen, tries: int = 4) -> dict:
    """A JSON answer from snapshot.debian.org, retried: it is slow and busy."""
    for attempt in range(tries):
        try:
            with opener(url, timeout=120) as response:
                return json.loads(response.read(8 << 20))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SourcesError(f"not in the snapshot: {url}") from exc
            error = exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = exc
        time.sleep(2 ** attempt)
    raise SourcesError(f"{url}: {error}")


def binaries(info: dict) -> List[Tuple[str, str]]:
    """Every Debian binary package of the image, its initrd and its kernel,
    with its version, once each."""
    found = {(p["name"], p["version"]) for p in info.get("packages", [])}
    found |= {(p["name"], p["version"]) for p in (info.get("initrd") or {}).get("packages", [])}
    found |= set((info.get("kernel") or {}).get("packages", {}).items())
    for name, version in found:
        if not NAME.fullmatch(name) or not VERSION.fullmatch(version):
            raise SourcesError(f"BUILD-INFO names an odd package: {name} {version}")
    return sorted(found)


def source_of(name: str, version: str, get: Callable[[str], dict]) -> Tuple[str, str]:
    answer = get(f"{SNAPSHOT}/mr/binary/{urllib.parse.quote(name)}/")
    for entry in answer.get("result", []):
        if entry.get("binary_version") == version:
            source, source_version = entry.get("source"), entry.get("version")
            if isinstance(source, str) and NAME.fullmatch(source) and isinstance(source_version, str) \
                    and VERSION.fullmatch(source_version):
                return source, source_version
    raise SourcesError(f"the snapshot has no source for {name} {version}")


def files_of(source: str, version: str, get: Callable[[str], dict]) -> List[Dict[str, object]]:
    """The source package's files (dsc, orig and debian tarballs), by name,
    with their SHA-1 and size; the same file listed by several archives once."""
    answer = get(f"{SNAPSHOT}/mr/package/{urllib.parse.quote(source)}/{urllib.parse.quote(version)}/srcfiles?fileinfo=1")
    listed: Dict[str, Dict[str, object]] = {}
    for digest, entries in (answer.get("fileinfo") or {}).items():
        if not DIGEST.fullmatch(digest):
            raise SourcesError(f"{source} {version}: odd digest {digest!r}")
        for entry in entries:
            name, size = entry.get("name"), entry.get("size")
            if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or not isinstance(size, int):
                raise SourcesError(f"{source} {version}: odd file {entry!r}")
            listed[name] = {"name": name, "sha1": digest, "size": size}
    if not any(str(name).endswith(".dsc") for name in listed):
        raise SourcesError(f"{source} {version}: no .dsc in the snapshot")
    return sorted(listed.values(), key=lambda f: str(f["name"]))


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(entry: Dict[str, object], directory: Path, opener: Callable = urllib.request.urlopen) -> bool:
    """One source file into `directory`, checked; False if it was there already."""
    target = directory / str(entry["name"])
    if target.is_file() and target.stat().st_size == entry["size"] and sha1_of(target) == entry["sha1"]:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix=".", suffix=".part")
    try:
        digest, size = hashlib.sha1(), 0
        with os.fdopen(handle, "wb") as sink, opener(f"{SNAPSHOT}/file/{entry['sha1']}", timeout=600) as response:
            for block in iter(lambda: response.read(CHUNK), b""):
                size += len(block)
                if size > int(entry["size"]):
                    raise SourcesError(f"{entry['name']}: larger than the snapshot says")
                digest.update(block)
                sink.write(block)
        if size != entry["size"] or digest.hexdigest() != entry["sha1"]:
            raise SourcesError(f"{entry['name']}: does not match its SHA-1")
        os.replace(temporary, target)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collect(info: dict, out: Path, fetch_files: bool = True, get: Optional[Callable[[str], dict]] = None,
            opener: Callable = urllib.request.urlopen, report: Callable[[str], None] = print) -> dict:
    """Resolve every package of `info` (a BUILD-INFO) to its source and, with
    `fetch_files`, download them under `out`; writes out/SOURCES.json."""
    get = get or (lambda url: fetch_json(url, opener))
    sources: Dict[Tuple[str, str], List[str]] = {}
    for name, version in binaries(info):
        sources.setdefault(source_of(name, version, get), []).append(f"{name} {version}")
    report(f"{len(sources)} source packages for {sum(len(b) for b in sources.values())} binary packages")
    records = []
    for (source, version), built in sorted(sources.items()):
        listed = files_of(source, version, get)
        if fetch_files:
            fetched = sum(download(entry, out / f"{source}_{version}", opener) for entry in listed)
            report(f"{source} {version}: {len(listed)} files" + (f", {fetched} fetched" if fetched else ", present"))
        records.append({"source": source, "version": version, "binaries": sorted(built), "files": listed})
    manifest = {"format": FORMAT, "build_id": info.get("build_id"), "snapshot": (info.get("debian") or {}).get("snapshot"),
                "sources": records}
    out.mkdir(parents=True, exist_ok=True)
    (out / "SOURCES.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
