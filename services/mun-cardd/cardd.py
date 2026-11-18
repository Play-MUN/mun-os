#!/usr/bin/env python3
"""mun-cardd: detects lab Game Cards, validates them and tells the shell.

Responsibilities (docs/game-cards.md): watch block devices, admit only virtio disks whose
serial starts with `NPT-` and, where the image names a board's card reader
(`MUN_CARDD_SLOTS`), the whole card in that reader, check the ext4
superblock, mount read-only under a
service-chosen slot directory, validate with the shared `mun_card` package
and publish state over a local UNIX socket. It never executes card content and
writes to a card only for a save. For a valid card it also copies the card's
MUN Shape package, checked, to RAM for the shell (docs/shape.md); it never
decodes it.

The core state machine lives in `CardManager`, which takes its platform pieces
(device events, mounting, validation) as injectable callables so host tests can
drive it without udev or root. `main()` wires the real ones.
"""

import argparse
import base64
import errno
import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import socket
import stat as statmod
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "mun-card"))

from mun_card import ext4, shape  # noqa: E402
from mun_card.errors import CardError  # noqa: E402
from mun_card.source import DirectorySource  # noqa: E402
from mun_card.validate import COVER_MAX_BYTES, SAVE_FORMATS, save_format, validate_card  # noqa: E402
# The integrity rule of a directory save's payload, shared with the card tool's converter.
from mun_card.saves import (FILES_PAYLOAD_MAX_FILES, SAVE_FILE_MAX_BYTES, SAVE_MAX_BYTES,  # noqa: E402,F401
                           envelope_problem, files_payload_problem, parse_envelope, save_bounds)

PROTOCOL = 1
SERIAL_PREFIX = "NPT-"
# A board's card readers, for a laboratory image that has one (a profile sets
# it; none by default): sysfs paths of the reader's controller, without /sys,
# separated by ':' (q6a-dev: /devices/platform/soc@0/8804000.mmc, the Dragon
# Q6A's microSD slot). Only a whole disk under one of them is a card, named by
# the card's own identity (its CID), so the console's other disks, the system's
# among them, never are, whatever the device policy lets this service open.
SLOTS = tuple(path.strip("/") for path in os.environ.get("MUN_CARDD_SLOTS", "").split(":") if path.strip("/"))
SYS_ROOT = Path("/sys")
MOUNT_ROOT = Path(os.environ.get("MUN_CARDD_MOUNT_ROOT", "/run/mun/cards"))
SOCKET_PATH = Path(os.environ.get("MUN_CARDD_SOCKET", "/run/mun/cardd.sock"))
SOCKET_GROUP = os.environ.get("MUN_CARDD_SOCKET_GROUP", "mun-shell")
# Root-only control socket: the launcher asks here for an executable to be
# staged out of the private mount. The shell never sees this socket.
CONTROL_SOCKET_PATH = Path(os.environ.get("MUN_CARDD_CONTROL_SOCKET", "/run/mun/cardd-control.sock"))
STAGE_MAX_BYTES = 64 * 1024 * 1024
# Read-only with the journal loaded (docs/saves.md): a ro mount writes nothing, and
# a card that needs recovery is rejected before mounting, so nothing is ever
# replayed implicitly. For one save the card is remounted rw and back; a mount
# with `noload` cannot do that with journaling, which is why it is gone.
#
# The error policy is the console's, never the card's: without `errors=` ext4
# takes it from the card's superblock, where `panic` stops the kernel at the
# first damaged inode it reads, read-only mounts included. `remount-ro` and
# not `continue`: read-only, the two act alike (the read fails and the card is
# reported invalid); in a save's read-write window `remount-ro` stops every
# write at the first inconsistency, so the save fails and says so, where
# `continue` would go on writing to, and could confirm a save on, a filesystem
# the kernel has just found damaged. Every mount and remount names it, so what
# holds never depends on which options a remount keeps.
MOUNT_FLAGS = "nosuid,nodev,noexec,errors=remount-ro"
MOUNT_OPTIONS = "ro," + MOUNT_FLAGS
LAUNCH_ROOT_PREFIX = "/run/mun/launch/"   # the only place a staged copy or save may go
PAYLOAD_OWNER_UID = 0                          # a directory-save payload file is the launcher's (root)
# Directory saves (docs/saves.md): the card declares the bound on the raw files;
# the envelope carries them in base64 (4/3) plus names and hashes.
SAVE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")   # same alphabet as a card id: path-safe
SAVE_DELAY = float(os.environ.get("MUN_CARDD_SAVE_DELAY", "0") or 0)   # lab hook: seconds inside the write
# Test hook: slows validation so removal-during-validation can be exercised. Off by default.
VALIDATION_DELAY = float(os.environ.get("MUN_CARDD_VALIDATION_DELAY", "0") or 0)
# MUN Shape (docs/shape.md): each valid card's package is copied here, one
# directory per insertion, for the shell to read. RAM, owned by this service.
SHAPE_ROOT = Path(os.environ.get("MUN_CARDD_SHAPE_ROOT", "/run/mun/shape"))
SHAPE_CHUNK = 256 * 1024          # read from the card in chunks; cancellation is checked between them
# How long a safe release waits for the copy to close its files before it
# answers "still in use"; the release then stays pending (docs/shape.md).
SHAPE_RELEASE_WAIT = float(os.environ.get("MUN_CARDD_SHAPE_RELEASE_WAIT", "3") or 3)
SHAPE_STOP_WAIT = 2.0             # at service stop, for a copy to notice it is cancelled
SHAPE_NOTES_MAX = 32              # notes carried in the card record; all are in the log
# Lab hook: seconds of pause before each chunk, so removal and release during
# a copy can be exercised. The pause ends at once if the copy is cancelled.
SHAPE_DELAY = float(os.environ.get("MUN_CARDD_SHAPE_DELAY", "0") or 0)
# Seconds between attempts to return to read-only a card that a save had to
# leave writable (CardManager._close_card). One `mount` call each, for as
# long as the card is in and writable.
READ_ONLY_RETRY = 5.0


def log(message: str) -> None:
    # A line may quote a card or a request, and JSON can spell a code point
    # that UTF-8 cannot carry (a lone surrogate): it is written as "?", so
    # that saying what happened never raises.
    print(message.encode("utf-8", "replace").decode("utf-8"), flush=True)


def guarded(what: str, work: Callable[[], object]) -> bool:
    """Run one piece of the event loop's work: a scheduled callback, one
    connection's event, a rescan.

    Cards, requests and the platform are not under the service's control,
    and the service holds what a running game needs: its card's insertion
    and its saves. Whatever one piece of work raises is therefore that
    piece's failure, logged once with its traceback, and never the loop's:
    a restarted service would give the card a new insertion and the session
    in course could no longer save. Returns False if the work raised."""
    try:
        work()
        return True
    except Exception:  # noqa: BLE001 - the point: nothing unforeseen ends the service
        log(f"internal error in {what}; the service goes on\n{traceback.format_exc().rstrip()}")
        return False


# ------------------------------------------------------------------ state model

@dataclass
class Card:
    slot: str            # service-generated identifier, safe for paths
    device: str          # kernel name, e.g. vdc
    serial: str
    # Identity of this one insertion. Slots restart at c1 with the service and
    # serials belong to the card, so neither proves that the card a consumer
    # saw earlier is the card present now; this random token does (within one
    # service lifetime; a restarted service issues new ones for rescanned cards).
    insertion: str = field(default_factory=lambda: secrets.token_hex(8))
    state: str = "reading"       # reading | valid | invalid | waiting
    active: bool = False
    info: Optional[dict] = None
    error: Optional[dict] = None
    mount: Optional[str] = None
    generation: int = 0          # bumps on removal; stale worker results are dropped
    saving: bool = False         # one write at a time per card
    releasing: bool = False      # safe removal requested: no new writes, unmount when idle
    release_reply: Optional[Callable[[dict], None]] = None
    release_timer: int = 0       # bumps per release; a deadline for an earlier one does nothing
    release_armed: bool = False  # this release's deadline for the Shape copy is set
    # Mounted read-write: set by a save for its write and cleared by the
    # remount to read-only that ends it. Still set once the save is over, that
    # remount was refused and is owed (CardManager._close_card).
    writable: bool = False
    closing: bool = False        # a retry of that remount is scheduled
    # MUN Shape: the record published with the card, and this insertion's copy.
    shape: Optional[dict] = None
    export: Optional["ShapeExport"] = None

    def to_dict(self) -> dict:
        # Mounts live in the service's private mount namespace; the shell never
        # sees them. The cover therefore travels inside the message.
        return {"slot": self.slot, "insertion": self.insertion, "device": self.device, "serial": self.serial,
                "state": self.state, "active": self.active, "info": self.info, "error": self.error,
                "shape": self.shape}


