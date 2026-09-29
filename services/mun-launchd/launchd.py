#!/usr/bin/env python3
"""mun-launchd: starts a game from the active Game Card and gets the shell back.

Contract (docs/runtime.md): the shell asks; this service checks the card with
mun-cardd at that moment, has cardd stage a bounded copy of the entry
into a launcher-owned directory on an exec-capable tmpfs, stops the shell,
runs the copy as the unprivileged `mun-game` user in a transient systemd
unit (own cgroup, device allow-list, no network), and when the unit ends for
any reason it removes the copy and starts the shell again with a result the
shell shows. A removed card or a lost card service ends the session.

`SessionManager` holds the state machine with injected platform pieces so
host tests can drive it; `main()` wires systemd, sockets and files.
"""

import argparse
import base64
import calendar
import ctypes
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import socket
import stat as statmod
import struct
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ElementTree
import zlib
from pathlib import Path
from typing import Callable, Dict, List, Optional

PROTOCOL = 1
SOCKET_PATH = Path(os.environ.get("MUN_LAUNCHD_SOCKET", "/run/mun/launchd.sock"))
SOCKET_GROUP = "mun-shell"
CARDD_SOCKET = Path(os.environ.get("MUN_CARDD_SOCKET", "/run/mun/cardd.sock"))
CARDD_CONTROL = Path(os.environ.get("MUN_CARDD_CONTROL_SOCKET", "/run/mun/cardd-control.sock"))
LAUNCH_ROOT = Path(os.environ.get("MUN_LAUNCH_ROOT", "/run/mun/launch"))
RESULT_FILE = LAUNCH_ROOT / "last-result.json"
GAME_USER = "mun-game"
SHELL_UNIT = "mun-shell.service"
UNIT_PREFIX = "mun-game-"
# Cleanup after a game unit, as a unit of its own (deploy/mun-launch-cleanup@.service)
# started by the game unit's OnSuccess=/OnFailure=; see unit_properties().
CLEANUP_UNIT = "mun-launch-cleanup@{sid}.service"
# How long the launcher keeps watching a session's cleanup after the session
# ended. The unit bounds its own run (TimeoutStartSec=10s, then it fails);
# this is the backstop for a cleanup that never started or never ends.
CLEANUP_TIMEOUT = 15.0
# Where a mount-access game sees its Game Card inside the unit (docs/runtime.md): a
# fixed path, so a game's data location can be built into it. systemd mounts
# the card's block device there (MountImages=) read-only, nosuid, nodev,
# noexec, in the unit's own mount namespace, and the mount goes with the
# unit. The card service's own mount lives in its private namespace and
# cannot be bound from outside; the device can. Same block device, same
# superblock: the service's read-write window for a save (docs/saves.md) never
# reaches the game, whose mount stays read-only on its own flags.
CONTENT_MOUNT = Path(os.environ.get("MUN_CONTENT_MOUNT", "/run/mun/card"))
# The same place under its name from before the MUN naming (docs/game-cards.md): a
# symbolic link to CONTENT_MOUNT that deploy/tmpfiles.conf creates, for games
# with /run/neptune/card compiled in. Resolved inside the game's namespace,
# where CONTENT_MOUNT is the mounted card.
LEGACY_CONTENT_MOUNT = Path("/run/neptune/card")
_DEVICE_NODE = re.compile(r"^/dev/[a-z][a-z0-9]*$")
_CONTENT_ROOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


def content_grant(content) -> Optional[dict]:
    """Check a staging reply's content description: {"device": "/dev/vdX",
    "root": "content"}; returns it, or None when it is not acceptable."""
    if not isinstance(content, dict):
        return None
    device, root = content.get("device"), content.get("root")
    if not isinstance(device, str) or not _DEVICE_NODE.match(device):
        return None
    if not isinstance(root, str) or not _CONTENT_ROOT.match(root) or ".." in root.split("/"):
        return None
    return {"device": device, "root": root}

PREPARE_TIMEOUT = 20.0     # staging must finish within this
START_TIMEOUT = 10.0       # unit must become active within this
STOP_TIMEOUT = 3.0         # SIGTERM grace before SIGKILL (also in the unit)
# cardd unreachable (or unable to confirm the session's card) for longer than
# this ends the session. The environment variable is a lab hook for timing
# experiments that would otherwise be decided by the clock instead of by the
# identity check; unset it is the 5 s policy.
READER_GRACE = float(os.environ.get("MUN_LAUNCHD_READER_GRACE", "5.0"))
POLL_INTERVAL = 0.5
# Largest line accepted from cardd's feed: the same budget cardd enforces when
# it serializes (a 1 MiB cover as base64 plus metadata). A longer unfinished
# line means the peer is not speaking the protocol; the feed is reconnected.
MAX_FRAME_BYTES = 2 * 1024 * 1024
# A game's save payload (docs/saves.md); the request line may carry it plus a little framing.
SAVE_MAX_BYTES = 64 * 1024
SAVE_LINE_MAX = SAVE_MAX_BYTES + 4096
SAVE_TIMEOUT = 20.0                   # the card service must answer a save within this
# Save envelope formats by the card's naming generation (docs/game-cards.md), as the
# card service validated it: "earlier" for cards with neptune.toml, "mun" for
# cards with mun.toml. A session record without it is from before and is earlier.
SAVE_FORMATS = {"earlier": "neptune-save/1", "mun": "mun-save/1"}


def card_naming(record: dict) -> str:
    naming = record.get("naming")
    return naming if naming in SAVE_FORMATS else "earlier"


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------- state machine

