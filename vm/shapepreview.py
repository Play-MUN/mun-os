"""The laboratory's MUN Shape preview: a package folder, seen in the real
console (host side of `./mun dev shape`).

    ./mun dev shape DIR [--guest NAME] [--build BUILD] [--base CARD] [--watch] [--window]

The preview makes a disposable Game Card from the folder and inserts it in a
laboratory console (an image guest, `shape` by default), where the card
service exports the package and MUN Shell shows it: the system itself, not a
mock. By default the card is MUN Collect, from the guest's build, with the
package; `--base CARD` takes a copy of one of the cards in .local/gamecards/
instead (that card is only read). With `--watch`, every change in the folder
is checked and, once the console may take it, the card is made again and put
in place of the previous one.

What it keeps to:

- Disposable cards only, known by what it made, not by their names. Its
  cards are `pv0-<guest>` and `pv1-<guest>` in .local/gamecards/. When it
  makes one it records the new file's identity (device, inode, size and,
  where the host has it, birth time) and the card's identifier in
  .local/gamecards/.shape-preview/; only a file that is still that one is
  the preview's to unplug or remake, and only while it still holds that
  card is it the preview's to delete. A file at those names it did not
  make (a card of yours, a copy put there, another tool's) is never
  written, detached, replaced or deleted: the preview refuses to start, or
  waits, and says which file it is. It never touches any other card, and
  it does not start in a guest that has another card inserted.
- One preview per console. A preview holds its guest's lock
  (.local/gamecards/.shape-preview/<guest>.lock) while it runs; a second
  one for the same guest is refused. Previews in different guests have
  different cards and records.
- Each insertion is its own. A change is a new card inserted after the
  previous one has left: the console sees one insertion leave and a new one
  arrive, with the package exported for that insertion only, never one
  insertion's package changed under it.
- The console's rules. The previous card leaves by the safe removal path
  (the console releases it, cancelling a copy still in progress, and only
  then is it unplugged), never abruptly. While a game is being played the
  new package waits for it to end; a release the console refuses is tried
  again later. *Eject safely* in the console takes the card out as a
  player's hand would; the next change inserts the new one.
- A folder the console would not use at all is reported and leaves the card
  in place; one it would use in part is shown, as a console shows it.
"""

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import munvm as vm

sys.path.insert(0, str(vm.REPO_ROOT / "tools" / "mun-card"))
from mun_card import cli as card_cli  # noqa: E402
from mun_card import image as card_image  # noqa: E402
from mun_card import shape as shape_rules  # noqa: E402
from mun_card import shapetools  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from mun_card.source import DebugfsSource, DirectorySource  # noqa: E402
from mun_card.validate import validate_card  # noqa: E402

CARD_TOOL = vm.REPO_ROOT / "tools" / "mun-card" / "mun-card"
PREVIEW_ID = "mun.shapepreview"
POLL_SECONDS = 1.0
SETTLE_SECONDS = 1.0     # a change is taken once the folder has been still this long
SHELL_GRACE_SECONDS = 3.0   # the shell's connection to the card service, once it is running

# In the guest, through qemu-ga: the launcher's state from its first message
# (a snapshot): "idle" when no game is being played.
LAUNCHER_STATE = r"""python3 - <<'EOF'
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(10)
    sock.connect("/run/mun/launchd.sock")
    buffer = b""
    while b"\n" not in buffer:
        data = sock.recv(65536)
        if not data:
            break
        buffer += data
print(json.loads(buffer.split(b"\n")[0] or b"{}").get("state", ""))
EOF"""


def card_names(guest: str) -> List[str]:
    """The preview's two cards for a guest (the one in, the next one). A card
    name has at most 16 characters; a guest name too long for `pvN-<guest>`
    keeps its start and a digest of the whole, so two guests never share
    cards."""
    names = []
    for index in (0, 1):
        name = f"pv{index}-{guest}"
        if len(name) > 16:
            name = f"pv{index}-{guest[:7]}-{hashlib.sha256(guest.encode()).hexdigest()[:4]}"
        vm.card_serial(name)
        names.append(name)
    return names


def records_dir() -> Path:
    """Where the preview records the cards it made and holds its locks."""
    return vm.CARD_ROOT / ".shape-preview"


def file_identity(info: os.stat_result) -> Dict[str, Optional[float]]:
    """What tells one file from another at the same name: a copy, a move or a
    new file put there has another inode, or another device. The size of a
    card image does not change while a console writes saves into it; the
    birth time, where the host keeps one, does not change at all."""
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "birth": getattr(info, "st_birthtime", None)}