class CardManager:
    """Single-threaded state machine; call methods from one thread (the event loop)."""

    def __init__(self, mounter: "Mounter", validator: Callable[[Path], dict],
                 publish: Callable[[dict], None], schedule: Callable[[Callable[[], None]], None],
                 shape_root: Optional[Path] = None,
                 later: Optional[Callable[[float, Callable[[], None]], None]] = None,
                 remove_export: Optional[Callable[[Path], None]] = None):
        self.mounter = mounter
        self.validator = validator
        self.publish = publish
        self.schedule = schedule      # runs a callable on the manager thread later
        self.cards: Dict[str, Card] = {}   # keyed by device name
        # Cards refused or gone whose unmount could not even start, by slot
        # (_unmount_or_owe); a gone card is kept here alone. They are tried
        # again by the service's loop (retry_unmounts), which needs no
        # thread: what stopped the unmount stops new threads too.
        self.unmounts_owed: Dict[str, Card] = {}
        self._next_unmount_try = 0.0
        # Timers that could not start a thread (_timer), for the loop.
        self._due: List["tuple[float, Callable[[], None]]"] = []
        self._slot_counter = 0
        # MUN Shape export, off unless a root is given (main() gives SHAPE_ROOT).
        # `later(seconds, fn)` runs fn on the manager thread after a delay;
        # `remove_export(path)` deletes an export off this thread.
        self.shape_root = shape_root
        self.later = later or self._timer
        self.remove_export = remove_export or _remove_in_background
        self._shape_running: Optional["ShapeExport"] = None   # at most one copy reads a card at a time
        self._shape_attempts = 0      # each copy's own names, so a late cleanup never meets a later copy
        self._shape_deferred: Optional[Card] = None           # a valid card waiting for that copy to end

    def _timer(self, seconds: float, fn: Callable[[], None]) -> None:
        timer = threading.Timer(seconds, lambda: self.schedule(fn))
        timer.daemon = True
        try:
            timer.start()
        except RuntimeError as exc:
            # No thread to be had (the service's task limit): the service's
            # loop, which wakes at least twice a second, runs it when due.
            log(f"a timer could not start its thread ({exc}); the service's loop runs it when due")
            self._due.append((time.monotonic() + seconds, fn))

    def run_due(self, now: Optional[float] = None) -> None:
        """Run the timers that could not have a thread of their own and are due
        (_timer); called by the service's loop on every wake."""
        now = time.monotonic() if now is None else now
        due = [entry for entry in self._due if entry[0] <= now]
        self._due = [entry for entry in self._due if entry[0] > now]
        for _, fn in due:
            # Each on its own: one that fails must not cost the others theirs.
            guarded("a timer run by the loop", fn)

    # --- events from the platform ---------------------------------------------
    def device_added(self, device: str, serial: str, path: str) -> None:
        if not serial.startswith(SERIAL_PREFIX):
            return
        if device in self.cards:
            return
        self._slot_counter += 1
        card = Card(slot=f"c{self._slot_counter}", device=device, serial=serial)
        self.cards[device] = card
        if any(other.active for other in self.cards.values() if other is not card):
            # Policy: first card stays active; a second one waits unmounted.
            card.state = "waiting"
            card.error = {"code": "another_card_active", "message": "Hay otra Game Card activa",
                          "detail": "Se evaluará cuando se retire la tarjeta actual"}
            self._emit(card)
            return
        self._activate(card, path)

    def device_removed(self, device: str) -> None:
        card = self.cards.pop(device, None)
        if card is None:
            return
        card.generation += 1
        if card.release_reply is not None:
            # A safe release that was waiting (for a save or the Shape copy):
            # the card went before it could finish, so this was not a safe
            # removal. The waits that end later find the card gone and stop.
            reply, card.release_reply = card.release_reply, None
            card.release_timer += 1
            reply({"type": "released", "ok": False, "slot": card.slot, "serial": card.serial,
                   "error": {"code": "card_removed", "message": "Se retiró la tarjeta antes de terminar la expulsión segura"}})
        # Said before the cleanup, which may lack a process or a thread.
        self.publish({"type": "removed", "slot": card.slot, "insertion": card.insertion, "device": device, "serial": card.serial})
        self._drop_shape(card)
        self._unmount_or_owe(card)
        if card.active:
            self._promote_waiting()

    def stage(self, request: dict, reply: Callable[[dict], None]) -> None:
        """Copy the active card's entry into a launcher-owned directory, on request only.

        The copy is bound to one insertion (slot) and content version; it is
        written as `game.part`, made read-only and renamed to `game` only when
        complete, and abandoned if the card disappears meanwhile.
        """
        slot, dest = request.get("slot"), request.get("dest")
        card = next((c for c in self.cards.values() if c.slot == slot), None)
        def fail(code: str, message: str, detail: str = "") -> None:
            reply({"type": "staged", "session": request.get("session"), "ok": False,
                   "error": {"code": code, "message": message, "detail": detail}})
        if card is None or not card.active or card.state != "valid" or not card.info or not card.mount:
            return fail("card_unavailable", "La tarjeta ya no está disponible")
        info = card.info
        if info.get("kind") != "game" or not info.get("entry"):
            return fail("not_runnable", "La tarjeta no contiene un juego ejecutable")
        if request.get("serial") != card.serial or request.get("version") != info.get("version"):
            return fail("card_mismatch", "La tarjeta o su versión no coinciden con la solicitud")
        if request.get("insertion") is not None and request.get("insertion") != card.insertion:
            return fail("card_mismatch", "La tarjeta activa no es la inserción solicitada")
        if not isinstance(dest, str) or not dest.startswith(LAUNCH_ROOT_PREFIX) or "/." in dest:
            return fail("bad_destination", "Destino de copia no permitido", str(dest))
        if card.releasing:
            return fail("card_unavailable", "La tarjeta se está retirando")
        source = Path(card.mount) / info["entry"]
        mount_path = Path(card.mount)
        device_node = self.mounter.device_path(card.device)
        generation = card.generation

        def work() -> None:
            outcome: dict
            try:
                outcome = _copy_bounded(source, Path(dest), generation, lambda: card.generation)
                # The game reads its progress from work/save.json; a missing file is
                # a new game, an oversized or unreadable one is copied as far as the
                # bound so the game reports it as damaged instead of starting over.
                outcome["save"] = _copy_save_out(mount_path, info, Path(dest) / "work" / "save.json")
                if outcome.get("ok") and info.get("access") == "mount":
                    # Mount access (docs/runtime.md): the launcher has systemd mount
                    # this device read-only inside the game unit. Our own mount
                    # lives in this service's private namespace and cannot be
                    # shared; the block device can, and it is the same
                    # superblock, so the content the game reads is the one
                    # validated here. The launcher checks the shape of both
                    # fields again before they reach a unit property.
                    outcome["content"] = {"device": device_node, "root": info["root"]}
            except CardError as exc:
                outcome = {"ok": False, "error": exc.to_dict()}
            except OSError as exc:
                outcome = {"ok": False, "error": {"code": "stage_failed", "message": "No se pudo copiar el ejecutable", "detail": str(exc)}}
            except Exception as exc:  # noqa: BLE001 - a thread that dies answers nobody: the launcher would wait out its timeout
                log(f"internal error staging from {slot}; the request fails and the service goes on\n"
                    f"{traceback.format_exc().rstrip()}")
                outcome = {"ok": False, "error": {"code": "stage_failed", "message": "No se pudo copiar el ejecutable",
                                                  "detail": f"fallo interno ({type(exc).__name__})"}}
            self.schedule(lambda: reply({"type": "staged", "session": request.get("session"), **outcome}))
        try:
            threading.Thread(target=work, name=f"stage-{slot}", daemon=True).start()
        except RuntimeError as exc:
            # Answered now: otherwise the launcher would wait out its timeout.
            log(f"staging from {slot} could not start its worker: {exc}")
            reply({"type": "staged", "session": request.get("session"), "ok": False,
                   "error": {"code": "stage_failed", "message": "No se pudo copiar el ejecutable", "detail": str(exc)}})

    def save(self, request: dict, reply: Callable[[dict], None]) -> None:
        """Write a game's save to the active card, on the launcher's authority (docs/saves.md).

        The request names the session and the insertion it was started from;
        the payload is the game's own object, stored verbatim inside the
        console's envelope. The write is atomic (tmp → fsync → rename → fsync
        dir), keeps the previous envelope as save.json.prev, moves a file that
        is not an envelope aside instead of deleting it, and reports success
        only after the card is read-only again.
        """
        def fail(code: str, message: str, detail: str = "") -> None:
            reply({"type": "saved", "session": request.get("session"), "ok": False,
                   "error": {"code": code, "message": message, "detail": detail}})
        card = next((c for c in self.cards.values() if c.slot == request.get("slot")), None)
        if card is None or not card.active or card.state != "valid" or not card.info or not card.mount:
            return fail("card_unavailable", "La tarjeta ya no está disponible")
        if card.releasing:
            return fail("card_unavailable", "La tarjeta se está retirando")
        info = card.info
        if request.get("insertion") != card.insertion or request.get("serial") != card.serial \
                or request.get("version") != info.get("version") or request.get("game") != info.get("id"):
            return fail("card_mismatch", "La tarjeta activa no es la de la sesión")
        payload_max, file_max = save_bounds(info)
        kind = request.get("payload_kind")
        if kind == "files":
            # Directory saves (docs/saves.md): the payload arrives as a root-owned file
            # in the session directory, named with its size and hash.
            if not info.get("saves_directory"):
                return fail("save_invalid", "Esta tarjeta no declara partidas de directorio")
            try:
                payload_obj = _read_payload_file(request, payload_max)
            except CardError as exc:
                return fail(exc.code, exc.message, exc.detail)
            problem = files_payload_problem(payload_obj, int(info["saves_max_bytes"]))
            if problem:
                return fail("save_invalid", "La partida no es íntegra", problem)
        elif kind is None:
            if info.get("saves_directory"):
                # Only the console writes this card's saves, as whole snapshots;
                # a single-object save would replace them.
                return fail("save_invalid", "Las partidas de este juego las guarda la consola")
            if not isinstance(request.get("payload"), dict):
                return fail("save_invalid", "La partida no es un objeto JSON")
            payload_obj = request["payload"]
            try:
                size = len(_save_bytes(payload_obj, separators=(",", ":")))
            except CardError as exc:
                return fail(exc.code, exc.message, exc.detail)
            if size > payload_max:
                return fail("save_too_large", "La partida supera el tamaño permitido", f"máximo {payload_max} bytes")
        else:
            return fail("save_invalid", "Tipo de partida desconocido", str(kind)[:40])
        schema = request.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            return fail("save_invalid", "La versión del esquema de guardado no es válida")
        if card.saving:
            return fail("save_busy", "Ya hay un guardado en curso")
        # The card's own generation (docs/game-cards.md): an earlier card keeps neptune-save/1.
        envelope = {"format": save_format(info), "game": info["id"], "content_version": info.get("version"),
                    "schema": schema, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "session": request.get("session"), "insertion": card.insertion, "payload": payload_obj}
        if kind == "files":
            envelope["payload_kind"] = "files"
        try:
            data = _save_bytes(envelope) + b"\n"
        except CardError as exc:       # a directory save's payload, or the session's name
            return fail(exc.code, exc.message, exc.detail)
        if len(data) > file_max:
            # The bound the next launch reads with (_copy_save_out), on the
            # bytes as they would be written: what is confirmed is read back
            # whole. Sized so that no payload within its own limit meets it
            # (mun_card.saves); refused here, before anything is written.
            return fail("save_too_large", "La partida supera el tamaño permitido",
                        f"ocuparía {len(data)} bytes en la tarjeta; máximo {file_max}")
        generation = card.generation
        card.saving = True

        def work() -> None:
            outcome: dict
            try:
                outcome = _write_save(self.mounter, card, info, data, generation, lambda: card.generation)
            except CardError as exc:
                outcome = {"ok": False, "error": exc.to_dict()}
            except OSError as exc:
                outcome = {"ok": False, "error": {"code": "write_failed", "message": "No se pudo escribir la partida", "detail": str(exc)}}
            except Exception as exc:  # noqa: BLE001 - a thread that dies would leave the card "saving" for good:
                # no further save, and a safe release that waits for ever.
                log(f"internal error saving to {card.slot}; the save fails and the service goes on\n"
                    f"{traceback.format_exc().rstrip()}")
                outcome = {"ok": False, "error": {"code": "write_failed", "message": "No se pudo escribir la partida",
                                                  "detail": f"fallo interno ({type(exc).__name__})"}}
            self.schedule(lambda: self._saved(card, request, outcome, reply))
        try:
            threading.Thread(target=work, name=f"save-{card.slot}", daemon=True).start()
        except RuntimeError as exc:
            # The system refused a new thread: the save fails here, as one
            # that fails in its worker would, and the card is not left saving.
            log(f"the save on {card.slot} could not start its worker: {exc}")
            self._saved(card, request, {"ok": False, "error": {"code": "write_failed",
                                                               "message": "No se pudo escribir la partida",
                                                               "detail": str(exc)}}, reply)

    def _saved(self, card: Card, request: dict, outcome: dict, reply: Callable[[dict], None]) -> None:
        card.saving = False
        log(f"save on {card.slot} ({card.serial}) for session {request.get('session')}: "
            + ("ok, " + str(outcome.get("bytes")) + " bytes" if outcome.get("ok")
               else "failed: " + outcome["error"]["code"] + (" (" + outcome["error"].get("detail", "") + ")" if outcome["error"].get("detail") else "")))
        reply({"type": "saved", "session": request.get("session"), **outcome})
        if card.writable and self.cards.get(card.device) is card:
            log(f"{card.slot} ({card.serial}) may still be writable after the save: returning it to read-only "
                f"is tried again every {READ_ONLY_RETRY:g} s and before any release")
            self._close_later(card)
        self._advance_release(card)

    def _close_card(self, card: Card) -> bool:
        """Return to read-only a card that a save had to leave writable.
        True when the card is read-only.

        The kernel refuses that remount (EBUSY) while a file removed from the
        card is still open, and a save removes one: the previous copy it
        replaces. A game with mount access reads the card through its own
        mount of the same device and can hold that file open. Until it closes
        it the filesystem stays writable and is not marked clean: pulled
        then, the card would need a recovery the console never performs. So
        the remount is owed, and tried again here: by a timer, and before a
        release. Never under a save in flight, which needs the card writable
        and ends with this same remount.

        A remount that did not end in time may still take effect: a late
        remount to read-write leaves the card writable although its save
        failed, a late remount to read-only leaves it closed although its
        save reported otherwise. So nothing is decided while one still runs
        on the card, and then the kernel's table says which it is."""
        if not card.writable or not card.mount:
            return True
        if card.saving or not self.mounter.settled(card.mount):
            return False
        if self.mounter.read_only(card.mount) is not True:
            try:
                self.mounter.remount(card, rw=False)
            except CardError:
                return False
        card.writable = False
        log(f"{card.slot} ({card.serial}) is read-only again")
        return True

    def _close_later(self, card: Card) -> None:
        if not card.closing:
            card.closing = True
            self.later(READ_ONLY_RETRY, lambda: self._close_due(card))

    def _close_due(self, card: Card) -> None:
        card.closing = False
        if self.cards.get(card.device) is not card or card.saving:
            return      # gone; or a save is writing, which closes the card itself or asks again
        if not self._close_card(card):
            self._close_later(card)

    def release(self, request: dict, reply: Callable[[dict], None]) -> None:
        """Safe removal: no new writes, finish the one in flight, stop the
        Shape copy, unmount, then say so.

        A Shape copy is cancelled and checks for that between chunks, but a
        read already blocked on a failing card cannot be interrupted. The
        release therefore waits for the copy asynchronously, never on this
        thread, for SHAPE_RELEASE_WAIT: past it the caller hears "still in
        use" and must not unplug, and the release stays pending (no new
        writes, no new copy) until the copy has closed its files, when the
        card is unmounted strictly and published as released."""
        card = next((c for c in self.cards.values() if c.serial == request.get("serial")), None)
        if card is None:
            gone = next((c for c in self.unmounts_owed.values() if c.serial == request.get("serial")), None)
            if gone is not None:
                return reply({"type": "released", "ok": False, "slot": gone.slot, "serial": gone.serial,
                              "error": {"code": "unmount_failed", "message": "No se pudo desmontar la tarjeta de forma segura",
                                        "detail": "la tarjeta ya no está y su desmontaje sigue pendiente"}})
            return reply({"type": "released", "ok": True, "detail": "no such card"})
        card.releasing = True
        card.release_reply = reply
        card.release_timer += 1
        card.release_armed = False
        self._cancel_shape(card)
        self._advance_release(card)

    def _advance_release(self, card: Card) -> None:
        """The one way a release proceeds, called by `release`, at the end of
        a save and at the end of a Shape copy: the card is unmounted only
        when no save is in flight and no Shape copy still reads it, in
        whichever order they end. Waiting for a save needs no deadline (it
        ends and reports); waiting for the copy arms one, once per release,
        so the caller is answered within SHAPE_RELEASE_WAIT. Nothing here
        blocks: each end calls back."""
        if not card.releasing or self.cards.get(card.device) is not card:
            return
        if card.saving:
            log(f"release of {card.slot} waits for the save in flight")
            return
        if self._shape_reading(card):
            if not card.release_armed:
                card.release_armed = True
                log(f"release of {card.slot} waits for the Shape copy to close its files")
                token = card.release_timer
                self.later(SHAPE_RELEASE_WAIT, lambda: self._release_overdue(card, token))
            return
        self._release_now(card)

    def _release_overdue(self, card: Card, token: int) -> None:
        if token != card.release_timer or not card.releasing or card.release_reply is None \
                or card.saving or not self._shape_reading(card):
            return
        reply, card.release_reply = card.release_reply, None
        log(f"release of {card.slot} ({card.serial}): the Shape copy has not closed its files after "
            f"{SHAPE_RELEASE_WAIT:g} s; still in use, release pending")
        reply({"type": "released", "ok": False, "slot": card.slot, "serial": card.serial,
               "error": {"code": "card_busy", "message": "La tarjeta sigue en uso; no la retires",
                         "detail": "la copia de MUN Shape no ha cerrado sus archivos; la expulsión sigue pendiente"}})

    def _release_now(self, card: Card) -> None:
        reply, card.release_reply = card.release_reply, None
        try:
            if not self._close_card(card):
                # Unmounting here would detach this service's mount and leave
                # the filesystem writable under whoever holds it: not a card
                # that is safe to pull.
                raise CardError("card_busy", "La tarjeta sigue en uso; no la retires",
                                "sigue montada para escritura: un programa mantiene abierta una partida anterior")
            self.mounter.unmount(card, strict=True)
        except CardError as exc:
            # Not released: the card keeps its state and mount, writes are
            # allowed again, and the caller must not unplug. It may retry.
            card.releasing, card.release_armed = False, False
            log(f"release of {card.slot} ({card.serial}) failed: {exc.detail or exc.message}; card still in use")
            if reply is not None:
                reply({"type": "released", "ok": False, "slot": card.slot, "serial": card.serial, "error": exc.to_dict()})
            if card.export is None:
                self._start_shape(card)     # the release had cancelled its copy; the card is still in use
            return
        self._drop_shape(card)
        card.state, card.info, card.error = "released", None, None
        self._emit(card)
        log(f"released {card.slot} ({card.serial}); safe to unplug")
        if reply is not None:
            reply({"type": "released", "ok": True, "slot": card.slot, "serial": card.serial})

    def snapshot(self) -> dict:
        return {"type": "snapshot", "protocol": PROTOCOL, "reader": "ready",
                "cards": [card.to_dict() for card in self.cards.values()]}

    # --- internals -------------------------------------------------------------
    def _activate(self, card: Card, path: str) -> None:
        card.active = True
        card.state = "reading"
        card.error = None
        card.info = None
        self._emit(card)
        generation = card.generation
        try:
            card.mount = self.mounter.mount(card, path)
        except CardError as exc:
            self._finish(card, generation, None, exc.to_dict())
            return
        except OSError as exc:
            # The device can vanish between the udev event and our open(); that
            # is an invalid card, not a service crash.
            self._finish(card, generation, None, {"code": "source_unreadable",
                                                  "message": "No se pudo leer la tarjeta", "detail": str(exc)})
            return

        mount_path = Path(card.mount)   # bound now: removal clears card.mount underneath the worker

        def work() -> None:
            info: Optional[dict] = None
            error: Optional[dict] = None
            try:
                if VALIDATION_DELAY:
                    time.sleep(VALIDATION_DELAY)
                info = self.validator(mount_path)
            except CardError as exc:
                error = exc.to_dict()
            except Exception as exc:  # noqa: BLE001 - a crash must become a visible card error
                error = {"code": "internal_error", "message": "Fallo interno al leer la tarjeta", "detail": repr(exc)}
            # Bind results now; `exc` does not survive past its except block.
            self.schedule(lambda: self._finish(card, generation, info, error))
        try:
            threading.Thread(target=work, name=f"validate-{card.slot}", daemon=True).start()
        except RuntimeError as exc:
            # The system refused a new thread: the card is refused now, as a
            # validation that fails would refuse it, and not left reading.
            log(f"the validation of {card.slot} could not start its worker: {exc}")
            self._finish(card, generation, None, {"code": "internal_error",
                                                  "message": "Fallo interno al leer la tarjeta", "detail": str(exc)})

    def _finish(self, card: Card, generation: int, info: Optional[dict], error: Optional[dict]) -> None:
        if card.generation != generation or self.cards.get(card.device) is not card:
            log(f"discarding stale result for {card.slot} ({card.device})")
            return
        if error is not None:
            card.state, card.error, card.info = "invalid", error, None
            # Once per insertion: the shell shows the card as invalid, and
            # whoever reads the journal must find which device and why.
            log(f"{card.slot} ({card.device}, {card.serial}) refused: {error.get('code')}: {error.get('message')}"
                + (f" ({error['detail']})" if error.get("detail") else ""))
            # Said before the unmount, which may not even start: the shell
            # hears the refusal whatever becomes of it.
            self._emit(card)
            self._unmount_or_owe(card)
            return
        card.state, card.info, card.error = "valid", info, None
        self._emit(card)
        # Only now, with the card's state published: nothing about Shape
        # delays `valid`, Play or Eject safely.
        self._start_shape(card)

    def _unmount_or_owe(self, card: Card) -> None:
        """Unmount a card nobody may use: refused, or gone. If not even a
        command can be started, the mount is still there and card.mount
        still names it: the unmount is owed and the service's loop tries it
        again every READ_ONLY_RETRY seconds (retry_unmounts), whether the
        card is still in or not. A safe release meanwhile is refused, not
        reported as done."""
        if self.mounter.unmount(card) is not False:
            self.unmounts_owed.pop(card.slot, None)
            return
        if card.slot not in self.unmounts_owed:
            if not self.unmounts_owed:
                self._next_unmount_try = time.monotonic() + READ_ONLY_RETRY
            self.unmounts_owed[card.slot] = card
            log(f"{card.slot} ({card.serial}) is still mounted; its unmount is tried again every {READ_ONLY_RETRY:g} s")

    def retry_unmounts(self, now: Optional[float] = None) -> None:
        """The owed unmounts, again, once every READ_ONLY_RETRY seconds;
        called by the service's loop on every wake."""
        now = time.monotonic() if now is None else now
        if not self.unmounts_owed or now < self._next_unmount_try:
            return
        self._next_unmount_try = now + READ_ONLY_RETRY
        for slot, card in list(self.unmounts_owed.items()):
            if card.mount and self.mounter.unmount(card) is False:
                continue
            self.unmounts_owed.pop(slot, None)
            log(f"{card.slot} ({card.serial}) unmounted at last")

    def _promote_waiting(self) -> None:
        for card in self.cards.values():
            if card.state == "waiting":
                path = self.mounter.device_path(card.device)
                self._activate(card, path)
                return

    def _emit(self, card: Card) -> None:
        self.publish({"type": "card", "card": card.to_dict()})

    # --- MUN Shape export --------------------------------------------------------
    def _publish_shape(self, card: Card) -> None:
        # Its own message, so a change of Shape state does not resend the cover.
        self.publish({"type": "shape", "slot": card.slot, "insertion": card.insertion, "shape": card.shape})

    def _start_shape(self, card: Card) -> None:
        """Start this insertion's copy, or defer it while an earlier copy has
        not closed its files: one copy reads a card at a time, so a copy stuck
        on a failing card never accumulates with the next insertions'."""
        if self.shape_root is None or not card.active or card.state != "valid" or not card.info \
                or not card.mount or card.releasing or card.export is not None:
            return
        card.shape = {"state": "preparing", "insertion": card.insertion, "version": card.info.get("version")}
        running = self._shape_running
        if running is not None and not running.finished:
            self._shape_deferred = card
            self._publish_shape(card)
            log(f"shape for {card.slot} waits for the copy of insertion {running.insertion} to close its files")
            return
        self._shape_attempts += 1
        export = ShapeExport(self.shape_root, Path(card.mount), card.info["root"], card.insertion,
                             card.info.get("version"), card.generation, lambda: card.generation,
                             attempt=self._shape_attempts)
        card.export, self._shape_running = export, export
        self._publish_shape(card)
        export.start(lambda outcome: self.schedule(lambda: self._shape_finished(card, export, outcome)))

    def _shape_reading(self, card: Card) -> bool:
        return card.export is not None and not card.export.finished

    def _cancel_shape(self, card: Card) -> None:
        if card.export is not None and not card.export.finished:
            card.export.cancel.set()
        if self._shape_deferred is card:
            self._shape_deferred = None

    def _drop_shape(self, card: Card) -> None:
        """The insertion is over (removed or released): cancel its copy and
        delete its export. A copy still running deletes what it made when it
        ends; its completion is stale."""
        self._cancel_shape(card)
        export, card.export, card.shape = card.export, None, None
        if export is not None and export.finished and export.published is not None:
            self.remove_export(export.published)

    def _shape_finished(self, card: Card, export: "ShapeExport", outcome: dict) -> None:
        """A copy ended (its files are closed). Accept its result only for
        the same, still current insertion; otherwise delete what it made."""
        export.finished = True
        if self._shape_running is export:
            self._shape_running = None
        current = (self.cards.get(card.device) is card and card.export is export
                   and card.generation == export.generation and card.insertion == export.insertion
                   and not export.cancel.is_set())
        if current:
            card.shape = outcome["record"]
            self._publish_shape(card)
            record = outcome["record"]
            log(f"shape for {card.slot} (insertion {card.insertion}): {record['state']}"
                + (f", {record['files']} files, {record['bytes']} bytes" if record.get("path") else "")
                + f" in {outcome['seconds']:.2f}s"
                + "".join(f"; {n['code']}" + (f" ({n['block']})" if n.get("block") else "") for n in outcome["notes"]))
        else:
            if export.published is not None:
                self.remove_export(export.published)
            if card.export is export:
                card.export = None
            log(f"discarding the Shape copy of insertion {export.insertion}"
                + (" (cancelled)" if export.cancel.is_set() else " (stale)"))
        if card.releasing:
            self._advance_release(card)     # a release may have been waiting for this copy
        elif not current and card.export is None and self.cards.get(card.device) is card:
            self._start_shape(card)         # cancelled, yet the card is still in use: copy again
        deferred, self._shape_deferred = self._shape_deferred, None
        if deferred is not None and self.cards.get(deferred.device) is deferred:
            self._start_shape(deferred)

    def stop_shape(self, wait: float = SHAPE_STOP_WAIT) -> None:
        """At service stop: cancel the copy and wait for it, bounded."""
        running = self._shape_running
        if running is not None:
            running.cancel.set()
            running.done.wait(wait)