class SessionManager:
    """States: idle → preparing → starting → running → stopping → idle."""

    def __init__(self, cards: "CardView", stager: Callable[[dict, Callable[[dict], None]], None],
                 runner: "Runner", shell: "ShellControl", publish: Callable[[dict], None],
                 schedule: Callable[[Callable[[], None]], None], clock: Callable[[], float] = time.monotonic,
                 results: Optional["ResultStore"] = None,
                 saver: Optional[Callable[[dict, Callable[[dict], None]], None]] = None):
        self.cards = cards
        self.stager = stager
        self.saver = saver or stager
        # Set by main(): open/close the per-session save socket in the event loop.
        self.gate_open: Optional[Callable[[str], None]] = None
        self.gate_close: Optional[Callable[[], None]] = None
        self.runner = runner
        self.shell = shell
        self.publish = publish
        self.schedule = schedule
        self.clock = clock
        self.results = results or ResultStore(None)
        self.state = "idle"
        self.session: Optional[dict] = None
        self.reader_lost_at: Optional[float] = None
        self.deadline: Optional[float] = None
        # Directory saves (docs/saves.md): the session's save directory, and the
        # result held back until its last capture has been answered.
        self.sync: Optional[DirectorySync] = None
        self.pending_result: Optional[dict] = None
        self.saves_failure: Optional[dict] = None
        self.sync_factory: Callable[..., DirectorySync] = DirectorySync
        # Cleanup units whose outcome is not known yet, by session id, with the
        # time after which the launcher stops waiting (docs/runtime.md). A
        # session may be over before its cleanup is; the id keeps any later
        # warning with that session's result.
        self.cleanups: Dict[str, float] = {}

    # --- saves (docs/saves.md) --------------------------------------------------------
    def save_request(self, sid: str, request: dict, reply: Callable[[dict], None]) -> None:
        """A save asked by the game of session `sid` over its own socket.

        Accepted only while that session is running; forwarded to the card
        service bound to the session's insertion, so a card that replaced the
        original cannot receive it. One save at a time per session. The reply
        is delivered only if the session still exists when it comes back.
        """
        def fail(code: str, message: str) -> None:
            reply({"type": "saved", "ok": False, "error": {"code": code, "message": message}})
        if not self.session or self.session["id"] != sid or self.state != "running":
            return fail("no_session", "No hay una sesión de juego en curso")
        if self.session.get("saves"):
            return fail("save_invalid", "Las partidas de este juego las guarda la consola")
        if not self.cards.connected:
            return fail("reader_unavailable", "El lector de tarjetas no está disponible")
        if request.get("type") != "save" or not isinstance(request.get("data"), dict):
            return fail("save_invalid", "La petición de guardado no es válida")
        schema = request.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            return fail("save_invalid", "La versión del esquema de guardado no es válida")
        if len(json.dumps(request["data"], separators=(",", ":")).encode("utf-8")) > SAVE_MAX_BYTES:
            return fail("save_too_large", "La partida supera el tamaño permitido")
        if self.session.get("saving"):
            return fail("save_busy", "Ya hay un guardado en curso")
        self.session["saving"] = True
        forwarded = {"type": "save", "slot": self.session["slot"], "insertion": self.session.get("insertion"),
                     "serial": self.session["serial"], "version": self.session.get("version"),
                     "game": self.session.get("card_id"), "session": sid, "schema": schema, "payload": request["data"]}
        log(f"session {sid}: save requested ({len(json.dumps(request['data']))} bytes, schema {schema})")
        self.saver(forwarded, lambda outcome: self.schedule(lambda: self._save_done(sid, outcome, reply)))

    def _save_done(self, sid: str, outcome: dict, reply: Callable[[dict], None]) -> None:
        if self.session and self.session["id"] == sid:
            self.session["saving"] = False
        if outcome.get("ok"):
            log(f"session {sid}: save completed ({outcome.get('bytes')} bytes)")
        else:
            log(f"session {sid}: save failed: {outcome.get('error', {}).get('code')}")
        if not self.session or self.session["id"] != sid:
            log(f"session {sid}: save reply after the session ended; not delivered")
            return
        reply({"type": "saved", "ok": bool(outcome.get("ok")), **({"bytes": outcome.get("bytes")} if outcome.get("ok") else {"error": outcome.get("error")})})

    def release_request(self, serial: str) -> Optional[dict]:
        """Safe removal asks the launcher first: a card with a live session stays."""
        if self.session and self.session.get("serial") == serial and self.state in ("preparing", "starting", "running", "stopping", "saving"):
            return {"ok": False, "error": {"code": "in_use", "message": "La tarjeta tiene una sesión de juego en curso"}}
        return None

    # --- requests -------------------------------------------------------------
    def launch(self, request: dict) -> dict:
        if self.state != "idle":
            return {"accepted": False, "error": {"code": "busy", "message": "Ya hay una sesión de juego en curso"}}
        if not self.cards.connected:
            return {"accepted": False, "error": {"code": "reader_unavailable", "message": "El lector de tarjetas no está disponible"}}
        card = self.cards.active()
        if card is None or card.get("slot") != request.get("slot") or card.get("serial") != request.get("serial"):
            return {"accepted": False, "error": {"code": "card_mismatch", "message": "La tarjeta solicitada ya no es la tarjeta activa"}}
        info = card.get("info") or {}
        if card.get("state") != "valid" or info.get("kind") != "game" or not info.get("entry"):
            return {"accepted": False, "error": {"code": "not_runnable", "message": "La tarjeta no contiene un juego ejecutable"}}
        if info.get("version") != request.get("version"):
            return {"accepted": False, "error": {"code": "card_mismatch", "message": "La versión del contenido cambió"}}
        try:
            saves = directory_saves_spec(info)
        except ValueError as exc:
            return {"accepted": False, "error": {"code": "not_runnable", "message": "La declaración de partidas de la tarjeta no es válida", "detail": str(exc)}}
        sid = "s" + uuid.uuid4().hex[:10]
        self.session = {"id": sid, "slot": card["slot"], "insertion": card.get("insertion"), "serial": card["serial"],
                        "card_id": info.get("id"), "title": info.get("title"), "version": info.get("version"),
                        "profile": info.get("profile"), "started_at": self.clock(), "saves": saves,
                        "naming": card_naming(info)}
        try:
            dest = self.runner.prepare_dir(sid)
        except OSError as exc:
            self.session = None
            return {"accepted": False, "error": {"code": "prepare_failed", "message": "No se pudo preparar la sesión", "detail": str(exc)}}
        self.session["dest"] = str(dest)
        # The copy is authorised against this one insertion and nothing may move
        # it while the copy is in flight; _staged() refuses a reply otherwise.
        self.session["stage_insertion"] = card.get("insertion")
        self._set_state("preparing")
        self.deadline = self.clock() + PREPARE_TIMEOUT
        log(f"session {sid}: launch accepted for {info.get('id')} {info.get('version')} on {card['slot']} ({card['serial']}); staging")
        self.stager({"type": "stage", "slot": card["slot"], "insertion": card.get("insertion"), "serial": card["serial"],
                     "version": info["version"], "session": sid, "dest": str(dest)},
                    lambda reply: self.schedule(lambda: self._staged(sid, reply)))
        return {"accepted": True, "session": sid}

    def acknowledge(self) -> None:
        self.results.acknowledge()
        self._emit()

    def snapshot(self) -> dict:
        return {"type": "snapshot", "protocol": PROTOCOL, "state": self.state,
                "session": self._public_session(), "last_result": self.results.current()}

    # --- card service events --------------------------------------------------
    def card_removed(self, slot: str, insertion: Optional[str] = None) -> None:
        if not self.session or self.state not in ("preparing", "starting", "running"):
            return
        same = (insertion == self.session.get("insertion")) if insertion and self.session.get("insertion") else (slot == self.session["slot"])
        if same:
            self._end("card_removed", "Se retiró la Game Card durante la sesión")

    def reader_disconnected(self) -> None:
        if self.state in ("preparing", "starting", "running") and self.reader_lost_at is None:
            self.reader_lost_at = self.clock()

    def reader_reconnected(self) -> None:
        if not self.session or self.state not in ("preparing", "starting", "running"):
            self.reader_lost_at = None
            return
        if self.reader_lost_at is None:
            self.reader_lost_at = self.clock()   # bound the verification even if the loss went unnoticed
        self._verify_card()

    def card_changed(self) -> None:
        """A card event arrived while a session waits for its card to be verified."""
        if self.session and self.reader_lost_at is not None and self.cards.connected:
            self._verify_card()

    def _verify_card(self) -> None:
        """Decide continuity once the reader speaks again; wait while it is still reading.

        A restarted reader announces a rescanned card as `reading` first, so an
        immediate verdict would end every session across a reader restart. The
        wait is bounded by the same READER_GRACE deadline (tick()). A session
        still `preparing` is cancelled instead of re-verified: see below.
        """
        card = self.cards.active()
        verdict = self._continuity(card)
        if verdict == "same":
            self.reader_lost_at = None
            return
        if self.state == "preparing":
            # A copy is in flight, authorised against one insertion. Re-verifying
            # by serial/id/version is right for a game already on screen, but here
            # it would let a late success start a copy the present card never
            # produced. Cancel instead: with no session, a reply finds nothing to
            # start. The shell was never stopped while preparing.
            log(f"session {self.session['id']}: card identity changed while preparing "
                f"(copy authorised against {self.session.get('stage_insertion')}, "
                f"now {card.get('insertion') if card else 'no card'}); cancelling")
            self._end("prepare_failed", "La Game Card dejó de ser la misma mientras se preparaba el juego")
            return
        if verdict == "reverified":
            # The reader restarted and re-registered the card under a new
            # insertion; serial, card id and content version all match.
            log(f"session {self.session['id']}: reader restarted; same card re-verified by serial/id/version "
                f"(slot {card.get('slot')}, insertion {card.get('insertion')})")
            self.session["slot"], self.session["insertion"] = card.get("slot"), card.get("insertion")
            self.reader_lost_at = None
            return
        if card is not None and card.get("state") == "reading" and card.get("serial") == self.session.get("serial"):
            return   # same serial still being validated: keep waiting, deadline stays armed
        self._end("card_removed", "La Game Card de la sesión ya no estaba presente al reconectar con el lector")

    def _continuity(self, card: Optional[dict]) -> str:
        """Is `card` the card this session was started from?

        "same": the insertion token matches (issued by the card service for that
        one insertion). "reverified": a different insertion, as after a card
        service restart, but the same serial, card id and content version.
        "other": anything else, including no valid active card. Neither a slot
        (restarts at c1) nor a serial alone (the card's own claim) is enough.
        """
        if not self.session or card is None or card.get("state") != "valid":
            return "other"
        if self.session.get("insertion") and card.get("insertion") == self.session.get("insertion"):
            return "same"
        info = card.get("info") or {}
        if (card.get("serial") == self.session.get("serial") and info.get("id") == self.session.get("card_id")
                and info.get("version") == self.session.get("version")):
            return "reverified"
        return "other"

    # --- periodic ---------------------------------------------------------------
    def tick(self) -> None:
        now = self.clock()
        self._watch_cleanups(now)
        if self.reader_lost_at is not None and now - self.reader_lost_at > READER_GRACE and self.state in ("preparing", "starting", "running"):
            if self.cards.connected:
                self._end("card_removed", "No se pudo confirmar la Game Card de la sesión tras reconectar con el lector")
            else:
                self._end("reader_lost", "Se perdió la comunicación con el lector de tarjetas")
            return
        if self.state == "preparing" and self.deadline and now > self.deadline:
            self._end("prepare_failed", "La preparación del juego tardó demasiado")
        elif self.state == "starting":
            status = self.runner.unit_status(self.session["id"])
            if status["active"]:
                self._set_state("running")
            elif status["finished"]:
                self._finished(status)
            elif self.deadline and now > self.deadline:
                self._end("start_failed", "El juego no llegó a arrancar")
        elif self.state == "running":
            if self.sync is not None:
                self.sync.poll()
            status = self.runner.unit_status(self.session["id"])
            if status["finished"] or not status["active"]:
                self._finished(status)
        elif self.state == "stopping":
            status = self.runner.unit_status(self.session["id"])
            if status["finished"] or not status["active"]:
                self._after_stop()
        elif self.state == "saving" and self.deadline and now > self.deadline:
            log(f"session {self.session['id']}: the last save was not answered in time")
            self.sync.abandon()
            self._cleanup()

    # --- reconciliation after a restart -------------------------------------------
    def adopt(self, sid: str, session: dict) -> None:
        """Continue supervising a game unit that outlived a launcher restart.

        The card service is not connected yet at this point (or may be down for
        good), so the reader grace starts now: an adopted game never outlives an
        absent reader by more than READER_GRACE, exactly like a supervised one.
        reader_reconnected() clears the deadline once the card is verified.
        """
        self.session = dict(session, id=sid)
        self.session["saving"] = False
        self.session["unit_started"] = True
        if self.session.get("saves"):
            # A capture may have been cut short between freeze and thaw.
            self.runner.thaw(sid)
            try:
                self.sync = self._new_sync(sid)
                self.sync.resume()
            except OSError as exc:
                log(f"session {sid}: the save directory could not be watched again: {exc}")
                if self.sync is not None:
                    self.sync.close()
                self.sync = None
                self.saves_failure = {"ok": False, "message": "La partida de esta sesión no se pudo guardar: el lanzador se reinició y no pudo volver a vigilarla",
                                      "error": {"code": "save_failed", "detail": str(exc)}}
        self._set_state("running")
        if self.gate_open:
            self.gate_open(sid)              # the previous launcher's socket died with it
        if not self.cards.connected:
            self.reader_lost_at = self.clock()
        log(f"adopted running session {sid}" + ("" if self.cards.connected else "; waiting for the card reader"))

    # --- internals -------------------------------------------------------------
    def _staged(self, sid: str, reply: dict) -> None:
        if not self.session or self.session["id"] != sid or self.state != "preparing":
            return  # stale: the session ended meanwhile
        if not reply.get("ok"):
            error = reply.get("error") or {"code": "prepare_failed", "message": "No se pudo preparar el juego"}
            self._end(error.get("code", "prepare_failed"), error.get("message", ""), error.get("detail", ""))
            return
        if self.session.get("insertion") != self.session.get("stage_insertion"):
            # Backstop: no path may move a preparing session onto another
            # insertion, so a reply for the old one cannot be accepted here.
            self._end("prepare_failed", "La Game Card dejó de ser la misma mientras se preparaba el juego")
            return
        if not self.cards.connected or self._continuity(self.cards.active()) != "same":
            # Only the very insertion that was staged may start: a reader restart
            # or a replaced card between copy and start is not proven continuity.
            self._end("card_removed", "La Game Card desapareció antes de arrancar")
            return
        self.session["binary_sha256"] = reply.get("sha256")
        self.session["binary_size"] = reply.get("size")
        content = reply.get("content")
        if content is not None:
            content = content_grant(content)
            if content is None:
                self._end("prepare_failed", "El acceso al contenido de la Game Card no es válido", str(reply.get("content")))
                return
        self.session["content"] = content
        staged_save = reply.get("save") or {}
        if not self.session.get("saves") and (staged_save.get("error") or {}).get("code") == "save_other_generation":
            # The card service kept it from the game (docs/saves.md); say so with the result.
            detail = staged_save["error"].get("detail") or ""
            log(f"session {sid}: the card's save was not used: {detail}")
            self.saves_failure = {"ok": False, "message": "La partida de la Game Card no se usó: no corresponde a esta "
                                  "tarjeta; se conserva en ella", "detail": detail}
        if self.session.get("saves"):
            try:
                self.sync = self._new_sync(sid)
                self.sync.prepare(Path(self.session["dest"]) / "work" / "save.json", reply.get("save"))
            except OSError as exc:
                log(f"session {sid}: save directory preparation failed: {exc}")
                self._end("prepare_failed", "No se pudo preparar la carpeta de partidas", str(exc))
                return
        log(f"session {sid}: staged {reply.get('size')} bytes in {self.clock() - self.session['started_at']:.2f}s; stopping shell, starting unit")
        self._set_state("starting")
        self.deadline = self.clock() + START_TIMEOUT
        try:
            if self.gate_open:
                self.gate_open(sid)          # before the game exists: it may save at any time
            self.shell.stop()
            self.runner.start_unit(sid, self.session)
            self.session["unit_started"] = True    # a cleanup unit will follow its end
        except Exception as exc:  # noqa: BLE001 - must become a visible result
            # Nothing is running: no unit to stop, go straight to cleanup.
            log(f"session {sid}: start failed: {exc}")
            self._record("start_failed", "No se pudo arrancar el juego", detail=str(exc))
            self._cleanup()

    def _finished(self, status: dict) -> None:
        """The game's result comes from its own process; what systemd says about
        the unit around it is reported next to it, never in its place (N1)."""
        code, unit_result = status.get("exit_code"), status.get("result")
        if status.get("started") is False:
            self._record("start_failed", "El juego no llegó a arrancar", detail=f"systemd: {unit_result}")
        elif status.get("signal"):
            self._record("crashed", f"El juego terminó de forma inesperada (señal {status['signal']})", signal=status["signal"])
        elif code not in (None, 0):
            self._record("failed", f"El juego terminó con error (código {code})", code=code)
        else:
            if unit_result not in (None, "success"):
                # The game exited 0; the unit failed for a reason of the console's.
                log(f"session {self.session['id']}: game exited 0 but the unit ended with result {unit_result}")
                self.session["platform"] = {"ok": False, "code": unit_result,
                                            "message": f"La consola no cerró la sesión con normalidad (systemd: {unit_result})"}
            self._record("exited", "La sesión terminó", code=0)
        self._after_stop()

    def _after_stop(self) -> None:
        """The unit is gone. A directory-save session captures once more
        (docs/saves.md) and cleans up once the card has answered."""
        if self.sync is None or self.state == "saving":
            self._cleanup()
            return
        reason = (self.pending_result or {}).get("reason")
        sid = self.session["id"]
        self._set_state("saving")
        self.deadline = self.clock() + SAVE_TIMEOUT + 5
        self.sync.final(lambda: self.schedule(lambda: self._saves_done(sid)),
                        write=reason not in ("card_removed", "reader_lost"))

    def _saves_done(self, sid: str) -> None:
        if self.session and self.session["id"] == sid and self.state == "saving":
            self._cleanup()

    def _new_sync(self, sid: str) -> "DirectorySync":
        return self.sync_factory(sid, self.session["saves"], self.runner.root / sid, self.runner.uid, self.runner.gid,
                                 self.runner, lambda path, size, digest, done: self._send_directory_save(sid, path, size, digest, done))

    def _send_directory_save(self, sid: str, path: Path, size: int, digest: str, done: Callable[[dict], None]) -> None:
        session = self.session
        if not session or session["id"] != sid:
            done({"ok": False, "error": {"code": "no_session", "message": "La sesión ya terminó"}})
            return
        forwarded = {"type": "save", "slot": session["slot"], "insertion": session.get("insertion"),
                     "serial": session["serial"], "version": session.get("version"), "game": session.get("card_id"),
                     "session": sid, "schema": DIRECTORY_SAVE_SCHEMA, "payload_kind": "files",
                     "payload_file": str(path), "payload_size": size, "payload_sha256": digest}
        self.saver(forwarded, lambda outcome: self.schedule(lambda: done(outcome)))

    def _end(self, reason: str, message: str, detail: str = "") -> None:
        """Terminate whatever is running and finish with the given reason."""
        self._record(reason, message, detail=detail)
        if self.state in ("starting", "running") and self.session:
            self._set_state("stopping")
            self.runner.stop_unit(self.session["id"])
            # tick() completes the cleanup once the unit is gone
            return
        self._cleanup()

    def _record(self, reason: str, message: str, code: Optional[int] = None, signal: Optional[int] = None, detail: str = "") -> None:
        if self.session is None:
            return
        elapsed = self.clock() - self.session.get("started_at", self.clock())
        log(f"session {self.session['id']}: result {reason} (code={code}, signal={signal}) after {elapsed:.2f}s"
            + (f": {detail}" if detail else ""))
        result = {"session": self.session["id"], "reason": reason, "message": message, "code": code,
                  "signal": signal, "detail": detail, "card_id": self.session.get("card_id"),
                  "title": self.session.get("title"), "version": self.session.get("version"),
                  "ended_at": self.clock(), "acknowledged": False}
        if self.saves_failure is not None:
            result["saves"] = self.saves_failure
        if self.session.get("platform"):
            result["platform"] = self.session["platform"]
        if self.sync is not None:
            # Stored by _cleanup() with the outcome of the last capture.
            self.pending_result = self.pending_result or result
            return
        self.results.store(result)

    def _cleanup(self) -> None:
        if self.gate_close:
            self.gate_close()
        failed_cleanup = self._check_cleanup(self.session["id"], bool(self.session.get("unit_started"))) \
            if self.session else None
        if self.sync is not None:
            if self.pending_result is not None:
                saves = self.sync.summary(self.pending_result["reason"])
                log(f"session {self.session['id'] if self.session else '?'}: saves "
                    + ("ok" if saves["ok"] else "NOT ok") + (": " + saves["message"].replace("\n", " / ") if saves["message"] else ""))
                result = dict(self.pending_result, saves=saves)
                if failed_cleanup and not result.get("platform"):
                    result["platform"] = failed_cleanup
                self.results.store(result)
            self.sync.close()
            self.sync = None
        elif failed_cleanup:
            current = self.results.current()
            if current and self.session and current.get("session") == self.session["id"] and not current.get("platform"):
                self.results.store(dict(current, platform=failed_cleanup))
        self.pending_result = None
        self.saves_failure = None
        if self.session:
            self.runner.remove_dir(self.session["id"])
            self.runner.forget_unit(self.session["id"])
            log(f"session {self.session['id']}: cleaned up, restoring shell")
        self.session = None
        self.deadline = None
        self.reader_lost_at = None
        self._set_state("idle")
        self.shell.start()

    def _check_cleanup(self, sid: str, expected: bool) -> Optional[dict]:
        """One look at the session's cleanup unit when the session ends: the
        notice to report with the result now (failed, missing), or None. A
        cleanup not finished yet is watched from tick() until it is, or until
        CLEANUP_TIMEOUT; one that has not even started is only watched when
        the session's unit did run, so a session that never started a unit
        does not wait for a cleanup that will never come."""
        status = self.runner.cleanup_status(sid)
        outcome = cleanup_outcome(status)
        if outcome == "pending":
            if expected or status.get("state") not in (None, "inactive"):
                self.cleanups.setdefault(sid, self.clock() + CLEANUP_TIMEOUT)
                log(f"session {sid}: cleanup unit still {status.get('state')}; watching it")
            return None
        self.cleanups.pop(sid, None)
        self.runner.release_cleanup(sid, outcome)
        notice = cleanup_notice(outcome, status)
        if notice:
            log(f"session {sid}: cleanup unit {outcome} ({status.get('result')}); the launcher cleans up instead")
        return notice

    def _watch_cleanups(self, now: float) -> None:
        for sid, deadline in list(self.cleanups.items()):
            if self.session and self.session["id"] == sid:
                continue                     # its own _cleanup() looks first
            status = self.runner.cleanup_status(sid)
            outcome = cleanup_outcome(status)
            if outcome == "pending":
                if now <= deadline:
                    continue
                outcome = "timeout"
            del self.cleanups[sid]
            self.runner.release_cleanup(sid, outcome)
            log(f"session {sid}: cleanup unit {outcome}" + (f" ({status.get('result')})" if outcome == "failed" else "")
                + " after the session ended")
            notice = cleanup_notice(outcome, status)
            if notice:
                self.attach_notice(sid, notice)

    def attach_notice(self, sid: str, notice: dict) -> None:
        """Add a platform notice that came after session `sid` ended to that
        session's stored result, and show it again. Never to another session's
        result: if a later one replaced it, the notice stays in the journal."""
        current = self.results.current()
        if not current or current.get("session") != sid:
            log(f"session {sid}: {notice['message']}; a later result replaced this session's, not shown")
            return
        platform = current.get("platform")
        if platform and notice["message"] not in platform.get("message", ""):
            notice = dict(platform, message=platform["message"] + "\n" + notice["message"])
        elif platform:
            return
        self.results.store(dict(current, platform=notice, acknowledged=False))
        self._emit()

    def _set_state(self, state: str) -> None:
        self.state = state
        self._emit()

    def _emit(self) -> None:
        self.publish({"type": "session", "state": self.state, "session": self._public_session(),
                      "last_result": self.results.current()})

    def _public_session(self) -> Optional[dict]:
        if not self.session:
            return None
        return {k: self.session.get(k) for k in ("id", "slot", "serial", "card_id", "title", "version")}


