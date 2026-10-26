#!/usr/bin/env python3
"""A composition profile's own build settings (os/mkosi/mkosi.profiles/<name>/),
Python 3 standard library only.

Two things a profile may say beyond its mkosi configuration:

- The image format, MUN_IMAGE_FORMAT in its mkosi.conf ([Build] Environment=,
  beside MUN_ENVIRONMENT): `qcow2`, the default, the laboratory's guest
  image; or `raw`, a whole disk to write to a board's storage, compressed
  with xz.
- Pinned packages, in pinned-packages.json: .deb files from outside the
  Debian snapshot (a development profile for a board may take its kernel
  this way), each named by an https URL, its SHA-256, its package name and
  version, its licence and the public source it was built from.

The declaration and every download are untrusted input: the fields are
validated, only https is followed, a file is used only after it matches its
SHA-256, and its own control fields must name the declared package and
version. Without a declaration nothing is fetched and the build is as before.

    profiles.py format PROFILE_DIR        the image format, checked
    profiles.py check PROFILE_DIR         validate the format and the declaration (the host does, before a builder starts)
    profiles.py fetch PROFILE_DIR DEST    download and verify the pinned packages into DEST; their record on stdout
"""

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

FORMATS = ("qcow2", "raw")
DEFAULT_FORMAT = "qcow2"
DECLARATION = "pinned-packages.json"
DECLARATION_FORMAT = "mun-pinned-packages/1"
ENTRY_FIELDS = {"name", "version", "url", "sha256", "license", "source"}
# Debian's rules for package names and versions (Debian Policy 5.6.1, 5.6.12).
NAME = re.compile(r"[a-z0-9][a-z0-9.+-]+")
VERSION = re.compile(r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~-]*")
DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,127}")
# The local name of a download: one plain file name, never a path.
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,200}\.deb")
# The image's architecture (mkosi.conf Architecture=), and packages for any.
ARCHITECTURES = ("arm64", "all")
# A kernel package is tens of megabytes; this bounds what a misbehaving
# server can write into the builder.
SIZE_LIMIT = 1 << 30
CHUNK = 1 << 20


class ProfileError(Exception):
    """A profile setting, declaration or download that cannot be used; the message says why."""


def settings(profile: Path) -> Dict[str, List[str]]:
    """Every MUN_* value the profile's mkosi.conf sets, each with all the
    values it is given (a setting given twice is an error for its reader)."""
    found: Dict[str, List[str]] = {}
    text = (profile / "mkosi.conf").read_text()
    for key, value in re.findall(r"^\s*(MUN_[A-Z_]+)=(\S*)", text, flags=re.MULTILINE):
        found.setdefault(key, []).append(value)
    return found


def image_format(profile: Path) -> str:
    """The profile's MUN_IMAGE_FORMAT: qcow2 when it sets none."""
    values = settings(profile).get("MUN_IMAGE_FORMAT", [DEFAULT_FORMAT])
    if len(values) != 1 or values[0] not in FORMATS:
        raise ProfileError(f"{profile / 'mkosi.conf'}: MUN_IMAGE_FORMAT must be one of {', '.join(FORMATS)}, "
                           f"once (given {', '.join(map(repr, values))})")
    return values[0]