def _copy_bounded(source: Path, dest_dir: Path, generation: int, current_generation: Callable[[], int]) -> dict:
    """Copy one regular file (no symlinks, size-capped) and publish it atomically as `game`."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not statmod.S_ISREG(st.st_mode):
            raise CardError("entry_not_file", "content.entry no es un archivo normal")
        if st.st_size == 0 or st.st_size > STAGE_MAX_BYTES:
            raise CardError("entry_too_large", "El ejecutable está vacío o supera el tamaño permitido",
                            f"{st.st_size} bytes; máximo {STAGE_MAX_BYTES}")
        if not dest_dir.is_dir():
            raise CardError("bad_destination", "El directorio de destino no existe", str(dest_dir))
        # The manifest only names the entry; the console checks here, before any
        # copy, that it is what the launcher can run: a little-endian 64-bit ELF
        # for AArch64. Anything else is reported instead of being executed.
        header = os.pread(fd, 20, 0)
        if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1 \
                or int.from_bytes(header[18:20], "little") != 183:
            raise CardError("entry_not_executable", "content.entry no es un ejecutable ARM64 (ELF)")
        part = dest_dir / "game.part"
        final = dest_dir / "game"
        digest = hashlib.sha256()
        copied = 0
        out = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o500)
        try:
            while True:
                if current_generation() != generation:
                    raise CardError("card_removed", "La tarjeta se retiró durante la preparación")
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > STAGE_MAX_BYTES:
                    raise CardError("entry_too_large", "El ejecutable supera el tamaño permitido")
                digest.update(chunk)
                os.write(out, chunk)
            os.fsync(out)
        finally:
            os.close(out)
        if current_generation() != generation:
            raise CardError("card_removed", "La tarjeta se retiró durante la preparación")
        os.chmod(part, 0o555)
        os.replace(part, final)   # published only now, complete and read-only
        return {"ok": True, "path": str(final), "size": copied, "sha256": digest.hexdigest()}
    except Exception:
        try:
            (dest_dir / "game.part").unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def _save_bytes(value: object, **options) -> bytes:
    """`value` as the UTF-8 JSON a save is measured and stored in.

    JSON can spell, as an escape in plain ASCII, a code point that UTF-8
    cannot carry (a lone surrogate). A save holding one is refused
    (CardError "save_invalid") rather than altered: the card would not keep
    what the game sent."""
    try:
        return json.dumps(value, ensure_ascii=False, **options).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CardError("save_invalid", "La partida contiene texto que no se puede guardar como UTF-8",
                        f"U+{ord(exc.object[exc.start]):04X}")


def save_path(mount: Path, info: dict) -> Path:
    """Where this card keeps this game's save: <saves.location>/<card id>/save.json (for messages)."""
    return mount / info.get("saves", "saves") / info["id"] / "save.json"