class ResultStore:
    """Last session result, persisted so the shell can read it at startup."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        self._current: Optional[dict] = None
        if path and path.exists():
            try:
                self._current = json.loads(path.read_text())
            except ValueError:
                self._current = None

    def store(self, result: dict) -> None:
        self._current = result
        self._write()

    def acknowledge(self) -> None:
        if self._current:
            self._current["acknowledged"] = True
            self._write()

    def current(self) -> Optional[dict]:
        return self._current

    def _write(self) -> None:
        if not self.path:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._current))
        os.chmod(tmp, 0o640)
        try:
            import grp
            os.chown(tmp, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
        except (KeyError, PermissionError, ImportError):
            pass
        tmp.replace(self.path)


# ---------------------------------------------------------------- platform pieces

class FrameTooLarge(Exception):
    """An unfinished line exceeded MAX_FRAME_BYTES."""


class LineReader:
    """Newline-delimited JSON from a stream, fed in whatever fragments arrive."""

    def __init__(self, limit: int = MAX_FRAME_BYTES):
        self.buffer = bytearray()
        self.limit = limit

    def feed(self, data: bytes) -> List[dict]:
        self.buffer.extend(data)
        messages: List[dict] = []
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self.buffer[:newline])
            del self.buffer[:newline + 1]
            try:
                message = json.loads(line)
            except ValueError:
                continue   # a malformed line is skipped, the stream stays in sync
            if isinstance(message, dict):
                messages.append(message)
        if len(self.buffer) > self.limit:
            self.buffer.clear()
            raise FrameTooLarge(f"unfinished line over {self.limit} bytes")
        return messages


class CardView:
    """What launchd knows from mun-cardd's event stream."""

    def __init__(self):
        self.connected = False
        self.cards: Dict[str, dict] = {}

    def apply(self, message: dict) -> Optional[str]:
        kind = message.get("type")
        if kind == "snapshot":
            self.cards = {c["slot"]: c for c in message.get("cards", [])}
            self.connected = True
        elif kind == "card":
            card = message["card"]
            self.cards[card["slot"]] = card
        elif kind == "removed":
            self.cards.pop(message.get("slot"), None)
            return message.get("slot")
        return None

    def active(self) -> Optional[dict]:
        for card in self.cards.values():
            if card.get("active"):
                return card
        return None



# ------------------------------------------------------------ directory saves
#
# Games that write their own save files (docs/saves.md). The console keeps a
# snapshot only at an instant it can show to be a save point: the game is
# frozen, no process of the game holds a unit open for writing (or mapped
# shared and writable), and every unit passes its completeness check. Quiet
# time proves nothing and is never used.

IN_MODIFY, IN_CLOSE_WRITE = 0x2, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_Q_OVERFLOW, IN_IGNORED, IN_ISDIR = 0x400, 0x800, 0x4000, 0x8000, 0x40000000
IN_NONBLOCK, IN_CLOEXEC, IN_ONLYDIR = 0o4000, 0o2000000, 0x01000000
# IN_MODIFY marks a unit changed (a crash may leave it torn with no clean close
# to trigger on); only a close after writing or a rename into place triggers.
WATCH_MASK = (IN_MODIFY | IN_CLOSE_WRITE | IN_CREATE | IN_DELETE | IN_MOVED_FROM | IN_MOVED_TO
              | IN_DELETE_SELF | IN_MOVE_SELF)
UNIT_CHECKS = ("zlib-xml", "zlib", "xml", "any")
DIRECTORY_SAVES_MAX_BYTES = 8 * 1024 * 1024   # the card format's ceiling (validator)
MAX_UNIT_FILES = 64                           # the card service's ceiling per snapshot
INFLATE_LIMIT = 64 * 1024 * 1024              # a unit that inflates beyond this is not a save
DIRECTORY_SAVE_SCHEMA = 1
_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,128}")
_UNIT_PATTERN = re.compile(r"[A-Za-z0-9._*-]{1,64}")


