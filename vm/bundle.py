"""The downloadable form of a MUN OS development image: a bundle, which
`./mun dev bundle` makes from a finished build and `./mun get` fetches,
verifies and installs, so that an image can be booted without building it.

A bundle is a directory, or the same files under one URL:

    release.json                the manifest (below)
    <image>.qcow2               the image as built (qcow2 with zstd clusters)
    BUILD-INFO.json             the build's record; the image is checked
                                against it again whenever a guest is made
    card-<name>.img.xz          ready Game Cards (xz), so that playing needs
                                no card tool
    LICENSE, *-OFL.txt          MUN OS's licence and those of the typefaces
                                compiled into MUN Shell

All files sit side by side, as a release's assets do.

The manifest (`format` mun-bundle/1) names the image's version, environment,
release flag and build id, and lists every file with its size and SHA-256.
File names are relative to the manifest, so a bundle can be served from any
directory or web location. Everything read from a bundle is untrusted until
checked: the manifest's fields and names are validated, each file is
refused as soon as it exceeds its declared size, and nothing is installed
before every file matches its digest. An installed download is a build like
any other (`.local/mun/builds/<name>/`, with ORIGIN.json saying where it came
from). A card that already exists is never replaced: it may hold saves.
"""

import contextlib
import hashlib
import json
import lzma
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Tuple

FORMAT = "mun-bundle/1"
MANIFEST = "release.json"
MANIFEST_LIMIT = 1 << 20                       # bytes; a manifest is a few kilobytes
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
CARD_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")
DIGEST = re.compile(r"[0-9a-f]{64}")
KINDS = ("image", "build-info", "card", "licence")
CHUNK = 1 << 20


class BundleError(Exception):
    """A bundle that cannot be made, read or trusted; the message says why."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------- making

def make(build: Path, destination: Path, cards: Dict[str, Tuple[Path, str]],
         licences: Tuple[Path, ...] = ()) -> Dict[str, Any]:
    """Write a bundle of `build` into `destination` (which must not exist):
    its image and BUILD-INFO.json, each card of `cards` (name -> (image,
    title)) compressed, and the `licences` texts. Returns the manifest."""
    info = json.loads((build / "BUILD-INFO.json").read_text())
    images = [name for name in info.get("artifacts", {}) if name.endswith(".qcow2")]
    if len(images) != 1:
        raise BundleError("BUILD-INFO does not name exactly one image")
    image = build / images[0]
    if sha256_file(image) != info["artifacts"][images[0]]["sha256"]:
        raise BundleError(f"{image} does not match its BUILD-INFO hash")
    if destination.exists():
        raise BundleError(f"{destination} exists")
    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    files: List[Dict[str, Any]] = []

    def add(path: Path, kind: str, **extra: Any) -> None:
        files.append({"name": path.relative_to(partial).as_posix(), "kind": kind, "size": path.stat().st_size,
                      "sha256": sha256_file(path), **extra})

    shutil.copy2(image, partial / image.name)
    add(partial / image.name, "image")
    shutil.copy2(build / "BUILD-INFO.json", partial / "BUILD-INFO.json")
    add(partial / "BUILD-INFO.json", "build-info")
    for licence in licences:
        shutil.copy2(licence, partial / licence.name)
        add(partial / licence.name, "licence")
    for name, (card, title) in sorted(cards.items()):
        if not CARD_NAME.fullmatch(name):
            raise BundleError(f"card name {name!r} is not a lab card name")
        packed = partial / f"card-{name}.img.xz"
        with card.open("rb") as source, lzma.open(packed, "wb", preset=6) as sink:
            shutil.copyfileobj(source, sink, CHUNK)
        add(packed, "card", card=name, title=title, image_size=card.stat().st_size,
            image_sha256=sha256_file(card))
    manifest = {
        "format": FORMAT,
        "name": info["name"], "version": info["version"], "environment": info["environment"],
        "release": info["release"], "build_id": info["build_id"],
        "source": {key: info.get("source", {}).get(key) for key in ("commit", "describe", "clean")},
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }
    (partial / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    partial.rename(destination)
    return manifest


# ------------------------------------------------------------------ reading

def check_manifest(data: Any) -> Dict[str, Any]:
    """The manifest's shape, or BundleError: every field `get` relies on."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BundleError(f"not a {FORMAT} manifest")
    for key in ("name", "version", "environment", "build_id"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise BundleError(f"manifest field {key} missing")
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}", data["build_id"]):
        raise BundleError("manifest build_id is not a build id")
    if not isinstance(data.get("release"), bool):
        raise BundleError("manifest field release missing")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise BundleError("manifest lists no files")
    seen = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise BundleError("manifest file entry is not an object")
        name, kind = entry.get("name"), entry.get("kind")
        if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or ".." in name or name in seen:
            raise BundleError(f"manifest file name not allowed: {name!r}")
        seen.add(name)
        if kind not in KINDS or (kind == "card") != name.startswith("card-"):
            raise BundleError(f"manifest file {name}: kind {kind!r} not allowed there")
        if not isinstance(entry.get("size"), int) or entry["size"] < 0:
            raise BundleError(f"manifest file {name}: no size")
        if not isinstance(entry.get("sha256"), str) or not DIGEST.fullmatch(entry["sha256"]):
            raise BundleError(f"manifest file {name}: no SHA-256")
        if kind == "image" and not name.endswith(".qcow2"):
            raise BundleError(f"manifest image {name} is not a qcow2 file")
        if kind == "licence" and not (name == "LICENSE" or name.endswith(".txt")):
            raise BundleError(f"manifest licence {name} is not a text file")
        if kind == "card":
            if not isinstance(entry.get("card"), str) or not CARD_NAME.fullmatch(entry["card"]) \
                    or name != f"card-{entry['card']}.img.xz":
                raise BundleError(f"manifest card {name}: its name does not match")
            if not isinstance(entry.get("image_size"), int) or not DIGEST.fullmatch(str(entry.get("image_sha256"))):
                raise BundleError(f"manifest card {name}: no size or SHA-256 of the card itself")
    kinds = [entry["kind"] for entry in files]
    if kinds.count("image") != 1 or kinds.count("build-info") != 1:
        raise BundleError("manifest must list exactly one image and one BUILD-INFO.json")
    return data