def _open_dir_component(dir_fd: int, name: str) -> int:
    """Open one directory component below `dir_fd` without following links."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise CardError("save_path_unsafe", "La ruta de partidas de la tarjeta contiene un enlace o no es una carpeta", name)
        raise
    if not statmod.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise CardError("save_path_unsafe", "La ruta de partidas de la tarjeta no es una carpeta", name)
    return fd


def open_save_dir(mount: Path, info: dict, create: bool) -> int:
    """A file descriptor for <mount>/<saves.location>/<card id>, walked component by
    component with O_NOFOLLOW: a symbolic link anywhere on the way is refused,
    so a crafted card cannot point the writer at its own content. With
    `create`, each directory missing on the way is made, the saves location
    itself included: a card is valid without one (docs/game-cards.md), and
    it must still be possible to save to it. A directory is made by name in
    its parent's descriptor and then opened like any other, so never through
    a link. Returns the descriptor; the caller closes it. Raises
    CardError("save_path_unsafe"), or FileNotFoundError (without `create`)
    when a directory on the way is absent.
    """
    if not SAVE_ID.fullmatch(str(info.get("id", ""))):
        raise CardError("save_path_unsafe", "El identificador del juego no sirve como ruta")
    parts = [part for part in str(info.get("saves", "saves")).split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise CardError("save_path_unsafe", "La ruta de partidas sale de la tarjeta")
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts + [str(info["id"])]:
            try:
                nxt = _open_dir_component(fd, part)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=0o755, dir_fd=fd)
                nxt = _open_dir_component(fd, part)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_regular(dir_fd: int, name: str, limit: int) -> Optional[bytes]:
    """Bytes of a regular, non-linked file in `dir_fd`, or None if absent/not regular.

    Never blocks on what a card author left under the name: the type is
    checked by name before opening (a FIFO opened for reading would wait for a
    writer that does not exist), the open itself is non-blocking so a
    replacement between the two checks cannot stall either, and the descriptor
    is checked again before reading. Anything that is not a regular file is
    reported as absent; callers treat it as damaged where a save should be.
    """
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not statmod.S_ISREG(st.st_mode):
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENXIO):
            return None
        raise
    try:
        if not statmod.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit)
    finally:
        os.close(fd)


def _is_envelope(data: Optional[bytes], limit: int = SAVE_FILE_MAX_BYTES, info: Optional[dict] = None) -> bool:
    if data is None or len(data) > limit:
        return False
    try:
        document = parse_envelope(data)
    except (ValueError, RecursionError):
        return False
    if not isinstance(document, dict) or document.get("format") != save_format(info or {}):
        return False
    if document.get("payload_kind") == "files" or (info or {}).get("saves_directory"):
        # What the launcher restores and nothing less (docs/saves.md): this
        # card's game, schema and declared units, each passing its check.
        return envelope_problem(document, info or {}, save_format(info or {})) is None
    return True


def _other_generation(data: Optional[bytes], info: dict) -> Optional[str]:
    """The format of `data` when it is a console envelope of the other naming
    generation than the card's (docs/game-cards.md), else None. Anything that does not
    even parse is not this: it stays a damaged save, as before."""
    if data is None:
        return None
    try:
        document = parse_envelope(data)
    except (ValueError, RecursionError):
        return None
    found = document.get("format") if isinstance(document, dict) else None
    if found in SAVE_FORMATS.values() and found != save_format(info):
        return found
    return None


def _marked_recovered(previous: Optional[bytes], limit: int, info: dict) -> Optional[bytes]:
    """The previous envelope carrying the mark that tells the game it is the
    previous save, or None when `previous` is not an envelope the console
    wrote. That includes one holding text UTF-8 cannot carry (spelled as a
    JSON escape): the console refuses to store such text, so it did not
    write this file, and it could not write it back marked."""
    if not _is_envelope(previous, limit, info):
        return None
    document = parse_envelope(previous)
    document["recovered_from"] = "save.json.prev"
    try:
        return _save_bytes(document) + b"\n"
    except CardError:
        return None


def _copy_save_out(mount: Path, info: dict, target: Path) -> dict:
    """Give the session a copy of the current save, or nothing if there is none.

    Interrupted-state recovery (docs/saves.md): the write protocol never leaves the
    card without `save.json` once one existed, but if it is missing or is not a
    file while `save.json.prev` is a valid envelope (an older writer, a manual
    edit), the previous save is handed out and the fact is reported, so a
    player's progress never silently reads as an empty slot.
    """
    try:
        dir_fd = open_save_dir(mount, info, create=False)
    except FileNotFoundError:
        return {"present": False}
    except CardError as exc:
        return {"present": True, "copied": False, "error": exc.to_dict()}
    _, limit = save_bounds(info)
    directory_saves = bool(info.get("saves_directory"))
    try:
        data = _read_regular(dir_fd, "save.json", limit + 1)   # one byte over the bound reads as damaged
        recovered = False
        # A MUN Collect style game is handed a damaged save.json and tells the
        # player itself. A directory-save game cannot: for it an envelope that
        # is not intact falls back to the previous copy (docs/saves.md). An envelope
        # of the other naming generation (docs/game-cards.md) is never handed out, on
        # either path: a game that reads both formats would take it.
        other = _other_generation(data, info)
        unusable = data is None or other or (directory_saves and not _is_envelope(data, limit, info))
        if unusable:
            # Marked so the game can tell the player it is the previous save.
            previous = _marked_recovered(_read_regular(dir_fd, "save.json.prev", limit + 1), limit, info)
            if previous is None:
                if other:
                    return {"present": True, "copied": False,
                            "error": {"code": "save_other_generation",
                                      "message": "La partida de la Game Card no corresponde a esta tarjeta",
                                      "detail": f"save.json es {other}; esta tarjeta guarda {save_format(info)}"}}
                if data is not None:
                    return {"present": True, "copied": False,
                            "error": {"code": "save_damaged", "message": "La partida de la Game Card está dañada",
                                      "detail": "save.json no es una partida íntegra y no hay copia anterior válida"}}
                return {"present": False}
            why = "missing" if data is None else (f"of the other generation ({other})" if other else "not intact")
            data, recovered = previous, True
            log(f"save.json {why} under {mount}; handing out save.json.prev as the recovered save")
    finally:
        os.close(dir_fd)
    # The session's work directory belongs to the game's user, so nothing in
    # it is taken on trust: the directory is opened without following a link,
    # a name already there is removed rather than opened, and the copy is
    # created exclusively. This service, which is root, never writes through
    # something left for it to find.
    work = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        owner = os.fstat(work)
        _unlink_quiet(work, target.name)
        out = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=work)
        try:
            view = memoryview(data)
            while view:          # a directory save's envelope can be megabytes
                view = view[os.write(out, view):]
            os.fchown(out, owner.st_uid, owner.st_gid)
        finally:
            os.close(out)
    finally:
        os.close(work)
    return {"present": True, "copied": True, "bytes": len(data), "recovered": recovered}


def _unlink_quiet(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _read_payload_file(request: dict, limit: int) -> object:
    """The launcher's payload file for a directory save: under the launch root,
    a regular root-owned file, not a link, within the bound, matching the
    size and SHA-256 the request names. Parsed as JSON."""
    path = request.get("payload_file")
    if not isinstance(path, str) or not path.startswith(LAUNCH_ROOT_PREFIX) or "/." in path or "\0" in path:
        raise CardError("save_invalid", "Ruta de partida no permitida", str(path)[:120])
    size, digest = request.get("payload_size"), request.get("payload_sha256")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit or not isinstance(digest, str):
        raise CardError("save_too_large" if isinstance(size, int) and size > limit else "save_invalid",
                        "La partida supera el tamaño permitido o no declara su huella", f"máximo {limit} bytes")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise CardError("save_invalid", "No se pudo leer la partida preparada", str(exc))
    try:
        st = os.fstat(fd)
        if not statmod.S_ISREG(st.st_mode) or st.st_uid != PAYLOAD_OWNER_UID or st.st_size != size:
            raise CardError("save_invalid", "La partida preparada no es un archivo del lanzador", path)
        data = b""
        while len(data) < size:
            chunk = os.read(fd, size - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise CardError("save_invalid", "La partida preparada no coincide con su huella", path)
    try:
        return json.loads(data)
    except ValueError:
        raise CardError("save_invalid", "La partida preparada no es JSON", path)


def _write_save(mounter: "Mounter", card: "Card", info: dict, data: bytes, generation: int,
                current_generation: Callable[[], int]) -> dict:
    """The save write protocol (docs/saves.md); every step that can fail reports why.

    Names are resolved through a directory descriptor obtained without following
    links (R1), the temporary file is created exclusively so no existing object
    is ever opened for writing, and the current save keeps its name until the
    new one replaces it in one rename (R2): the previous envelope is preserved
    as a hard link under save.json.prev first, so no failure at any boundary
    leaves the card without a loadable save.json.
    """
    if current_generation() != generation:
        raise CardError("card_removed", "La tarjeta se retiró antes de guardar")
    mount = Path(card.mount)
    target = save_path(mount, info)
    # Writable from before the remount to read-write until a remount to
    # read-only succeeds. Set first: a remount to read-write that does not
    # end in time fails the save but may still take effect afterwards, and
    # the card must then count as writable. If the remount that ends this
    # save is refused, the flag stays and the manager tries again
    # (CardManager._close_card): the card is never left writable.
    card.writable = True
    mounter.remount(card, rw=True)
    dir_fd = -1

    def read_only_again() -> None:
        mounter.remount(card, rw=False)
        card.writable = False

    def back_to_read_only() -> None:
        # Best effort on the failure path: the card may already be gone.
        try:
            read_only_again()
        except CardError as exc:
            log(f"could not remount {card.slot} read-only after a failed save: {exc.detail or exc.message}")

    try:
        dir_fd = open_save_dir(mount, info, create=True)
        # A stale temporary (ours, or anything a card author left under that
        # name) is removed by name; unlink never follows links, and the new
        # temporary is created exclusively, so nothing existing is written to.
        _unlink_quiet(dir_fd, "save.json.tmp")
        fd = os.open("save.json.tmp", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dir_fd)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        if SAVE_DELAY:
            time.sleep(SAVE_DELAY)   # lab hook: the window where tmp exists and the current save is intact
        if current_generation() != generation:
            raise CardError("card_removed", "La tarjeta se retiró mientras se guardaba")
        # Preserve the current save under its backup name *without* removing it:
        # a hard link gives the old inode a second name, then the final rename
        # swaps the canonical name to the new file atomically.
        _, limit = save_bounds(info)
        current = _read_regular(dir_fd, "save.json", limit + 1)
        try:
            st = os.stat("save.json", dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            # Only an intact envelope may become the previous copy; a damaged one
            # is kept aside under its own name and never replaces save.json.prev.
            keep_as = "save.json.prev" if (statmod.S_ISREG(st.st_mode) and _is_envelope(current, limit, info)) \
                else f"save.json.damaged-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            if statmod.S_ISREG(st.st_mode):
                _unlink_quiet(dir_fd, keep_as + ".tmp")
                os.link("save.json", keep_as + ".tmp", src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                os.replace(keep_as + ".tmp", keep_as, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            else:
                # A link or other object under the canonical name: move the name aside,
                # never open it. Between here and the final rename the slot has no
                # save.json, but it never had a loadable one either.
                os.replace("save.json", keep_as, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.replace("save.json.tmp", "save.json", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Exception as exc:
        # Whatever failed, foreseen or not: no temporary is left behind and
        # the card does not stay writable.
        if dir_fd >= 0:
            _unlink_quiet(dir_fd, "save.json.tmp")
        back_to_read_only()
        if not isinstance(exc, OSError):
            raise
        if exc.errno == errno.ENOSPC:
            raise CardError("no_space", "No hay espacio en la Game Card para guardar la partida", str(exc))
        if exc.errno == errno.EIO:
            raise CardError("sync_failed", "La Game Card devolvió un error al escribir", str(exc))
        raise CardError("write_failed", "No se pudo escribir la partida", str(exc))
    finally:
        if dir_fd >= 0:
            os.close(dir_fd)
    # Read-only again: this flushes the journal and marks the filesystem clean,
    # which is what the next console will find. It is part of "saved"; if it
    # fails the player is told, even though the bytes are on the card: a card
    # pulled while writable is one the console would refuse to mount.
    try:
        read_only_again()
    except CardError as exc:
        if exc.code != "sync_failed":
            raise
        raise CardError("sync_failed", "La partida se escribió, pero la tarjeta no pudo cerrarse; no la retires todavía",
                        exc.detail or exc.message)
    return {"ok": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "path": str(target)}


# ------------------------------------------------------------------ MUN Shape export

class _ShapeCancelled(Exception):
    """The copy was cancelled or its card changed; raised between chunks."""


def _shape_group() -> Optional[int]:
    """The shell's group, which may read exports; None where it does not exist."""
    try:
        import grp
        return grp.getgrnam(SOCKET_GROUP).gr_gid
    except (KeyError, ImportError):
        return None