def directory_saves_spec(info: dict) -> Optional[dict]:
    """The card's directory-save declaration, re-checked; None for a card without one.

    The card service validated the manifest already; the launcher checks the
    shape again before any of it becomes a path, like the content grant."""
    if not info.get("saves_directory"):
        return None
    directory, units, checks, max_bytes = (info.get("saves_directory"), info.get("saves_units"),
                                           info.get("saves_checks"), info.get("saves_max_bytes"))
    segments = directory.split("/") if isinstance(directory, str) else []
    if not segments or len(segments) > 8 or any(not _SEGMENT.fullmatch(p) or p in (".", "..") for p in segments):
        raise ValueError(f"saves.directory {str(directory)[:80]!r}")
    if not isinstance(units, list) or not isinstance(checks, list) or not 1 <= len(units) <= 8 or len(units) != len(checks) \
            or any(not isinstance(u, str) or not _UNIT_PATTERN.fullmatch(u) for u in units) \
            or any(c not in UNIT_CHECKS for c in checks):
        raise ValueError("saves.units / saves.checks")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1024 <= max_bytes <= DIRECTORY_SAVES_MAX_BYTES:
        raise ValueError(f"saves.max_bytes {max_bytes!r}")
    return {"directory": directory, "units": list(units), "checks": list(checks), "max_bytes": max_bytes,
            "card_id": info.get("id"), "naming": card_naming(info)}


class Inotify:
    """The three libc calls inotify needs, through ctypes (no dependency)."""

    def __init__(self):
        self._libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")

    def add(self, path: Path) -> int:
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(str(path)), WATCH_MASK | IN_ONLYDIR)
        if wd < 0:
            raise OSError(ctypes.get_errno(), f"inotify_add_watch {path} failed")
        return wd

    def read(self) -> List[tuple]:
        """Every queued event as (mask, name); never blocks."""
        events: List[tuple] = []
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return events
            offset = 0
            while offset + 16 <= len(data):
                _wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
                name = data[offset + 16:offset + 16 + length].split(b"\0", 1)[0].decode("utf-8", "replace")
                events.append((mask, name))
                offset += 16 + length

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def unit_check(check: str, data: bytes) -> Optional[str]:
    """Why `data` is not one complete unit under `check` (docs/saves.md), or None."""
    if check == "any":
        return None
    if check in ("zlib", "zlib-xml"):
        # Inflate in bounded steps and keep the output only when XML follows:
        # a thumbnail inflates to 4 MiB and is checked for its end marker only.
        inflater, pending, total, parts = zlib.decompressobj(), data, 0, []
        try:
            while True:
                chunk = inflater.decompress(pending, 1 << 20)
                total += len(chunk)
                if total > INFLATE_LIMIT:
                    return "se descomprime más allá del límite"
                if check == "zlib-xml":
                    parts.append(chunk)
                pending = inflater.unconsumed_tail
                if inflater.eof or (not chunk and not pending):
                    break
        except zlib.error as exc:
            return f"no es un flujo zlib válido ({exc})"
        if not inflater.eof:
            return "el flujo zlib está incompleto (escritura a medias)"
        if inflater.unused_data:
            return "hay datos después del flujo zlib"
        if check == "zlib":
            return None
        data = b"".join(parts)
    # Some engines write their XML with the C string terminator (measured on
    # one: a NUL at the very end); their own loaders stop there, so one final
    # NUL is not part of the document. A NUL anywhere else still fails the parse.
    if data.endswith(b"\0"):
        data = data[:-1]
    # Some games' files have several top-level elements: wrap them in one
    # root, after the XML declaration if there is one (it must stay first).
    text, declaration = data, b""
    if text.startswith(b"\xef\xbb\xbf"):
        declaration, text = text[:3], text[3:]
    if text.lstrip().startswith(b"<?xml"):
        text = text.lstrip()
        end = text.find(b"?>")
        if end < 0:
            return "declaración XML incompleta"
        declaration, text = declaration + text[:end + 2], text[end + 2:]
    if b"<!DOCTYPE" in text or b"<!ENTITY" in text:
        return "XML con declaraciones no admitidas"
    try:
        ElementTree.fromstring(declaration + b"<mun-unit>" + text + b"</mun-unit>")
    except ElementTree.ParseError as exc:
        return f"XML incompleto o dañado ({exc})"
    return None


def read_lease(fd: int) -> Optional[bool]:
    """Take a read lease on `fd` (opened read-only): the kernel grants it only
    when no process holds the file open for writing, and a shared writable
    mapping keeps it open for writing even after its descriptor is closed.
    True: no writer (the lease is held until the descriptor is closed).
    False: a writer. None: leases cannot be taken here (no CAP_LEASE, a file
    system without leases), which is no proof either way."""
    try:
        fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_RDLCK)
    except BlockingIOError:
        return False
    except OSError:
        return None
    return True