class NotOurs(vm.LabError):
    """A file at one of the preview's card names that the preview did not make."""


def folder_digest(folder: Path) -> str:
    """What the folder holds, by name, size and modification time: a change
    of any of them is a change to preview. Hidden files are left out."""
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts):
            continue
        info = os.lstat(path)
        digest.update(f"{relative.as_posix()}\0{info.st_mode}\0{info.st_size}\0{info.st_mtime_ns}\n".encode())
    return digest.hexdigest()


class Preview:
    def __init__(self, folder: Path, guest: str, build_dir: Path, base: Optional[Path], title: str,
                 say: Callable[[str], None] = lambda text: print(f"[shape] {text}", flush=True)):
        self.folder, self.guest, self.build_dir, self.base, self.title = folder, guest, build_dir, base, title
        self.names = card_names(guest)
        self.say = say
        self.game = build_dir / "games" / "mun-collect" / "mun-collect"
        if base is None and not self.game.is_file():
            fallback = vm.CARD_ROOT / "collect.img"
            if not fallback.is_file():
                raise vm.LabError(f"no MUN Collect in build {build_dir.name} and no .local/gamecards/collect.img: "
                                  "give a card to start from with --base")
            self.base = fallback
        self._told: set = set()
        self._lock: Optional[int] = None
        self.claim()

    def tell_once(self, key: str, text: str) -> None:
        if key not in self._told:
            self._told.add(key)
            self.say(text)

    # ------------------------------------------------------------ one preview per console

    def claim(self) -> None:
        """Hold this guest's preview lock until `release` (or the process
        ends): two previews in one console would take each other's cards."""
        records_dir().mkdir(parents=True, exist_ok=True)
        fd = os.open(records_dir() / f"{self.guest}.lock", os.O_RDWR | os.O_CREAT, 0o644)
        if not vm.host.try_lock(vm.HOST, fd):
            os.close(fd)
            raise vm.LabError(f"another preview is running in guest {self.guest}: one preview per console "
                              "(end that one, or give this one another --guest)")
        self._lock = fd

    def release(self) -> None:
        if self._lock is not None:
            vm.host.unlock(vm.HOST, self._lock)
            os.close(self._lock)
            self._lock = None

    # ------------------------------------------------------------ which cards are the preview's

    def record_path(self, name: str) -> Path:
        return records_dir() / f"{name}.json"

    def own(self, name: str) -> bool:
        """Whether the file at `name` is the one this guest's preview made."""
        try:
            record = json.loads(self.record_path(name).read_text())
            info = os.lstat(vm.CARD_ROOT / f"{name}.img")
        except (OSError, ValueError):
            return False
        return (stat.S_ISREG(info.st_mode) and record.get("guest") == self.guest
                and record.get("identity") == file_identity(info))

    def held_elsewhere(self, name: str) -> Optional[str]:
        """The instance (other than this guest) that holds `name`, if any."""
        holder = vm.load_registry().get(name) or {}
        if holder.get("instance") not in (None, self.guest) and vm.pid_alive(int(holder.get("pid", 0) or 0)):
            return str(holder["instance"])
        return None

    def not_ours(self, name: str) -> str:
        return (f"{vm.CARD_ROOT / (name + '.img')} was not made by this preview, so it is left as it is: "
                f"rename or move it, or give the preview another --guest")

    def forget(self, name: str) -> None:
        try:
            self.record_path(name).unlink()
        except FileNotFoundError:
            pass

    def remove_own(self, name: str) -> bool:
        """Delete the preview's card `name`: only the very file it made. The
        check is repeated on the file itself after it has been moved aside, so
        a file put at the name in between is put back, unchanged."""
        if not self.own(name):
            return False
        path = vm.CARD_ROOT / f"{name}.img"
        aside = records_dir() / f"gone-{name}-{secrets.token_hex(4)}.img"
        try:
            os.rename(path, aside)
        except FileNotFoundError:
            self.forget(name)
            return True
        record = json.loads(self.record_path(name).read_text())
        if file_identity(os.lstat(aside)) == record.get("identity") and self.same_card(aside, record):
            os.unlink(aside)
            self.forget(name)
            return True
        self.forget(name)                 # whatever it is, it is not the preview's card any more
        try:
            os.link(aside, path)          # never over a file that appeared since
        except FileExistsError:
            self.say(f"a file put at {path} while the preview removed its own is kept as {aside}")
            return False
        os.unlink(aside)
        return False

    @staticmethod
    def same_card(path: Path, record: dict) -> bool:
        """Whether the image at `path` is still the card the preview made: a
        card copied into the preview's file (`cp` over it keeps the inode)
        carries its own identifier, and is kept."""
        try:
            return validate_card(DebugfsSource(path, card_image.find_tool("debugfs"))).id == record.get("card")
        except (CardError, OSError, ValueError):
            return False

    def attached(self) -> List[str]:
        return sorted(vm.load_state().get("cards", {}))

    def ours_in(self) -> Optional[str]:
        return next((name for name in self.attached() if name in self.names and self.own(name)), None)

    def foreign(self) -> List[str]:
        return [name for name in self.attached() if not (name in self.names and self.own(name))]

    def tidy(self) -> None:
        """At the start: drop records whose file is gone or is another one now,
        and the preview's half-made cards left by an interrupted run."""
        for name in self.names:
            if self.record_path(name).exists() and not self.own(name):
                self.forget(name)
            for left in records_dir().glob(f"new-{name}-*.img"):
                left.unlink()

    # ------------------------------------------------------------ making a card

    def make_card(self, name: str):
        """Make the preview card `name` from the folder; returns the console's
        verdict on the package as it is on the card. The card is made aside and
        put at its name only if nothing is there: an earlier card of the
        preview's own is removed first, any other file is refused (NotOurs)."""
        destination = vm.card_path(name)
        if name in self.attached():
            raise vm.LabError(f"{name} is inserted; the preview makes the other card")
        holder = self.held_elsewhere(name)
        if holder:
            raise NotOurs(f"{destination} is inserted in guest {holder}; it is left as it is")
        if os.path.lexists(destination) and not self.remove_own(name):
            raise NotOurs(self.not_ours(name))
        made = records_dir() / f"new-{name}-{secrets.token_hex(4)}.img"
        try:
            if self.base is not None:
                base_info = validate_card(DebugfsSource(self.base, card_image.find_tool("debugfs")))
                shapetools.clone_image(self.base, made)
                with tempfile.TemporaryDirectory(prefix="mun-shape-preview-") as staging:
                    shapetools.stage_package(self.folder, Path(staging) / shape_rules.PACKAGE_DIR)
                    shapetools.replace_package(made, base_info.root, Path(staging) / shape_rules.PACKAGE_DIR)
            else:
                result = subprocess.run([str(CARD_TOOL), "create", str(made), "--variant", "game",
                                         "--game", str(self.game), "--title", self.title, "--id", PREVIEW_ID,
                                         "--shape", str(self.folder), "--shape-partial"],
                                        capture_output=True, text=True)
                if result.returncode != 0:
                    raise vm.LabError(f"the preview card was not made: {(result.stderr or result.stdout).strip()}")
            card = validate_card(DebugfsSource(made, card_image.find_tool("debugfs"))).id
            # Recorded before it is published: a run interrupted in between
            # leaves a record of a file that is not at the name, which `tidy`
            # drops, never an unrecorded card of the preview's at the name.
            vm.write_json_atomically(self.record_path(name), {
                "guest": self.guest, "name": name, "card": card, "made": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "identity": file_identity(os.lstat(made))})
            try:
                os.link(made, destination)    # refused if anything appeared at the name meanwhile
            except FileExistsError:
                self.forget(name)
                raise NotOurs(self.not_ours(name)) from None
        finally:
            try:
                made.unlink()
            except FileNotFoundError:
                pass
        source = DebugfsSource(destination, card_image.find_tool("debugfs"))
        info = validate_card(source)
        return card_cli.shape_summary(shape_rules.check_card(source, info.root))

    def report(self, name: str, summary: dict) -> None:
        state = summary["state"]
        self.say(f"{name}: {card_cli._STATE_WORDS[state]}")
        for block, item in summary.get("blocks", {}).items():
            if item["state"] == "dropped":
                self.say(f"  {block} dropped [{item['code']}] {item['message']}"
                         + (f" ({item['where']})" if item.get("where") else ""))
        for note in summary.get("notes", []):
            self.say(f"  [{note['code']}] {note['message']}" + (f" ({note['where']})" if note.get("where") else ""))

    def delete_unused(self) -> None:
        """Delete the preview's own cards that no console holds; any other file
        at its names stays."""
        inserted = set(self.attached()) if vm.read_pid() is not None else set()
        for name in self.names:
            if name not in inserted and not self.held_elsewhere(name):
                self.remove_own(name)

    # ------------------------------------------------------------ the console

    def game_running(self) -> bool:
        state = vm.guest_command(LAUNCHER_STATE, timeout=30).stdout.strip()
        return state != "idle"

    def insert(self, name: str) -> None:
        self.report(name, self.make_card(name))
        vm.cmd_card_attach(argparse.Namespace(name=name))
        self.say(f"{name} inserted")

    def change(self) -> bool:
        """Take the folder as it is now. True when it is taken (or need not
        be), False to try again later."""
        check = shape_rules.check_package(DirectorySource(self.folder))
        if check.state == "unused":
            self.say("the folder's package would not be used: the card in the console stays")
            for note in check.notes:
                self.say(f"  [{note.code}] {note.message}" + (f" — {note.detail}" if note.detail else ""))
            return True
        if self.game_running():
            self.tell_once("game", "a game is being played: the new package waits for it to end")
            return False
        current = self.ours_in()
        following = next(name for name in self.names if name != current)
        try:
            summary = self.make_card(following)
        except NotOurs as exc:
            # Nothing is touched; the change is taken once the file is gone.
            self.tell_once(f"not-ours-{following}", f"the change waits: {exc}")
            return False
        except (vm.LabError, CardError) as exc:
            self.say(f"the preview card was not made, the card in the console stays: {exc}")
            return True
        if current is not None:
            try:
                vm.detach_card(current)           # the safe removal: released by the console first
            except vm.LabError as exc:
                self.tell_once(f"release-{current}", f"{current} not released yet ({exc}); trying again")
                return False
            self.say(f"{current} released and removed")
        self.report(following, summary)
        vm.cmd_card_attach(argparse.Namespace(name=following))
        self.say(f"{following} inserted")
        self._told.clear()
        self.delete_unused()
        return True

    def wait_for_console(self, stop: threading.Event) -> bool:
        """Until the console is up and its shell is running: a card inserted
        before the shell watches is one it finds at start, without the
        arrival a player sees (its transition and cue)."""
        while not stop.is_set():
            if vm.read_pid() is not None and vm.guest_ready():
                break
            stop.wait(2)
        while not stop.is_set():
            if vm.guest_command("systemctl is-active mun-shell", timeout=30).stdout.strip() == "active":
                stop.wait(SHELL_GRACE_SECONDS)
                return not stop.is_set()
            stop.wait(2)
        return False

    def run(self, stop: threading.Event, watch: bool, follow: bool) -> None:
        """Insert the folder's card and, with `watch`, follow the folder until
        `stop`; with `follow`, keep unplugging what the console releases."""
        if not self.wait_for_console(stop):
            return
        self.tidy()
        foreign = self.foreign()
        if foreign:
            self.say(f"guest {self.guest} has {', '.join(foreign)} inserted: the preview uses a console with no "
                     "other card (remove it, or give another --guest)")
            return
        strangers = [name for name in self.names
                     if os.path.lexists(vm.CARD_ROOT / f"{name}.img") and not self.own(name)]
        if strangers:
            for name in strangers:
                self.say(self.not_ours(name))
            return
        self.delete_unused()
        current = self.ours_in()
        if current is None:
            self.insert(self.names[0])
        else:
            self.change()
        if not watch and not follow:
            return
        seen = folder_digest(self.folder)
        pending_since: Optional[float] = None
        while not stop.is_set():
            vm.reconcile_released(announce=self.say)
            if watch:
                now_seen = folder_digest(self.folder)
                if now_seen != seen:
                    seen, pending_since = now_seen, time.monotonic()
                if pending_since is not None and time.monotonic() - pending_since >= SETTLE_SECONDS:
                    if self.change():
                        pending_since = None
            stop.wait(POLL_SECONDS)

    def finish(self) -> None:
        """At the end: the card out the safe way while the console runs (not
        during a game), the preview's own cards deleted, its lock released."""
        try:
            if vm.read_pid() is not None:
                current = self.ours_in()
                if current is not None:
                    try:
                        if self.game_running():
                            self.say(f"a game is being played: {current} stays in; take it out with "
                                     f"./mun dev vm {self.guest} card-detach {current}")
                            return
                        vm.detach_card(current)
                        self.say(f"{current} released and removed")
                    except vm.LabError as exc:
                        self.say(f"{current} stays in: {exc}")
                        return
            else:
                vm.clear_cards()
            self.delete_unused()
        finally:
            self.release()