def _open_package_file(mount: Path, relative: str) -> int:
    """A descriptor for a regular file of the card, reached one component at
    a time without following links and opened non-blocking, so that neither
    a link nor a FIFO left under a package name can redirect or stall the
    copy. Raises CardError."""
    parts = relative.split("/")
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise CardError("path_symlink", "Una ruta del paquete atraviesa un enlace o no es una carpeta", relative)
                raise
            os.close(fd)
            fd = nxt
        st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if not statmod.S_ISREG(st.st_mode):
            raise CardError("path_type", "Una ruta del paquete no es un archivo normal", relative)
        try:
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC,
                              dir_fd=fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENXIO):
                raise CardError("path_type", "Una ruta del paquete no es un archivo normal", relative)
            raise
        if not statmod.S_ISREG(os.fstat(file_fd).st_mode):
            os.close(file_fd)
            raise CardError("path_type", "Una ruta del paquete no es un archivo normal", relative)
        return file_fd
    finally:
        os.close(fd)


class _StagingSource:
    """The card as the Shape checker sees it, copying as it reads: every
    file the checker reads is read once from the card, in chunks, into the
    staging directory, and the checker validates exactly those bytes. Entries
    are looked at without following links (DirectorySource)."""

    def __init__(self, mount: Path, base: str, staging: Path, check: Callable[[], None]):
        self.mount, self.base, self.staging, self.check = mount, base.strip("/"), staging, check
        self.cards = DirectorySource(mount)
        self.created = False

    def stat(self, relative: str):
        self.check()
        return self.cards.stat(relative)

    def read(self, relative: str, limit: int) -> bytes:
        self.check()
        if not relative.startswith(self.base + "/"):
            raise CardError("path_unsafe", "Ruta fuera del paquete", relative)
        inner = relative[len(self.base) + 1:]     # validated by the checker's path rules
        target = self.staging / inner
        if target.is_file():
            return target.read_bytes()[:limit + 1]   # already copied: never read the card twice
        # A failure to read the card is the card's (a CardError: the block that
        # names the file is dropped); a failure to write the copy in RAM is the
        # service's and propagates, ending the export.
        try:
            source = _open_package_file(self.mount, relative)
        except OSError as exc:
            raise CardError("source_unreadable", "No se pudo leer la tarjeta", f"{inner}: {exc.strerror}")
        try:
            if not self.created:
                os.mkdir(self.staging, 0o700)
                self.created = True
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            data = bytearray()
            try:
                while len(data) <= limit:
                    self.check()
                    try:
                        chunk = os.read(source, min(SHAPE_CHUNK, limit + 1 - len(data)))
                    except OSError as exc:
                        raise CardError("source_unreadable", "No se pudo leer la tarjeta", f"{inner}: {exc.strerror}")
                    if not chunk:
                        break
                    data += chunk
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(out, view):]
            finally:
                os.close(out)
        finally:
            os.close(source)
        return bytes(data)


