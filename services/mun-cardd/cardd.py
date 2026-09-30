#!/usr/bin/env python3
"""mun-cardd: detects lab Game Cards, validates them and tells the shell.

Responsibilities (docs/game-cards.md): watch block devices, admit only virtio disks whose
serial starts with `NPT-`, check the ext4 superblock, mount read-only under a
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
                           files_payload_problem, save_bounds)

PROTOCOL = 1
SERIAL_PREFIX = "NPT-"
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
MOUNT_OPTIONS = "ro,nosuid,nodev,noexec"
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


def log(message: str) -> None:
    print(message, flush=True)


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
        self._slot_counter = 0
        # MUN Shape export, off unless a root is given (main() gives SHAPE_ROOT).
        # `later(seconds, fn)` runs fn on the manager thread after a delay;
        # `remove_export(path)` deletes an export off this thread.
        self.shape_root = shape_root
        self.later = later or self._timer
        self.remove_export = remove_export or _remove_in_background
        self._shape_running: Optional["ShapeExport"] = None   # at most one copy reads a card at a time
        self._shape_deferred: Optional[Card] = None           # a valid card waiting for that copy to end

    def _timer(self, seconds: float, fn: Callable[[], None]) -> None:
        timer = threading.Timer(seconds, lambda: self.schedule(fn))
        timer.daemon = True
        timer.start()

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
        self._drop_shape(card)
        if card.release_reply is not None and not card.saving:
            # A safe release that was waiting for the Shape copy: the card went
            # before it could finish, so this was not a safe removal.
            reply, card.release_reply = card.release_reply, None
            card.release_timer += 1
            reply({"type": "released", "ok": False, "slot": card.slot, "serial": card.serial,
                   "error": {"code": "card_removed", "message": "Se retiró la tarjeta antes de terminar la expulsión segura"}})
        self.mounter.unmount(card)
        self.publish({"type": "removed", "slot": card.slot, "insertion": card.insertion, "device": device, "serial": card.serial})
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
            self.schedule(lambda: reply({"type": "staged", "session": request.get("session"), **outcome}))
        threading.Thread(target=work, name=f"stage-{slot}", daemon=True).start()

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
        payload_max, _ = save_bounds(info)
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
            payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > payload_max:
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
        data = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
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
            self.schedule(lambda: self._saved(card, request, outcome, reply))
        threading.Thread(target=work, name=f"save-{card.slot}", daemon=True).start()

    def _saved(self, card: Card, request: dict, outcome: dict, reply: Callable[[dict], None]) -> None:
        card.saving = False
        log(f"save on {card.slot} ({card.serial}) for session {request.get('session')}: "
            + ("ok, " + str(outcome.get("bytes")) + " bytes" if outcome.get("ok")
               else "failed: " + outcome["error"]["code"] + (" (" + outcome["error"].get("detail", "") + ")" if outcome["error"].get("detail") else "")))
        reply({"type": "saved", "session": request.get("session"), **outcome})
        if card.releasing and card.release_reply is not None:
            self._release_now(card)

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
            return reply({"type": "released", "ok": True, "detail": "no such card"})
        card.releasing = True
        card.release_reply = reply
        card.release_timer += 1
        self._cancel_shape(card)
        if card.saving:
            log(f"release of {card.slot} waits for the save in flight")
            return
        if self._shape_reading(card):
            log(f"release of {card.slot} waits for the Shape copy to close its files")
            token = card.release_timer
            self.later(SHAPE_RELEASE_WAIT, lambda: self._release_overdue(card, token))
            return
        self._release_now(card)

    def _release_overdue(self, card: Card, token: int) -> None:
        if token != card.release_timer or not card.releasing or card.release_reply is None \
                or not self._shape_reading(card):
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
            self.mounter.unmount(card, strict=True)
        except CardError as exc:
            # Not released: the card keeps its state and mount, writes are
            # allowed again, and the caller must not unplug. It may retry.
            card.releasing = False
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
        threading.Thread(target=work, name=f"validate-{card.slot}", daemon=True).start()

    def _finish(self, card: Card, generation: int, info: Optional[dict], error: Optional[dict]) -> None:
        if card.generation != generation or self.cards.get(card.device) is not card:
            log(f"discarding stale result for {card.slot} ({card.device})")
            return
        if error is not None:
            card.state, card.error, card.info = "invalid", error, None
            self.mounter.unmount(card)
        else:
            card.state, card.info, card.error = "valid", info, None
        self._emit(card)
        if error is None:
            # Only now, with the card's state published: nothing about Shape
            # delays `valid`, Play or Eject safely.
            self._start_shape(card)

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
        export = ShapeExport(self.shape_root, Path(card.mount), card.info["root"], card.insertion,
                             card.info.get("version"), card.generation, lambda: card.generation)
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
        if card.releasing and card.export is None and not card.saving and self.cards.get(card.device) is card:
            self._release_now(card)     # a release was waiting for this copy
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
    `create`, the per-game directory is made (never through a link). Returns
    the descriptor; the caller closes it. Raises CardError("save_path_unsafe")
    or FileNotFoundError (without `create`) when the game directory is absent.
    """
    if not SAVE_ID.fullmatch(str(info.get("id", ""))):
        raise CardError("save_path_unsafe", "El identificador del juego no sirve como ruta")
    parts = [part for part in str(info.get("saves", "saves")).split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise CardError("save_path_unsafe", "La ruta de partidas sale de la tarjeta")
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts:
            nxt = _open_dir_component(fd, part)
            os.close(fd)
            fd = nxt
        game = str(info["id"])
        try:
            nxt = _open_dir_component(fd, game)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(game, mode=0o755, dir_fd=fd)
            nxt = _open_dir_component(fd, game)
        os.close(fd)
        return nxt
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
        document = json.loads(data)
    except ValueError:
        return False
    if not isinstance(document, dict) or document.get("format") != save_format(info or {}):
        return False
    if document.get("payload_kind") == "files":
        max_bytes = int((info or {}).get("saves_max_bytes") or 0)
        return max_bytes > 0 and files_payload_problem(document.get("payload"), max_bytes) is None
    return True


def _other_generation(data: Optional[bytes], info: dict) -> Optional[str]:
    """The format of `data` when it is a console envelope of the other naming
    generation than the card's (docs/game-cards.md), else None. Anything that does not
    even parse is not this: it stays a damaged save, as before."""
    if data is None:
        return None
    try:
        document = json.loads(data)
    except ValueError:
        return None
    found = document.get("format") if isinstance(document, dict) else None
    if found in SAVE_FORMATS.values() and found != save_format(info):
        return found
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
            previous = _read_regular(dir_fd, "save.json.prev", limit + 1)
            if previous is None or not _is_envelope(previous, limit, info):
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
    if recovered:
        # Mark the copy so the game can tell the player it is the previous save.
        document = json.loads(data)
        document["recovered_from"] = "save.json.prev"
        data = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
    owner = os.stat(target.parent)
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(data)
        while view:          # a directory save's envelope can be megabytes
            view = view[os.write(out, view):]
        os.fchown(out, owner.st_uid, owner.st_gid)
    finally:
        os.close(out)
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
    mounter.remount(card, rw=True)
    dir_fd = -1

    def back_to_read_only() -> None:
        # Best effort on the failure path: the card may already be gone.
        try:
            mounter.remount(card, rw=False)
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
    except CardError:
        if dir_fd >= 0:
            _unlink_quiet(dir_fd, "save.json.tmp")
        back_to_read_only()
        raise
    except OSError as exc:
        if dir_fd >= 0:
            _unlink_quiet(dir_fd, "save.json.tmp")
        back_to_read_only()
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
    # fails the player is told, even though the bytes may be on the card.
    mounter.remount(card, rw=False)
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
    and in chunks, into `.<insertion>.part/` (0700) under the root; keeps
    only the files the checker accepted and adds the normalised
    `shape.json`; makes files 0440 and folders 0550 (group: the shell's);
    and publishes `<insertion>/` with one rename. Anything that fails,
    including cancellation, ends with the staging directory deleted and
    nothing published. `finished` is set on the manager thread when it
    receives the outcome; `done` by the worker as its very last step."""

    def __init__(self, root: Path, mount: Path, content_root: str, insertion: str, version: Optional[str],
                 generation: int, current_generation: Callable[[], int]):
        self.root, self.mount, self.content_root = root, mount, content_root
        self.insertion, self.version, self.generation = insertion, version, generation
        self.current_generation = current_generation
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.finished = False
        self.staging = root / f".{insertion}.part"
        self.final = root / insertion
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
        try:
            on_done({"record": record, "notes": notes, "seconds": time.monotonic() - started})
        finally:
            self.done.set()

    def _copy(self):
        self.check()
        self.root.mkdir(mode=0o755, parents=True, exist_ok=True)
        source = _StagingSource(self.mount, f"{self.content_root.strip('/')}/{shape.PACKAGE_DIR}", self.staging, self.check)
        result = shape.check_card(source, self.content_root)
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
        document = dict(result.shape, insertion=self.insertion, version=self.version)
        out = os.open(self.staging / shape.MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      0o600)
        with os.fdopen(out, "wb") as handle:
            handle.write((json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        group = _shape_group()
        _seal(self.staging, group)
        self.check()
        os.rename(self.staging, self.final)       # complete and immutable, or nothing
        self.published = self.final
        os.chmod(self.final, 0o550)
        if group is not None:
            os.chown(self.final, -1, group)
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
    """Files 0440 and folders 0550, group the shell's; the top folder is
    sealed after it is renamed."""
    for directory, folders, files in os.walk(staging, topdown=False):
        for name in files:
            path = os.path.join(directory, name)
            os.chmod(path, 0o440)
            if group is not None:
                os.chown(path, -1, group)
        if directory != str(staging):
            os.chmod(directory, 0o550)
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
    threading.Thread(target=_remove_tree, args=(path,), name="shape-remove", daemon=True).start()


def clear_shape_root(root: Path) -> None:
    """At service start and stop: no export outlives the service that made
    it, `.part` copies included."""
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    for entry in os.listdir(root):
        _remove_tree(root / entry)


# ------------------------------------------------------------------ platform bits

class Mounter:
    """Real mounts under MOUNT_ROOT. Slot names come from the manager, never from the card."""

    def __init__(self, root: Path = MOUNT_ROOT):
        self.root = root

    def device_path(self, device: str) -> str:
        return f"/dev/{device}"

    def mount(self, card: Card, path: str) -> str:
        ext4.check_mountable(Path(path))
        target = self.root / card.slot
        target.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["mount", "-t", "ext4", "-o", MOUNT_OPTIONS, path, str(target)],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise CardError("mount_failed", "No se pudo montar la tarjeta en solo lectura", result.stderr.strip())
        return str(target)

    def remount(self, card: Card, rw: bool) -> None:
        if not card.mount:
            raise CardError("card_removed", "La tarjeta ya no está montada")
        result = subprocess.run(["mount", "-o", "remount," + ("rw" if rw else "ro"), card.mount],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise CardError("sync_failed" if not rw else "write_failed",
                            "No se pudo cambiar el modo de la tarjeta", result.stderr.strip())

    def unmount(self, card: Card, strict: bool = False) -> None:
        """Unmount the card.

        `strict` is the safe-removal path: a plain umount must succeed, or a
        CardError("unmount_failed") is raised and the mount stays tracked so the
        caller can report and retry. Without `strict` (the device is already
        gone) a failed umount falls back to a lazy detach and the mount is
        forgotten regardless: there is nothing left to be careful with.
        """
        if not card.mount:
            return
        target = card.mount
        result = subprocess.run(["umount", target], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            if strict:
                raise CardError("unmount_failed", "No se pudo desmontar la tarjeta de forma segura",
                                (result.stderr or result.stdout).strip() or f"umount exit {result.returncode}")
            subprocess.run(["umount", "-l", target], capture_output=True, check=False)
        card.mount = None
        try:
            Path(target).rmdir()
        except OSError:
            pass


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
        return Path(f"/sys/block/{device}/serial").read_text().strip()
    except OSError:
        return ""


def enumerate_devices() -> List[tuple]:
    found = []
    for entry in sorted(Path("/sys/block").iterdir()):
        name = entry.name
        if not name.startswith("vd"):
            continue
        serial = block_serial(name)
        if serial.startswith(SERIAL_PREFIX):
            found.append((name, serial, f"/dev/{name}"))
    return found


# ------------------------------------------------------------------ socket server

# One JSON line, including a base64 cover: a 1 MiB PNG (the validator's limit)
# becomes ~1.37 MiB of base64 plus metadata. Every consumer of this socket
# (shell CardClient, launchd feed reader) accepts at least this much per line.
MAX_FRAME_BYTES = 2 * 1024 * 1024
# Bytes a client may leave unread before it is considered dead and dropped.
OUTBOX_MAX_BYTES = 8 * 1024 * 1024


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
    """
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_FRAME_BYTES:
        log(f"warning: frame of {len(data)} bytes exceeds {MAX_FRAME_BYTES}; cover omitted")
        data = (json.dumps(_without_cover(message), ensure_ascii=False) + "\n").encode("utf-8")
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
        conn.setblocking(False)
        self.clients.append(conn)
        if self.manager:
            self._send(conn, self.manager.snapshot())
        # The snapshot send may already have dropped a dead peer.
        return conn if conn in self.clients else None

    def broadcast(self, message: dict) -> None:
        for conn in list(self.clients):
            self._send(conn, message)

    def _send(self, conn: socket.socket, message: dict) -> None:
        frame = encode_frame(message)
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
        if len(self.buffers[conn]) > SAVE_MAX_BYTES + 8192 and b"\n" not in self.buffers[conn]:
            self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "Petición demasiado grande"}})
            self.drop(conn)
            return False
        while b"\n" in self.buffers[conn]:
            line, _, self.buffers[conn] = self.buffers[conn].partition(b"\n")
            try:
                request = json.loads(line)
            except ValueError:
                self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "JSON inválido"}})
                continue
            if request.get("type") == "stage":
                self.manager.stage(request, lambda message, c=conn: self._reply(c, message))
            elif request.get("type") == "save":
                self.manager.save(request, lambda message, c=conn: self._reply(c, message))
            elif request.get("type") == "release":
                self.manager.release(request, lambda message, c=conn: self._reply(c, message))
            else:
                self._reply(conn, {"type": "error", "error": {"code": "bad_request", "message": "Petición desconocida"}})
        return True

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
                callback("add", name, block_serial(name), device.device_node or f"/dev/{name}")
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
        f"serial prefix {SERIAL_PREFIX}, validation delay {VALIDATION_DELAY}s")

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
    rescan()  # cards already inserted when the service starts

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
                control.on_event(key.fileobj, mask)   # drop() unregisters on close
            elif key.data == "wake":
                wake_r.recv(4096)
                with queue_lock:
                    pending, queue[:] = queue[:], []
                for fn in pending:
                    fn()
            else:
                server.on_event(key.fileobj, mask)
        if args.rescan and time.monotonic() - last_rescan >= args.rescan:
            rescan()
            last_rescan = time.monotonic()

    manager.stop_shape()
    for card in list(manager.cards.values()):
        manager.mounter.unmount(card)
    clear_shape_root(SHAPE_ROOT)
    for path in (Path(args.socket), CONTROL_SOCKET_PATH):
        try:
            path.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
