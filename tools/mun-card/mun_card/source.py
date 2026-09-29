"""Where a card's bytes come from: a mounted directory or an unmounted image.

Both sources answer the same questions so the validator never touches the
filesystem directly. Paths are card-relative, already checked for safety by
the validator; sources still refuse to follow symlinks on any component.
"""

import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

from .errors import CardError


class EntryInfo:
    __slots__ = ("kind", "size")

    def __init__(self, kind: str, size: int):
        self.kind = kind    # "file" | "dir" | "symlink" | "other"
        self.size = size


class DirectorySource:
    """A card mounted (or staged) at `root`. Never follows symlinks."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def stat(self, relative: str) -> Optional[EntryInfo]:
        path = self.root
        for part in PurePosixPath(relative).parts:
            path = path / part
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise CardError("source_unreadable", "No se pudo leer la tarjeta", str(exc))
            if stat.S_ISLNK(info.st_mode):
                return EntryInfo("symlink", 0)
        if stat.S_ISDIR(info.st_mode):
            return EntryInfo("dir", 0)
        if stat.S_ISREG(info.st_mode):
            return EntryInfo("file", info.st_size)
        return EntryInfo("other", 0)

    def read(self, relative: str, limit: int) -> bytes:
        path = self.root / PurePosixPath(relative)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise CardError("source_unreadable", "No se pudo leer la tarjeta", f"{relative}: {exc}")
        with os.fdopen(fd, "rb") as handle:
            return handle.read(limit + 1)


class DebugfsSource:
    """Reads an ext4 image without mounting it, through e2fsprogs' `debugfs`.

    Used by the host inspection tool, which never mounts the card (macOS
    cannot mount ext4, and mounting needs root elsewhere). Each call is a
    short-lived read-only process on the image file.
    """

    _TYPE = re.compile(r"Type:\s+(\w+)")
    _SIZE = re.compile(r"Size:\s+(\d+)")

    def __init__(self, image: Path, debugfs: str):
        self.image = Path(image)
        self.debugfs = debugfs

    def _run(self, request: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.debugfs, "-R", request, str(self.image)],
                              capture_output=True, check=False)

    def stat(self, relative: str) -> Optional[EntryInfo]:
        # Check every component so a symlinked directory is caught, as the
        # in-console DirectorySource would catch it.
        prefix = ""
        for part in PurePosixPath(relative).parts:
            prefix = f"{prefix}/{part}"
            result = self._run(f'stat "{prefix}"')
            text = result.stdout.decode("utf-8", "replace")
            if "File not found" in result.stderr.decode("utf-8", "replace") or not text.strip():
                return None
            kind = self._TYPE.search(text)
            kind_name = kind.group(1).lower() if kind else "other"
            if kind_name == "symlink":
                return EntryInfo("symlink", 0)
        size = self._SIZE.search(text)
        mapping = {"regular": "file", "directory": "dir"}
        return EntryInfo(mapping.get(kind_name, "other"), int(size.group(1)) if size else 0)

    def read(self, relative: str, limit: int) -> bytes:
        result = self._run(f'cat "/{relative}"')
        if result.returncode != 0:
            raise CardError("source_unreadable", "No se pudo leer la tarjeta",
                            result.stderr.decode("utf-8", "replace").strip())
        return result.stdout[: limit + 1]

    _LS = re.compile(r"^\s*(\d+)\s+(\d+)\s+\((\d+)\)\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+(.*)$")

    def list(self, relative: str = "") -> List[Tuple[str, str, int]]:
        """Entries of one directory as (name, kind, size); `.`/`..`/lost+found omitted."""
        result = self._run(f'ls -l "/{relative}"' if relative else "ls -l /")
        if result.returncode != 0:
            raise CardError("source_unreadable", "No se pudo listar la tarjeta",
                            result.stderr.decode("utf-8", "replace").strip())
        entries: List[Tuple[str, str, int]] = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            match = self._LS.match(line)
            if not match:
                continue
            mode, size, name = int(match.group(2), 8), int(match.group(6)), match.group(7).strip()
            if name in (".", "..", "lost+found"):
                continue
            kind = {0o040000: "dir", 0o100000: "file", 0o120000: "symlink"}.get(mode & 0o170000, "other")
            entries.append((name, kind, size))
        return entries

    def entries(self) -> List[Tuple[str, str, int]]:
        """Every entry on the image, of every kind, as (relative path, kind,
        size), sorted; symlinked directories are listed, not entered."""
        found: List[Tuple[str, str, int]] = []
        pending = [""]
        while pending:
            directory = pending.pop()
            for name, kind, size in self.list(directory):
                path = f"{directory}/{name}" if directory else name
                found.append((path, kind, size))
                if kind == "dir":
                    pending.append(path)
        return sorted(found)

    def walk(self) -> List[Tuple[str, int]]:
        """Every regular file on the image as (relative path, size), sorted."""
        files: List[Tuple[str, int]] = []
        pending = [""]
        while pending:
            directory = pending.pop()
            for name, kind, size in self.list(directory):
                path = f"{directory}/{name}" if directory else name
                if kind == "dir":
                    pending.append(path)
                elif kind == "file":
                    files.append((path, size))
        return sorted(files)