class ShapeExport:
    """One insertion's copy of its card's Shape package (docs/shape.md).

    A worker thread reads the package through the shared checker, bounded
    and in chunks, into `.<insertion>.<attempt>.part/` (0700) under the
    root; keeps only the files the checker accepted and adds the normalised
    `shape.json`; seals files 0440, folders 0550 and its own folder 0750
    (group: the shell's); and, as its last step, publishes
    `<insertion>.<attempt>/` with one
    rename. Anything that fails, including cancellation, ends with this
    attempt's staging copy and result deleted and nothing published.
    `finished` is set on the manager thread when it receives the outcome;
    `done` by the worker as its very last step."""

    def __init__(self, root: Path, mount: Path, content_root: str, insertion: str, version: Optional[str],
                 generation: int, current_generation: Callable[[], int], attempt: int = 1):
        self.root, self.mount, self.content_root = root, mount, content_root
        self.insertion, self.version, self.generation = insertion, version, generation
        self.current_generation = current_generation
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.finished = False
        # Names of this attempt alone: a copy started again for the same
        # insertion (after a failed release) never meets the one before it,
        # nor that one's cleanup, which may still be running.
        name = f"{insertion}.{attempt}"
        self.staging = root / f".{name}.part"
        self.final = root / name
        self.published: Optional[Path] = None

    def start(self, on_done: Callable[[dict], None]) -> None:
        threading.Thread(target=self._run, args=(on_done,), name=f"shape-{self.insertion}", daemon=True).start()

    def check(self) -> None:
        if SHAPE_DELAY:
            self.cancel.wait(SHAPE_DELAY)
        if self.cancel.is_set() or self.current_generation() != self.generation:
            raise _ShapeCancelled()

    def _run(self, on_done: Callable[[dict], None]) -> None:
        started = time.monotonic()
        notes: List[dict] = []
        try:
            record, notes = self._copy()
        except _ShapeCancelled:
            record = {"state": "unused", "insertion": self.insertion, "version": self.version}
            self.cancel.set()
        except Exception as exc:  # noqa: BLE001 - any failure is a note, never a service crash
            notes = [{"code": "shape_export_failed", "level": "unused", "block": None, "where": "",
                      "detail": f"{type(exc).__name__}: {exc}"}]
            record = {"state": "unused", "insertion": self.insertion, "version": self.version, "notes": notes}
        finally:
            _remove_tree(self.staging)
        if record.get("path") is None and self.published is not None:
            # Published, then failed: remove this attempt's own result, which
            # no other attempt shares a name with.
            _remove_tree(self.published)
            self.published = None
        try:
            on_done({"record": record, "notes": notes, "seconds": time.monotonic() - started})
        finally:
            self.done.set()

    def _copy(self):
        self.check()
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        source = _StagingSource(self.mount, f"{self.content_root.strip('/')}/{shape.PACKAGE_DIR}", self.staging, self.check)
        # Measured with this card's version: the document written below is
        # the one the checker measured (shape.export_bytes).
        result = shape.check_card(source, self.content_root, version=self.version)
        self.check()
        notes = [{"code": n.code, "level": n.level, "block": n.block, "where": n.where, "detail": n.detail[:200]}
                 for n in result.notes]
        record = {"state": result.state, "insertion": self.insertion, "version": self.version,
                  "notes": notes[:SHAPE_NOTES_MAX]}
        if len(notes) > SHAPE_NOTES_MAX:
            record["notes_omitted"] = len(notes) - SHAPE_NOTES_MAX
        if result.shape is None:
            return record, notes
        # The export holds what the shell may use and nothing else: the
        # accepted files and the normalised document, bound to this insertion.
        _prune(self.staging, set(result.files))
        data = shape.export_bytes(result.shape, self.insertion, self.version)
        if len(data) > shape.EXPORT_MAX_BYTES:
            # The checker measured it with a 16-character insertion; an
            # insertion of another length could still take it over. Then
            # nothing is published: the shell would read none of it.
            notes.append({"code": "shape_export_size", "level": "unused", "block": None, "where": "",
                          "detail": f"{len(data)} bytes; el shell lee hasta {shape.EXPORT_MAX_BYTES}"})
            return {"state": "unused", "insertion": self.insertion, "version": self.version,
                    "notes": notes[:SHAPE_NOTES_MAX]}, notes
        out = os.open(self.staging / shape.MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600)
        with os.fdopen(out, "wb") as handle:
            handle.write(data)
        _seal(self.staging, _shape_group())
        self.check()
        # Publication is the last step: nothing that can fail comes after it.
        os.rename(self.staging, self.final)       # complete and immutable, or nothing
        self.published = self.final
        record.update(path=str(self.final), files=len(result.files),
                      bytes=sum(details["bytes"] for details in result.files.values()))
        return record, notes


def _prune(staging: Path, keep: set) -> None:
    """Delete from the staging copy every file the checker did not accept
    (a dropped block's, the card's own shape.json), then empty folders."""
    for directory, folders, files in os.walk(staging, topdown=False):
        for name in files:
            path = Path(directory) / name
            if path.relative_to(staging).as_posix() not in keep:
                path.unlink()
        for name in folders:
            try:
                (Path(directory) / name).rmdir()
            except OSError:
                pass      # not empty: it holds accepted files


def _seal(staging: Path, group: Optional[int]) -> None:
    """Files 0440 and folders 0550, group the shell's, all before the rename
    that publishes the copy. The top folder is 0750: the shell's group reads
    it as any other and never writes it, and its owner keeps the write
    permission some systems ask for to rename a folder (macOS refuses to
    rename one its owner cannot write; Linux does not ask). Its owner is
    this service, which on the console runs as root and writes it anyway."""
    for directory, folders, files in os.walk(staging, topdown=False):
        for name in files:
            path = os.path.join(directory, name)
            os.chmod(path, 0o440)
            if group is not None:
                os.chown(path, -1, group)
        os.chmod(directory, 0o750 if Path(directory) == staging else 0o550)
        if group is not None:
            os.chown(directory, -1, group)