class Source:
    """Where a bundle's files come from: a local directory (or its
    release.json) or a web location (http or https)."""

    def __init__(self, location: str):
        self.location = location
        parsed = urllib.parse.urlparse(location)
        scheme = parsed.scheme.lower()
        if scheme in ("http", "https"):
            self.remote, self.insecure = True, scheme == "http"
            self.base = location if parsed.path.endswith(MANIFEST) else location.rstrip("/") + "/" + MANIFEST
        elif scheme in ("", "file") or len(scheme) == 1:        # one letter: a Windows drive
            self.remote, self.insecure = False, False
            path = Path(urllib.parse.unquote(parsed.path)) if scheme == "file" else Path(location)
            self.base = str(path if path.name == MANIFEST else path / MANIFEST)
        else:
            raise BundleError(f"unsupported location {location!r}: a directory, a release.json or an http(s) URL")

    def url(self, name: str) -> str:
        return urllib.parse.urljoin(self.base, name) if self.remote else str(Path(self.base).parent / name)

    def open(self, name: str, offset: int = 0) -> Tuple[BinaryIO, int]:
        """A stream of `name` from `offset`, and the offset it really starts
        at (0 when a server ignores the range asked for)."""
        if not self.remote:
            handle = open(self.url(name), "rb")
            handle.seek(offset)
            return handle, offset
        request = urllib.request.Request(self.url(name))
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        response = urllib.request.urlopen(request, timeout=60)
        return response, (offset if offset and response.status == 206 else 0)

    def manifest(self) -> Dict[str, Any]:
        stream, _ = self.open(MANIFEST)
        with stream:
            raw = stream.read(MANIFEST_LIMIT + 1)
        if len(raw) > MANIFEST_LIMIT:
            raise BundleError("manifest too large")
        try:
            return check_manifest(json.loads(raw))
        except ValueError as exc:
            raise BundleError(f"manifest is not JSON: {exc}") from exc


def fetch_file(source: Source, entry: Dict[str, Any], target: Path, report: Callable[[str], None]) -> None:
    """`entry` into `target`, resuming a partial download, and checked."""
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > entry["size"]:
        part.unlink()
        have = 0
    digest = hashlib.sha256()
    if have:
        with part.open("rb") as previous:
            for block in iter(lambda: previous.read(CHUNK), b""):
                digest.update(block)
    stream, start = source.open(entry["name"], have)
    if start != have:                              # the server sent the whole file again
        digest, have = hashlib.sha256(), 0
    last = time.monotonic()
    with stream, part.open("r+b" if have else "wb") as sink:
        sink.seek(have)
        sink.truncate()
        while True:
            block = stream.read(CHUNK)
            if not block:
                break
            have += len(block)
            if have > entry["size"]:
                raise BundleError(f"{entry['name']} is larger than the manifest says")
            digest.update(block)
            sink.write(block)
            if time.monotonic() - last > 2:
                report(f"  {entry['name']}: {have * 100 // max(1, entry['size'])} %")
                last = time.monotonic()
    if have != entry["size"] or digest.hexdigest() != entry["sha256"]:
        part.unlink()
        raise BundleError(f"{entry['name']} does not match the manifest (size or SHA-256); it was discarded")
    os.replace(part, target)


