#!/usr/bin/python3
"""mun-reset: Settings › System › Reset console.

The console returns to how it was the first time it started: the player's
settings (the language included), the system's history and the shell's
caches go. Nothing of a Game Card, a game or a save is ever touched, and
nothing is reinstalled, downgraded or securely erased: the files are deleted,
not overwritten. Two steps, both as root:

  mun-reset request   asked by the shell through sudo (the exact rule is in
                      services/mun-shell/deploy/mun-shell.sudoers). Refuses
                      while a Game Card is in the slot, while a game runs or
                      starts, while the console is shutting down, or when the
                      card service or the launcher does not answer; otherwise
                      starts COMMIT_UNIT and waits for it.
  mun-reset commit    run by COMMIT_UNIT (deploy/mun-reset-commit.service),
                      outside the shell's sandbox: the shell's unit keeps the
                      file system read-only for it and for whatever it runs,
                      sudo included. Writes the mark (MARK) and restarts the
                      console; if the restart cannot be asked for, takes the
                      mark back.
  mun-reset apply     at the next boot (deploy/mun-reset.service), before the
                      system journal moves to the disk and before the
                      console's services start: deletes what PLAN lists, and
                      nothing else, then the mark. Every step can run again: a
                      boot cut short leaves the mark, and the next boot
                      finishes the reset.

PLAN is the table the README gives; tests/test_reset.py checks the two
agree. A path in PLAN that is a symbolic link is removed as a link, never
followed. Standard library only; Python 3.9 or later.
"""

import argparse
import glob
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# What the reset deletes, relative to the root: (path or pattern, how).
#   file      the file goes;
#   contents  what the directory holds goes, the directory stays (systemd
#             made it for a service: StateDirectory, CacheDirectory);
#   truncate  the file stays, empty (its owner and mode stay).
PLAN: Tuple[Tuple[str, str], ...] = (
    # The player's settings, the language included (MUN Shell).
    ("var/lib/mun-shell/settings.ini", "file"),
    # A write of them cut short, and their lock (QSettings).
    ("var/lib/mun-shell/settings.ini.*", "file"),
    # The shell's font cache; fontconfig makes it again.
    ("var/cache/mun-shell", "contents"),
    # The system's history: which cards and games were used, errors.
    ("var/log/journal", "contents"),
    # The record of boots and sessions.
    ("var/log/wtmp", "truncate"),
    ("var/log/btmp", "truncate"),
    ("var/log/lastlog", "truncate"),
)
MARK = "var/lib/mun-reset/pending"
FORMAT = "mun-reset/1"
COMMIT_UNIT = "mun-reset-commit.service"

CARDD_SOCKET = "/run/mun/cardd.sock"
LAUNCHD_SOCKET = "/run/mun/launchd.sock"
ANSWER_TIMEOUT = 5.0

# Exit statuses of `request`, which the shell words for the player.
EXIT_CARD = 65        # a Game Card is in the slot
EXIT_GAME = 66        # the launcher is not idle: a game is prepared, starts, runs, saves or stops
EXIT_STOPPING = 67    # the console is shutting down
EXIT_NO_ANSWER = 68   # the card service or the launcher did not answer
EXIT_USAGE = 64