def _remove_tree(path: Path) -> None:
    """Delete an export or a staging copy, sealed or not, never following a link."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not statmod.S_ISDIR(info.st_mode):
        os.unlink(path)
        return
    for directory, folders, _ in os.walk(path):
        os.chmod(directory, 0o700)       # a sealed folder: this service's own, made removable
    shutil.rmtree(path, ignore_errors=False, onerror=lambda func, name, exc: log(f"could not remove {name}: {exc[1]}"))


def _remove_in_background(path: Path) -> None:
    try:
        threading.Thread(target=_remove_tree, args=(path,), name="shape-remove", daemon=True).start()
    except RuntimeError:
        # No thread to be had: the export is in RAM and is removed here. If
        # even that fails it stays until the service's next start, which
        # clears every export (clear_shape_root); the caller goes on.
        try:
            _remove_tree(path)
        except OSError as exc:
            log(f"could not remove the export {path.name} ({exc}); the service's next start clears it")


def clear_shape_root(root: Path) -> None:
    """At service start and stop: no export outlives the service that made
    it, `.part` copies included."""
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    for entry in os.listdir(root):
        _remove_tree(root / entry)


# ------------------------------------------------------------------ platform bits

# How long one mount, remount or umount may take. A healthy card answers in
# well under a second; one that stops answering leaves the command waiting in
# the kernel, where no signal reaches it. Past this bound the operation is
# reported as failed and the process is kept (Mounter.stalled, reaped from the
# loop), never abandoned; nothing more is started on that mount point until it
# has ended, and a card whose unmount has not ended is never safe to remove.
MOUNT_TIMEOUT = 10.0


class Mounter:
    """Real mounts under MOUNT_ROOT. Slot names come from the manager, never from the card."""

    def __init__(self, root: Path = MOUNT_ROOT):
        self.root = root
        # Mount point -> (what was asked: mount, remount or umount; its process).
        # Saves remount from their worker thread while the loop reaps: one lock.
        self.stalled: Dict[str, "tuple[str, subprocess.Popen]"] = {}
        self.stalled_lock = threading.Lock()

    def device_path(self, device: str) -> str:
        return f"/dev/{device}"

    @staticmethod
    def mounted(target: str) -> bool:
        """Whether `target` is a mount point in this service's namespace,
        from the kernel's table: a stat of it could wait on the card itself."""
        try:
            with open("/proc/self/mountinfo", "rb") as table:
                return any(line.split(b" ")[4].decode("unicode_escape", "replace") == target
                           for line in table if line.count(b" ") >= 5)
        except OSError:
            return True

    @staticmethod
    def read_only(target: str) -> Optional[bool]:
        """Whether the filesystem mounted at `target` is read-only: its
        superblock's options in the kernel's table (after the " - "), which
        every mount of the device shares. None when the table cannot be read
        or does not hold `target`."""
        state = None
        try:
            with open("/proc/self/mountinfo", "rb") as table:
                for line in table:
                    fields = line.rstrip(b"\n").split(b" ")
                    if len(fields) < 10 or fields[4].decode("unicode_escape", "replace") != target:
                        continue
                    try:
                        separator = fields.index(b"-", 6)
                    except ValueError:
                        continue
                    if len(fields) > separator + 3:
                        # The last line wins: a later mount on the same point covers the earlier.
                        state = fields[separator + 3].split(b",")[0] == b"ro"
        except OSError:
            return None
        return state

    def settled(self, target: str) -> bool:
        """No command started on `target` is still running."""
        self.reap()
        with self.stalled_lock:
            return target not in self.stalled

    def reap(self) -> None:
        """Forget stalled commands that have ended. A mount that succeeded
        after it was reported failed has no card using it: detached."""
        detaching = []
        with self.stalled_lock:
            ended = [(target, command, proc) for target, (command, proc) in self.stalled.items()
                     if proc.poll() is not None]
            for target, command, proc in ended:
                del self.stalled[target]
                # Only a first mount has no card behind it; a late remount or
                # umount concerns the card that is still tracked. The detach
                # starts under the lock and stays this mount point's command
                # until it ends: a card mounted here in between would be the
                # one it detaches.
                if command == "mount" and proc.returncode == 0:
                    detach = self._start_detach(target)
                    if detach is not None:
                        self.stalled[target] = ("detach", detach)
                        detaching.append((target, detach))
        for target, command, proc in ended:
            log(f"{command} on {target} ended at last (exit {proc.returncode})")
        for target, detach in detaching:
            self._finish_detach(target, detach)

    @staticmethod
    def _start_detach(target: str) -> "Optional[subprocess.Popen]":
        try:
            return subprocess.Popen(["umount", "-l", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log(f"umount -l on {target} could not start: {exc}")
            return None

    def _finish_detach(self, target: str, detach: "subprocess.Popen") -> None:
        """Wait for a lazy detach within MOUNT_TIMEOUT. It does not wait on
        the device and is expected to end at once; one that does not is
        kept as the mount point's command, like any other, and never waited
        on again from here."""
        try:
            detach.wait(timeout=MOUNT_TIMEOUT)
        except subprocess.TimeoutExpired:
            with self.stalled_lock:
                self.stalled.setdefault(target, ("detach", detach))
            log(f"umount -l on {target} still running after {MOUNT_TIMEOUT:.0f} s; kept until it ends")
            return
        with self.stalled_lock:
            if self.stalled.get(target, (None, None))[1] is detach:
                del self.stalled[target]

    def _run(self, kind: str, argv: List[str], target: str, code: str, message: str) -> "tuple[int, str]":
        """Run a mount-family command (`kind`: mount, remount or umount) on
        `target` within MOUNT_TIMEOUT."""
        self.reap()
        with self.stalled_lock:
            if target in self.stalled:
                raise CardError(code, message, f"{self.stalled[target][0]} sigue en curso en {target}")
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            # No process to be had (the service's task limit, memory): the
            # operation fails as itself, and nothing changed on the card.
            raise CardError(code, message, f"{kind} no pudo empezar: {exc}") from exc
        try:
            out, err = proc.communicate(timeout=MOUNT_TIMEOUT)
        except subprocess.TimeoutExpired:
            with self.stalled_lock:
                self.stalled[target] = (kind, proc)
            log(f"{kind} on {target} still running after {MOUNT_TIMEOUT:.0f} s; kept until it ends")
            raise CardError(code, message, f"{kind} no terminó en {MOUNT_TIMEOUT:.0f} s")
        return proc.returncode, (err or out or "").strip()

    def mount(self, card: Card, path: str) -> str:
        ext4.check_mountable(Path(path))
        target = self.root / card.slot
        target.mkdir(parents=True, exist_ok=True)
        code, detail = self._run("mount", ["mount", "-t", "ext4", "-o", MOUNT_OPTIONS, path, str(target)], str(target),
                                 "mount_failed", "No se pudo montar la tarjeta en solo lectura")
        if code != 0:
            raise CardError("mount_failed", "No se pudo montar la tarjeta en solo lectura", detail)
        return str(target)

    def remount(self, card: Card, rw: bool) -> None:
        if not card.mount:
            raise CardError("card_removed", "La tarjeta ya no está montada")
        code, detail = self._run("remount", ["mount", "-o", f"remount,{'rw' if rw else 'ro'},{MOUNT_FLAGS}", card.mount],
                                 card.mount, "sync_failed" if not rw else "write_failed",
                                 "No se pudo cambiar el modo de la tarjeta")
        if code != 0:
            raise CardError("sync_failed" if not rw else "write_failed",
                            "No se pudo cambiar el modo de la tarjeta", detail)

    def unmount(self, card: Card, strict: bool = False) -> bool:
        """Unmount the card.

        `strict` is the safe-removal path: a plain umount must succeed, or a
        CardError("unmount_failed") is raised and the mount stays tracked so the
        caller can report and retry. Without `strict` (a card refused, or
        already gone) a failed umount falls back to a lazy detach, and the
        mount is forgotten once one of them ran. True then. False when
        neither could even start (no process to be had): the mount is still
        there, card.mount still names it, and the caller tries again later.
        """
        if not card.mount:
            return True
        target = card.mount
        self.reap()
        # An earlier umount that ran past its bound may have finished since.
        with self.stalled_lock:
            pending = target in self.stalled
        if not pending and not self.mounted(target):
            card.mount = None
            return True
        try:
            code, detail = self._run("umount", ["umount", target], target, "unmount_failed",
                                     "No se pudo desmontar la tarjeta de forma segura")
        except CardError as exc:
            if strict:
                raise
            code, detail = 1, exc.detail
        if code != 0:
            if strict:
                raise CardError("unmount_failed", "No se pudo desmontar la tarjeta de forma segura",
                                detail or f"umount exit {code}")
            # A lazy detach does not wait on the device.
            detach = self._start_detach(target)
            if detach is None:
                log(f"{target} stays mounted: neither umount nor a lazy detach could start ({detail})")
                return False
            self._finish_detach(target, detach)
        card.mount = None
        try:
            Path(target).rmdir()
        except OSError:
            pass
        return True


def validate_mount(mount: Path) -> dict:
    """Validate a mounted card and attach the cover bytes (already size-checked)."""
    source = DirectorySource(mount)
    info = validate_card(source).to_dict()
    if info.get("cover"):
        data = source.read(info["cover"], COVER_MAX_BYTES)
        info["cover_data"] = base64.b64encode(data[:COVER_MAX_BYTES]).decode("ascii")
    return info


def block_serial(device: str) -> str:
    try:
        return (SYS_ROOT / "block" / device / "serial").read_text().strip()
    except OSError:
        return ""


def slot_serial(device: str) -> str:
    """The identity of the card a board's reader holds as `device`: `NPT-sd-`
    and the card's CID (32 hex digits, the card's own, as a virtio card's
    serial is), or "" for any disk not in one of SLOTS or without a CID."""
    if not SLOTS:
        return ""
    try:
        where = (SYS_ROOT / "block" / device).resolve().relative_to(SYS_ROOT.resolve()).parts
        cid = (SYS_ROOT / "block" / device / "device" / "cid").read_text().strip().lower()
    except (OSError, ValueError):
        return ""
    if not any(where[:len(slot.split("/"))] == tuple(slot.split("/")) for slot in SLOTS):
        return ""
    return f"{SERIAL_PREFIX}sd-{cid}" if re.fullmatch(r"[0-9a-f]{32}", cid) else ""


def card_serial(device: str) -> str:
    """A block device's card identity: a virtio disk's serial, or a board's
    reader's card (slot_serial); device_added admits only what begins with
    SERIAL_PREFIX."""
    return block_serial(device) if device.startswith("vd") else slot_serial(device)


def enumerate_devices() -> List[tuple]:
    found = []
    for entry in sorted((SYS_ROOT / "block").iterdir()):
        name = entry.name
        if not name.startswith("vd") and not SLOTS:
            continue
        serial = card_serial(name)
        if serial.startswith(SERIAL_PREFIX):
            found.append((name, serial, f"/dev/{name}"))
    return found


# ------------------------------------------------------------------ socket server

# One JSON line, including a base64 cover: a 1 MiB PNG (the validator's limit)
# becomes ~1.37 MiB of base64 plus metadata. Every consumer of this socket
# (shell CardClient, launchd feed reader) accepts at least this much per line.
MAX_FRAME_BYTES = 2 * 1024 * 1024
# Bytes a client may leave unread before it is considered dead and dropped:
# room for a snapshot and a run of card events with covers while a reader is
# busy for a moment.
OUTBOX_MAX_BYTES = 8 * 1024 * 1024
# Bytes all clients together may leave unread. Past it, the clients with the
# most unread bytes are dropped first (the newest of equals): a reader that
# keeps up has next to nothing queued, so it is never the one chosen while one
# that does not read holds more.
OUTBOX_TOTAL_MAX_BYTES = 16 * 1024 * 1024
# Connections kept at once. The socket's readers are the shell, the launcher
# (its feed, and a short read when it starts) and the console reset: four at
# most, with room for a reconnection that overlaps the connection it
# replaces. A connection past this replaces a client that leaves bytes unread,
# or is refused when every client reads.
MAX_CLIENTS = 8


def _without_cover(value):
    """Copy of a message with every `cover_data` removed and `cover_omitted` set."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "cover_data":
                out["cover_omitted"] = True
            else:
                out[key] = _without_cover(item)
        return out
    if isinstance(value, list):
        return [_without_cover(item) for item in value]
    return value


def encode_frame(message: dict) -> bytes:
    """Serialize one line within MAX_FRAME_BYTES.

    Covers are size-checked before they are attached, so a frame over budget
    should not happen; if it does, the card state still travels, without the
    picture, instead of a line no consumer will accept.

    A frame is always valid UTF-8. Its strings may quote a card (a note
    about a card's file) or a request, and JSON can spell a code point that
    UTF-8 cannot carry (a lone surrogate): each is sent as "?", so that
    nothing a card or a request holds can make a frame fail to encode, and
    no consumer is handed text it cannot decode.
    """
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8", "replace")
    if len(data) > MAX_FRAME_BYTES:
        log(f"warning: frame of {len(data)} bytes exceeds {MAX_FRAME_BYTES}; cover omitted")
        data = (json.dumps(_without_cover(message), ensure_ascii=False) + "\n").encode("utf-8", "replace")
    return data


class Outbox:
    """Per-client write queues for nonblocking sockets.

    A partial write (the peer has not drained its buffer yet) keeps the rest
    queued and asks the selector for a writable event instead of dropping the
    client; a peer that leaves more than `limit` bytes unread is dropped.
    send()/flush() return False when the client must be dropped.
    """

    def __init__(self, limit: int = OUTBOX_MAX_BYTES):
        self.pending: Dict[socket.socket, bytearray] = {}
        self.limit = limit
        self.selector: Optional[selectors.BaseSelector] = None

    def send(self, conn: socket.socket, data: bytes) -> bool:
        self.pending.setdefault(conn, bytearray()).extend(data)
        return self.flush(conn)

    def flush(self, conn: socket.socket) -> bool:
        buf = self.pending.get(conn)
        if buf is None:
            return True
        while buf:
            try:
                sent = conn.send(buf)
            except BlockingIOError:
                break
            except OSError:
                return False
            del buf[:sent]
        if len(buf) > self.limit:
            return False
        self._interest(conn, bool(buf))
        return True

    def interest(self, conn: socket.socket) -> int:
        return selectors.EVENT_READ | (selectors.EVENT_WRITE if self.pending.get(conn) else 0)

    def forget(self, conn: socket.socket) -> None:
        self.pending.pop(conn, None)

    def total(self) -> int:
        """Bytes all clients leave unread."""
        return sum(len(queued) for queued in self.pending.values())

    def most_behind(self) -> Optional[socket.socket]:
        """The client with the most unread bytes, or None when none has any.

        Of clients equally behind, the one that connected last: the
        long-lived readers (the launcher's feed, the shell) connected first,
        and a frame just sent leaves them as far behind as any other until
        they read it.
        """
        conn = max(reversed(list(self.pending)), key=lambda key: len(self.pending[key]), default=None)
        return conn if conn is not None and self.pending[conn] else None

    def _interest(self, conn: socket.socket, want_write: bool) -> None:
        if self.selector is None:
            return
        try:
            key = self.selector.get_key(conn)
        except (KeyError, ValueError):
            return   # not registered yet (snapshot sent from accept); the loop registers with interest()
        mask = selectors.EVENT_READ | (selectors.EVENT_WRITE if want_write else 0)
        if key.events != mask:
            self.selector.modify(conn, mask, key.data)


class Server:
    """Newline-delimited JSON over a UNIX socket; snapshot on connect, events after."""

    def __init__(self, path: Path, group: str):
        self.path = path
        self.group = group
        self.clients: List[socket.socket] = []
        self.listener: Optional[socket.socket] = None
        self.manager: Optional[CardManager] = None
        self.outbox = Outbox()
        self._selector: Optional[selectors.BaseSelector] = None

    @property
    def selector(self) -> Optional[selectors.BaseSelector]:
        return self._selector

    @selector.setter
    def selector(self, value: Optional[selectors.BaseSelector]) -> None:
        self._selector = value
        self.outbox.selector = value

    def drop(self, conn: socket.socket) -> None:
        """Forget a client everywhere: a closed fd number is reused by the next accept."""
        if conn in self.clients:
            self.clients.remove(conn)
        self.outbox.forget(conn)
        if self.selector is not None:
            try:
                self.selector.unregister(conn)
            except (KeyError, ValueError):
                pass
        conn.close()

    def start(self) -> socket.socket:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o660)
        try:
            import grp
            os.chown(self.path, 0, grp.getgrnam(self.group).gr_gid)
        except (KeyError, PermissionError, ImportError):
            log(f"warning: could not chown socket to group {self.group}")
        listener.listen(8)
        listener.setblocking(False)
        self.listener = listener
        return listener

    def accept(self) -> Optional[socket.socket]:
        conn, _ = self.listener.accept()
        if len(self.clients) >= MAX_CLIENTS:
            behind = self.outbox.most_behind()
            if behind is None:
                log(f"refusing a connection: {len(self.clients)} clients already read card events")
                conn.close()
                return None
            log("dropping a client that does not read its card events, to admit a new connection")
            self.drop(behind)
        conn.setblocking(False)
        self.clients.append(conn)
        if self.manager:
            self._send(conn, self.manager.snapshot())
        # The snapshot send may already have dropped a dead peer.
        return conn if conn in self.clients else None

    def broadcast(self, message: dict) -> None:
        frame = encode_frame(message)
        self._make_room(len(frame), everyone=True)
        for conn in list(self.clients):
            self._send_frame(conn, frame)

    def _make_room(self, frame: int, everyone: bool) -> None:
        """Drop the clients furthest behind until a frame of `frame` bytes
        fits, sent to every client or to one.

        Judged before a frame goes out, on what each client has left unread
        of earlier frames: during a broadcast the client sent the frame first
        would otherwise look furthest behind only for having been first.
        """
        while self.outbox.total() + frame * (len(self.clients) if everyone else 1) > OUTBOX_TOTAL_MAX_BYTES:
            behind = self.outbox.most_behind()
            if behind is None:
                return
            log(f"dropping the client furthest behind: clients would leave over {OUTBOX_TOTAL_MAX_BYTES} bytes unread")
            self.drop(behind)

    def _send(self, conn: socket.socket, message: dict) -> None:
        frame = encode_frame(message)
        self._make_room(len(frame), everyone=False)
        if conn in self.clients:
            self._send_frame(conn, frame)

    def _send_frame(self, conn: socket.socket, frame: bytes) -> None:
        if not self.outbox.send(conn, frame):
            log("dropping a client that does not read its card events")
            self.drop(conn)
            return
        queued = len(self.outbox.pending.get(conn, b""))
        if queued:
            # Evidence that backpressure was handled, not dropped; one line per large frame at most.
            log(f"client fd {conn.fileno()}: {queued} of {len(frame)} bytes queued until the socket is writable")

    def interest(self, conn: socket.socket) -> int:
        """Selector mask for a just-accepted client (the snapshot may be partly queued)."""
        return self.outbox.interest(conn)

    def on_event(self, conn: socket.socket, mask: int) -> None:
        if mask & selectors.EVENT_WRITE and not self.outbox.flush(conn):
            self.drop(conn)
            return
        if mask & selectors.EVENT_READ:
            self.drop_dead(conn)

    def drop_dead(self, conn: socket.socket) -> None:
        try:
            if conn.recv(4096) == b"":
                raise OSError("closed")
        except BlockingIOError:
            return
        except OSError:
            self.drop(conn)


class ControlServer:
    """Root-only request socket (mode 0600). One JSON request per line, one reply."""

    REPLIES = {"stage": "staged", "save": "saved", "release": "released"}

    def __init__(self, path: Path, manager: CardManager):
        self.path = path
        self.manager = manager
        self.listener: Optional[socket.socket] = None
        self.buffers: Dict[socket.socket, bytes] = {}
        self.outbox = Outbox()
        self._selector: Optional[selectors.BaseSelector] = None

    @property
    def selector(self) -> Optional[selectors.BaseSelector]:
        return self._selector

    @selector.setter
    def selector(self, value: Optional[selectors.BaseSelector]) -> None:
        self._selector = value
        self.outbox.selector = value

    def drop(self, conn: socket.socket) -> None:
        self.buffers.pop(conn, None)
        self.outbox.forget(conn)
        if self.selector is not None:
            try:
                self.selector.unregister(conn)
            except (KeyError, ValueError):
                pass
        conn.close()

    def start(self) -> socket.socket:
        if self.path.exists():
            self.path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        listener.listen(4)
        listener.setblocking(False)
        self.listener = listener
        return listener

    def accept(self) -> socket.socket:
        conn, _ = self.listener.accept()
        conn.setblocking(False)
        self.buffers[conn] = b""
        return conn

    def on_event(self, conn: socket.socket, mask: int) -> bool:
        if mask & selectors.EVENT_WRITE and not self.outbox.flush(conn):
            self.drop(conn)
            return False
        if mask & selectors.EVENT_READ:
            return self.handle(conn)
        return True

    def handle(self, conn: socket.socket) -> bool:
        """Read available bytes; dispatch complete lines. Returns False when the peer closed."""
        try:
            data = conn.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:
            self.drop(conn)
            return False
        self.buffers[conn] += data
        # A save's line carries the payload with a space after each separator,
        # as the envelope does: the same bound admits every save within its limit.
        if len(self.buffers[conn]) > SAVE_FILE_MAX_BYTES and b"\n" not in self.buffers[conn]:
            self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "Petición demasiado grande"}})
            self.drop(conn)
            return False
        # A reply that cannot be sent drops the connection, its buffer with it.
        while b"\n" in self.buffers.get(conn, b""):
            line, _, self.buffers[conn] = self.buffers[conn].partition(b"\n")
            try:
                request = json.loads(line)
            except (ValueError, RecursionError):     # RecursionError: nested deeper than the parser goes
                self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "JSON inválido"}})
                continue
            if not isinstance(request, dict):
                self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "La petición no es un objeto JSON"}})
                continue
            kind = request.get("type")
            try:
                if kind == "stage":
                    self.manager.stage(request, lambda message, c=conn: self._reply(c, message))
                elif kind == "save":
                    self.manager.save(request, lambda message, c=conn: self._reply(c, message))
                elif kind == "release":
                    self.manager.release(request, lambda message, c=conn: self._reply(c, message))
                else:
                    self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "Petición desconocida"}})
            except Exception as exc:  # noqa: BLE001 - this request's failure, said to who asked; not the service's
                log(f"internal error handling a {kind} request; it fails and the service goes on\n"
                    f"{traceback.format_exc().rstrip()}")
                self._reply(conn, {"type": self.REPLIES.get(str(kind), "error"), "session": request.get("session"), "ok": False,
                                   "error": {"code": "internal_error", "message": "Fallo interno del servicio de tarjetas",
                                             "detail": type(exc).__name__}})
        return conn in self.buffers

    def _reply(self, conn: socket.socket, message: dict) -> None:
        if conn not in self.buffers:
            return   # peer already gone (e.g. a stage reply after the launcher disconnected)
        if not self.outbox.send(conn, encode_frame(message)):
            self.drop(conn)


# ------------------------------------------------------------------ udev monitor

def start_udev_monitor(callback: Callable[[str, str, str, str], None]) -> Optional[threading.Thread]:
    """Feed add/remove block events to `callback(action, device, serial, path)`."""
    try:
        import pyudev
    except ImportError:
        log("pyudev not available; relying on periodic rescans only")
        return None
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by("block", "disk")

    def loop() -> None:
        for device in iter(monitor.poll, None):
            name = device.sys_name
            if device.action == "add":
                callback("add", name, card_serial(name), device.device_node or f"/dev/{name}")
            elif device.action == "remove":
                callback("remove", name, "", "")
    thread = threading.Thread(target=loop, name="udev", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------ main loop

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--socket", default=str(SOCKET_PATH))
    parser.add_argument("--mount-root", default=str(MOUNT_ROOT))
    parser.add_argument("--rescan", type=float, default=5.0, help="seconds between sysfs rescans (safety net)")
    args = parser.parse_args(argv)

    queue: List[Callable[[], None]] = []
    queue_lock = threading.Lock()
    wake_r, wake_w = socket.socketpair()

    def schedule(fn: Callable[[], None]) -> None:
        with queue_lock:
            queue.append(fn)
        try:
            wake_w.send(b"x")
        except OSError:
            pass

    server = Server(Path(args.socket), SOCKET_GROUP)
    clear_shape_root(SHAPE_ROOT)
    manager = CardManager(Mounter(Path(args.mount_root)), validate_mount, server.broadcast, schedule,
                          shape_root=SHAPE_ROOT)
    server.manager = manager
    listener = server.start()
    control = ControlServer(CONTROL_SOCKET_PATH, manager)
    control_listener = control.start()
    log(f"mun-cardd ready: socket {args.socket}, mounts under {args.mount_root}, "
        f"serial prefix {SERIAL_PREFIX}, validation delay {VALIDATION_DELAY}s, "
        f"readers: {', '.join('/' + slot for slot in SLOTS) or 'none (virtio cards only)'}")

    def on_udev(action: str, device: str, serial: str, path: str) -> None:
        if action == "add":
            schedule(lambda: manager.device_added(device, serial, path))
        else:
            schedule(lambda: manager.device_removed(device))
    start_udev_monitor(on_udev)

    def rescan() -> None:
        present = {name: (serial, path) for name, serial, path in enumerate_devices()}
        for name, (serial, path) in present.items():
            manager.device_added(name, serial, path)
        for name in list(manager.cards):
            if name not in present:
                manager.device_removed(name)
    guarded("the first device scan", rescan)  # cards already inserted when the service starts

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    selector = selectors.DefaultSelector()
    server.selector = selector
    control.selector = selector
    selector.register(listener, selectors.EVENT_READ, "accept")
    selector.register(control_listener, selectors.EVENT_READ, "control-accept")
    selector.register(wake_r, selectors.EVENT_READ, "wake")
    last_rescan = time.monotonic()
    while not stop["flag"]:
        for key, mask in selector.select(timeout=0.5):
            if key.data == "accept":
                conn = server.accept()
                if conn is not None:
                    selector.register(conn, server.interest(conn), "client")
            elif key.data == "control-accept":
                selector.register(control.accept(), selectors.EVENT_READ, "control")
            elif key.data == "control":
                # drop() unregisters on close. A connection whose event could
                # not be handled is closed: left registered, the selector
                # would hand the same event back at once, without end.
                if not guarded("a control connection", lambda: control.on_event(key.fileobj, mask)):
                    control.drop(key.fileobj)
            elif key.data == "wake":
                wake_r.recv(4096)
                with queue_lock:
                    pending, queue[:] = queue[:], []
                for fn in pending:
                    # Results of worker threads, timers and device events:
                    # one that raises does not take the others with it.
                    guarded("a scheduled callback", fn)
            elif not guarded("a client connection", lambda: server.on_event(key.fileobj, mask)):
                server.drop(key.fileobj)
        if args.rescan and time.monotonic() - last_rescan >= args.rescan:
            guarded("a device rescan", rescan)
            last_rescan = time.monotonic()
        if manager.mounter.__dict__.get("stalled"):
            guarded("ending stalled mount commands", manager.mounter.reap)
        if manager._due:
            guarded("timers due", manager.run_due)
        if manager.unmounts_owed:
            guarded("owed unmounts", manager.retry_unmounts)

    manager.stop_shape()
    for card in [*manager.cards.values(), *manager.unmounts_owed.values()]:
        manager.mounter.unmount(card)
    clear_shape_root(SHAPE_ROOT)
    selector.close()
    for sock in (listener, control_listener, wake_r, wake_w, *server.clients, *control.buffers):
        sock.close()
    for path in (Path(args.socket), CONTROL_SOCKET_PATH):
        try:
            path.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
