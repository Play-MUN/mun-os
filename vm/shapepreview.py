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

- Disposable cards only. Its cards are `pv0-<guest>` and `pv1-<guest>` in
  .local/gamecards/, made and deleted by it; it never writes, detaches or
  deletes any other card, and it does not start in a guest that has another
  card inserted.
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
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

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
    """The preview's two cards for a guest (the one in, the next one)."""
    names = [f"pv{index}-{guest}"[:16].rstrip("-") for index in (0, 1)]
    for name in names:
        vm.card_serial(name)
    return names


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

    def tell_once(self, key: str, text: str) -> None:
        if key not in self._told:
            self._told.add(key)
            self.say(text)

    # ------------------------------------------------------------ the cards

    def attached(self) -> List[str]:
        return sorted(vm.load_state().get("cards", {}))

    def ours_in(self) -> Optional[str]:
        return next((name for name in self.attached() if name in self.names), None)

    def foreign(self) -> List[str]:
        return [name for name in self.attached() if name not in self.names]

    def make_card(self, name: str):
        """Make the preview card `name` from the folder; returns the console's
        verdict on the package as it is on the card."""
        destination = vm.card_path(name)
        if name in self.attached():
            raise vm.LabError(f"{name} is inserted; the preview makes the other card")
        if destination.exists():
            destination.unlink()          # a card of the preview's own, not inserted
        if self.base is not None:
            base_info = validate_card(DebugfsSource(self.base, card_image.find_tool("debugfs")))
            shapetools.clone_image(self.base, destination)
            with tempfile.TemporaryDirectory(prefix="mun-shape-preview-") as staging:
                shapetools.stage_package(self.folder, Path(staging) / shape_rules.PACKAGE_DIR)
                shapetools.replace_package(destination, base_info.root, Path(staging) / shape_rules.PACKAGE_DIR)
        else:
            made = subprocess.run([str(CARD_TOOL), "create", str(destination), "--variant", "game",
                                   "--game", str(self.game), "--title", self.title, "--id", PREVIEW_ID,
                                   "--shape", str(self.folder), "--shape-partial"], capture_output=True, text=True)
            if made.returncode != 0:
                raise vm.LabError(f"the preview card was not made: {(made.stderr or made.stdout).strip()}")
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
        inserted = set(self.attached()) if vm.read_pid() is not None else set()
        for name in self.names:
            path = vm.CARD_ROOT / f"{name}.img"
            if name not in inserted and path.is_file() and not path.is_symlink():
                path.unlink()

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
        summary = self.make_card(following)
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
        while not stop.is_set():
            if vm.read_pid() is not None and vm.guest_ready():
                return True
            stop.wait(2)
        return False

    def run(self, stop: threading.Event, watch: bool, follow: bool) -> None:
        """Insert the folder's card and, with `watch`, follow the folder until
        `stop`; with `follow`, keep unplugging what the console releases."""
        if not self.wait_for_console(stop):
            return
        foreign = self.foreign()
        if foreign:
            self.say(f"guest {self.guest} has {', '.join(foreign)} inserted: the preview uses a console with no "
                     "other card (remove it, or give another --guest)")
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
        during a game), and the preview's cards deleted."""
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