def _payload_bytes(snapshot: Dict[str, bytes], carried: List[str], captured_at: float, restored: bool = False) -> bytes:
    payload = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(captured_at)),
               "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "data": base64.b64encode(data).decode("ascii")} for name, data in sorted(snapshot.items())],
               "carried": sorted(carried)}
    if restored:
        payload["restored"] = True    # the launcher's own record only: what the card held at the start
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class DirectorySync:
    """The save directory of one session (docs/saves.md).

    `prepare()` restores the card's save into the directory and starts the
    watch before the game exists (`resume()` does the same for a session
    adopted after a launcher restart); `poll()` runs from the launcher's tick
    and captures after a unit was closed for writing; `final()` captures once
    the game has stopped. `summary()` is what the player is told.

    `host` freezes and thaws the unit; `sender(path, size, sha256, done)` hands one
    payload file to the card service and calls `done(outcome)` on the
    launcher's loop. Everything else runs on that loop too."""

    def __init__(self, sid: str, spec: dict, dest: Path, uid: int, gid: int, host: "Runner",
                 sender: Callable[[Path, int, str, Callable[[dict], None]], None],
                 wall: Callable[[], float] = time.time, inotify_factory: Callable[[], "Inotify"] = Inotify,
                 root_ids: "tuple[int, int]" = (0, 0), lease: Callable[[int], Optional[bool]] = read_lease):
        self.sid, self.spec, self.dest, self.uid, self.gid = sid, spec, dest, uid, gid
        self.root_ids = root_ids     # owner of sync/: root on the console, the test user on a host
        self.lease = lease
        self.host, self.sender, self.wall, self.inotify_factory = host, sender, wall, inotify_factory
        self.patterns = list(zip(spec["units"], spec["checks"]))
        self.max_bytes = int(spec["max_bytes"])
        self.sync_dir = dest / "sync"
        self.dir_fd = -1
        self.watch: Optional["Inotify"] = None
        self.event_gen = 0          # bumped by every change to a unit name
        self.synced_gen = 0         # event_gen the card is known to hold
        self.pending = False        # a unit was closed for writing since the last capture
        self.in_flight: Optional[dict] = None
        self.last_synced: Dict[str, bytes] = {}   # what the card holds, as far as this session knows
        self.last_ok_at: Optional[float] = None     # this session's last confirmed write
        self.card_saved_at: Optional[float] = None  # when the save restored at the start was written
        self.last_error: Optional[dict] = None
        self.lost: Optional[str] = None           # the directory itself was moved or deleted
        self.carried: List[str] = []
        self.omitted: List[str] = []
        self.restore: dict = {"state": "none"}
        self.sequence = 0
        self.frozen_ms: Optional[int] = None     # the last capture's freeze, freeze to thaw
        self._final_done: Optional[Callable[[], None]] = None
        self._final_write = True
        self._final_captured = False

    def unit_pattern(self, name: str) -> Optional[str]:
        """The check for a file name that is a unit, or None."""
        for pattern, check in self.patterns:
            if fnmatch.fnmatchcase(name, pattern):
                return check
        return None

    # --- start -------------------------------------------------------------
    def prepare(self, envelope_path: Path, staged_save: Optional[dict]) -> None:
        """Before the game runs: create the directory, restore the card's save, watch.
        `envelope_path` is `work/<name>` as staged by the card service."""
        self.sync_dir.mkdir(mode=0o700)
        os.chown(self.sync_dir, *self.root_ids)
        work_fd = self._walk(self.dest, ["work"], create=False)
        try:
            self.dir_fd = self._walk(work_fd, self.spec["directory"].split("/"), create=True)
            self._restore(work_fd, envelope_path.name, staged_save or {})
        finally:
            os.close(work_fd)
        self._keep_synced(_payload_bytes(self.last_synced, [], self.wall(), restored=True))
        self._start_watch()

    def resume(self) -> None:
        """A session adopted after a launcher restart: nothing to restore; what
        the card holds is the last snapshot the previous launcher saw written.
        Captures work at once: the writer check does not depend on history.
        Raises OSError if any component of the directory is no longer a plain
        directory (the game may have replaced one while no launcher watched)."""
        self.dir_fd = self._walk(self.dest, ["work"] + self.spec["directory"].split("/"), create=False)
        try:
            self.last_synced, restored = self._load_synced()
            if not restored:
                self.last_ok_at = os.stat(self.sync_dir / "synced.json").st_mtime
        except (OSError, ValueError) as exc:
            log(f"session {self.sid}: no record of the last save written ({exc}); unit versions cannot be carried")
        self.restore = {"state": "adopted"}
        for leftover in self.sync_dir.glob("payload-*.json"):
            leftover.unlink()          # a write whose answer died with the previous launcher
        self._start_watch()
        self.event_gen, self.pending = 1, True   # closes before the restart went unseen

    def _walk(self, start, parts: List[str], create: bool) -> int:
        """A descriptor for `start`/`parts`, resolved one component at a time
        without following a link, each step relative to the descriptor of the
        step before. The game owns `work/` and everything below it, so any
        component may have been replaced by a link or a file; that is ELOOP or
        ENOTDIR here, never a path outside the session. `start` is the
        session directory (root-owned, not writable by the game) or a
        descriptor already obtained this way. With `create`, missing
        components are made and every one is given to the game user."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(start, flags) if not isinstance(start, int) else os.dup(start)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                step = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = step
                if create:
                    os.fchown(fd, self.uid, self.gid)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _start_watch(self) -> None:
        # Watch the directory the descriptor holds, not a path: /proc/self/fd/N
        # resolves to that very inode, so a component replaced after the walk
        # cannot redirect the watch.
        self.watch = self.inotify_factory()
        self.watch.add(Path(f"/proc/self/fd/{self.dir_fd}"))

    def _restore(self, work_fd: int, envelope_name: str, staged: dict) -> None:
        try:
            if staged.get("present") and not staged.get("copied"):
                error = staged.get("error") or {}
                self.restore = {"state": "damaged", "detail": error.get("detail") or error.get("message") or "no se pudo leer"}
                return
            try:
                fd = os.open(envelope_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=work_fd)
            except FileNotFoundError:
                return
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(self.max_bytes * 2 + 256 * 1024)
            problem, files, document = self._check_envelope(raw)
            recovered = bool(document.get("recovered_from"))
            if problem:
                self.restore = {"state": "damaged", "detail": problem}
                log(f"session {self.sid}: the card's save was refused: {problem}")
                return
            for name, data in sorted(files.items()):
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                             dir_fd=self.dir_fd)
                try:
                    _write_all(fd, data)
                    os.fchown(fd, self.uid, self.gid)
                finally:
                    os.close(fd)
            self.last_synced = dict(files)
            try:
                self.card_saved_at = float(calendar.timegm(time.strptime(document.get("saved_at", ""), "%Y-%m-%dT%H:%M:%SZ")))
            except (TypeError, ValueError):
                self.card_saved_at = None
            self.restore = {"state": "recovered" if recovered else "restored", "units": len(files)}
            log(f"session {self.sid}: restored {len(files)} save unit(s) into {self.spec['directory']}"
                + (" from the previous copy" if recovered else ""))
        finally:
            try:
                os.unlink(envelope_name, dir_fd=work_fd)
            except FileNotFoundError:
                pass

    def _check_envelope(self, raw: bytes) -> "tuple[Optional[str], Dict[str, bytes], dict]":
        """Refuse the whole envelope for any flaw; nothing is unpacked outside the directory."""
        try:
            document = json.loads(raw)
        except ValueError:
            return "no es JSON", {}, {}
        if not isinstance(document, dict) or document.get("format") not in SAVE_FORMATS.values():
            return "no es una partida de la consola", {}, {}
        if document.get("format") != SAVE_FORMATS[card_naming(self.spec)]:
            return f"es una partida {document.get('format')} y esta tarjeta guarda {SAVE_FORMATS[card_naming(self.spec)]}", {}, {}
        if document.get("game") != self.spec.get("card_id"):
            return "la partida es de otro juego", {}, {}
        if document.get("payload_kind") != "files" or document.get("schema") != DIRECTORY_SAVE_SCHEMA:
            return "no es una partida de carpeta de esta versión", {}, {}
        problem, files = self._check_files(document.get("payload"))
        return problem, files, document

    def _check_files(self, payload: object) -> "tuple[Optional[str], Dict[str, bytes]]":
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list) \
                or len(payload["files"]) > MAX_UNIT_FILES:
            return "partida sin lista de archivos válida", {}
        files: Dict[str, bytes] = {}
        total = 0
        for entry in payload["files"]:
            name = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not _SEGMENT.fullmatch(name) or name in (".", "..") or name in files:
                return f"nombre no permitido: {str(name)[:40]}", {}
            check = self.unit_pattern(name)
            if check is None:
                return f"{name} no es un archivo de partida declarado", {}
            try:
                data = base64.b64decode(entry.get("data", ""), validate=True)
            except (ValueError, TypeError):
                return f"{name}: datos no válidos", {}
            total += len(data)
            if total > self.max_bytes:
                return "la partida supera el tamaño declarado", {}
            if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                return f"{name}: tamaño o huella no coinciden", {}
            problem = unit_check(check, data)
            if problem:
                return f"{name}: {problem}", {}
            files[name] = data
        return None, files

    def _keep_synced(self, raw: bytes, source: Optional[Path] = None) -> None:
        """Remember what the card holds (root-only, in RAM) for a launcher restart."""
        target = self.sync_dir / "synced.json"
        if source is not None:
            os.replace(source, target)
            return
        tmp = self.sync_dir / "synced.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            _write_all(fd, raw)
        finally:
            os.close(fd)
        os.replace(tmp, target)

    def _load_synced(self) -> "tuple[Dict[str, bytes], bool]":
        with open(self.sync_dir / "synced.json", "rb") as handle:
            raw = handle.read(self.max_bytes * 2 + 256 * 1024)
        payload = json.loads(raw)
        problem, files = self._check_files(payload)
        if problem:
            raise ValueError(problem)
        return files, bool(payload.get("restored"))

    # --- watch -------------------------------------------------------------
    def _drain(self) -> None:
        if self.watch is None:
            return
        for mask, name in self.watch.read():
            if mask & IN_Q_OVERFLOW:
                self.event_gen += 1
                self.pending = True       # a close may be among the lost events: try once
                continue
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_IGNORED):
                if self.lost is None:
                    self.lost = "El juego movió o borró su carpeta de partidas"
                    log(f"session {self.sid}: the save directory was moved or deleted; no further captures")
                continue
            if mask & IN_ISDIR or not name or self.unit_pattern(name) is None:
                continue
            self.event_gen += 1
            if mask & (IN_CLOSE_WRITE | IN_MOVED_TO):
                self.pending = True

    def poll(self) -> None:
        """From the launcher's tick while the game runs."""
        try:
            self._drain()
            if self.pending and self.in_flight is None and self.lost is None:
                self._attempt(frozen=True)
        except OSError as exc:
            # The loop must go on; the failure is the player's to know.
            self.last_error = {"code": "save_failed", "message": "No se pudieron leer los archivos de partida", "detail": str(exc)}
            log(f"session {self.sid}: save capture failed: {exc}")

    # --- capture -------------------------------------------------------------
    def _read_units(self, check_writers: bool) -> "tuple[Dict[str, Optional[bytes]], bool, Optional[str]]":
        """Every unit's bytes (None when it is not a plain file within bounds),
        whether the directory holds more than a snapshot may, and, with
        `check_writers`, why there is no save point (a unit open for writing,
        or no way to tell), which abandons the capture."""
        found: Dict[str, Optional[bytes]] = {}
        total = 0
        for name in sorted(os.listdir(self.dir_fd)):
            if self.unit_pattern(name) is None:
                continue
            if len(found) >= MAX_UNIT_FILES:
                return found, True, None
            found[name] = None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.dir_fd)
            except OSError:
                continue
            try:
                st = os.fstat(fd)
                # A hard link could be written through another name the watch never sees.
                if not statmod.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    continue
                if check_writers:
                    # The lease stays held while the unit is read: no writer
                    # can even open it until the descriptor is closed.
                    free = self.lease(fd)
                    if free is None:
                        return found, False, "open files cannot be checked (no read lease)"
                    if not free:
                        return found, False, f"{name} is open for writing"
                chunks, size = [], 0
                while size <= self.max_bytes - total:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                total += size
                if total > self.max_bytes:
                    return found, True, None
                found[name] = b"".join(chunks)
            finally:
                os.close(fd)
        return found, False, None

    def _attempt(self, frozen: bool) -> None:
        """One capture. `frozen` while the game runs; after the stop nothing can write."""
        self.pending = False
        started = time.monotonic()
        if frozen:
            if not self.host.freeze(self.sid):
                log(f"session {self.sid}: could not freeze the game for a save capture; waiting for the next save point")
                self.host.thaw(self.sid)
                return
        try:
            self._drain()                 # every event up to the freeze is queued by now
            gen = self.event_gen
            found, too_many, no_point = self._read_units(check_writers=frozen)
            if no_point:
                log(f"session {self.sid}: no save point: {no_point}")
                return                    # the writer's close triggers the next attempt
        finally:
            if frozen:
                self.host.thaw(self.sid)
                self.frozen_ms = int((time.monotonic() - started) * 1000)
        if too_many:
            self.last_error = {"code": "save_too_large", "message": "La partida supera el tamaño declarado por la Game Card"}
            log(f"session {self.sid}: the save directory exceeds {self.max_bytes} bytes or {MAX_UNIT_FILES} files; not written")
            return
        previous = self.last_synced
        snapshot: Dict[str, bytes] = {}
        carried, omitted = [], []
        for name, data in found.items():
            check = self.unit_pattern(name) or "any"
            problem = "no es un archivo normal" if data is None else unit_check(check, data)
            if problem is None:
                snapshot[name] = data
            elif name in previous:
                snapshot[name] = previous[name]
                carried.append(name)
                log(f"session {self.sid}: {name} is not complete ({problem}); its previous version is kept")
            else:
                omitted.append(name)
                log(f"session {self.sid}: {name} is not complete ({problem}) and has no previous version; left out")
        for name, data in previous.items():
            snapshot.setdefault(name, data)   # a deleted unit stays on the card in v0
        self.carried, self.omitted = carried, omitted
        if sum(len(d) for d in snapshot.values()) > self.max_bytes or len(snapshot) > MAX_UNIT_FILES:
            self.last_error = {"code": "save_too_large", "message": "La partida supera el tamaño declarado por la Game Card"}
            log(f"session {self.sid}: the snapshot exceeds the card's bound; not written")
            return
        if snapshot == previous:
            # The card already holds exactly this (or there is nothing yet: a new
            # game before its first save). Not written again.
            self.synced_gen, self.last_error = gen, None
            return
        self._write(snapshot, carried, gen)

    def _write(self, snapshot: Dict[str, bytes], carried: List[str], gen: int) -> None:
        raw = _payload_bytes(snapshot, carried, self.wall())
        self.sequence += 1
        path = self.sync_dir / f"payload-{self.sequence}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            _write_all(fd, raw)
        finally:
            os.close(fd)
        self.in_flight = {"snapshot": snapshot, "gen": gen, "path": path}
        log(f"session {self.sid}: saving {len(snapshot)} unit(s), {sum(len(d) for d in snapshot.values())} bytes, to the card"
            + (f" (game frozen {self.frozen_ms} ms)" if self.frozen_ms is not None else " (game stopped)"))
        self.frozen_ms = None
        self.sender(path, len(raw), hashlib.sha256(raw).hexdigest(), self._written)

    def _written(self, outcome: dict) -> None:
        flight, self.in_flight = self.in_flight, None
        if flight is None or flight.get("abandoned"):
            return
        if outcome.get("ok"):
            try:
                self._keep_synced(b"", source=flight["path"])
            except OSError as exc:
                log(f"session {self.sid}: could not keep the record of the last save: {exc}")
            self.last_synced = flight["snapshot"]
            self.synced_gen = max(self.synced_gen, flight["gen"])
            self.last_ok_at = self.wall()
            self.last_error = None
            log(f"session {self.sid}: save written to the card ({outcome.get('bytes')} bytes)")
        else:
            try:
                flight["path"].unlink()
            except OSError:
                pass
            self.last_error = outcome.get("error") or {"code": "save_failed", "message": "No se pudo guardar"}
            log(f"session {self.sid}: save to the card failed: {self.last_error.get('code')} {self.last_error.get('detail', '')}")
        if self._final_done is not None:
            self._final_step()

    # --- end of session ------------------------------------------------------
    def final(self, done: Callable[[], None], write: bool = True) -> None:
        """The game has stopped: nothing runs that could be mid-write, so one last
        capture without the freeze. `done` is called once the card has answered,
        or at once when there is nothing to write. With `write` False (the card
        is gone) only the accounting is completed.

        Every process of the game is gone, so every event it caused is queued
        now; the events are accounted for before anything is decided, and again
        after a write that was still in flight, since a game can save and quit
        between the last poll and the moment its stop is seen."""
        self._final_done = done
        self._final_write = write
        self._final_captured = False
        self._drain()
        if self.in_flight is None:
            self._final_step()

    def _final_step(self) -> None:
        """Runs once no write is in flight: drain, then one capture of whatever
        the card does not hold yet (a failed write is tried once more), then
        done. Bounded: at most one final capture, whatever the answers."""
        self._drain()
        if (not self._final_captured and self._final_write and self.lost is None
                and (self.event_gen > self.synced_gen or self.last_error is not None)):
            self._final_captured = True
            try:
                self._attempt(frozen=False)
            except OSError as exc:
                self.last_error = {"code": "save_failed", "message": "No se pudieron leer los archivos de partida", "detail": str(exc)}
            if self.in_flight is not None:
                return                    # _written() comes back here with the answer
        self._finish_final()

    def _finish_final(self) -> None:
        done, self._final_done = self._final_done, None
        if done is not None:
            done()

    def abandon(self) -> None:
        """The card never answered the last write before the deadline."""
        if self.in_flight is not None:
            self.in_flight["abandoned"] = True
            self.in_flight = None
            self.last_error = {"code": "save_timeout", "message": "El lector no respondió a tiempo"}
        self._final_done = None

    # --- what the player is told ---------------------------------------------
    def summary(self, reason: str) -> dict:
        at = time.strftime("%H:%M", time.localtime(self.last_ok_at)) if self.last_ok_at else None
        # What the card still holds if nothing was written this session.
        held = self.last_ok_at or self.card_saved_at
        held_at = time.strftime("%H:%M", time.localtime(held)) if held else None
        unsynced = self.event_gen > self.synced_gen or self.in_flight is not None
        lines, ok = [], True
        if self.restore.get("state") == "recovered":
            lines.append("Partida recuperada de la copia anterior")
        elif self.restore.get("state") == "damaged":
            lines.append(f"La partida de la Game Card no se pudo cargar: {self.restore.get('detail')}")
            ok = False
        if reason in ("card_removed", "reader_lost") and unsynced:
            lines.append(f"Se perdieron los cambios de la partida posteriores a las {held_at}" if held_at
                         else "Los cambios de la partida de esta sesión no llegaron a la Game Card")
            ok = False
        elif self.lost is not None:
            lines.append(f"La última partida no se guardó: {self.lost}")
            ok = False
        elif self.last_error is not None:
            lines.append(f"La última partida no se guardó: {self.last_error.get('message', 'error desconocido')}")
            ok = False
        elif unsynced:
            lines.append("La última partida no se pudo comprobar completa y no se guardó")
            ok = False
        elif self.last_ok_at is not None:
            lines.append(f"Partida guardada en la Game Card ({at})")
        if self.carried:
            lines.append("Una ranura no estaba completa; se conservó su versión anterior")
            ok = False
        if self.omitted:
            lines.append("Una ranura nueva estaba incompleta y no se guardó")
            ok = False
        return {"ok": ok, "message": "\n".join(lines), "synced_at": self.last_ok_at,
                "carried": list(self.carried), "omitted": list(self.omitted),
                "error": self.last_error, "restore": dict(self.restore)}

    def close(self) -> None:
        if self.watch is not None:
            self.watch.close()
            self.watch = None
        if self.dir_fd >= 0:
            os.close(self.dir_fd)
            self.dir_fd = -1


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