def install_card(packed: Path, entry: Dict[str, Any], cards_root: Path) -> Optional[Path]:
    """Unpack a card into cards_root unless one of that name exists (it may
    hold saves). Returns the new card, or None if it was left alone.

    The card is unpacked into a temporary file of its own, checked, and then
    published under its name with a hard link, which fails rather than
    replace a card that appeared meanwhile (another download, a card made by
    hand); where links are not available an exclusive create does the same."""
    destination = cards_root / f"{entry['card']}.img"
    if destination.exists() or destination.is_symlink():
        return None
    cards_root.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=str(cards_root), prefix=f".{entry['card']}.", suffix=".part")
    temporary = Path(name)
    try:
        digest, size = hashlib.sha256(), 0
        with os.fdopen(handle, "wb") as sink, lzma.open(packed, "rb") as source:
            for block in iter(lambda: source.read(CHUNK), b""):
                size += len(block)
                if size > entry["image_size"]:
                    raise BundleError(f"card {entry['card']} unpacks larger than the manifest says")
                digest.update(block)
                sink.write(block)
        if size != entry["image_size"] or digest.hexdigest() != entry["image_sha256"]:
            raise BundleError(f"card {entry['card']} does not match the manifest once unpacked")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return None
        except OSError:
            try:
                with open(destination, "xb") as sink, temporary.open("rb") as source:
                    shutil.copyfileobj(source, sink, CHUNK)
            except FileExistsError:
                return None
        return destination
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def default_name(manifest: Dict[str, Any]) -> str:
    """A build name for a download: d<month><day>-<hour><minute><second> of its build id."""
    build_id = manifest["build_id"]
    return f"d{build_id[4:8]}-{build_id[9:15]}"


def get(location: str, builds_root: Path, cards_root: Path, name: Optional[str] = None,
        report: Callable[[str], None] = print) -> Tuple[Path, List[Path]]:
    """Fetch the bundle at `location`, verify every file and install it as
    build `name`. Returns the build directory and the cards unpacked."""
    source = Source(location)
    if source.insecure:
        report("warning: plain http: the manifest is not protected in transit; use https except for a local test")
    manifest = source.manifest()
    name = name or default_name(manifest)
    final = builds_root / name
    if final.exists():
        try:
            installed = json.loads((final / "BUILD-INFO.json").read_text())["build_id"]
        except (OSError, ValueError, KeyError):
            installed = None
        if installed != manifest["build_id"]:
            raise BundleError(f"build {name} exists and is another image; choose another --name")
        report(f"build {name} is already this image ({manifest['build_id']})")
        return final, []
    report(f"{manifest['name']} {manifest['version']} ({manifest['environment']}, "
           f"release={str(manifest['release']).lower()}), build {manifest['build_id']}")
    staging = builds_root / f"{name}.download"
    for entry in manifest["files"]:
        target = staging / entry["name"]
        if target.exists() and target.stat().st_size == entry["size"] and sha256_file(target) == entry["sha256"]:
            continue
        report(f"downloading {entry['name']} ({entry['size'] / 1e6:.1f} MB) from {source.url(entry['name'])}")
        fetch_file(source, entry, target, report)
    info = json.loads((staging / "BUILD-INFO.json").read_text())
    image = next(entry for entry in manifest["files"] if entry["kind"] == "image")
    if info.get("build_id") != manifest["build_id"] or \
            info.get("artifacts", {}).get(image["name"], {}).get("sha256") != image["sha256"]:
        raise BundleError("the image and its BUILD-INFO.json do not belong together")
    cards = []
    for entry in manifest["files"]:
        if entry["kind"] == "card":
            installed = install_card(staging / entry["name"], entry, cards_root)
            if installed:
                cards.append(installed)
            else:
                report(f"card {entry['card']} exists; left as it is (it may hold saves)")
    result = builds_root / f"{name}.partial"
    if result.exists():
        shutil.rmtree(result)
    result.mkdir(parents=True)
    os.replace(staging / image["name"], result / image["name"])
    os.replace(staging / "BUILD-INFO.json", result / "BUILD-INFO.json")
    licences = [entry["name"] for entry in manifest["files"] if entry["kind"] == "licence"]
    if licences:
        (result / "licences").mkdir()
        for name in licences:
            os.replace(staging / name, result / "licences" / name)
    (result / "ORIGIN.json").write_text(json.dumps({
        "source": location, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "manifest": {key: manifest[key] for key in ("format", "build_id", "created") if key in manifest},
    }, indent=2) + "\n")
    result.rename(final)
    shutil.rmtree(staging, ignore_errors=True)
    return final, cards