class Refused(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ----------------------------------------------------------------- request

def snapshot(path: str, timeout: float = ANSWER_TIMEOUT) -> dict:
    """The first snapshot a service sends on its socket (one JSON object per
    line). Raises OSError or ValueError when there is none."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        buffer = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = sock.recv(65536)
            if not data:
                break
            buffer += data
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                message = json.loads(line)
                if isinstance(message, dict) and message.get("type") == "snapshot":
                    return message
            if len(buffer) > 1 << 20:
                break
    raise ValueError(f"no snapshot on {path}")


def refusal(cardd: str = CARDD_SOCKET, launchd: str = LAUNCHD_SOCKET, system_state: Optional[str] = None) -> None:
    """Raises Refused when the reset may not start now."""
    if system_state is None:
        system_state = subprocess.run(["systemctl", "is-system-running"], capture_output=True, text=True).stdout.strip()
    if system_state == "stopping":
        raise Refused(EXIT_STOPPING, "the console is shutting down")
    try:
        cards = snapshot(cardd).get("cards")
    except (OSError, ValueError) as exc:
        raise Refused(EXIT_NO_ANSWER, f"the card service did not answer: {exc}")
    if not isinstance(cards, list):
        raise Refused(EXIT_NO_ANSWER, "the card service's snapshot lists no cards")
    # Any card the service knows of, whatever its state (being read, valid,
    # not valid, released but not yet removed): the slot must be empty.
    if cards:
        raise Refused(EXIT_CARD, "a Game Card is in the slot")
    try:
        state = snapshot(launchd).get("state")
    except (OSError, ValueError) as exc:
        raise Refused(EXIT_NO_ANSWER, f"the launcher did not answer: {exc}")
    if state != "idle":
        raise Refused(EXIT_GAME, f"the launcher is {state!r}, not idle")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_mark(root: Path, now: Optional[float] = None) -> Path:
    """Writes the mark whole or not at all: a temporary file, synced, renamed
    over the mark, the directory synced."""
    mark = root / MARK
    mark.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = json.dumps({"format": FORMAT,
                       "requested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}) + "\n"
    temporary = mark.with_name(mark.name + ".new")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, body.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(str(temporary), str(mark))
    fsync_directory(mark.parent)
    return mark


def request() -> int:
    try:
        refusal()
    except Refused as refused:
        print(f"mun-reset: refused: {refused.reason}", file=sys.stderr)
        return refused.status
    committed = subprocess.run(["systemctl", "start", COMMIT_UNIT], capture_output=True, text=True)
    if committed.returncode != 0:
        print(f"mun-reset: the reset could not be marked: {committed.stderr.strip()}", file=sys.stderr)
        return 1
    print("mun-reset: the console resets as it restarts")
    return 0


def commit(root: Path = Path("/")) -> int:
    mark = write_mark(root)
    # No restart, no reset: a mark left after any failure here would reset
    # the console at some later boot the player did not ask for.
    try:
        rebooting = subprocess.run(["systemctl", "reboot"], capture_output=True, text=True)
        failure = None if rebooting.returncode == 0 else rebooting.stderr.strip()
    except Exception as exc:   # noqa: BLE001 (whatever it was, the mark goes back)
        failure = str(exc)
    if failure is None:
        return 0
    if os.path.lexists(mark):
        os.unlink(mark)
    fsync_directory(mark.parent)
    print(f"mun-reset: the console could not restart: {failure}", file=sys.stderr)
    return 1


# ------------------------------------------------------------------- apply

def inside(root: Path, path: Path) -> bool:
    """`path` names a place under `root`, its parent directories followed
    (not the path itself)."""
    try:
        Path(os.path.realpath(path.parent)).relative_to(os.path.realpath(root))
    except ValueError:
        return False
    return True


def remove(path: Path) -> bool:
    """Deletes a file or a link (never what a link points to); a directory
    with what it holds. False when there was nothing."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)
    return True


def truncate(path: Path) -> bool:
    """Empties a regular file in place; a link or anything else is left."""
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, OSError):
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return False
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def targets(root: Path) -> List[Tuple[Path, str]]:
    """PLAN resolved under `root`: every path apply() may act on."""
    found = []
    for pattern, how in PLAN:
        if how == "contents":
            directory = root / pattern
            if directory.is_dir() and not directory.is_symlink():
                found += [(directory / name, "file") for name in sorted(os.listdir(directory))]
        elif any(c in pattern for c in "*?["):
            found += [(Path(p), how) for p in sorted(glob.glob(str(root / pattern)))]
        else:
            found.append((root / pattern, how))
    return [(path, how) for path, how in found if inside(root, path)]


def say(line: str) -> None:
    # Each line as it happens: under systemd stdout is a pipe, which Python
    # would otherwise buffer to the end, and the journal's times (and what a
    # reset cut short had done) would be lost.
    print(line, flush=True)


def apply(root: Path = Path("/"), log=say) -> bool:
    """Carries out a pending reset; False when none was asked for."""
    mark = root / MARK
    if not os.path.lexists(mark):
        return False
    touched = set()
    for path, how in targets(root):
        done = truncate(path) if how == "truncate" else remove(path)
        if done:
            log(f"mun-reset: {'emptied' if how == 'truncate' else 'deleted'} /{path.relative_to(root)}")
            touched.add(path.parent)
    for directory in sorted(touched):
        if directory.is_dir():
            fsync_directory(directory)
    # Last: until the mark goes, the next boot does all of it again.
    os.unlink(mark)
    fsync_directory(mark.parent)
    log("mun-reset: the console is as it was the first time it started")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mun-reset", description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("request", "commit", "apply"))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("mun-reset: must run as root", file=sys.stderr)
        return EXIT_USAGE
    # One line for the shell to show, never a traceback.
    try:
        if args.step == "request":
            return request()
        if args.step == "commit":
            return commit()
        apply()
        return 0
    except Exception as exc:   # noqa: BLE001 (any failure is reported, nothing half done is hidden)
        print(f"mun-reset: {args.step} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