def https_url(value: Any, what: str) -> str:
    """`value` if it is a plain https URL: a host, no credentials, query or fragment."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ProfileError(f"{what}: an https URL is needed")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ProfileError(f"{what}: only https is allowed ({value!r})")
    if parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in value):
        raise ProfileError(f"{what}: a plain https URL without credentials, query or fragment is needed")
    return value


def file_name(url: str) -> str:
    """The file a pinned package is stored as: the URL's last path segment,
    which must be a plain .deb file name."""
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    if not FILE_NAME.fullmatch(name):
        raise ProfileError(f"{url}: the URL must end in a plain .deb file name, not {name!r}")
    return name


def check_entry(entry: Any, where: str) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise ProfileError(f"{where}: a package is not an object")
    missing, unknown = sorted(ENTRY_FIELDS - set(entry)), sorted(set(entry) - ENTRY_FIELDS)
    if missing:
        raise ProfileError(f"{where}: missing {', '.join(missing)}")
    if unknown:
        raise ProfileError(f"{where}: unknown {', '.join(unknown)}")
    name, version = entry["name"], entry["version"]
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 128:
        raise ProfileError(f"{where}: name is not a Debian package name: {name!r}")
    where = f"{where} ({name})"
    if not isinstance(version, str) or not VERSION.fullmatch(version) or len(version) > 128:
        raise ProfileError(f"{where}: version is not a Debian version: {version!r}")
    file_name(https_url(entry["url"], f"{where}: url"))
    if not isinstance(entry["sha256"], str) or not DIGEST.fullmatch(entry["sha256"]):
        raise ProfileError(f"{where}: sha256 must be 64 lowercase hexadecimal digits")
    if not isinstance(entry["license"], str) or not entry["license"].strip() or len(entry["license"]) > 256:
        raise ProfileError(f"{where}: state the package's licence")
    source = entry["source"]
    if not isinstance(source, dict) or set(source) not in ({"repository", "commit"}, {"repository", "tag"}):
        raise ProfileError(f"{where}: source must name its repository and one commit or tag")
    https_url(source["repository"], f"{where}: source repository")
    if "commit" in source and not (isinstance(source["commit"], str) and COMMIT.fullmatch(source["commit"])):
        raise ProfileError(f"{where}: source commit must be the full hexadecimal commit")
    if "tag" in source and not (isinstance(source["tag"], str) and TAG.fullmatch(source["tag"])
                                and ".." not in source["tag"]):
        raise ProfileError(f"{where}: source tag not allowed: {source['tag']!r}")
    return entry


def check_declaration(data: Any, where: str) -> List[Dict[str, Any]]:
    """The packages of a pinned-packages.json document, checked."""
    if not isinstance(data, dict) or data.get("format") != DECLARATION_FORMAT:
        raise ProfileError(f"{where}: not a {DECLARATION_FORMAT} declaration")
    unknown = set(data) - {"format", "packages", "_comment"}
    if unknown:
        raise ProfileError(f"{where}: unknown {', '.join(sorted(unknown))}")
    if not isinstance(data.get("_comment", ""), str):
        raise ProfileError(f"{where}: _comment must be text")
    packages = data.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ProfileError(f"{where}: packages must list at least one package (or remove the file)")
    entries = [check_entry(entry, f"{where}: package {index + 1}") for index, entry in enumerate(packages)]
    names, files = [entry["name"] for entry in entries], [file_name(entry["url"]) for entry in entries]
    for label, seen in (("package", names), ("file name", files)):
        twice = sorted({value for value in seen if seen.count(value) > 1})
        if twice:
            raise ProfileError(f"{where}: {label} given twice: {', '.join(twice)}")
    return entries


def pinned(profile: Path) -> List[Dict[str, Any]]:
    """The profile's pinned packages, checked; none without a declaration."""
    path = profile / DECLARATION
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ProfileError(f"{path}: {exc}") from exc
    return check_declaration(data, str(path))


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ProfileError(f"{req.full_url} redirects to {newurl}: only https is followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_opener() -> Callable:
    return urllib.request.build_opener(HttpsOnly).open


def download(url: str, target: Path, digest: str, opener: Optional[Callable] = None, tries: int = 3) -> None:
    """`url` into `target` only if it matches `digest`; a mismatch is never
    retried and nothing is left behind."""
    opener = opener or https_opener()
    error: Exception = ProfileError(f"{url}: not downloaded")
    for attempt in range(tries):
        handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".", suffix=".part")
        try:
            hasher, size = hashlib.sha256(), 0
            with os.fdopen(handle, "wb") as sink, opener(url, timeout=120) as response:
                for block in iter(lambda: response.read(CHUNK), b""):
                    size += len(block)
                    if size > SIZE_LIMIT:
                        raise ProfileError(f"{url}: larger than {SIZE_LIMIT >> 20} MiB")
                    hasher.update(block)
                    sink.write(block)
            if hasher.hexdigest() != digest:
                raise ProfileError(f"{url}: SHA-256 {hasher.hexdigest()} does not match the pinned {digest}")
            os.replace(temporary, target)
            return
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            error = exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if attempt + 1 < tries:
            time.sleep(2 ** attempt)
    raise ProfileError(f"{url}: download failed: {error}")


def deb_fields(path: Path, run: Callable = subprocess.run) -> Dict[str, str]:
    """Package, Version and Architecture from the .deb's own control file."""
    result = run(["dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
                 capture_output=True, text=True)
    if result.returncode:
        raise ProfileError(f"{path.name}: not a readable .deb: {result.stderr.strip()}")
    fields = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def fetch(profile: Path, destination: Path, opener: Optional[Callable] = None,
          run: Callable = subprocess.run) -> List[Dict[str, Any]]:
    """Every pinned package of `profile` into `destination`, verified; the
    record BUILD-INFO keeps of them."""
    records = []
    entries = pinned(profile)
    if entries:
        destination.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = destination / file_name(entry["url"])
        print(f"== pinned package {entry['name']} {entry['version']}: {entry['url']}", file=sys.stderr, flush=True)
        download(entry["url"], target, entry["sha256"], opener)
        fields = deb_fields(target, run)
        found = (fields.get("Package"), fields.get("Version"))
        if found != (entry["name"], entry["version"]):
            target.unlink()
            raise ProfileError(f"{target.name} is {found[0]} {found[1]}, not the declared "
                               f"{entry['name']} {entry['version']}")
        if fields.get("Architecture") not in ARCHITECTURES:
            target.unlink()
            raise ProfileError(f"{target.name} is for {fields.get('Architecture')}, not {' or '.join(ARCHITECTURES)}")
        records.append({"name": entry["name"], "version": entry["version"], "architecture": fields["Architecture"],
                        "url": entry["url"], "sha256": entry["sha256"], "size": target.stat().st_size,
                        "license": entry["license"], "source": dict(entry["source"])})
    return records


def main(argv: List[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "format":
            print(image_format(Path(argv[1])))
            return 0
        if len(argv) == 2 and argv[0] == "check":
            profile = Path(argv[1])
            print(f"profile {profile.name}: format {image_format(profile)}, {len(pinned(profile))} pinned package(s)")
            return 0
        if len(argv) == 3 and argv[0] == "fetch":
            records = fetch(Path(argv[1]), Path(argv[2]))
            json.dump(records, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0
    except (ProfileError, OSError) as exc:
        print(f"profiles: {exc}", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