# Runtime profiles (docs/runtime.md): what a game unit may touch
# and what it is told, chosen by the manifest's content.profile the validator
# already accepted. Everything else about the unit is common: no capabilities,
# no network, read-only system, the session's work/ as the only writable path.
#
# linux-arm64-v0: framebuffer and evdev, as before. linux-arm64-gl-v0
# (provisional until a complete game has run on it): DRM/KMS with a render
# node for Mesa, ALSA for audio, evdev for input; SDL is pointed at KMSDRM and
# ALSA and OpenAL Soft at ALSA, because the console runs no display or sound
# server and their probing would otherwise burn time before falling back.
# Memory and task ceilings are provisional lab figures for a software-rendered
# 2D game, not console specifications.
FRAMEBUFFER_PROFILE = "linux-arm64-v0"
GL_PROFILE = "linux-arm64-gl-v0"
PROFILES = {
    FRAMEBUFFER_PROFILE: {
        "groups": ("video", "input"),
        "devices": ("/dev/fb0 rw", "char-input rw"),
        "memory_max": "512M", "tasks_max": "32",
        "environment": {},
    },
    GL_PROFILE: {
        "groups": ("video", "render", "audio", "input"),
        "devices": ("char-drm rw", "char-alsa rw", "char-input rw"),
        "memory_max": "2G", "tasks_max": "64",
        "environment": {"SDL_VIDEODRIVER": "kmsdrm", "SDL_AUDIODRIVER": "alsa", "ALSOFT_DRIVERS": "alsa"},
    },
}


def unit_properties(profile: Optional[str], sid: str, dest: Path, content: Optional[dict] = None) -> List[str]:
    """systemd properties of a game unit for `profile`; raises for an unknown one.
    `content` is the card's {"device", "root"} to mount read-only at CONTENT_MOUNT
    (mount access), or None for a copy-only game."""
    if profile not in PROFILES:
        raise RuntimeError(f"unknown runtime profile {profile!r}")
    spec = PROFILES[profile]
    properties = [
        "KillMode=control-group", f"TimeoutStopSec={int(STOP_TIMEOUT)}",
        f"WorkingDirectory={dest / 'work'}", f"ReadWritePaths={dest / 'work'}",
    ]
    if content is not None:
        # PID 1 mounts the card's device inside the unit's namespace; the
        # device cgroup must let the unit's setup open it (read-only), the
        # node's root:disk 0660 mode still keeps the game itself away from it.
        # The leading "-" makes a vanished device non-fatal for namespace
        # set-up; the launcher ends the session on removal anyway.
        properties.append(f"MountImages=-{content['device']}:{CONTENT_MOUNT}:root:ro,nosuid,nodev,noexec")
    properties += [f"SupplementaryGroups={group}" for group in spec["groups"]]
    properties += [
        "NoNewPrivileges=yes", "CapabilityBoundingSet=", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes",
        "PrivateNetwork=yes", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
        "ProtectControlGroups=yes", "RestrictNamespaces=yes", "RestrictRealtime=yes",
        "LockPersonality=yes", "RestrictSUIDSGID=yes", f"MemoryMax={spec['memory_max']}", f"TasksMax={spec['tasks_max']}",
        "DevicePolicy=closed",
    ]
    properties += [f"DeviceAllow={device}" for device in spec["devices"]]
    if content is not None:
        properties.append(f"DeviceAllow={content['device']} r")
    # The cleanup runs as root when the unit stops, whatever happened to
    # launchd: copy gone, shell back if no launcher is there to do it. It is a
    # unit of its own, in the host's mount namespace, and not ExecStopPost=:
    # systemd gives every process of this unit, a "+" one included, the
    # unit's MountImages=, and while the card service holds the card
    # read-write for a save the kernel refuses a new read-only mount of it
    # ("Can't mount, would change RO state", EBUSY). The hook then never ran
    # and the unit failed with exit-code although the game had exited 0.
    # OnSuccess= covers an exit 0 and a stop, OnFailure= a crash, a kill and
    # a non-zero exit; the unit's own Result is then only the game's.
    cleanup = CLEANUP_UNIT.format(sid=sid)
    properties += [
        "StandardOutput=journal", "StandardError=journal",
        f"OnSuccess={cleanup}", f"OnFailure={cleanup}",
    ]
    return properties


# The game environment is one set of facts published under MUN_ and, for
# cards of the earlier naming generation only (docs/game-cards.md), also under
# NEPTUNE_, the names games on those cards were built against (docs/game-cards.md).
# Both always carry the same values, because they come from the same entry.
GAME_ENV_PREFIXES = {"mun": ("MUN_",), "earlier": ("MUN_", "NEPTUNE_")}


def unit_environment(profile: Optional[str], sid: str, session: dict, dest: Path) -> Dict[str, str]:
    """Environment a game unit receives: session facts, the save contract, profile hints."""
    if profile not in PROFILES:
        raise RuntimeError(f"unknown runtime profile {profile!r}")
    facts = {}
    if session.get("content") is not None:
        facts["CONTENT_DIR"] = str(CONTENT_MOUNT / session["content"]["root"])
    facts.update({
        "CARD_ID": str(session.get("card_id", "")),
        "CONTENT_VERSION": str(session.get("version", "")),
        "SESSION": sid,
        "RUNTIME_PROFILE": profile,
    })
    if not session.get("saves"):
        # Saves (docs/saves.md): the current save as a file, and the socket to ask for a
        # write. A game with directory saves (docs/saves.md) is saved by the console instead.
        facts["SAVE_FILE"] = str(dest / "work" / "save.json")
        facts["SAVE_SOCKET"] = str(dest / "save.sock")
    prefixes = GAME_ENV_PREFIXES[card_naming(session)]
    environment = {prefix + name: value for name, value in facts.items() for prefix in prefixes}
    environment["HOME"] = str(dest / "work")
    environment.update(PROFILES[profile]["environment"])
    return environment


def parse_unit_status(show: str) -> dict:
    """Interpret `systemctl show` output for a game unit.

    A transient unit exists before its start job has run; then it is
    `inactive` with `Result=success` and no main-process start timestamp. That
    is "not started yet", never "finished with exit code 0": mistaking the two
    made the launcher clean the session up under a unit about to start."""
    props = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    active_state = props.get("ActiveState", "inactive")
    active = active_state in ("active", "activating", "deactivating") or props.get("SubState") == "running"
    never_ran = props.get("ExecMainStartTimestampMonotonic", "0") in ("", "0") and props.get("Result", "success") == "success"
    finished = props.get("LoadState") == "not-found" or (active_state in ("inactive", "failed") and not never_ran)
    code = int(props.get("ExecMainStatus", "0") or 0)
    main_code = props.get("ExecMainCode", "")
    # ExecMainCode (a CLD_* value): 1 = exited, 2 = killed, 3 = dumped core;
    # for 2 and 3 the status is the signal number.
    signal_number = code if main_code in ("2", "3") else None
    # Whether the game's own process was ever started: False when the unit
    # failed before that (its namespace, for one), None when systemd no longer
    # knows the unit. A unit that never ran did not end with the game's code.
    started = None if props.get("LoadState") == "not-found" \
        else props.get("ExecMainStartTimestampMonotonic", "0") not in ("", "0")
    return {"active": active and not finished, "finished": finished, "result": props.get("Result"),
            "exit_code": None if signal_number else code, "signal": signal_number, "started": started,
            "raw": props}


class Runner:
    """Transient systemd units for game sessions and the launcher-owned directories."""

    def __init__(self, root: Path = LAUNCH_ROOT):
        self.root = root
        import grp
        import pwd
        self.uid = pwd.getpwnam(GAME_USER).pw_uid
        self.gid = grp.getgrnam(GAME_USER).gr_gid

    def prepare_dir(self, sid: str) -> Path:
        dest = self.root / sid
        dest.mkdir(mode=0o750)
        os.chown(dest, 0, self.gid)            # game may traverse and execute, not write
        work = dest / "work"
        work.mkdir(mode=0o700)
        os.chown(work, self.uid, self.gid)      # the only writable place for the game
        return dest

    def remove_dir(self, sid: str) -> None:
        shutil.rmtree(self.root / sid, ignore_errors=True)

    def open_save_socket(self, sid: str) -> socket.socket:
        """A listening socket only this session's game can reach (root:mun-game 0660)."""
        path = self.root / sid / "save.sock"
        if path.exists():
            path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chown(path, 0, self.gid)
        os.chmod(path, 0o660)
        listener.listen(2)
        listener.setblocking(False)
        return listener

    def unit_name(self, sid: str) -> str:
        return f"{UNIT_PREFIX}{sid}.service"

    def forget_unit(self, sid: str) -> None:
        """Drop a finished (possibly failed) transient unit once its result was read."""
        subprocess.run(["systemctl", "reset-failed", self.unit_name(sid)], capture_output=True, check=False)

    def start_unit(self, sid: str, session: dict) -> None:
        dest = self.root / sid
        (dest / "session.json").write_text(json.dumps(session))
        # Cards validated before this increment carry no profile in the
        # session; they were all framebuffer games.
        profile = session.get("profile") or FRAMEBUFFER_PROFILE
        # Blocking on the start job (no --no-block): it completes as soon as the
        # main process is forked, so the wait is milliseconds, and a unit that
        # cannot even be set up (a namespace or mount failure) makes systemd-run
        # return non-zero here, becoming start_failed with systemd's message
        # instead of a unit that is polled before it exists. Not --collect: a
        # crashed unit must stay around, with its Result and exit code, until
        # unit_status() has read them; forget_unit() removes it then.
        cmd = ["systemd-run", "--unit", self.unit_name(sid)[:-8], "--description", f"Game session {sid}",
               "--uid", str(self.uid), "--gid", str(self.gid), "--quiet"]
        for prop in unit_properties(profile, sid, dest, session.get("content")):
            cmd += ["--property", prop]
        for name, value in unit_environment(profile, sid, session, dest).items():
            cmd.append(f"--setenv={name}={value}")
        cmd += ["--", str(dest / "game")]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "systemd-run failed")

    def unit_status(self, sid: str) -> dict:
        show = subprocess.run(["systemctl", "show", self.unit_name(sid), "-p",
                               "ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,LoadState,ExecMainStartTimestampMonotonic"],
                              capture_output=True, text=True, check=False).stdout
        return parse_unit_status(show)

    def cleanup_status(self, sid: str) -> dict:
        """State of the session's cleanup unit: {"load", "state", "substate", "result"}."""
        props = dict(line.split("=", 1) for line in subprocess.run(
            ["systemctl", "show", CLEANUP_UNIT.format(sid=sid), "-p", "LoadState,ActiveState,SubState,Result"],
            capture_output=True, text=True, check=False).stdout.splitlines() if "=" in line)
        return {"load": props.get("LoadState"), "state": props.get("ActiveState"),
                "substate": props.get("SubState"), "result": props.get("Result")}

    def release_cleanup(self, sid: str, outcome: str) -> None:
        """Let systemd forget a cleanup unit once its outcome is recorded: a
        succeeded one stays active (RemainAfterExit) until stopped, a failed one
        until reset; one still running past every bound is stopped."""
        unit = CLEANUP_UNIT.format(sid=sid)
        if outcome in ("succeeded", "timeout"):
            subprocess.run(["systemctl", "stop", "--no-block", unit], capture_output=True, check=False)
        elif outcome == "failed":
            subprocess.run(["systemctl", "reset-failed", unit], capture_output=True, check=False)

    def list_cleanup_units(self) -> List[str]:
        """Session ids of the cleanup units systemd lists, in any state."""
        out = subprocess.run(["systemctl", "list-units", "--plain", "--no-legend", "--all", "mun-launch-cleanup@*"],
                             capture_output=True, text=True, check=False).stdout
        prefix, suffix = "mun-launch-cleanup@", ".service"
        return [name[len(prefix):-len(suffix)] for name in (line.split()[0] for line in out.splitlines() if line.strip())
                if name.startswith(prefix) and name.endswith(suffix)]

    def stop_unit(self, sid: str) -> None:
        subprocess.run(["systemctl", "stop", "--no-block", self.unit_name(sid)], capture_output=True, check=False)

    def freeze(self, sid: str) -> bool:
        """Freeze the unit's cgroup (docs/saves.md); True only once systemd reports it frozen."""
        unit = self.unit_name(sid)
        try:
            subprocess.run(["systemctl", "freeze", unit], capture_output=True, check=False, timeout=10)
            state = subprocess.run(["systemctl", "show", "-p", "FreezerState", "--value", unit],
                                   capture_output=True, text=True, check=False, timeout=10).stdout.strip()
        except subprocess.SubprocessError as exc:
            log(f"session {sid}: freeze failed: {exc}")
            return False
        return state == "frozen"

    def thaw(self, sid: str) -> None:
        try:
            subprocess.run(["systemctl", "thaw", self.unit_name(sid)], capture_output=True, check=False, timeout=10)
        except subprocess.SubprocessError as exc:
            log(f"session {sid}: thaw failed: {exc}")

    def list_game_units(self) -> List[str]:
        out = subprocess.run(["systemctl", "list-units", "--plain", "--no-legend", "--all", f"{UNIT_PREFIX}*"],
                             capture_output=True, text=True, check=False).stdout
        return [line.split()[0] for line in out.splitlines() if line.strip()]


class ShellControl:
    def stop(self) -> None:
        subprocess.run(["systemctl", "stop", SHELL_UNIT], capture_output=True, check=False)

    def start(self) -> None:
        subprocess.run(["systemctl", "start", "--no-block", SHELL_UNIT], capture_output=True, check=False)


def stage_via_cardd(request: dict, reply: Callable[[dict], None]) -> None:
    """Ask cardd (root-only control socket) to copy the entry, write a save or
    release a card; the answer arrives on a thread."""
    def work() -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(PREPARE_TIMEOUT if request.get("type") == "stage" else SAVE_TIMEOUT)
                sock.connect(str(CARDD_CONTROL))
                sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
                data = b""
                while b"\n" not in data:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            failure = ("save_failed", "El lector no respondió al guardado") if request.get("type") == "save" \
                else ("prepare_failed", "El lector no respondió a la preparación")
            reply(json.loads(data.split(b"\n", 1)[0]) if data.strip() else
                  {"ok": False, "error": {"code": failure[0], "message": failure[1]}})
        except (OSError, ValueError) as exc:
            code = "save_failed" if request.get("type") == "save" else "prepare_failed"
            reply({"ok": False, "error": {"code": code, "message": "No se pudo hablar con el lector de tarjetas", "detail": str(exc)}})
    threading.Thread(target=work, name="stage", daemon=True).start()


# ---------------------------------------------------------------- shell-facing socket

class Server:
    def __init__(self, path: Path, manager: SessionManager):
        self.path = path
        self.manager = manager
        self.clients: Dict[socket.socket, bytes] = {}
        self.listener: Optional[socket.socket] = None
        self.selector: Optional[selectors.BaseSelector] = None

    def drop(self, conn: socket.socket) -> None:
        """Forget a client everywhere: a closed fd number is reused by the next accept."""
        self.clients.pop(conn, None)
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
            os.chown(self.path, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
        except (KeyError, PermissionError, ImportError):
            log(f"warning: could not chown socket to group {SOCKET_GROUP}")
        listener.listen(4)
        listener.setblocking(False)
        self.listener = listener
        return listener

    def accept(self) -> Optional[socket.socket]:
        conn, _ = self.listener.accept()
        conn.setblocking(False)
        self.clients[conn] = b""
        self._send(conn, self.manager.snapshot())
        return conn if conn in self.clients else None

    def handle(self, conn: socket.socket) -> bool:
        try:
            data = conn.recv(65536)
        except OSError:
            data = b""
        if not data:
            self.drop(conn)
            return False
        self.clients[conn] += data
        while b"\n" in self.clients.get(conn, b""):
            line, _, self.clients[conn] = self.clients[conn].partition(b"\n")
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if request.get("type") == "launch":
                result = self.manager.launch(request)
                if not result.get("accepted"):
                    log(f"launch rejected: {result.get('error', {}).get('code')} (state {self.manager.state})")
                self._send(conn, {"type": "launch_result", **result})
                if result.get("accepted"):
                    self.manager._emit()
            elif request.get("type") == "ack":
                self.manager.acknowledge()
            elif request.get("type") == "status":
                self._send(conn, self.manager.snapshot())
            elif request.get("type") == "release":
                refusal = self.manager.release_request(str(request.get("serial", "")))
                if refusal is not None:
                    self._send(conn, {"type": "released", **refusal})
                else:
                    stage_via_cardd({"type": "release", "serial": request.get("serial")},
                                    lambda outcome, c=conn: self.manager.schedule(lambda: self._send(c, {"type": "released", **outcome})))
        return True

    def broadcast(self, message: dict) -> None:
        for conn in list(self.clients):
            self._send(conn, message)

    def _send(self, conn: socket.socket, message: dict) -> None:
        try:
            conn.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            self.drop(conn)


# ---------------------------------------------------------------- main loop

def record_interrupted(results: "ResultStore", sid: str, dest: Path, status: Optional[dict] = None,
                       platform: Optional[dict] = None) -> None:
    """Store the result of a session that ended while no launcher supervised it.

    Called before anything of `dest` is removed: the session file is the only
    record of which game ran and whether its saves were the console's to
    take. A game with directory saves gets no final capture on this path
    (docs/saves.md); the player is told the last save may not have reached the
    card, which still holds its last written envelope."""
    session: dict = {}
    try:
        session = json.loads((dest / "session.json").read_text())
    except (OSError, ValueError):
        pass
    detail = ""
    if status and status.get("signal"):
        detail = f"el juego terminó con la señal {status['signal']}"
    elif status and status.get("finished") and status.get("exit_code") not in (None, 0):
        detail = f"el juego terminó con código {status['exit_code']}"
    result = {"session": sid, "reason": "interrupted", "message": "El lanzador se reinició durante la sesión",
              "code": None, "signal": None, "detail": detail, "card_id": session.get("card_id"),
              "title": session.get("title"), "version": session.get("version"), "ended_at": time.monotonic(),
              "acknowledged": False}
    if session.get("saves"):
        result["saves"] = {"ok": False, "message": "No se pudo comprobar si la última partida llegó a la Game Card"}
    if platform:
        result["platform"] = platform
    results.store(result)
    log(f"session {sid}: ended while no launcher supervised it; recorded as interrupted"
        + (" (directory saves unconfirmed)" if session.get("saves") else ""))


def reconcile_sessions(runner: "Runner", manager: SessionManager, root: Path,
                       cleanup_notices: Optional[Dict[str, dict]] = None) -> bool:
    """At launcher start: adopt the one active game unit that has a session
    file, and end everything else. A unit that is listed but not active (a
    failed one kept for its result, or one still stopping) and a session
    directory with no unit at all both mean a session ended unsupervised:
    its result is recorded before its directory is removed. True if a
    session was adopted. `cleanup_notices` (from sweep_cleanups) go with the
    result of their own session: recorded here, or added to the stored result
    when that session's result was already stored."""
    notices = dict(cleanup_notices or {})
    adopted: Optional[str] = None
    for unit in runner.list_game_units():
        sid = unit[len(UNIT_PREFIX):-len(".service")]
        session_file = root / sid / "session.json"
        status = runner.unit_status(sid)
        if status["active"] and adopted is None and session_file.exists():
            try:
                manager.adopt(sid, json.loads(session_file.read_text()))
                adopted = sid
                continue
            except (OSError, ValueError):
                pass
        runner.stop_unit(sid)
        if (root / sid).is_dir():
            record_interrupted(manager.results, sid, root / sid, status, notices.pop(sid, None))
        runner.remove_dir(sid)
        runner.forget_unit(sid)
    for leftover in sorted(root.iterdir()):
        if leftover.is_dir() and leftover.name != adopted:
            # A session directory without a unit: the launcher died between Play
            # and the end of the session. Say so instead of silently coming back.
            record_interrupted(manager.results, leftover.name, leftover, platform=notices.pop(leftover.name, None))
            shutil.rmtree(leftover, ignore_errors=True)
    for sid, notice in notices.items():
        manager.attach_notice(sid, notice)
    return adopted is not None


def cleanup_outcome(status: dict) -> str:
    """What a cleanup unit's state says (docs/runtime.md): "succeeded" (the
    oneshot is RemainAfterExit=yes, so success stays visible as active/exited
    until the launcher releases it), "failed", "missing" (its template is not
    installed) or "pending" (not started yet, or running). An unfinished
    cleanup is never taken for a successful one."""
    if status.get("load") == "not-found":
        return "missing"
    if status.get("state") == "failed":
        return "failed"
    if status.get("state") == "active" and status.get("substate") == "exited":
        return "succeeded"
    return "pending"


def cleanup_notice(outcome: str, status: dict) -> Optional[dict]:
    """The result's platform entry for a cleanup outcome, or None when it succeeded."""
    if outcome == "failed":
        message = f"La limpieza de la sesión falló (systemd: {status.get('result')}); la consola la completó"
    elif outcome == "missing":
        message = "La limpieza de la sesión no se pudo comprobar: su unidad no está instalada"
    elif outcome == "timeout":
        message = f"La limpieza de la sesión no terminó en {int(CLEANUP_TIMEOUT)} s; la consola la completó"
    else:
        return None
    return {"ok": False, "code": f"cleanup_{outcome}", "message": message}


def sweep_cleanups(runner: "Runner", manager: SessionManager) -> Dict[str, dict]:
    """At launcher start: cleanup units left by sessions that ended while no
    launcher watched. A finished one is released and a failure returned by
    session id, to go with that session's result; one still running is
    watched as if this launcher had started it. An inactive one belongs to a
    game unit that is still there (it loads its trigger) and is left alone."""
    notices: Dict[str, dict] = {}
    for sid in runner.list_cleanup_units():
        status = runner.cleanup_status(sid)
        outcome = cleanup_outcome(status)
        if outcome == "pending":
            if status.get("state") not in (None, "inactive"):
                manager.cleanups[sid] = manager.clock() + CLEANUP_TIMEOUT
            continue
        runner.release_cleanup(sid, outcome)
        notice = cleanup_notice(outcome, status)
        if notice:
            log(f"session {sid}: its cleanup ended {outcome} while no launcher watched")
            notices[sid] = notice
    return notices


def release_card(serial: str, socket_path: Path) -> int:
    """CLI for safe removal: ask the running launcher (which asks the card service)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(SAVE_TIMEOUT + 5)
            sock.connect(str(socket_path))
            sock.sendall((json.dumps({"type": "release", "serial": serial}) + "\n").encode("utf-8"))
            reader = LineReader()
            while True:
                data = sock.recv(65536)
                if not data:
                    print(json.dumps({"ok": False, "error": {"code": "no_reply", "message": "launcher closed the connection"}}))
                    return 1
                for message in reader.feed(data):
                    if message.get("type") == "released":
                        print(json.dumps(message))
                        return 0 if message.get("ok") else 1
    except (OSError, FrameTooLarge) as exc:
        print(json.dumps({"ok": False, "error": {"code": "launcher_unavailable", "message": str(exc)}}))
        return 1


CARD_RECORD_FIELDS = ("slot", "serial", "state", "insertion", "active", "device")


def card_records(cardd_socket: Path, timeout: float = 10.0) -> int:
    """CLI for the lab: print the card service's current records, one JSON line.

    It reads the snapshot cardd sends every client on connect and keeps only
    identity and state (no manifest, no cover). The lab's automatic unplugger
    uses it as the authority for "this insertion was released", instead of
    trusting a log line that may be old (the insertion token changes with
    every plug)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(cardd_socket))
            reader = LineReader()
            while True:
                data = sock.recv(65536)
                if not data:
                    print(json.dumps({"ok": False, "error": {"code": "no_snapshot", "message": "card service closed the connection"}}))
                    return 1
                for message in reader.feed(data):
                    if message.get("type") == "snapshot":
                        cards = [{key: card.get(key) for key in CARD_RECORD_FIELDS}
                                 for card in message.get("cards", []) if isinstance(card, dict)]
                        print(json.dumps({"ok": True, "reader": message.get("reader"), "cards": cards}))
                        return 0
    except (OSError, FrameTooLarge) as exc:
        print(json.dumps({"ok": False, "error": {"code": "cardd_unavailable", "message": str(exc)}}))
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--socket", default=str(SOCKET_PATH))
    parser.add_argument("action", nargs="?", choices=["serve", "release", "cards"], default="serve")
    parser.add_argument("serial", nargs="?", help="with `release`: the card serial to release safely")
    args = parser.parse_args(argv)
    if args.action == "release":
        if not args.serial:
            parser.error("release needs a card serial")
        return release_card(args.serial, Path(args.socket))
    if args.action == "cards":
        return card_records(CARDD_SOCKET)

    LAUNCH_ROOT.mkdir(parents=True, exist_ok=True)
    CONTENT_MOUNT.mkdir(parents=True, exist_ok=True)   # mount point for mount-access games
    if os.path.realpath(LEGACY_CONTENT_MOUNT) != str(CONTENT_MOUNT):
        log(f"warning: {LEGACY_CONTENT_MOUNT} does not lead to {CONTENT_MOUNT}; games built with the "
            "earlier content path will not find their data (deploy/tmpfiles.conf)")
    mount_opts = subprocess.run(["findmnt", "-no", "OPTIONS", str(LAUNCH_ROOT)], capture_output=True, text=True, check=False).stdout
    if not mount_opts.strip() or "noexec" in mount_opts:
        log(f"warning: {LAUNCH_ROOT} is not an exec-capable mount ({mount_opts.strip() or 'not a mount point'}); launches will fail")

    queue: List[Callable[[], None]] = []
    lock = threading.Lock()
    wake_r, wake_w = socket.socketpair()

    def schedule(fn: Callable[[], None]) -> None:
        with lock:
            queue.append(fn)
        try:
            wake_w.send(b"x")
        except OSError:
            pass

    cards = CardView()
    runner = Runner(LAUNCH_ROOT)
    manager = SessionManager(cards, stage_via_cardd, runner, ShellControl(), lambda m: server.broadcast(m),
                             schedule, results=ResultStore(RESULT_FILE), saver=stage_via_cardd)
    server = Server(Path(args.socket), manager)
    listener = server.start()
    log(f"mun-launchd ready: socket {args.socket}, launch root {LAUNCH_ROOT}, game user {GAME_USER}")

    # Reconcile: adopt a surviving game unit, or make sure the shell is back.
    if not reconcile_sessions(runner, manager, LAUNCH_ROOT, sweep_cleanups(runner, manager)):
        ShellControl().start()

    # Card service feed: a client connection with reconnection.
    cardd_sock: Optional[socket.socket] = None
    cardd_reader = LineReader()
    last_cardd_attempt = 0.0

    def connect_cardd() -> Optional[socket.socket]:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(CARDD_SOCKET))
            s.setblocking(False)
            return s
        except OSError:
            return None

    stop = {"flag": False}
    # A read lease (directory saves) is broken with SIGIO if someone opens the
    # file for writing while it is held; the default action would end the
    # launcher. The game is frozen then, and the lease lasts one read.
    signal.signal(signal.SIGIO, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))

    selector = selectors.DefaultSelector()
    server.selector = selector
    selector.register(listener, selectors.EVENT_READ, "accept")
    selector.register(wake_r, selectors.EVENT_READ, "wake")

    # Per-session save socket (docs/saves.md): opened right before the game starts,
    # closed with the session. Each connection carries one bounded request line.
    gate = {"listener": None, "sid": None, "clients": {}}

    def gate_send(conn: socket.socket, message: dict) -> None:
        try:
            conn.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass
        gate_drop(conn)

    def gate_drop(conn: socket.socket) -> None:
        gate["clients"].pop(conn, None)
        try:
            selector.unregister(conn)
        except (KeyError, ValueError):
            pass
        conn.close()

    def gate_close() -> None:
        for conn in list(gate["clients"]):
            gate_drop(conn)
        if gate["listener"] is not None:
            try:
                selector.unregister(gate["listener"])
            except (KeyError, ValueError):
                pass
            gate["listener"].close()
        gate["listener"], gate["sid"] = None, None

    def gate_open(sid: str) -> None:
        gate_close()
        gate["listener"] = runner.open_save_socket(sid)
        gate["sid"] = sid
        selector.register(gate["listener"], selectors.EVENT_READ, "save-accept")

    manager.gate_open, manager.gate_close = gate_open, gate_close
    last_tick = time.monotonic()
    while not stop["flag"]:
        if cardd_sock is None and time.monotonic() - last_cardd_attempt > 1.0:
            last_cardd_attempt = time.monotonic()
            cardd_sock = connect_cardd()
            if cardd_sock is not None:
                selector.register(cardd_sock, selectors.EVENT_READ, "cardd")
                cardd_reader = LineReader()
        for key, _ in selector.select(timeout=POLL_INTERVAL):
            if key.data == "accept":
                conn = server.accept()
                if conn is not None:
                    selector.register(conn, selectors.EVENT_READ, "client")
            elif key.data == "client":
                server.handle(key.fileobj)   # drop() unregisters on close
            elif key.data == "save-accept":
                try:
                    conn, _ = gate["listener"].accept()
                except OSError:
                    continue
                conn.setblocking(False)
                gate["clients"][conn] = LineReader(limit=SAVE_LINE_MAX)
                selector.register(conn, selectors.EVENT_READ, "save-client")
            elif key.data == "save-client":
                conn = key.fileobj
                reader = gate["clients"].get(conn)
                try:
                    data = conn.recv(65536)
                    requests = reader.feed(data) if (data and reader) else []
                except (OSError, FrameTooLarge):
                    data = b""
                    requests = []
                if not data:
                    gate_drop(conn)
                    continue
                for request in requests[:1]:   # one request per connection
                    manager.save_request(gate["sid"], request, lambda message, c=conn: gate_send(c, message))
            elif key.data == "wake":
                wake_r.recv(4096)
                with lock:
                    pending, queue[:] = queue[:], []
                for fn in pending:
                    fn()
            elif key.data == "cardd":
                try:
                    data = cardd_sock.recv(1 << 20)
                    messages = cardd_reader.feed(data) if data else []
                except FrameTooLarge as exc:
                    log(f"card service feed: {exc}; reconnecting for a fresh snapshot")
                    data = b""
                except OSError:
                    data = b""
                if not data:
                    selector.unregister(cardd_sock)
                    cardd_sock.close()
                    cardd_sock = None
                    cards.connected = False
                    manager.reader_disconnected()
                    continue
                for message in messages:
                    was_connected = cards.connected
                    removed = cards.apply(message)
                    if message.get("type") == "snapshot" and not was_connected:
                        manager.reader_reconnected()
                    elif message.get("type") == "card":
                        manager.card_changed()
                    if removed:
                        manager.card_removed(removed, message.get("insertion"))
        if time.monotonic() - last_tick >= POLL_INTERVAL:
            manager.tick()
            last_tick = time.monotonic()

    try:
        Path(args.socket).unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
