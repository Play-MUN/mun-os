#!/usr/bin/env python3
"""Behaviour regressions of MUN Shell's MUN Shape code, on the real binary.

    python3 tests/behaviour.py BUILD_DIR/mun-shell [--work DIR] [--only NAME] [--platform eglfs] [--timing record]

Every case runs the compiled shell with one of tests/scenes/ in place of its
Main.qml (MUN_SHELL_QML_DIR), offscreen with the software renderer: the
scene uses the shell's own QML module, its Shape controller and loader, its
CardClient, its arc and panel. The card service's exports are made here as
the service makes them (the card tool's checker, the accepted files and the
normalised document bound to the insertion), and a stand-in for the card
service speaks its socket protocol where a case needs the card's record to
come from CardClient. The cases:

- card: a card window that does not decode (a well-formed PNG whose image
  data ends early, which the checker accepts) leaves MUN's object whole, no
  outline and no light, keeps the palette and the sounds, and its light
  reaches no ambient; likewise a cover that stands in for the window; a
  good window is the control. A result that comes after a newer card is
  dropped.
- cue: the insertion cue is due only for a card that arrived while the shell
  was watching, however long its copy takes: not for a card found at start
  whose copy ends later, nor after a restart, nor twice for one insertion
  (the runtime directory's marker), nor after a reconnection; a card taking
  over from another is greeted.
- surfaces: the dressed arc and panel with the two samples and a palette at
  the contrast rule's limit, over a white and a black world, on Home, with
  the panel's options focused and back on Home: text on every dressed plate
  keeps 4.5:1, the focused option's frame 3:1 and its text 4.5:1, measured
  on the grabbed frames; the arc then shows no chosen bar, so the panel's
  option is the only focus. Also mid-transition, at points of every plan.
- presence: Shape's phases and the reasons it leaves, a result held while
  it leaves, lent colours (controller.qml).
- world: ShapeWorld alone (world.qml): its detail within the budget, its
  frames in motion, still and at rest, a still world set whole at once
  (shown, not the empty frame before it), every option the samples do not
  use (a swaying layer, an orbit, a pulsing light added, a still one), its
  steps down, a layer that does not decode.
- memory: what a world takes at its peak, the shell's own resident memory
  measured (wait4) against the engine's estimate at 1080p and 1440p, for
  worlds that keep every limit but scale large or decode larger, and a
  sample stepped down to still; how the extreme ones look.
- boot: the start-up as the first start of a boot plays it (boot.qml), the
  shell's own Main.qml against stand-ins for the card service and the
  launcher: with both answering at once, "A Skip" shows and A ends it while
  another key does nothing; with them answering late, nothing skips it, the
  LED waits for them once the picture has faded and Home follows them; with
  neither, Home comes after its 20 s; with the interface sounds off its
  clock runs from the start; with a sound device expected
  (MUN_SHELL_SOUND_WAIT) that never comes, the LED breathes until the
  wait is over, then the picture runs on its own and the journal says so;
  a sound device that is not there is said once, however often it is tried;
  every time, standby from the first frame (the LED lit at once, as the
  system's standby picture has it) until the start-up begins.
- home: the shell's own Main.qml (home.qml) against stand-ins for the card
  service and the launcher: an arrival, dialogs, a return, removal, Eject
  safely confirmed and refused, another card, the player's choices, a
  defective world, Settings; and, with the options focused, a package that
  arrives late, Eject safely, a changed choice and another card, frame by
  frame (a dressed entry whole on every frame); an arrival after Settings
  or a dialog only once Home is wholly on screen; the card object, for
  packages with only their card block (an organic outline and a card's),
  with a world, and with a window that does not decode, each window found
  on screen by its own colour; idle dimming and the key that wakes it.
  Three ways, each required: the previous design on the software renderer
  (the reference), the orbital design on the software renderer (marked
  orbital-sw/, as the virtual console draws it) and through OpenGL
  (orbital-gl/: Xvfb, or a board's GPU with --platform eglfs; without Xvfb
  it fails). Every case first requires the design and renderer it asked
  for, its ten menu entries and its card object to be found. Steps and the
  stand-ins' events follow the shell's states (a step waits for its state,
  a service for the scene's line), each within a limit that fails the
  case; durations are judged or recorded (--timing).
- motion: the MUN Shape motion player alone (motion.qml; format
  mun-shape/1.1, draft), with each of its two ways of drawing (cpu: one
  image composed off the GUI thread; graph: a scene graph node per thing):
  as what it draws grows while it is on screen (particles appearing), it
  keeps drawing and the shell keeps running; a still frame of the same
  package matches between the two within measured bounds; a plant bends
  from its foot in its bands, alike both ways; a reaction to Play in focus
  takes the character along its path to the path's end.
- paths: the two drawing paths (paths.qml), the software renderer and
  OpenGL (an X server from Xvfb, Mesa's llvmpipe): each says in the journal
  which renderer and which clock it took, and the same still screen (Home's
  network, objects loaded after a change, the panel's surface and shadow, a
  chosen entry's bar, glow and knob) matches between them within measured
  bounds. With --platform eglfs, OpenGL is the board's own GPU; without
  Xvfb elsewhere it fails.
- settings: the player's settings file (settings.qml): a damaged file reads
  as the defaults and the next choice writes a valid one; a value outside
  the allowed set reads as its default; a change replaces the file whole (a
  new file renamed over the old one) and a temporary file left by an
  interrupted write is ignored; Reset settings keeps the language alone.

Linux, with the shell's run-time libraries (the image's). The image build
runs this after compiling the shell (os/mkosi/mkosi.build.chroot); exit
status 1 if any expectation fails. --work keeps the scenes, logs, grabs and
the measurements (report.json) there.
"""
import argparse
import base64
import contextlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools" / "mun-card"))

from mun_card import shape, shapetools  # noqa: E402
from mun_card.source import DirectorySource  # noqa: E402

SAMPLES = REPO / "examples" / "shape"
TEXT_RATIO, FOCUS_RATIO = shape.TEXT_RATIO, shape.FOCUS_RATIO

# A palette at the contrast rule's limit: over the worst world its entries
# (paper) keep 4.50:1 and 3.01:1, its bands (glass at 0.961) 4.51:1 and
# 3.02:1 (tests/test_shape.py checks it with the checker).
EDGE = {
    "format": "mun-shape/1",
    "palette": {"light": "#EEF3E6", "mid": "#8FA07A", "deep": "#26301C",
                "plate": "#858D79", "text": "#1F1C19", "accent": "#6F1C12"},
    "surfaces": {"entries": {"material": "paper"}, "panel": {"material": "solid"}},
}


# ---------------------------------------------------------------- fixtures

def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def short_png() -> bytes:
    """A 1x1 PNG, every chunk and CRC right, whose zlib stream ends after its
    header: the checker's structural reading accepts it; decoding fails."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", b"\x78\x9c") + _chunk(b"IEND", b"")


def png(width: int, height: int, rows: list, depth: int = 8, colour: int = 2, palette: list = None) -> bytes:
    """A PNG of any depth and colour type the checker takes: `rows` are each
    row's bytes (unfiltered), `palette` the colours of type 3."""
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
    body = _chunk(b"IHDR", header)
    if palette:
        body += _chunk(b"PLTE", b"".join(bytes(c) for c in palette))
    data = zlib.compress(b"".join(b"\x00" + row for row in rows), 9)
    return b"\x89PNG\r\n\x1a\n" + body + _chunk(b"IDAT", data) + _chunk(b"IEND", b"")


def tone(seconds: float) -> bytes:
    frames = []
    for i in range(int(48000 * seconds)):
        value = 6000 if (i // 60) % 2 else -6000
        frames.append((value, value))
    return shapetools.encode_wav(frames)


def write_package(folder: Path, document: dict, files: dict) -> Path:
    folder.mkdir(parents=True)
    (folder / "shape.json").write_text(json.dumps(document), encoding="utf-8")
    for relative, data in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(data)
    return folder


def export(root: Path, insertion: str, package: Path) -> dict:
    """The card service's export of `package` for `insertion`, and its record."""
    result = shape.check_package(DirectorySource(package))
    assert result.shape is not None, (package, result.to_dict())
    target = root / f"{insertion}.1"
    target.mkdir(parents=True)
    for relative in result.files:
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / relative, target / relative)
    document = dict(result.shape, insertion=insertion, version="1.0.0")
    (target / shape.MANIFEST).write_text(json.dumps(document, allow_nan=False) + "\n", encoding="utf-8")
    return {"state": result.state, "insertion": insertion, "version": "1.0.0", "path": str(target)}


def record(insertion: str, shape_record, slot: str = "c1", active: bool = True, cover: bytes = b"") -> dict:
    """A card service record of a valid card."""
    info = {"title": "Test", "id": "mun.test", "version": "1.0.0"}
    if cover:
        info["cover_data"] = base64.b64encode(cover).decode("ascii")
    return {"slot": slot, "insertion": insertion, "device": "vdb", "serial": "MUN-TEST",
            "state": "valid" if active else "waiting", "active": active, "info": info if active else None,
            "error": None, "shape": shape_record}


def insertion(number: int) -> str:
    return f"{number:016x}"


# ------------------------------------------------------------------ running

class Shell:
    def __init__(self, binary: Path, work: Path, platform: str = "offscreen"):
        self.binary, self.work, self.platform = binary, work, platform
        self.peak_kib = {}   # each run's peak resident memory (the kernel's ru_maxrss), by name

    def run(self, name: str, scene: str, config: dict, runtime: Path = None, socket_path: Path = None,
            exports: Path = None, timeout: float = 90, launcher: Path = None, settings: str = "",
            scale: float = 1, display: str = None, software: bool = False, design: str = None,
            watch=None, env: dict = None, kill_when=None) -> str:
        """Runs the shell with `scene` and returns its output. `env` adds
        to (or replaces in) its environment. `kill_when`, asked every 2 ms
        while the shell runs: once true, the shell is killed (SIGKILL, as a
        crash: nothing of its own runs) and the output ends "(killed)". `scale` is the
        display's device pixels per canvas pixel (4/3: 2560x1440). With an X
        `display` it draws there through OpenGL (xcb) instead of offscreen
        with the software renderer; `software` keeps that renderer on a
        board's display too. `design` ("orbital" or "previous") chooses
        Home's design (MUN_SHELL_DESIGN); none: the shell's own. `watch` is
        called with each line of the output as it is written, while the
        shell runs (the stand-in services follow the scene by it)."""
        case = self.work / name
        qml = case / "qml"
        qml.mkdir(parents=True, exist_ok=True)
        text = (HERE / "scenes" / scene).read_text(encoding="utf-8")
        (qml / "Main.qml").write_text(text.replace("CONFIG", json.dumps(config)), encoding="utf-8")
        runtime = runtime or case / "runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = case / "state"
        state.mkdir(parents=True, exist_ok=True)
        # None: the caller wrote the file itself.
        if isinstance(settings, bytes):
            (state / "settings.ini").write_bytes(settings)
        elif settings is not None:
            (state / "settings.ini").write_text(settings, encoding="utf-8")
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                           QT_SCALE_FACTOR=repr(scale),
                           QT_FORCE_STDERR_LOGGING="1", LANG="C.UTF-8", HOME=str(case / "home"),
                           XDG_CONFIG_HOME=str(case / "config"), XDG_RUNTIME_DIR=str(runtime),
                           MUN_SHELL_QML_DIR=str(qml), MUN_SHAPE_ROOT=str(exports or self.work / "exports"),
                           MUN_CARDD_SOCKET=str(socket_path or case / "no-service.sock"),
                           MUN_LAUNCHD_SOCKET=str(launcher or case / "no-launcher.sock"), STATE_DIRECTORY=str(state))
        for key in ("QT_QPA_KMS_CONFIG", "QT_QPA_EGLFS_KMS_CONFIG", "WAYLAND_DISPLAY", "DISPLAY", "MUN_SHELL_TIMING",
                    "MUN_SHELL_DESIGN", "MUN_SHELL_MOTION_BACKEND", "MUN_SHELL_MOTION_TRACE"):
            environment.pop(key, None)
        if design:
            environment["MUN_SHELL_DESIGN"] = design
        if display:
            environment.pop("QT_QUICK_BACKEND")
            environment.update(QT_QPA_PLATFORM="xcb", DISPLAY=display)
        elif self.platform == "eglfs" and not software:
            # A board's own display and GPU (the console's service stopped).
            environment.pop("QT_QUICK_BACKEND")
            environment.update(QT_QPA_PLATFORM="eglfs", QT_QPA_EGLFS_INTEGRATION="eglfs_kms")
        environment.update(env or {})
        # Reaped with wait4, which also gives this process's own peak memory.
        log = case / "output.log"
        with open(log, "wb") as sink, open(log, "rb") as source:
            process = subprocess.Popen([str(self.binary)], env=environment, stdout=sink, stderr=subprocess.STDOUT, cwd=case)
            deadline, timed_out, killed = time.monotonic() + timeout, False, False
            pending = b""

            def follow():
                nonlocal pending
                pending += source.read()
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    watch(line.decode("utf-8", "replace"))
            while True:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if watch:
                    follow()
                if pid:
                    break
                if kill_when is not None and not killed and kill_when():
                    process.kill()
                    killed = True
                if time.monotonic() > deadline:
                    process.kill()
                    pid, status, usage = os.wait4(process.pid, 0)
                    timed_out = True
                    break
                time.sleep(0.002 if kill_when is not None else 0.05)
            process.returncode = os.waitstatus_to_exitcode(status)
        self.peak_kib[name] = usage.ru_maxrss
        output = log.read_text(encoding="utf-8", errors="replace")
        if timed_out:
            output += "\n(timed out)"
        elif killed:
            output += "\n(killed)"
        elif process.returncode:
            output += f"\n(exit status {process.returncode})"
        log.write_text(output, encoding="utf-8")
        return output


def lines(output: str, tag: str) -> list:
    return [json.loads(m.group(1)) for m in re.finditer(rf"{tag} (\{{.*\}})\s*$", output, re.M)]


class Service:
    """A stand-in for the card service (or the launcher) on its socket: one
    script per connection, each a list of (seconds after the connection,
    message); a message "close" ends that connection. `answer(service,
    message)` sees every line the shell sends; push() sends a message on the
    connection that is open.

    A script's time is seconds after the connection; or {"on": EVENT,
    "after": s}: s seconds after the scene showed EVENT (signal(): a PHASE
    line's label, or "phase:" and its phase), awaited at most "within"
    seconds (60 by default); or {"wait": s}: s seconds after the message
    before. A functional step then follows what the shell did, not a second
    clock. An answer() waits for an event with expect(). An event that does
    not come within its limit, or before the stand-in is closed, is kept in
    `errors`, naming it, and nothing that should have followed it is sent:
    that script, and those after it, stop there. Once close() returns,
    `errors` holds every wait it ended, and home_cases fails the case with
    them."""

    def __init__(self, path: Path, scripts: list, answer=None):
        self.path, self.scripts, self.answer = path, scripts, answer
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.stopping = False
        self.held = []
        self.current = None
        self.lock = threading.Lock()
        self.received = []
        self.events = {}
        self.awaiting = {}     # a wait in progress: its token, the event's name
        self.errors = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def event(self, name):
        with self.lock:
            return self.events.setdefault(name, threading.Event())

    def signal(self, name):
        self.event(name).set()

    def expect(self, name, within):
        """Wait at most `within` seconds for the scene to show `name`: True
        once it has. False when it did not come in time, or the stand-in was
        closed first: the failure is then in `errors` and the caller sends
        nothing of what should have followed. Already closed: False, as
        nothing more is sent."""
        event, token = self.event(name), object()
        with self.lock:
            if self.stopping:
                return False
            self.awaiting[token] = name
        deadline = time.monotonic() + within
        while True:
            came = event.wait(max(0.0, min(0.05, deadline - time.monotonic())))
            with self.lock:
                if token not in self.awaiting:     # close() ended it, and said so
                    return False
                if came:
                    del self.awaiting[token]
                    return True
                if time.monotonic() >= deadline:
                    del self.awaiting[token]
                    self.errors.append(f"{self.path.name}: {name!r} did not come within {within:g} s; "
                                       "nothing after it was sent")
                    return False

    def push(self, message):
        with self.lock:
            if self.current is not None:
                try:
                    self.current.sendall((json.dumps(message) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def _read(self, conn):
        buffer = b""
        while not self.stopping:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                self.received.append(message)
                if self.answer:
                    self.answer(self, message)

    def _serve(self):
        for script in self.scripts:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with self.lock:
                self.current = conn
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()
            start = last = time.monotonic()
            for at, message in script:
                if isinstance(at, dict) and "on" in at:
                    if not self.expect(at["on"], at.get("within", 60)):
                        self._hold(conn)
                        return
                    time.sleep(at.get("after", 0))
                elif isinstance(at, dict):
                    time.sleep(max(0.0, last + at["wait"] - time.monotonic()))
                else:
                    time.sleep(max(0.0, start + at - time.monotonic()))
                last = time.monotonic()
                if self.stopping:
                    return
                if message == "close":
                    with self.lock:
                        self.current = None
                    conn.close()
                    break
                self.push(message)
            else:
                self._hold(conn)

    def _hold(self, conn):
        # Kept open until close(), so the shell does not see a disconnection
        # the script did not ask for.
        with self.lock:
            if not self.stopping:
                self.held.append(conn)
                return
        conn.close()

    def close(self):
        with self.lock:
            self.stopping = True
            for name in self.awaiting.values():
                self.errors.append(f"{self.path.name}: {name!r} had not come when the scene ended; "
                                   "nothing after it was sent")
            self.awaiting.clear()
            held, self.held = self.held, []
        self.server.close()
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass


def snapshot(*cards) -> dict:
    return {"type": "snapshot", "protocol": 1, "reader": "ready", "cards": list(cards)}


# --------------------------------------------------------------- measuring

def read_ppm(path: Path):
    data = path.read_bytes()
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not match or match.group(3) != b"255":
        raise ValueError(f"{path} is not an 8-bit binary PPM")
    width, height = int(match.group(1)), int(match.group(2))
    return width, height, data[match.end():match.end() + width * height * 3]


_LINEAR = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in range(256)]


def luminance(pixel) -> float:
    return 0.2126 * _LINEAR[pixel[0]] + 0.7152 * _LINEAR[pixel[1]] + 0.0722 * _LINEAR[pixel[2]]


def ratio(a: float, b: float) -> float:
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def pixels(image, box):
    width, height, data = image
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3])
    for y in range(y0, y1):
        row = y * width * 3
        for x in range(x0, x1):
            yield data[row + 3 * x:row + 3 * x + 3]


def text_contrast(image, box) -> dict:
    """The laboratory's measure: the plate is the region's median luminance,
    the text the mean of the 1.5 % of pixels farthest from it on the side
    the text lies (antialiased edges fall between)."""
    values = sorted(luminance(p) for p in pixels(image, box))
    if len(values) < 200:
        raise ValueError(f"region {box} holds {len(values)} pixels")
    plate = values[len(values) // 2]
    k = max(1, int(len(values) * 0.015))
    light, dark = sum(values[-k:]) / k, sum(values[:k]) / k
    text = light if light - plate > plate - dark else dark
    return {"plate": round(plate, 4), "text": round(text, 4), "ratio": round(ratio(text, plate), 2)}


def label_contrast(image, box) -> dict:
    """text_contrast over the label's own extent inside `box`: the pixels
    farther from the plate than half the farthest one bound it, with 3 px
    around. A short label in a wide row is otherwise too few pixels for the
    1.5 % the measure takes as its text."""
    width = image[0]
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(width, box[2]), min(image[1], box[3])
    lums = [(x, y, luminance(image[2][(y * width + x) * 3:(y * width + x) * 3 + 3]))
            for y in range(y0, y1) for x in range(x0, x1)]
    plate = sorted(value for _, _, value in lums)[len(lums) // 2]
    far = max(abs(value - plate) for _, _, value in lums)
    ink = [(x, y) for x, y, value in lums if abs(value - plate) >= far / 2]
    xs, ys = [x for x, _ in ink], [y for _, y in ink]
    fitted = [max(x0, min(xs) - 3), max(y0, min(ys) - 3), min(x1, max(xs) + 4), min(y1, max(ys) + 4)]
    return dict(text_contrast(image, fitted), label=fitted)


def colour(hex_colour: str):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def focus_frame(image, box, focus: str, near: int = 30):
    """The pixels of `box` within `near` of the focus colour: their bounds
    and median luminance, or None if there are fewer than 40."""
    width = image[0]
    target = colour(focus)
    found, lums = [], []
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(width, box[2]), min(image[1], box[3])
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * width + x) * 3
            p = image[2][i:i + 3]
            if sum((p[c] - target[c]) ** 2 for c in range(3)) <= near * near:
                found.append((x, y))
                lums.append(luminance(p))
    if len(found) < 40:
        return None
    xs, ys = [x for x, _ in found], [y for _, y in found]
    lums.sort()
    return {"box": [min(xs), min(ys), max(xs) + 1, max(ys) + 1], "count": len(found),
            "luminance": round(lums[len(lums) // 2], 4)}


# ------------------------------------------------------------------- checks

class Checks:
    def __init__(self):
        self.failed = 0
        self.passed = 0
        self.skipped = 0
        self.failing = []   # the cases' folders, for their logs
        # Put before every case's name: a second run of cases in a folder of
        # the work directory ("orbital-sw/").
        self.tag = ""
        # Durations (how long a transition took, a key to its frame): judged
        # like any check ("check", where the machine is steady), or measured
        # and recorded without judging ("record": a busy builder, where a
        # slow frame is the machine's, not the shell's). What happens, and in
        # what order, is always judged.
        self.timing = "check"
        self.timings = []

    def skip(self, case: str, why: str):
        self.skipped += 1
        print(f"  skip  {self.tag}{case}: {why}", flush=True)

    def expect(self, case: str, what: str, ok: bool, detail=""):
        case = self.tag + case
        if ok:
            self.passed += 1
            print(f"  ok    {case}: {what}", flush=True)
        else:
            self.failed += 1
            print(f"  FAIL  {case}: {what} {detail}", flush=True)
            folder = case.split(" ")[0]
            if folder not in self.failing:
                self.failing.append(folder)


    def duration(self, case: str, what: str, ok: bool, detail=""):
        if self.timing == "check":
            self.expect(case, what, ok, detail)
            return
        self.timings.append({"case": self.tag + case, "what": what, "within": ok, "detail": str(detail)})
        print(f"  time  {self.tag}{case}: {what}: {'within' if ok else 'OUTSIDE'} (recorded, not judged here) {detail}",
              flush=True)


def last_probe(output: str, label: str):
    found = [p for p in lines(output, "PROBE") if p["label"] == label]
    return found[-1] if found else None


def card_cases(shell: Shell, checks: Checks, exports: Path, packages: Path):
    sea = json.loads((SAMPLES / "sea" / "shape.json").read_text(encoding="utf-8"))
    palette = sea["palette"]
    sounds = {f"sfx/{name}.wav": tone(0.2 if name != "insert" else 0.5) for name in ("move", "enter", "back", "insert")}
    base = {key: sea[key] for key in ("format", "palette", "card", "surfaces", "sounds")}
    window = shapetools.encode_png(8, 8, [bytes((20, 90, 120)) * 8] * 8, alpha=False)
    good = write_package(packages / "good", base, dict(sounds, **{"card/window.png": window}))
    bad = write_package(packages / "bad", base, dict(sounds, **{"card/window.png": short_png()}))
    coverless = dict(base, card={k: v for k, v in sea["card"].items() if k != "window"})
    by_cover = write_package(packages / "by-cover", coverless, sounds)

    records = {"good": record(insertion(1), export(exports, insertion(1), good)),
               "bad": record(insertion(2), export(exports, insertion(2), bad)),
               "by-cover": record(insertion(3), export(exports, insertion(3), by_cover), cover=short_png())}
    light, mid, glow = palette["light"].lower(), palette["mid"].lower(), sea["card"]["glow"].lower()
    menu = ["back", "enter", "insert", "move"]

    def one(name, which, mode):
        output = shell.run(name, "controller.qml", {"steps": [
            {"at": 0, "mode": mode, "cards": [records[which]]},
            {"at": 1500, "probe": "final"}, {"at": 1600, "quit": True}]}, exports=exports)
        return output, last_probe(output, "final")

    output, p = one("card-bad-window", "bad", "full")
    checks.expect("card-bad-window", "the package's identity is applied", p and p["source"] == "shape" and p["dressed"], p)
    checks.expect("card-bad-window", "MUN's object: no window, MUN's outline, no light",
                  p and not p["window"] and p["cardShape"] == "card" and p["morph"] == 0 and p["glow"] == "", p)
    checks.expect("card-bad-window", "the ambient is the palette's light, the tint its mid",
                  p and p["ambient"] == light and p["tint"] == mid, p)
    checks.expect("card-bad-window", "the sounds are kept", p and p["sounds"] == menu, p)
    checks.expect("card-bad-window", "the journal says the object is MUN's", "card object MUN's" in output)

    output, p = one("card-bad-window-colours", "bad", "colours")
    checks.expect("card-bad-window-colours", "colours only: MUN's object, the palette's light, no sounds",
                  p and p["dressed"] and not p["window"] and p["cardShape"] == "card" and p["glow"] == ""
                  and p["ambient"] == light and p["sounds"] == [], p)

    output, p = one("card-bad-cover", "by-cover", "full")
    checks.expect("card-bad-cover", "a cover that does not decode leaves MUN's object whole",
                  p and p["source"] == "shape" and not p["window"] and p["cardShape"] == "card" and p["morph"] == 0
                  and p["glow"] == "" and p["ambient"] == light and p["sounds"] == menu, p)

    output, p = one("card-good-window", "good", "full")
    checks.expect("card-good-window", "control: the window, the organic outline, the card's light as ambient",
                  p and p["window"] and p["cardShape"] == "organic" and abs(p["morph"] - 0.6) < 1e-6
                  and p["glow"] == glow and p["ambient"] == glow and p["sounds"] == menu, p)

    output, p = one("card-good-window-colours", "good", "colours")
    checks.expect("card-good-window-colours", "colours only: no object of the game's, the palette's light",
                  p and not p["window"] and p["cardShape"] == "card" and p["glow"] == "" and p["ambient"] == light, p)

    # A late result: two records in one turn; the first's load is stale.
    output = shell.run("card-late-result", "controller.qml", {"steps": [
        {"at": 0, "cards": [records["good"], records["bad"]]},
        {"at": 1500, "probe": "final"}, {"at": 1600, "quit": True}]}, exports=exports)
    applied = [p["insertion"] for p in lines(output, "PROBE") if p["label"] == "changed" and p["source"] == "shape"]
    p = last_probe(output, "final")
    checks.expect("card-late-result", "only the newer card's identity is applied",
                  applied == [insertion(2)] and p and p["insertion"] == insertion(2), applied)


def cue_cases(shell: Shell, checks: Checks, exports: Path, packages: Path):
    sea = write_package(packages / "cue", {k: v for k, v in json.loads(
        (SAMPLES / "sea" / "shape.json").read_text(encoding="utf-8")).items() if k in ("format", "palette", "surfaces")}, {})
    ids = {name: insertion(0x100 + n) for n, name in enumerate(("found", "quick", "slow", "take-a", "take-b", "again"))}
    ready = {name: export(exports, number, sea) for name, number in ids.items()}

    def preparing(name):
        return {"state": "preparing", "insertion": ids[name], "version": "1.0.0"}

    def shape_message(name, slot="c1"):
        return {"type": "shape", "slot": slot, "insertion": ids[name], "shape": ready[name]}

    def card_message(name, shape_record, slot="c1"):
        return {"type": "card", "card": record(ids[name], shape_record, slot=slot)}

    def run(name, scripts, end, runtime=None):
        path = shell.work / f"{name}.sock"
        service = Service(path, scripts)
        try:
            output = shell.run(name, "arrival.qml", {"end": end}, runtime=runtime, socket_path=path, exports=exports)
        finally:
            service.close()
        return [(a["insertion"], a["live"]) for a in lines(output, "ADOPTED")]

    got = run("cue-found-slow-copy", [[(0, snapshot(record(ids["found"], preparing("found")))),
                                       (5.6, shape_message("found"))]], 7500)
    checks.expect("cue-found-slow-copy", "a card found at start whose copy ends 5.6 s later: no cue",
                  got == [(ids["found"], False)], got)

    shared = shell.work / "cue-runtime"
    got = run("cue-arrives-quickly", [[(0, snapshot()), (0.3, card_message("quick", ready["quick"]))]], 2000,
              runtime=shared)
    checks.expect("cue-arrives-quickly", "a card inserted 0.3 s after start: the cue", got == [(ids["quick"], True)], got)
    marker = (shared / "shape-cue").read_text(encoding="ascii").strip() if (shared / "shape-cue").exists() else ""
    checks.expect("cue-arrives-quickly", "the runtime directory marks its cue spent", marker == ids["quick"], marker)

    got = run("cue-after-restart", [[(0, snapshot(record(ids["quick"], ready["quick"])))]], 2000, runtime=shared)
    checks.expect("cue-after-restart", "the same card after a restart: no cue", got == [(ids["quick"], False)], got)

    got = run("cue-spent", [[(0, snapshot()), (0.3, card_message("quick", ready["quick"]))]], 2000, runtime=shared)
    checks.expect("cue-spent", "the same insertion announced as new after a restart: no cue (the marker)",
                  got == [(ids["quick"], False)], got)

    got = run("cue-arrives-slow-copy", [[(0, snapshot()), (0.3, card_message("slow", preparing("slow"))),
                                         (5.6, shape_message("slow"))]], 7500)
    checks.expect("cue-arrives-slow-copy", "a card inserted whose copy ends 5.3 s later: the cue",
                  got == [(ids["slow"], True)], got)

    got = run("cue-takeover", [[(0, snapshot(record(ids["take-a"], ready["take-a"]),
                                            record(ids["take-b"], None, slot="c2", active=False))),
                                (0.8, {"type": "removed", "slot": "c1", "insertion": ids["take-a"]}),
                                (0.9, card_message("take-b", ready["take-b"], slot="c2"))]], 2500)
    checks.expect("cue-takeover", "found card no cue; the waiting card taking over: the cue",
                  got == [(ids["take-a"], False), (ids["take-b"], True)], got)

    got = run("cue-reconnection", [[(0, snapshot()), (0.3, card_message("again", ready["again"])), (1.0, "close")],
                                   [(0, snapshot(record(ids["again"], ready["again"])))]], 4500)
    checks.expect("cue-reconnection", "a reconnection finds the card again: one cue in all",
                  got == [(ids["again"], True)], got)


def surface_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, report: dict):
    edge = write_package(packages / "edge", EDGE, {})
    cards = [("sea", SAMPLES / "sea"), ("paper", SAMPLES / "paper"), ("edge", edge)]
    entries = [{"label": "Game Card", "detail": "Ready"}, {"label": "My games"}, {"label": "Settings"},
               {"label": "Turn off"}]
    panel = {"kicker": "GAME CARD", "title": "Tide Keeper", "text": "The card is ready.",
             "options": [{"kind": "action", "label": "Play"}, {"kind": "action", "label": "Eject safely"}]}
    for number, (name, package) in enumerate(cards):
        shape_record = export(exports, insertion(0x200 + number), package)
        document = json.loads((Path(shape_record["path"]) / shape.MANIFEST).read_text(encoding="utf-8"))
        colours = document["surfaces"]["colours"]
        if colours.get("source") != "shape":
            checks.expect(f"surfaces-{name}", "the package's surfaces hold", False, colours)
            continue
        out = shell.work / f"surfaces-{name}" / "grabs"
        out.mkdir(parents=True, exist_ok=True)
        shots = [{"name": f"{world}-{state}", "world": hex_colour, "state": state.split("-")[0]}
                 for world, hex_colour in (("white", "#FFFFFF"), ("black", "#000000"))
                 for state in ("home", "options", "home-again")]
        output = shell.run(f"surfaces-{name}", "surfaces.qml", {
            "card": record(insertion(0x200 + number), shape_record), "entries": entries, "panel": panel,
            "shots": shots, "out": str(out), "settle": 900, "timeout": 60000}, exports=exports)
        taken = {s["name"]: s for s in lines(output, "SHOT")}
        checks.expect(f"surfaces-{name}", "every shot was grabbed", len(taken) == len(shots) and "SHOT-FAILED" not in output,
                      sorted(taken))
        bar = luminance(colour(colours["bar"]))
        results = report.setdefault(name, {})
        for shot in shots:
            if shot["name"] not in taken:
                continue
            case = f"surfaces-{name} {shot['name']}"
            where = taken[shot["name"]]["regions"]
            image = read_ppm(Path(taken[shot["name"]]["path"]))
            measured = {"entries": {e["label"]: dict(text_contrast(image, e["box"]), chosen=e["chosen"])
                                    for e in where["entries"]},
                        "panel": text_contrast(image, where["panelText"])}
            frame = focus_frame(image, where["panel"], colours["focus"])
            if frame:
                inner = [frame["box"][0] + 8, frame["box"][1] + 8, frame["box"][2] - 8, frame["box"][3] - 8]
                row = label_contrast(image, inner)
                measured["focused option"] = dict(row, frame=round(ratio(frame["luminance"], row["plate"]), 2),
                                                  box=frame["box"])
            results[shot["name"]] = measured
            low = {label: m["ratio"] for label, m in measured["entries"].items() if m["ratio"] < TEXT_RATIO}
            checks.expect(case, "every entry's text keeps 4.5:1", not low, low or "")
            checks.expect(case, "the panel's text keeps 4.5:1", measured["panel"]["ratio"] >= TEXT_RATIO,
                          measured["panel"])
            chosen = next(m for m in measured["entries"].values() if m["chosen"])
            others = [m["plate"] for m in measured["entries"].values() if not m["chosen"]]
            if shot["state"] == "home":
                checks.expect(case, "the chosen entry is its bar; the panel shows no focus",
                              abs(chosen["plate"] - bar) < 0.02 and frame is None, (chosen, frame))
            else:
                checks.expect(case, "the arc shows no chosen bar: the chosen entry is a plate like the others",
                              abs(chosen["plate"] - bar) > 0.05 and all(abs(chosen["plate"] - o) < 0.02 for o in others),
                              (chosen, others))
                ok = frame is not None and frame["box"][3] - frame["box"][1] < 90
                checks.expect(case, "one option framed in the focus colour", ok, frame)
                if ok:
                    option = measured["focused option"]
                    checks.expect(case, "the focused option's frame keeps 3:1 and its text 4.5:1",
                                  option["frame"] >= FOCUS_RATIO and option["ratio"] >= TEXT_RATIO, option)
        # The same over the worst worlds, mid-transition (a tide): every
        # surface the front has reached is on a blending plate, and its text
        # holds at every point of its plan; the rest is MUN's, over MUN's world.
        tide_shots = [{"name": f"{world}-tide-{int(p * 100)}", "world": hex_colour, "state": "home", "progress": p}
                      for world, hex_colour in (("white", "#FFFFFF"), ("black", "#000000"))
                      for p in (0.31, 0.33, 0.35, 0.37, 0.39, 0.41, 0.43, 0.5, 0.6, 0.7)]
        output = shell.run(f"surfaces-{name}-tide", "surfaces.qml", {
            "card": record(insertion(0x280 + number), export(exports, insertion(0x280 + number), package)), "entries": entries,
            "panel": panel, "shots": tide_shots, "out": str(out), "settle": 700, "timeout": 60000, "kind": "tide"},
            exports=exports)
        taken = {s["name"]: s for s in lines(output, "SHOT")}
        checks.expect(f"surfaces-{name} tide", "every mid-transition shot was grabbed", len(taken) == len(tide_shots), sorted(taken))
        for shot in tide_shots:
            if shot["name"] not in taken:
                continue
            where = taken[shot["name"]]["regions"]
            image = read_ppm(Path(taken[shot["name"]]["path"]))
            reached = {e["label"]: e["reached"] for e in where["entries"] if e["reached"] > 0}
            if where.get("panelReached", 0) > 0:
                reached["panel"] = where["panelReached"]
            low = {}
            for e in where["entries"]:
                if e["reached"] > 0:
                    m = text_contrast(image, e["box"])
                    if m["ratio"] < TEXT_RATIO:
                        low[e["label"]] = (m["ratio"], round(e["reached"], 2))
            if where.get("panelReached", 0) > 0:
                m = text_contrast(image, where["panelText"])
                if m["ratio"] < TEXT_RATIO:
                    low["panel"] = (m["ratio"], round(where["panelReached"], 2))
            results[shot["name"]] = {"reached": reached, "low": low}
            checks.expect(f"surfaces-{name} {shot['name']}", "each reached surface's text keeps 4.5:1 mid-blend", not low, low)
        for world in ("white", "black"):
            before, after = results.get(f"{world}-home"), results.get(f"{world}-home-again")
            if before and after:
                same = all(abs(before["entries"][k]["ratio"] - after["entries"][k]["ratio"]) < 0.05
                           for k in before["entries"])
                checks.expect(f"surfaces-{name} {world}", "leaving the options restores Home as it was", same,
                              (before["entries"], after["entries"]))


def first_layer(sample: str) -> str:
    """The first layer a sample's world names (read, not assumed)."""
    document = json.loads((SAMPLES / sample / "shape.json").read_text(encoding="utf-8"))
    return document["world"]["layers"][0]["image"]


def sample_copy(packages: Path, name: str, sample: str, change=None, replace=None) -> Path:
    """A copy of a sample package, its document changed by `change`, files
    replaced by `replace` (relative path: bytes)."""
    target = packages / name
    shutil.copytree(SAMPLES / sample, target)
    if change:
        document = json.loads((target / "shape.json").read_text(encoding="utf-8"))
        change(document)
        (target / "shape.json").write_text(json.dumps(document), encoding="utf-8")
    for relative, data in (replace or {}).items():
        (target / relative).write_bytes(data)
    return target


def presence_cases(shell: Shell, checks: Checks, exports: Path, packages: Path):
    ids = {n: insertion(0x300 + i) for i, n in enumerate(("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"))}
    sea = {k: export(exports, v, SAMPLES / "sea") for k, v in ids.items() if k in "abcdefg"}
    paper = {k: export(exports, v, SAMPLES / "paper") for k, v in ids.items() if k in "hij"}
    rec = lambda k, shape_record: record(ids[k], shape_record)
    released = lambda r: dict(r, state="released", info=None)

    def run(name, steps):
        output = shell.run(name, "controller.qml", {"steps": steps}, exports=exports)
        return output, {p["label"]: p for p in lines(output, "PROBE")}, [p for p in lines(output, "PROBE") if p["label"] == "phase"]

    out, probes, phases = run("presence-arrival", [
        {"at": 0, "arrival": ids["a"], "cards": [rec("a", sea["a"])]}, {"at": 800, "probe": "applied"},
        {"at": 900, "begin": "tide"}, {"at": 950, "progress": 0.5}, {"at": 1000, "probe": "mid"},
        {"at": 1100, "progress": 1, "arrived": True}, {"at": 1200, "probe": "present"}, {"at": 1300, "quit": True}])
    a = probes.get("applied", {})
    checks.expect("presence-arrival", "applied, not shown: ready, an arrival, with its world and plates",
                  a.get("phase") == "ready" and a.get("entry") == "arrival" and a.get("world") and a.get("plated")
                  and a.get("transition") == ["tide", "tide", 3.2], a)
    checks.expect("presence-arrival", "begun: entering the tide; arrived: present",
                  probes.get("mid", {}).get("phase") == "entering" and probes.get("mid", {}).get("kind") == "tide"
                  and probes.get("present", {}).get("phase") == "present", (probes.get("mid"), probes.get("present")))

    out, probes, _ = run("presence-release", [
        {"at": 0, "cards": [rec("b", sea["b"])]}, {"at": 800, "begin": "fade", "progress": 1, "arrived": True},
        {"at": 900, "cards": [released(rec("b", sea["b"]))]}, {"at": 1000, "probe": "leaving"},
        {"at": 1100, "leaveWith": "tide"}, {"at": 1150, "progress": 0}, {"at": 1200, "left": True},
        {"at": 1300, "probe": "after"}, {"at": 1400, "quit": True}])
    l, after = probes.get("leaving", {}), probes.get("after", {})
    checks.expect("presence-release", "released: it leaves (release), still showing its identity",
                  l.get("phase") == "leaving" and l.get("exit") == "release" and l.get("insertion") == ids["b"]
                  and l.get("source") == "shape" and l.get("dressed"), l)
    checks.expect("presence-release", "left: MUN's", after.get("phase") == "none" and after.get("source") == "none", after)

    out, probes, _ = run("presence-removal-entering", [
        {"at": 0, "arrival": ids["c"], "cards": [rec("c", sea["c"])]}, {"at": 800, "begin": "tide"},
        {"at": 850, "progress": 0.4}, {"at": 900, "cards": [{}]}, {"at": 1000, "probe": "leaving"},
        {"at": 1100, "progress": 0, "left": True}, {"at": 1200, "probe": "after"}, {"at": 1300, "quit": True}])
    l = probes.get("leaving", {})
    checks.expect("presence-removal-entering", "removed while it came in: it leaves (removal) from where it was",
                  l.get("phase") == "leaving" and l.get("exit") == "removal" and l.get("kind") == "tide"
                  and abs(l.get("progress", 0) - 0.4) < 1e-6, l)
    checks.expect("presence-removal-entering", "left: MUN's", probes.get("after", {}).get("source") == "none", probes.get("after"))

    out, probes, _ = run("presence-change", [
        {"at": 0, "cards": [rec("d", sea["d"])]}, {"at": 800, "begin": "fade", "progress": 1, "arrived": True},
        {"at": 900, "cards": [rec("h", paper["h"])]}, {"at": 1000, "probe": "leaving"},
        {"at": 1800, "probe": "held"}, {"at": 1900, "progress": 0, "left": True}, {"at": 2000, "probe": "after"},
        {"at": 2100, "quit": True}])
    l, held, after = probes.get("leaving", {}), probes.get("held", {}), probes.get("after", {})
    checks.expect("presence-change", "another card: the first leaves (change) and keeps its identity meanwhile",
                  l.get("exit") == "change" and held.get("phase") == "leaving" and held.get("insertion") == ids["d"], (l, held))
    checks.expect("presence-change", "once it has left, the new card's identity is applied, ready",
                  after.get("phase") == "ready" and after.get("insertion") == ids["h"]
                  and after.get("transition") == ["sweep", "fade", 1.6], after)

    out, probes, _ = run("presence-stale-held", [
        {"at": 0, "cards": [rec("e", sea["e"])]}, {"at": 800, "begin": "fade", "progress": 1, "arrived": True},
        {"at": 900, "cards": [rec("i", paper["i"])]}, {"at": 1500, "cards": [rec("f", sea["f"])]},
        {"at": 2300, "progress": 0, "left": True}, {"at": 2400, "probe": "after"}, {"at": 2500, "quit": True}])
    after = probes.get("after", {})
    checks.expect("presence-stale-held", "two cards while one leaves: only the last is applied",
                  after.get("phase") == "ready" and after.get("insertion") == ids["f"], after)

    out, probes, _ = run("presence-mode", [
        {"at": 0, "cards": [rec("g", sea["g"])]}, {"at": 800, "begin": "fade", "progress": 1, "arrived": True},
        {"at": 900, "mode": "colours"}, {"at": 1000, "probe": "leaving"}, {"at": 1700, "progress": 0, "left": True},
        {"at": 1800, "probe": "after"}, {"at": 1900, "quit": True}])
    l, after = probes.get("leaving", {}), probes.get("after", {})
    checks.expect("presence-mode", "colours only chosen: it leaves (mode), then comes back without its world",
                  l.get("exit") == "mode" and after.get("phase") == "ready" and not after.get("world")
                  and after.get("plated") and after.get("entry") == "return", (l, after))

    lent = {"slot": "c1", "insertion": ids["j"], "state": "valid", "active": True,
            "info": {"title": "Lent", "id": "mun.lent", "accent": "#2E7EC5", "background": "#BFD9F2"}, "shape": {"state": "none"}}
    out, probes, _ = run("presence-lent", [{"at": 0, "cards": [lent]}, {"at": 800, "probe": "applied"}, {"at": 900, "quit": True}])
    a = probes.get("applied", {})
    checks.expect("presence-lent", "lent colours tint MUN at once: no transition, no plates",
                  a.get("source") == "lent" and a.get("phase") == "none" and not a.get("plated"), a)


def world_cases(shell: Shell, checks: Checks, exports: Path, packages: Path):
    def world_of(shape_record):
        document = json.loads((Path(shape_record["path"]) / shape.MANIFEST).read_text(encoding="utf-8"))
        return dict(document["world"], bytes=0), shape_record["path"]

    sea_world, sea_root = world_of(export(exports, insertion(0x400), SAMPLES / "sea"))
    broken = sample_copy(packages, "world-broken", "sea", replace={first_layer("sea"): short_png()})
    broken_world, broken_root = world_of(export(exports, insertion(0x401), broken))
    out_dir = shell.work / "world-grabs"
    out_dir.mkdir(exist_ok=True)

    def run(name, steps, under="#000000"):
        output = shell.run(name, "world.qml", {"steps": steps, "out": str(out_dir), "under": under}, exports=exports)
        return output, {w["label"]: w for w in lines(output, "WORLD")}, {g["name"]: g for g in lines(output, "GRAB")}

    base = [{"at": 0, "root": sea_root, "world": sea_world, "transition": "tide"}]
    out, probes, grabs = run("world-sea", base + [
        {"at": 1200, "probe": "ready"}, {"at": 1300, "progress": 0.45}, {"at": 1800, "grab": "world-tide"},
        {"at": 2000, "progress": 1}, {"at": 2600, "probe": "whole"}, {"at": 2700, "grab": "world-whole"},
        {"at": 3000, "probe": "moving-1"}, {"at": 4500, "probe": "moving-2"}, {"at": 4600, "still": True},
        {"at": 5200, "probe": "still-1"}, {"at": 6700, "probe": "still-2"}, {"at": 6800, "still": False, "running": False},
        {"at": 9600, "probe": "rest-1"}, {"at": 11100, "probe": "rest-2"}, {"at": 11200, "quit": True}])
    r = probes.get("ready", {})
    checks.expect("world-sea", "prepared at full detail within the 1080p budget",
                  r.get("ready") and r.get("drawn") and r.get("detail") == 0 and 0 < r.get("estimate", 0) <= r.get("budget", 0), r)
    checks.expect("world-sea", "at progress 1 it covers the canvas", probes.get("whole", {}).get("covering"), probes.get("whole"))
    moving = probes.get("moving-2", {}).get("frames", 0) - probes.get("moving-1", {}).get("frames", 0)
    checks.expect("world-sea", "a world in motion shows new frames (20 per second: 1.5 s)", moving >= 12, moving)
    still = probes.get("still-2", {}).get("frames", 0) - probes.get("still-1", {}).get("frames", 0)
    checks.expect("world-sea", "a still world shows nothing new (Reduce motion)", still <= 1, still)
    rest = probes.get("rest-2", {}).get("frames", 0) - probes.get("rest-1", {}).get("frames", 0)
    checks.expect("world-sea", "at rest the world eases to a stop and shows nothing new", rest <= 1, rest)
    tide = grabs.get("world-tide", {})
    if tide.get("path"):
        image = read_ppm(Path(tide["path"]))
        width = image[0]
        def at(x, y):
            i = (y * width + x) * 3
            return image[2][i:i + 3]
        inside, outside = at(470, 570 - 100), at(1900, 20)
        checks.expect("world-sea", "mid-tide: the world inside the front, the canvas beneath outside it",
                      sum(inside) > 60 and tuple(outside) == (0, 0, 0), (tuple(inside), tuple(outside)))
    else:
        checks.expect("world-sea", "mid-tide frame grabbed", False, tide)

    # Whole and still from the start (Reduce motion, no transition): if the
    # world is prepared while an empty frame waits to be shown, its own frame
    # still comes once that one is taken (three times: a race when it fails).
    for attempt in range(3):
        case = f"world-still-at-once-{attempt}"
        out, probes, grabs = run(case, [{"at": 0, "root": sea_root, "world": sea_world, "progress": 1, "still": True},
                                        {"at": 1500, "grab": case}, {"at": 1600, "probe": "end"}, {"at": 1700, "quit": True}])
        grab = grabs.get(case, {})
        shown = None
        if grab.get("path"):
            image = read_ppm(Path(grab["path"]))
            i = (540 * image[0] + 1500) * 3
            shown = tuple(image[2][i:i + 3])
        checks.expect(case, "a still world set whole at once is shown, not the empty frame before it",
                      shown is not None and sum(shown) > 60, (shown, probes.get("end")))

    # Every option no sample uses, drawn: a swaying layer, an orbiting
    # emitter, a light that pulses and is added, a still one screened. Two
    # grabs 2.5 s of world time apart: the bars moved, within their sway; the
    # sprites keep to their orbit around the card object and moved; the light
    # brightens the backdrop and breathes.
    W, H = 1920, 1080
    bars = [bytes(4 * W)] * (H - 160) + [b"".join(bytes([240, 240, 240, 255]) if (x // 60) % 2 == 0 else bytes(4)
                                                  for x in range(W))] * 160
    dot = [b"".join(bytes([230, 40, 40, 255]) if (x - 16) ** 2 + (y - 16) ** 2 < 196 else bytes(4) for x in range(32))
           for y in range(32)]
    options = write_package(packages / "world-options", {"format": "mun-shape/1", "world": {
        "backdrop": {"gradient": ["#101418", "#101418"]}, "rate": 20,
        "layers": [{"image": "bars.png", "motion": "sway", "speed": 90}],
        "emitters": [{"sprite": "dot.png", "count": 12, "path": "orbit", "speed": 160, "band": [0.3, 0.7]}],
        "light": [{"texture": "glow.png", "motion": "pulse", "blend": "add", "opacity": 0.25},
                  {"texture": "haze.png", "motion": "still", "blend": "screen", "opacity": 0.4}]}}, {
        "bars.png": png(W, H, bars, colour=6), "dot.png": png(32, 32, dot, colour=6),
        "glow.png": png(64, 36, [bytes([255, 255, 255, 255]) * 64] * 36, colour=6),
        "haze.png": png(64, 36, [bytes([40, 80, 120, 255]) * 64] * 36, colour=6)})
    options_world, options_root = world_of(export(exports, insertion(0x402), options))
    out, probes, grabs = run("world-options", [{"at": 0, "root": options_root, "world": options_world, "progress": 1},
                                               {"at": 1500, "grab": "options-a"}, {"at": 1600, "probe": "a"},
                                               {"at": 4000, "grab": "options-b"}, {"at": 4100, "probe": "b"},
                                               {"at": 4300, "quit": True}])
    a, b = probes.get("a", {}), probes.get("b", {})
    checks.expect("world-options", "every option accepted and drawn at full detail, in motion",
                  a.get("drawn") and a.get("detail") == 0 and b.get("frames", 0) - a.get("frames", 0) >= 30, (a, b))
    shots = [read_ppm(Path(grabs[name]["path"])) for name in ("options-a", "options-b") if grabs.get(name, {}).get("path")]
    if len(shots) == 2:
        def at(image, x, y):
            i = (y * image[0] + x) * 3
            return image[2][i:i + 3]

        def sprites(image):   # red over everything else, however the light brightens it
            return {(x, y) for y in range(0, H, 4) for x in range(0, W, 4)
                    if (lambda p: p[0] - p[1] > 90 and p[0] - p[2] > 90)(at(image, x, y))}

        def bar_edge(image):
            row = [at(image, x, H - 80)[0] > 150 for x in range(W)]
            return next((x for x in range(1, W) if row[x] and not row[x - 1]), None)

        found = [sprites(image) for image in shots]
        # The orbit: 280 to 280 + 0.6 of the band (432) canvas px around (470, 570), flattened to 0.32.
        outside = [p for group in found for p in group if abs(p[0] - 470) > 560 or abs(p[1] - 570) > 560 * 0.32 + 20]
        checks.expect("world-options", "orbit: the sprites circle the card object, and move",
                      all(found) and not outside and found[0] != found[1],
                      ([len(group) for group in found], outside[:3]))
        edges = [bar_edge(image) for image in shots]
        checks.expect("world-options", "sway: the layer moved, within its 90 px either way",
                      None not in edges and 0 < abs(edges[0] - edges[1]) <= 180, edges)
        clear = [tuple(at(image, 1700, 120)) for image in shots]
        checks.expect("world-options", "lights: added and screened over the backdrop, the added one breathing",
                      all(p[2] > 24 + 40 for p in clear) and clear[0] != clear[1], clear)
    else:
        checks.expect("world-options", "both frames grabbed", False, grabs)

    out, probes, _ = run("world-steps", base + [
        {"at": 1200, "probe": "d0"}, {"at": 1300, "stepDown": "test"}, {"at": 1600, "probe": "d1"},
        {"at": 1700, "stepDown": "test"}, {"at": 2000, "probe": "d2"}, {"at": 2100, "stepDown": "test"},
        {"at": 2600, "probe": "d3"}, {"at": 2700, "stepDown": "test"}, {"at": 3000, "probe": "d4"}, {"at": 3100, "quit": True}])
    details = [probes.get(f"d{i}", {}).get("detail") for i in range(5)]
    checks.expect("world-steps", "each step down lowers the detail by one, to none", details == [0, 1, 2, 3, 4], details)
    checks.expect("world-steps", "at none the world is not drawn", probes.get("d4", {}).get("drawn") is False, probes.get("d4"))

    out, probes, _ = run("world-broken", [{"at": 0, "root": broken_root, "world": broken_world},
                                           {"at": 1500, "probe": "after"}, {"at": 1600, "quit": True}])
    b = probes.get("after", {})
    checks.expect("world-broken", "a layer that does not decode drops the whole world: failed, not drawn",
                  b.get("ready") and b.get("failed") and not b.get("drawn"), b)
    checks.expect("world-broken", "the journal says why", "layer 1 could not be decoded" in out)


def memory_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, report: dict):
    """What a world takes at its peak, the shell's own memory measured, at
    1080p and 1440p: worlds whose images keep every limit but scale large (a
    layer 512x16 and one 2048x1, a backdrop 512x16), decode to 16 bits or to a
    palette (converted on a copy), and a sample stepped down to still. Each
    world's rise over the shell without one stays within the engine's
    estimate, and the estimate within the display's budget; stepping down
    makes nothing more (the still world composed onto the backdrop in place).
    The extreme worlds are also grabbed: the canvas covered, no empty edge,
    the backdrop's middle where it should be."""
    counter = iter(range(0x700, 0x800))
    out_dir = shell.work / "memory-grabs"
    out_dir.mkdir(exist_ok=True)
    mib = 1024 * 1024

    def world_of(folder):
        shape_record = export(exports, insertion(next(counter)), folder)
        document = json.loads((Path(shape_record["path"]) / shape.MANIFEST).read_text(encoding="utf-8"))
        assert "world" in document, (folder, shape_record)
        return dict(document["world"], bytes=0), shape_record["path"]

    def package(name, world, files):
        return world_of(write_package(packages / name, {"format": "mun-shape/1", "world": world}, files))

    def solid(width, height, rgb):
        return png(width, height, [bytes(rgb) * width] * height)

    gradient = {"gradient": ["#001020", "#203040"]}
    # Red rising and green falling across its 512 columns: where the canvas
    # is cut from it shows.
    ramp = png(512, 16, [bytes(v for c in range(512) for v in (c // 2, 255 - c // 2, 128))] * 16)
    worlds = {
        "wide-layer": (3, package("memory-wide-layer", {"backdrop": gradient, "layers": [
            {"image": "wide.png", "motion": "drift", "speed": 30}]}, {"wide.png": solid(512, 16, (50, 100, 130))})),
        "thin-layer": (3, package("memory-thin-layer", {"backdrop": gradient, "layers": [{"image": "thin.png"}]},
                                  {"thin.png": solid(2048, 1, (50, 100, 130))})),
        "wide-backdrop": (0, package("memory-wide-backdrop", {"backdrop": {"image": "ramp.png"}}, {"ramp.png": ramp})),
        "deep-lights": (0, package("memory-deep-lights", {"backdrop": gradient, "light": [
            {"texture": "deep.png", "motion": "sway"}, {"texture": "stripes.png", "motion": "ripple"}]}, {
            "deep.png": png(1024, 1024, [bytes([255, 255, 255, 255, 255, 255, 64, 0]) * 1024] * 1024, depth=16, colour=6),
            "stripes.png": png(2048, 1, [bytes(i % 2 for i in range(2048))], colour=3, palette=[(0, 0, 0), (255, 255, 255)])})),
        "palette-layers": (0, package("memory-palette-layers", {"backdrop": gradient, "layers": [
            {"image": "indexed.png", "motion": "drift", "speed": 20}, {"image": "grey16.png", "motion": "parallax"}]}, {
            "indexed.png": png(2048, 512, [bytes([r % 2]) * 2048 for r in range(512)], colour=3,
                               palette=[(20, 40, 60), (200, 210, 220)]),
            "grey16.png": png(1024, 256, [bytes([0x80, 0x00]) * 1024] * 256, depth=16, colour=0)})),
        "sea": (0, world_of(SAMPLES / "sea")),
    }

    def run(name, steps, scale, end=3000):
        output = shell.run(name, "world.qml", {"steps": steps + [{"at": end, "quit": True}], "out": str(out_dir)},
                           exports=exports, scale=scale)
        return output, {w["label"]: w for w in lines(output, "WORLD")}, {g["name"]: g for g in lines(output, "GRAB")}

    # The shell's own variation between two runs (its allocator, Qt's caches).
    slack = 6 * mib
    for size, scale in (("1080p", 1), ("1440p", 4 / 3)):
        run(f"memory-none-{size}", [{"at": 2500, "probe": "end"}], scale)
        none = shell.peak_kib[f"memory-none-{size}"] * 1024
        for key, (detail, (world, root)) in worlds.items():
            case = f"memory-{key}-{size}"
            # Brought in by a transition, so the transition's content is made too.
            out, probes, _ = run(case, [{"at": 0, "root": root, "world": world, "transition": "fade"},
                                        {"at": 400, "progress": 0.5}, {"at": 900, "progress": 1},
                                        {"at": 2500, "probe": "end"}], scale)
            end = probes.get("end", {})
            rise = shell.peak_kib[case] * 1024 - none
            report.setdefault("memory", {})[case] = {
                "detail": end.get("detail"), "estimate_mib": round(end.get("estimate", 0) / mib, 1),
                "budget_mib": round(end.get("budget", 0) / mib, 1), "rise_mib": round(rise / mib, 1)}
            checks.expect(case, f"drawn at detail {detail}", end.get("drawn") and end.get("detail") == detail, end)
            checks.expect(case, "its estimate within the display's budget",
                          0 < end.get("estimate", 0) <= end.get("budget", 0), end)
            checks.expect(case, "the memory it adds, at its peak, within its estimate",
                          rise <= end.get("estimate", 0) + slack,
                          (round(rise / mib, 1), round(end.get("estimate", 0) / mib, 1)))
        world, root = worlds["sea"][1]
        case = f"memory-sea-steps-{size}"
        out, probes, _ = run(case, [{"at": 0, "root": root, "world": world, "transition": "fade"},
                                    {"at": 400, "progress": 0.5}, {"at": 900, "progress": 1},
                                    {"at": 1300, "stepDown": "test"}, {"at": 1600, "stepDown": "test"},
                                    {"at": 1900, "stepDown": "test"}, {"at": 2500, "probe": "end"}], scale)
        stepped = shell.peak_kib[case] * 1024 - shell.peak_kib[f"memory-sea-{size}"] * 1024
        report["memory"][case] = {"detail": probes.get("end", {}).get("detail"), "over_unstepped_mib": round(stepped / mib, 1)}
        checks.expect(case, "stepped down to still", probes.get("end", {}).get("detail") == 3, probes.get("end"))
        checks.expect(case, "the still world composed onto the backdrop in place (no copy of it)",
                      "still world composed in place" in out, "")
        checks.expect(case, "stepping down raises no peak", stepped <= 3 * mib, round(stepped / mib, 1))

    # How the extreme worlds look (at 1080p): the canvas covered to its
    # edges, the backdrop cut from its middle.
    def pixel(image, x, y):
        i = (y * image[0] + x) * 3
        return tuple(image[2][i:i + 3])

    for key in ("wide-layer", "thin-layer", "wide-backdrop"):
        world, root = worlds[key][1]
        out, _, grabs = run(f"look-{key}", [{"at": 0, "root": root, "world": world, "transition": "fade", "progress": 0.999,
                                             "still": True}, {"at": 1300, "progress": 1}, {"at": 1800, "grab": key}],
                            1, end=2300)
        grab = grabs.get(key, {})
        if not grab.get("path"):
            checks.expect(f"look-{key}", "grabbed", False, grab)
            continue
        image = read_ppm(Path(grab["path"]))
        points = [(x, y) for x in (0, 1, 480, 960, 1440, 1918, 1919) for y in (0, 1, 540, 1078, 1079)]
        if key != "wide-backdrop":
            off = [(p, pixel(image, *p)) for p in points if max(abs(a - b) for a, b in zip(pixel(image, *p), (50, 100, 130))) > 2]
            checks.expect(f"look-{key}", "the layer covers the canvas to its edges", not off, off[:4])
        else:
            # Cover: 512x16 to 34560x1080, its middle 1920 columns shown:
            # source columns 241.8 to 270.2, red (column / 2) 121 to 135.
            reds = {x: pixel(image, x, 540)[0] for x in (0, 960, 1919)}
            edges = [pixel(image, x, y) for x in (0, 1919) for y in (0, 540, 1079)]
            checks.expect("look-wide-backdrop", "its middle is shown: red 119-123 at the left, 126-129 in the "
                          "middle, 133-137 at the right", 119 <= reds[0] <= 123 and 126 <= reds[960] <= 129
                          and 133 <= reds[1919] <= 137, reds)
            checks.expect("look-wide-backdrop", "no empty edge", all(p[2] >= 120 for p in edges), edges)


def home_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, report: dict, design: str = "previous",
               api: str = "software", display: str = None):
    """The shell's own Main.qml, against stand-ins for the card service and
    the launcher: the moments of docs/shape.md, "How the console uses a
    package". In Home's `design`, on `api`: "software", Qt Quick's software
    renderer (offscreen, or a board's display); "opengl", OpenGL on the X
    `display` (Xvfb) or a board's GPU.

    A player's steps and a card's events follow the shell's states, not two
    clocks: a step waits for the state it needs (the scene's `when`), and a
    stand-in service sends what follows a moment the scene has shown (its
    {"on": ...}), each within a limit whose passing fails the case. How long
    things take is Checks.duration."""
    slow = sample_copy(packages, "home-slow", "sea", change=lambda d: d["transition"].update(seconds=4.0))
    broken = sample_copy(packages, "home-broken", "sea", replace={first_layer("sea"): short_png()})
    counter = iter(range(0x500, 0x600))

    def card(package):
        number = insertion(next(counter))
        return number, record(number, export(exports, number, package))

    def serve(name, scripts, launcher=None, answer=None):
        return Service(shell.work / f"{name}.sock", scripts), \
               Service(shell.work / f"{name}-launch.sock", launcher or [[(0, {"type": "snapshot", "state": "idle"})]], answer)

    def run(name, cardd, launchd, steps, grabs=(), settings="", end=9000, trace=False, until=None, runtime=None,
            kill_when=None):
        # `until`: a state to wait for after the steps (at most 20 s); the
        # scene then ends 1.5 s later, and not before `end`. `runtime`:
        # another run's runtime directory, as a shell started again finds it
        # (its markers kept); `kill_when`: see Shell.run.
        out_dir = shell.work / name / "grabs"
        out_dir.mkdir(parents=True, exist_ok=True)
        runtime = runtime or shell.work / name / "runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        (runtime / "started").write_text("")   # after a game: no start-up

        # The stand-ins follow the scene: each PHASE line's label (a probe)
        # and its phase are events they may wait for.
        def watch(line):
            found = re.search(r"PHASE (\{.*\})\s*$", line)
            try:
                shown = json.loads(found.group(1)) if found else None
            except ValueError:
                shown = None
            if shown:
                for service in (cardd, launchd):
                    service.signal(shown["label"])
                    service.signal("phase:" + shown["phase"])
        tail = [{"when": until, "within": 20000, "probe": "until"}] if until else []
        try:
            output = shell.run(name, "home.qml", {"steps": list(steps) + tail + [{"at": end, "wait": 1500, "quit": True}],
                                                  "grabs": list(grabs), "out": str(out_dir), "trace": trace},
                               runtime=runtime, socket_path=cardd.path, launcher=launchd.path, exports=exports,
                               settings=settings, timeout=end / 1000 + 120, display=display, design=design,
                               software=api == "software", watch=watch, kill_when=kill_when)
        finally:
            cardd.close()
            launchd.close()
        phases = [p for p in lines(output, "PHASE")]
        # The scene measures what it finds: it must have found the design
        # asked for, its ten menu entries (four on Home, six in Settings) and
        # its card object, or a green result would say nothing.
        start = next((p for p in phases if p["label"] == "start"), None)
        drawn = re.search(r"mun-shell: renderer: (\w+)", output)
        checks.expect(name, f"the {design} design came up on {api}, its 10 menu entries and its card object found",
                      start is not None and start.get("design") == design and start.get("entriesFound") == 10
                      and start.get("cardsFound") == 1 and drawn is not None and drawn.group(1) == api,
                      (start and {k: start.get(k) for k in ("design", "entriesFound", "cardsFound")}, drawn and drawn.group(1)))
        late = lines(output, "TIMEOUT")
        checks.expect(name, "every state a step waited for came within its limit", not late,
                      [{k: t.get(k) for k in ("t", "step", "when", "within")} for t in late[:3]])
        missed = cardd.errors + launchd.errors
        checks.expect(name, "every moment a stand-in waited for came within its limit", not missed, missed[:3])
        return output, phases, {g["name"]: g for g in lines(output, "GRAB")}

    def first(phases, since=None, **match):
        # The first line matching, after `since` (a line) if given.
        for p in phases:
            if since is not None and p["t"] <= since["t"]:
                continue
            if all(p.get(k) == v for k, v in match.items()):
                return p
        return None

    def surfaces_hold(case, grab, name, dressed_only=False, sample=False):
        # dressed_only: MUN's entries, dimmed while the options have the
        # focus, are MUN's own look, not measured here. sample: a grab at a
        # point of a moving transition, which a slow machine may pass between
        # two frames (no frame at that point to grab): its absence is then a
        # duration, judged or recorded; a state that lasts (the first dressed
        # frame, present, Settings) must always be grabbed.
        if not grab or not grab.get("path"):
            if sample:
                checks.duration(case, f"{name}: a frame at that point of the transition grabbed", False,
                                "the transition passed that point between two frames")
            else:
                checks.expect(case, f"{name}: grabbed", False, grab)
            return
        image = read_ppm(Path(grab["path"]))
        where = grab["regions"]
        low = {}
        for e in where["entries"]:
            if dressed_only and not e["shaped"]:
                continue
            if e["shaped"] and e["effective"] < 0.999:
                low[e["label"]] = ("opacity", e["effective"])
                continue
            measured = text_contrast(image, e["box"])
            if measured["ratio"] < TEXT_RATIO:
                low[e["label"]] = (measured["ratio"], round(e["reached"], 2))
        if where.get("panel"):
            measured = text_contrast(image, where["panel"]["text"])
            if measured["ratio"] < TEXT_RATIO:
                low["panel"] = (measured["ratio"], round(where["panel"]["reached"], 2))
        # A point of the world clear of every surface: what the grab shows of it.
        i = (960 * image[0] + 1150) * 3
        report.setdefault("home", {})[f"{case} {name}"] = {"progress": round(grab["progress"], 3), "low": low,
                                                          "world_at_1150_960": tuple(image[2][i:i + 3])}
        checks.expect(case, f"{name} (progress {grab['progress']:.2f}): every entry's and the panel's text keeps 4.5:1",
                      not low, low)

    def whole_when_dressed(case, out):
        # On every frame an entry is dressed, its opacity on screen is 1.
        samples = lines(out, "TRACE")
        low = [(t["t"], e["label"], e["effective"]) for t in samples for e in t["entries"]
               if e["shaped"] and e["effective"] < 0.999]
        checks.expect(case, "a dressed entry is whole (opacity 1 on screen) on every frame it is dressed",
                      bool(samples) and any(e["shaped"] for t in samples for e in t["entries"]) and not low, low[:6])

    def eases_to_mun(case, out):
        # An entry turning back into MUN's eases into MUN's look: no step.
        steps, last = [], {}
        for t in lines(out, "TRACE"):
            for e in t["entries"]:
                before = last.get(e["label"])
                if before and not e["shaped"] and abs(e["opacity"] - before["opacity"]) > 0.35:
                    steps.append((t["t"], e["label"], before["opacity"], e["opacity"]))
                last[e["label"]] = e
        checks.expect(case, "MUN's entries change their opacity smoothly, never in one step", not steps, steps[:6])

    def first_dressed(out):
        for t in lines(out, "TRACE"):
            if any(e["shaped"] for e in t["entries"]):
                return t
        return None

    # An arrival: the tide, the cue, the surfaces as the front reaches them.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-arrival", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-arrival", cardd, launchd, [], grabs=[
        {"name": "tide-20", "phase": "entering", "at": 0.2}, {"name": "tide-45", "phase": "entering", "at": 0.45},
        {"name": "tide-70", "phase": "entering", "at": 0.7}, {"name": "present", "phase": "present"}], end=4000,
        until={"phase": "present"})
    entering, present = first(phases, phase="entering"), first(phases, phase="present")
    checks.expect("home-arrival", "an arrival comes in with the package's tide",
                  entering is not None and entering["entry"] == "arrival" and entering["kind"] == "tide"
                  and present is not None, (entering, present))
    took = present["t"] - entering["t"] if entering and present else None
    checks.duration("home-arrival", "the tide takes the package's 3.2 s (2.6 to 4.4 s)", took is not None and 2600 <= took <= 4400,
                    took)
    checks.expect("home-arrival", "adopted as an arrival, its cue due", [a["live"] for a in lines(out, "ADOPTED")] == [True],
                  lines(out, "ADOPTED"))
    cues = [s for s in lines(out, "SOUND") if s["name"] == "insert"]
    checks.expect("home-arrival", "the cue plays once, as the tide begins",
                  len(cues) == 1 and entering is not None and cues[0]["phase"] == "entering", (cues, entering and entering["t"]))
    checks.duration("home-arrival", "the cue within 0.3 s of the tide's start",
                    len(cues) == 1 and entering is not None and abs(cues[0]["t"] - entering["t"]) <= 300,
                    (cues, entering and entering["t"]))
    for name in ("tide-20", "tide-45", "tide-70", "present"):
        surfaces_hold("home-arrival", grabs.get(name), name, sample=name != "present")

    # A dialog open: the arrival waits for Home.
    x, rx = card(SAMPLES / "sea")
    result = {"type": "snapshot", "state": "idle", "last_result": {"session": "s1", "reason": "exited", "title": "Test"}}
    cardd, launchd = serve("home-dialog", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]], [[(0, result)]])
    out, phases, _ = run("home-dialog", cardd, launchd, [
        {"when": {"phase": "ready"}, "probe": "waiting"}, {"wait": 1500, "probe": "still-waiting"},
        {"call": "dismissResult", "probe": "dismissed"}], end=1000, until={"phase": "present"})
    still, dismissed, entering = first(phases, label="still-waiting"), first(phases, label="dismissed"), first(phases, phase="entering")
    checks.expect("home-dialog", "with the session's dialog open the identity waits, ready; dismissed, it comes in",
                  still is not None and still["phase"] == "ready" and still["modal"] and dismissed is not None
                  and entering is not None and entering["t"] >= dismissed["t"], (still, dismissed, entering))

    # Back from a game under the same dialog: a short fade, at once.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-return", [[(0, snapshot(rx))]], [[(0, result)]])
    out, phases, _ = run("home-return", cardd, launchd, [], end=1000, until={"phase": "present"})
    entering, present = first(phases, phase="entering"), first(phases, phase="present")
    checks.expect("home-return", "found at start (after a game): a fade, even under a dialog",
                  entering is not None and entering["entry"] == "return" and entering["kind"] == "fade" and entering["modal"]
                  and present is not None, (entering, present))
    checks.duration("home-return", "the fade is short (1.2 s at most)",
                    entering is not None and present is not None and present["t"] - entering["t"] <= 1200, (entering, present))
    checks.expect("home-return", "no cue", [a["live"] for a in lines(out, "ADOPTED")] == [False]
                  and not [s for s in lines(out, "SOUND") if s["name"] == "insert"], lines(out, "ADOPTED"))
    sounded = [s["name"] for s in lines(out, "SYSSOUND")]
    checks.expect("home-return", "the game's result, shown on return, sounds once", sounded.count("game_return") == 1, sounded)

    # Removed while the tide comes in: back to MUN at once, the way it came.
    x, rx = card(slow)
    cardd, launchd = serve("home-removal", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                             ({"on": "phase:entering", "after": 1.0},
                                              {"type": "removed", "slot": "c1", "insertion": x})]])
    out, phases, grabs = run("home-removal", cardd, launchd, [{"when": {"phase": "leaving"}, "probe": "left"}],
                             grabs=[{"name": "leaving", "phase": "leaving", "at": 0.99}], end=1000, until={"phase": "none"})
    leaving = first(phases, phase="leaving")
    gone = first(phases, since=leaving, phase="none") if leaving else None
    checks.expect("home-removal", "removed mid-tide: it leaves (removal) by the same tide, from where it was",
                  leaving is not None and leaving["exit"] == "removal" and leaving["kind"] == "tide" and leaving["progress"] < 1,
                  leaving)
    checks.expect("home-removal", "then MUN alone", gone is not None and gone["source"] == "none", gone)
    # docs/shape.md: 0.5 s back to MUN; 0.3 s more for the frames between.
    checks.duration("home-removal", "MUN in the 0.5 s the contract gives (0.8 s measured at most)",
                    gone is not None and leaving is not None and gone["t"] - leaving["t"] <= 800, (leaving, gone))

    # Eject safely: the identity stays until the release is confirmed, then leaves.
    def eject(ok):
        x, rx = card(SAMPLES / "sea")
        cardd = Service(shell.work / f"home-eject-{ok}.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])

        def answer(service, message):
            if message.get("type") == "release":
                # Confirmed only once the scene has looked (`asked`), as a
                # card service takes its time; never if it does not look.
                if not service.expect("asked", 30):
                    return
                time.sleep(0.2)
                if ok:
                    cardd.push({"type": "card", "card": dict(rx, state="released", info=None)})
                    service.push({"type": "released", "ok": True, "slot": "c1", "serial": rx["serial"]})
                else:
                    service.push({"type": "released", "ok": False, "slot": "c1", "serial": rx["serial"],
                                  "error": {"code": "card_busy", "message": "La tarjeta sigue en uso; no la retires"}})
        launchd = Service(shell.work / f"home-eject-{ok}-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]], answer)
        steps = [{"when": {"phase": "present"}, "probe": "before"}, {"wait": 300, "call": "eject"}, {"wait": 100, "probe": "asked"}]
        if ok:
            out, phases, _ = run("home-eject-ok", cardd, launchd, steps + [{"when": {"phase": "leaving"}, "probe": "leaving"}],
                                 end=1000, until={"phase": "none"})
        else:
            out, phases, _ = run("home-eject-busy", cardd, launchd, steps + [{"wait": 3000, "probe": "later"}], end=1000)
        return out, phases, launchd

    out, phases, launchd = eject(True)
    asked, leaving = first(phases, label="asked"), first(phases, phase="leaving")
    gone = first(phases, since=leaving, phase="none") if leaving else None
    checks.expect("home-eject-ok", "asked to eject, the identity stays until the release is confirmed",
                  asked is not None and asked["phase"] == "present" and any(m.get("type") == "release" for m in launchd.received),
                  (asked, launchd.received))
    checks.expect("home-eject-ok", "confirmed: it leaves (release) with the package's out tide, to MUN",
                  leaving is not None and leaving["exit"] == "release" and leaving["kind"] == "tide" and gone is not None,
                  (leaving, gone))
    checks.duration("home-eject-ok", "the out tide takes the package's 2.4 s (1.8 to 3.4 s)",
                    leaving is not None and gone is not None and 1800 <= gone["t"] - leaving["t"] <= 3400, (leaving, gone))
    sounded = [s["name"] for s in lines(out, "SYSSOUND")]
    checks.expect("home-eject-ok", "the release done sounds, and so does Shape leaving",
                  sounded.count("eject") == 1 and sounded.count("shape_exit") == 1 and "warning" not in sounded, sounded)
    out, phases, launchd = eject(False)
    later = first(phases, label="later")
    checks.expect("home-eject-busy", "a release that fails keeps the identity",
                  later is not None and later["phase"] == "present" and first(phases, phase="leaving") is None, later)
    sounded = [s["name"] for s in lines(out, "SYSSOUND")]
    checks.expect("home-eject-busy", "refused: a warning, not the release's sound",
                  sounded.count("warning") == 1 and "eject" not in sounded and "shape_exit" not in sounded, sounded)

    # Another card: the first leaves, the second comes in with its own transition.
    x, rx = card(SAMPLES / "sea")
    y, ry = card(SAMPLES / "paper")
    cardd, launchd = serve("home-change", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                            ({"on": "phase:present", "after": 1.0}, {"type": "removed", "slot": "c1", "insertion": x}),
                                            ({"wait": 0.1}, {"type": "card", "card": ry})]])
    out, phases, _ = run("home-change", cardd, launchd, [], end=1000, until={"phase": "present", "insertion": y})
    left = [p for p in phases if p["phase"] in ("leaving", "none", "ready", "entering", "present")]
    second = [p for p in left if p["insertion"] == y and p["phase"] == "entering"]
    checks.expect("home-change", "A leaves (removal); B then comes in (arrival, its sweep)",
                  first(phases, phase="leaving", exit="removal") is not None and second and second[0]["entry"] == "arrival"
                  and second[0]["kind"] == "sweep", [(p["phase"], p["insertion"][-3:], p["exit"], p["kind"]) for p in left])

    # The player's choices.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-reduce-motion", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-reduce-motion", cardd, launchd, [
        {"when": {"phase": "present"}, "wait": 2500, "probe": "still-1"}, {"wait": 1500, "probe": "still-2"}],
        settings="[display]\nreduce-motion=on\n", end=1000)
    entering = first(phases, phase="entering")
    still = [first(phases, label=f"still-{i}") for i in (1, 2)]
    checks.expect("home-reduce-motion", "Reduce motion: a fade instead of the tide, and a still world",
                  entering is not None and entering["kind"] == "fade" and all(still)
                  and still[0]["still"] and still[1]["frames"] - still[0]["frames"] <= 1, (entering, still))

    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-colours", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-colours", cardd, launchd, [{"when": {"phase": "present"}, "wait": 500, "probe": "after"}],
                         settings="[shape]\nmode=colours\n", end=1000)
    after = first(phases, label="after")
    checks.expect("home-colours", "Colours only: the identity comes in without a world",
                  after is not None and after["phase"] == "present" and not after["world"] and not after["worldDrawn"], after)

    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-off", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-off", cardd, launchd, [{"at": 3000, "probe": "after"}], settings="[shape]\nmode=off\n", end=3500)
    checks.expect("home-off", "Off: MUN alone", all(p["phase"] == "none" and p["source"] == "none" for p in phases), phases)

    x, rx = card(broken)
    cardd, launchd = serve("home-broken", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-broken", cardd, launchd, [{"when": {"phase": "present"}, "wait": 500, "probe": "after"}], end=1000)
    after = first(phases, label="after")
    checks.expect("home-broken", "a world that does not decode: the identity comes in, the palette over MUN's world",
                  after is not None and after["phase"] == "present" and after["worldFailed"] and not after["worldDrawn"], after)

    # Settings over the world, and the options focused, measured.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-settings", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-settings", cardd, launchd, [
        {"when": {"phase": "present"}, "call": "enter"}, {"when": {"level": 2}, "wait": 1200, "grab": "options"},
        {"wait": 300, "call": "back"}, {"when": {"level": 0}, "wait": 200, "set": {"mainIndex": 2}},
        {"wait": 200, "call": "enter"}, {"when": {"level": 1}, "wait": 1300, "grab": "settings"},
        {"wait": 200, "probe": "settings"}], end=1000)
    surfaces_hold("home-settings", grabs.get("options"), "options focused")
    surfaces_hold("home-settings", grabs.get("settings"), "Settings over the scrimmed world")
    settings = first(phases, label="settings")
    checks.expect("home-settings", "in Settings the world is under MUN's scrim",
                  settings is not None and settings["level"] == 1 and settings["dim"] > 0.7, settings)
    checks.expect("home-settings", "Settings has its six sections, in order, and no account",
                  settings is not None and settings["sections"] == ["display", "sound", "appearance", "language",
                                                                    "network", "system"], settings)

    # In Spanish, the longer language: every section with its options
    # focused, no text on screen cut short.
    steps = [{"at": 1500, "set": {"mainIndex": 2}}, {"wait": 200, "call": "enter"}]
    for i in range(6):
        steps += [{"when": {"level": 1}, "wait": 600, "set": {"settingsIndex": i}}, {"wait": 100, "call": "enter"},
                  {"when": {"level": 2}, "wait": 1100, "probe": f"section-{i}"}, {"wait": 100, "call": "back"}]
    cardd, launchd = serve("home-settings-es", [[(0, snapshot())]])
    out, phases, _ = run("home-settings-es", cardd, launchd, steps, settings="[interface]\nlanguage=es\n", end=1000)
    sections = [p for p in phases if p["label"].startswith("section-")]
    checks.expect("home-settings-es", "in Spanish, every section's options show whole, nothing cut short",
                  len(sections) == 6 and all(p["level"] == 2 and not p["cut"] for p in sections),
                  [(p["label"], p["level"], p["cut"]) for p in sections])

    # The options focused when the identity comes, goes or changes: a dressed
    # entry whole from its first frame (never easing out of MUN's fade),
    # measured frame by frame and on grabs from the first dressed frame on.
    edge = sample_copy(packages, "home-edge", "paper", change=lambda d: d.update(
        palette=EDGE["palette"], world={"backdrop": {"gradient": ["#FFFFFF", "#FFFFFF"]}},
        transition={"in": "fade", "out": "fade", "seconds": 4}))
    for name, package, grab_points in (
            ("home-late-options", edge, [("fade-02", 0.02), ("fade-10", 0.1), ("fade-50", 0.5)]),
            ("home-late-options-tide", SAMPLES / "sea", [("tide-20", 0.2), ("tide-45", 0.45), ("tide-70", 0.7)])):
        x, rx = card(package)
        preparing = dict(rx, shape={"state": "preparing", "insertion": x})
        cardd, launchd = serve(name, [[(0, snapshot()), (0.3, {"type": "card", "card": preparing}),
                                       ({"on": "focused", "after": 0.3}, {"type": "card", "card": rx})]])
        grabs_asked = [{"name": "first", "first": "shaped"}] + [{"name": g, "phase": "entering", "at": a} for g, a in grab_points] \
            + [{"name": "present", "phase": "present"}]
        out, phases, grabs = run(name, cardd, launchd, [{"when": {"card": "valid"}, "wait": 300, "call": "enter"},
                                                        {"when": {"level": 2}, "probe": "focused"}],
                                 grabs=grabs_asked, end=1000, trace=True, until={"phase": "present"})
        focused, entering = first(phases, label="focused"), first(phases, phase="entering")
        checks.expect(name, "the options focused before the package was ready; then it comes in",
                      focused is not None and focused["level"] == 2 and focused["phase"] == "none"
                      and entering is not None and entering["level"] == 2, (focused, entering))
        whole_when_dressed(name, out)
        for g in ["first"] + [g for g, _ in grab_points] + ["present"]:
            surfaces_hold(name, grabs.get(g), g, dressed_only=True, sample=g not in ("first", "present"))

    # Leaving with the options focused: Eject safely, confirmed.
    x, rx = card(SAMPLES / "sea")
    cardd = Service(shell.work / "home-leave-options.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])

    def release(service, message):
        if message.get("type") == "release":
            time.sleep(0.3)
            cardd.push({"type": "card", "card": dict(rx, state="released", info=None)})
            service.push({"type": "released", "ok": True, "slot": "c1", "serial": rx["serial"]})
    launchd = Service(shell.work / "home-leave-options-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]], release)
    out, phases, grabs = run("home-leave-options", cardd, launchd,
                             [{"when": {"phase": "present"}, "call": "enter"}, {"when": {"level": 2}, "probe": "focused"},
                              {"wait": 200, "call": "eject"}, {"when": {"phase": "leaving"}, "probe": "leaving"}],
                             grabs=[{"name": f"leaving-{int(a * 100)}", "phase": "leaving", "at": a} for a in (0.8, 0.5, 0.2)],
                             end=1000, trace=True, until={"phase": "none"})
    leaving = first(phases, phase="leaving")
    checks.expect("home-leave-options", "Eject safely from the options: it leaves (release)",
                  (first(phases, label="focused") or {}).get("level") == 2 and leaving is not None
                  and leaving["exit"] == "release",
                  (first(phases, label="focused"), leaving))
    whole_when_dressed("home-leave-options", out)
    eases_to_mun("home-leave-options", out)
    for a in (80, 50, 20):
        surfaces_hold("home-leave-options", grabs.get(f"leaving-{a}"), f"leaving-{a}", dressed_only=True, sample=True)

    # A changed choice with the options focused: colours only, off, full again.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-mode-options", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-mode-options", cardd, launchd, [
        {"when": {"phase": "present"}, "call": "enter"},
        {"when": {"level": 2}, "wait": 400, "settings": {"shapeMode": "colours"}, "mark": "colours"},
        {"when": {"phase": "present", "world": False}, "wait": 400, "probe": "colours"},
        {"settings": {"shapeMode": "off"}}, {"when": {"source": "none"}, "wait": 400, "probe": "off"},
        {"settings": {"shapeMode": "full"}, "mark": "full"}, {"when": {"phase": "present", "world": True}, "wait": 400, "probe": "full"}],
        grabs=[{"name": "leaving", "phase": "leaving", "at": 0.5, "after": "colours"},
               {"name": "colours-in", "phase": "entering", "at": 0.3, "after": "colours"},
               {"name": "full-in", "phase": "entering", "at": 0.5, "after": "full"}], end=1000, trace=True)
    probes = {k: first(phases, label=k) for k in ("colours", "off", "full")}
    checks.expect("home-mode-options", "colours only, off and full again, the options focused throughout",
                  all(probes.values()) and [probes[k]["level"] for k in probes] == [2, 2, 2]
                  and probes["colours"]["phase"] == "present" and not probes["colours"]["world"]
                  and probes["off"]["source"] == "none" and probes["full"]["phase"] == "present" and probes["full"]["world"],
                  probes)
    whole_when_dressed("home-mode-options", out)
    eases_to_mun("home-mode-options", out)
    for g in ("leaving", "colours-in", "full-in"):
        surfaces_hold("home-mode-options", grabs.get(g), g, dressed_only=True, sample=True)

    # Another card with the options focused: the first leaves, the second comes.
    x, rx = card(SAMPLES / "sea")
    y, ry = card(SAMPLES / "paper")
    cardd, launchd = serve("home-change-options", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                                    ({"on": "focused", "after": 0.2}, {"type": "removed", "slot": "c1", "insertion": x}),
                                                    ({"wait": 0.1}, {"type": "card", "card": ry})]])
    out, phases, grabs = run("home-change-options", cardd, launchd,
                             [{"when": {"phase": "present"}, "call": "enter"}, {"when": {"level": 2}, "probe": "focused"}],
                             grabs=[{"name": f"b-{int(a * 100)}", "phase": "entering", "at": a, "insertion": y} for a in (0.3, 0.6)]
                             + [{"name": "b-present", "phase": "present", "insertion": y}], end=1000, trace=True,
                             until={"phase": "present", "insertion": y})
    second = [p for p in phases if p["insertion"] == y and p["phase"] == "entering"]
    checks.expect("home-change-options", "from the options: A leaves, B comes in",
                  (first(phases, label="focused") or {}).get("level") == 2 and bool(second),
                  (first(phases, label="focused"), second[:1]))
    whole_when_dressed("home-change-options", out)
    eases_to_mun("home-change-options", out)
    for g in ("b-30", "b-60", "b-present"):
        surfaces_hold("home-change-options", grabs.get(g), g, dressed_only=True, sample=g != "b-present")

    # An arrival waits for Home wholly on screen: back from Settings (its arc
    # fading in), a dialog closed (its layer fading out).
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-from-settings", [[(0, snapshot()), ({"on": "in-settings", "after": 0.5},
                                                                      {"type": "card", "card": rx})]])
    out, phases, _ = run("home-from-settings", cardd, launchd, [
        {"at": 800, "set": {"mainIndex": 2}}, {"wait": 100, "call": "enter"}, {"when": {"level": 1}, "probe": "in-settings"},
        {"wait": 2000, "call": "back", "probe": "back"}], end=1000, trace=True, until={"phase": "present"})
    entering, dressed, back = first(phases, phase="entering"), first_dressed(out), first(phases, label="back")
    checks.expect("home-from-settings", "inserted in Settings: it comes in once Home's arc is whole (0.45 s after)",
                  entering is not None and back is not None and entering["t"] >= back["t"] + 400
                  and entering["entry"] == "arrival", (back, entering))
    whole_when_dressed("home-from-settings", out)
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-dialog-closed", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]], [[(0, result)]])
    # The dialog is closed once its layer is wholly on screen, for a fade to
    # follow: a busy shell may not have shown it yet a fixed time after Home
    # was ready, and a layer never shown has nothing to fade. Animations
    # follow the frames, not the clock (the same 0.5 s fade has taken 0.42
    # to 0.61 s of clock on the OpenGL path), so the order (the arrival only
    # once the layer has faded out, through the values between) is judged
    # always, and the 0.5 s, a duration, where durations are (--timing).
    out, phases, _ = run("home-dialog-closed", cardd, launchd,
                         [{"when": {"phase": "ready", "layerShown": True}, "wait": 1000, "call": "dismissResult",
                           "probe": "dismissed"}],
                         end=1000, trace=True, until={"phase": "present"})
    entering, dressed, dismissed = first(phases, phase="entering"), first_dressed(out), first(phases, label="dismissed")
    layer = [line for line in lines(out, "LAYER") if dismissed is not None and line["t"] >= dismissed["t"]]
    hidden = next((line for line in layer if not line["visible"]), None)
    between = [line for line in layer if 0 < line["opacity"] < 1 and (hidden is None or line["t"] <= hidden["t"])]
    checks.expect("home-dialog-closed", "the dialog closed: its layer fades out, then the card comes in",
                  entering is not None and dismissed is not None and hidden is not None and bool(between)
                  and entering["t"] >= hidden["t"] and dressed is not None and not dressed["modalShown"],
                  (dismissed, hidden, len(between), entering, dressed))
    checks.duration("home-dialog-closed", "the layer's fade takes its 0.5 s: the card 0.4 s or more after the dialog closed",
                    entering is not None and dismissed is not None and entering["t"] >= dismissed["t"] + 400,
                    (dismissed, entering))
    whole_when_dressed("home-dialog-closed", out)

    # Idle: once dimmed, the first key only brings the interface back (and
    # held down, its repeats do nothing); let go, its next press acts.
    cardd, launchd = serve("home-idle-dim", [[(0, snapshot())]])
    down = 0x01000015  # Qt::Key_Down
    out, phases, _ = run("home-idle-dim", cardd, launchd, [
        {"at": 1500, "probe": "awake"}, {"wait": 100, "set": {"dimmed": True}}, {"wait": 100, "probe": "dimmed"},
        {"wait": 100, "call": "key", "args": [down, False]}, {"wait": 100, "probe": "woken"},
        {"wait": 100, "call": "key", "args": [down, True]}, {"wait": 100, "probe": "held"},
        {"wait": 50, "call": "keyReleased", "args": [down]},
        {"wait": 50, "call": "key", "args": [down, False]}, {"wait": 100, "probe": "moved"}], end=3000)
    at = {p["label"]: p for p in phases if p["label"] in ("awake", "dimmed", "woken", "held", "moved")}
    checks.expect("home-idle-dim", "dimmed, the first key only undims: the focus stays",
                  len(at) == 5 and at["dimmed"]["dimmed"] and not at["woken"]["dimmed"]
                  and at["woken"]["mainIndex"] == at["awake"]["mainIndex"], at)
    checks.expect("home-idle-dim", "that key held down does nothing; let go, its next press moves the focus",
                  len(at) == 5 and at["held"]["mainIndex"] == at["awake"]["mainIndex"]
                  and at["moved"]["mainIndex"] != at["awake"]["mainIndex"], at)
    checks.expect("home-idle-dim", "the menu shows the entry in focus as chosen, and the next one once the focus moves",
                  len(at) == 5 and at["awake"]["focused"] != "" and at["moved"]["focused"] != at["awake"]["focused"]
                  and at["awake"]["chosen"] == [at["awake"]["focused"]] and at["moved"]["chosen"] == [at["moved"]["focused"]],
                  {label: (at[label]["focused"], at[label]["chosen"]) for label in at})
    # The key that woke it stays spent until it is let go, whatever is
    # pressed meanwhile: held Enter wakes, Down moves to Settings, a repeat
    # of Enter must not open it; let go, a fresh Enter does.
    cardd, launchd = serve("home-idle-held", [[(0, snapshot())]])
    enter = 0x01000004  # Qt::Key_Return
    out, phases, _ = run("home-idle-held", cardd, launchd, [
        {"at": 1500, "set": {"mainIndex": 1}}, {"wait": 100, "set": {"dimmed": True}},
        {"wait": 100, "call": "key", "args": [enter, False]}, {"wait": 100, "call": "key", "args": [down, False]},
        {"wait": 100, "probe": "moved"}, {"wait": 100, "call": "key", "args": [enter, True]}, {"wait": 600, "probe": "repeat"},
        {"wait": 100, "call": "keyReleased", "args": [enter]}, {"wait": 100, "call": "key", "args": [enter, False]},
        {"wait": 600, "probe": "fresh"}], end=4000)
    at = {p["label"]: p for p in phases if p["label"] in ("moved", "repeat", "fresh")}
    checks.expect("home-idle-held", "another key between: the waking key's repeat still does nothing",
                  len(at) == 3 and at["moved"]["mainIndex"] == 2 and at["repeat"]["level"] == at["moved"]["level"]
                  and at["repeat"]["sections"] == at["moved"]["sections"], at)
    checks.expect("home-idle-held", "let go, a fresh press of it acts (Settings opens)",
                  len(at) == 3 and (at["fresh"]["level"] != at["moved"]["level"]
                                    or at["fresh"]["sections"] != at["moved"]["sections"]), at)

    # The card object (docs/shape.md, "card"), MUN Disk in focus: the card's
    # window on screen in the object, from a package that gives only the
    # object and from one with a whole world; a window that does not decode
    # leaves MUN's object whole. The windows are solid colours MUN never
    # draws, so the grab shows the window itself, not an item holding it.
    def solid_window(rgb):
        return png(128, 128, [bytes(rgb) * 128] * 128)

    def window_seen(grab, rgb):
        # The pixels of the card object's square in the window's colour.
        cards = grab["regions"]["cards"] if grab and grab.get("path") else []
        if len(cards) != 1:
            return None
        found = focus_frame(read_ppm(Path(grab["path"])), cards[0]["box"], "#%02X%02X%02X" % rgb, near=40)
        return found["count"] if found else 0

    sea = json.loads((SAMPLES / "sea" / "shape.json").read_text(encoding="utf-8"))
    paper = json.loads((SAMPLES / "paper" / "shape.json").read_text(encoding="utf-8"))
    magenta, green, violet = (232, 0, 232), (0, 208, 64), (120, 40, 255)
    for name, package, rgb in (
            ("home-object-organic", write_package(packages / "home-object-organic", {"format": "mun-shape/1", "card": sea["card"]},
                                                  {"card/window.png": solid_window(magenta)}), magenta),
            ("home-object-card", write_package(packages / "home-object-card", {"format": "mun-shape/1", "card": paper["card"]},
                                               {"card/window.png": solid_window(green)}), green),
            ("home-object-world", sample_copy(packages, "home-object-world", "sea",
                                              replace={"card/window.png": solid_window(violet)}), violet)):
        x, rx = card(package)
        cardd, launchd = serve(name, [[(0, snapshot()), ({"on": "before", "after": 0.5}, {"type": "card", "card": rx})]])
        out, phases, grabs = run(name, cardd, launchd, [{"at": 1500, "probe": "before", "grab": "before"},
                                                        {"when": {"phase": "present"}, "wait": 1500, "probe": "after"},
                                                        {"wait": 100, "grab": "object"}],
                                 settings="[display]\nreduce-motion=on\n", end=1000)
        before, after, shown = grabs.get("before"), first(phases, label="after"), grabs.get("object")
        objects = shown["regions"]["cards"] if shown else []
        seen = (window_seen(before, rgb), window_seen(shown, rgb))
        report.setdefault("home", {})[f"{name} window pixels"] = {"before": seen[0], "object": seen[1]}
        checks.expect(name, "the identity is present, MUN Disk in focus",
                      after is not None and after["phase"] == "present" and after["mainIndex"] == 0, after)
        checks.expect(name, "the card object holds the package's window, wholly revealed, its outline the package's",
                      len(objects) == 1 and objects[0]["window"] and objects[0]["shown"] and objects[0]["reveal"] >= 0.999
                      and objects[0]["shape"] == json.loads((package / "shape.json").read_text(encoding="utf-8"))["card"]["shape"],
                      objects if shown else "no grab taken")
        checks.expect(name, "the window is on screen in the object (none of its colour before the card)",
                      seen[0] == 0 and seen[1] is not None and seen[1] >= 2000, seen)
        if name == "home-object-world":
            checks.expect(name, "the world covers MUN's night behind the object",
                          after is not None and after["world"] and after["worldDrawn"], after)
        applied = [line for line in out.splitlines() if "shape for insertion" in line]
        checks.expect(name, "the shell applied the window (128x128)", any("window 128x128" in line for line in applied), applied)

    # A window that does not decode: the identity comes, the object stays MUN's.
    broken_window = sample_copy(packages, "home-object-broken", "sea", replace={"card/window.png": short_png()})
    x, rx = card(broken_window)
    cardd, launchd = serve("home-object-broken", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-object-broken", cardd, launchd, [{"when": {"phase": "present"}, "wait": 1500, "probe": "after"},
                                                                    {"wait": 100, "grab": "object"}],
                             settings="[display]\nreduce-motion=on\n", end=1000)
    after, shown = first(phases, label="after"), grabs.get("object")
    objects = shown["regions"]["cards"] if shown else []
    applied = [line for line in out.splitlines() if "shape for insertion" in line]
    checks.expect("home-object-broken", "the identity comes in, its world drawn",
                  after is not None and after["phase"] == "present" and after["worldDrawn"], after)
    checks.expect("home-object-broken", "the object is MUN's: no window, MUN's outline",
                  len(objects) == 1 and not objects[0]["window"] and objects[0]["shape"] == "card",
                  objects if shown else "no grab taken")
    checks.expect("home-object-broken", "the shell says the object is MUN's",
                  any("card object MUN's (its image did not decode)" in line for line in applied), applied)

    motion_home_cases(shell, checks, packages, card, serve, run, first, design)


def motion_home_package(packages: Path, name: str, plane_rgb=(250, 250, 250), sheet: bytes = None) -> Path:
    """A mun-shape/1.1 package for Home's own Main.qml: a v1 world (a dark
    backdrop) and, as its motion, a plane of one colour (white by default:
    nothing of MUN's light text reads on it without a plate) and a small
    character whose sheet is `sheet` (one that decodes, by default)."""
    def rgba_rows(width, height, rgb):
        return [bytes(rgb + (255,)) * width] * height
    document = {
        "format": "mun-shape/1.1", "motion_draft": 1,
        "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
                    "accent": "#F2B85C"},
        "world": {"backdrop": {"image": "posters/dark.png"}},
        "scene": {"look": "pixel", "planes": [{"image": "planes/plane.png"}]},
        "actors": {"hero": {"sheet": {"image": "sheets/hero.png"}, "at": [960, 900]}},
    }
    files = {"posters/dark.png": shapetools.encode_png(192, 108, rgba_rows(192, 108, (10, 30, 40)), alpha=True),
             "planes/plane.png": shapetools.encode_png(640, 360, rgba_rows(640, 360, plane_rgb), alpha=True),
             "sheets/hero.png": sheet or shapetools.encode_png(16, 16, rgba_rows(16, 16, (240, 150, 40)), alpha=True)}
    return write_package(packages / name, document, files)


def heavy_motion_package(packages: Path, name: str) -> Path:
    """A mun-shape/1.1 package whose motion takes a while to decode, for
    catching the shell while it does: three planes of 2048 x 2048 (16 MiB
    each decoded, 48 MiB together, within the player's 64 MiB), each row a
    pattern of its own; and a dark v1 world."""
    def plane(seed):
        raw = b"".join(b"\x00" + bytes((i * 37 + y * 11 + seed) & 0xFF for i in range(256)) * 32 for y in range(2048))

        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2048, 2048, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    document = {
        "format": "mun-shape/1.1", "motion_draft": 1,
        "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
                    "accent": "#F2B85C"},
        "world": {"backdrop": {"image": "posters/dark.png"}},
        "scene": {"look": "pixel", "planes": [{"image": f"planes/plane-{i}.png"} for i in range(3)]},
    }
    files = {"posters/dark.png": shapetools.encode_png(192, 108, [bytes((10, 30, 40, 255)) * 192] * 108, alpha=True)}
    files.update({f"planes/plane-{i}.png": plane(i * 50) for i in range(3)})
    return write_package(packages / name, document, files)


def reef_painted(packages: Path, name: str) -> Path:
    """The reef sample with its amber school's sheet painted magenta, its
    alpha kept: where the school goes, found by its pixels."""
    data = (SAMPLES / "reef" / "sheets" / "fish-near.png").read_bytes()
    chunks = list(shapetools._chunks(data))
    width, height, depth, kind = struct.unpack(">IIBB", next(body for key, body in chunks if key == b"IHDR")[:10])
    assert (depth, kind) == (8, 6), "an 8-bit RGBA sheet"
    rows = shapetools._unfilter(zlib.decompress(b"".join(body for key, body in chunks if key == b"IDAT")), width, height, 32)
    painted = [bytes(v for x in range(width) for v in (255, 0, 255, row[4 * x + 3])) for row in rows]
    return sample_copy(packages, name, "reef", replace={"sheets/fish-near.png": shapetools.encode_png(width, height, painted,
                                                                                                    alpha=True)})


def motion_home_cases(shell: Shell, checks: Checks, packages: Path, card, serve, run, first, design: str):
    """Shape motion with Home's own Main.qml (home_cases' stand-ins and
    runner): where the reef's school mills with Play in focus, measured from
    the card object, and every fish of it in sight all the way round; Play pressed, the launch refused and accepted; a focus
    reaction still settling when Play is pressed; coming back after a game;
    released and removed; MUN's panel over the motion; a motion that cannot
    be loaded, and one whose decoding took the shell down."""
    tag = design

    def game(rx):
        # a card with a game: Play among its options, first
        rx["info"].update(kind="game", entry="bin/game", title="Reef")
        return rx

    def actors(out, name):
        return [a for a in lines(out, "ACTOR") if a.get("label", "").startswith(name)]

    def applied(out, event):
        return [line for line in out.splitlines() if f"motion: {event} at step" in line]

    reef = SAMPLES / "reef"

    # Play in focus: the school mills below the card object, wherever the
    # design draws it (its points measured from the disc).
    x, rx = card(reef)
    cardd, launchd = serve(f"motion-home-play-{tag}", [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)})]])
    steps = [{"at": 6500, "call": "enter"}] + [{"at": 11000 + 2250 * i, "actor": "fish-near", "label": f"mill-{i}"}
                                             for i in range(4)]
    out, phases, _ = run(f"motion-home-play-{tag}", cardd, launchd, steps + [{"at": 18000, "probe": "end"}], end=18300)
    mills = actors(out, "mill")
    checks.expect(f"motion-home-play-{tag}", "Play in focus reaches the motion, once",
                  len(applied(out, "focus.play")) == 1, applied(out, "focus.play"))
    checks.expect(f"motion-home-play-{tag}", "the school mills below the card object, within 420 pixels either side "
                  "and 150 to 450 below its centre, all the way round",
                  len(mills) == 4 and all(m.get("state") == "gather" and abs(m["x"] - m["centre"][0]) <= 420
                                          and 150 <= m["y"] - m["centre"][1] <= 450 for m in mills),
                  [(m.get("state"), round(m.get("x", 0)), round(m.get("y", 0)), m.get("centre")) for m in mills])

    # Its whole round, every fish: below the card object and above the
    # console's bottom line (the hints' row: 60 + 36 pixels from the
    # bottom, Main.qml), the school painted magenta to be found.
    x, rx = card(reef_painted(packages, f"reef-magenta-{tag}"))
    cardd, launchd = serve(f"motion-home-school-{tag}", [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)})]])
    out, phases, grabs = run(f"motion-home-school-{tag}", cardd, launchd,
                             [{"at": 6500, "call": "enter"}, {"at": 6600, "probe": "focused"}]
                             + [{"at": 9000 + 750 * i, "grab": f"round-{i:02d}"} for i in range(13)], end=19000)
    focused, union = first(phases, label="focused"), None
    for grab in grabs.values():
        if grab.get("path"):
            count, box = coloured(read_ppm(Path(grab["path"])), (255, 0, 255), (0, 0, 1920, 1080), tolerance=70)
            if count:
                union = box if union is None else [min(union[0], box[0]), min(union[1], box[1]),
                                                   max(union[2], box[2]), max(union[3], box[3])]
    centre = focused["objectCentre"] if focused else None
    checks.expect(f"motion-home-school-{tag}", "all the way round the school is below the card object (140 pixels "
                  "below its centre and more) and above the bottom line (980 and above): in sight",
                  len(grabs) == 13 and union is not None and centre is not None and union[1] >= centre[1] + 140
                  and union[3] <= 980 and union[0] >= 0, (union, centre))

    def launching(name, accepted, quick=False):
        x, rx = card(reef)
        cardd = Service(shell.work / f"{name}.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)})]])

        def answer(service, message):
            if message.get("type") != "launch":
                return
            time.sleep(0.2)
            if accepted:
                service.push({"type": "launch_result", "accepted": True})
                service.push({"type": "session", "state": "launching"})
            else:
                service.push({"type": "launch_result", "accepted": False,
                              "error": {"code": "card_busy", "message": "La tarjeta está ocupada"}})
        launchd = Service(shell.work / f"{name}-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]], answer)
        # Play in focus, then pressed: after its reaction has settled, or
        # (quick) 0.2 s after, while it is still settling.
        press = 6700 if quick else 10500
        steps = [{"at": 6500, "call": "enter"}, {"at": press, "call": "enter"},
                 {"at": press + 3200, "actor": "fish-near", "label": "after"}, {"at": press + 3300, "probe": "end"}]
        out, phases, _ = run(name, cardd, launchd, steps, end=press + 3600)
        after = next(iter(actors(out, "after")), {})
        return out, after, launchd

    name = f"motion-home-refused-{tag}"
    out, after, launchd = launching(name, accepted=False)
    checks.expect(name, "Play pressed: asked to launch, and the launch reaches the motion",
                  any(m.get("type") == "launch" for m in launchd.received) and len(applied(out, "launch")) == 1,
                  applied(out, "launch"))
    checks.expect(name, "refused: the motion is told, once", len(applied(out, "launch.refused")) == 1,
                  applied(out, "launch.refused"))
    checks.expect(name, "refused: the school is back, milling again with Play still in focus (not dived and gone)",
                  after.get("state") == "gather" and after.get("hidden") is False, after)

    name = f"motion-home-launched-{tag}"
    out, after, _ = launching(name, accepted=True)
    checks.expect(name, "accepted: the school dives into the disc and is gone, nothing given back",
                  after.get("hidden") is True and not applied(out, "launch.refused"), (after, applied(out, "launch.refused")))
    # Starting drops for a moment as the launcher accepts (its answer comes
    # before its new state): the motion takes that launch once.
    checks.expect(name, "the launch reaches the motion once, though starting dropped as it was accepted",
                  len(applied(out, "launch")) == 1, applied(out, "launch"))

    name = f"motion-home-quick-{tag}"
    out, after, _ = launching(name, accepted=True, quick=True)
    checks.expect(name, "Play pressed while its focus reaction settled: the reaction does not come after the dive",
                  after.get("hidden") is True and after.get("state") != "gather", after)
    checks.expect(name, "the launch reaches the motion once", len(applied(out, "launch")) == 1, applied(out, "launch"))

    # Accepted, then its preparation fails (the launcher ends the session
    # with the shell still running): the school comes back and Play can be
    # pressed again; the second launch is taken. Meanwhile an earlier
    # session's result and a repeated message come: only this shell's own
    # session, once, gives the launch back.
    x, rx = card(reef)
    name = f"motion-home-prepare-failed-{tag}"
    cardd = Service(shell.work / f"{name}.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)})]])
    earlier = {"session": "s-earlier", "reason": "exited", "message": "", "code": 0, "signal": None, "detail": "",
               "card_id": "mun.test", "title": "Reef", "version": "1.0.0", "ended_at": 1, "acknowledged": True}
    launches = []

    def failing(service, message):
        if message.get("type") != "launch":
            return
        launches.append(message)
        session = {"id": f"s-{len(launches)}", "slot": "c1", "serial": rx["serial"], "card_id": "mun.test",
                   "title": "Reef", "version": "1.0.0"}
        time.sleep(0.2)
        service.push({"type": "session", "state": "preparing", "session": session, "last_result": earlier})
        service.push({"type": "launch_result", "accepted": True, "session": session["id"]})
        if len(launches) > 1:
            service.push({"type": "session", "state": "starting", "session": session, "last_result": earlier})
            return
        time.sleep(0.6)
        # an earlier session's result, idle: not this launch's
        service.push({"type": "session", "state": "idle", "session": None, "last_result": earlier})
        time.sleep(0.4)
        service.push({"type": "session", "state": "preparing", "session": session, "last_result": earlier})
        time.sleep(0.4)
        failed = dict(earlier, session=session["id"], reason="prepare_failed", message="No se pudo preparar el juego",
                      code=None, acknowledged=False)
        for _ in range(2):   # said twice, as after a reconnection
            service.push({"type": "session", "state": "idle", "session": None, "last_result": failed})
    launchd = Service(shell.work / f"{name}-launch.sock",
                      [[(0, {"type": "snapshot", "state": "idle", "last_result": earlier})]], failing)
    out, phases, _ = run(name, cardd, launchd, [
        {"at": 6500, "call": "enter"}, {"at": 10500, "call": "enter"},
        {"at": 14500, "actor": "fish-near", "label": "failed"}, {"at": 14600, "call": "dismissResult"},
        {"at": 16000, "call": "enter"}, {"at": 19500, "actor": "fish-near", "label": "retried"},
        {"at": 19600, "probe": "end"}], end=19900)
    failed, retried = next(iter(actors(out, "failed")), {}), next(iter(actors(out, "retried")), {})
    checks.expect(name, "the failed launch is given back once, for its own session only",
                  len(applied(out, "launch.refused")) == 1, applied(out, "launch.refused"))
    checks.expect(name, "after it: the school is back, milling with Play in focus",
                  failed.get("state") == "gather" and failed.get("hidden") is False, failed)
    checks.expect(name, "Play pressed again: launched again, the second launch taken and the school gone",
                  len(launches) == 2 and len(applied(out, "launch")) == 2 and retried.get("hidden") is True,
                  (len(launches), applied(out, "launch"), retried))

    # Back from a game: the card is there when the shell starts.
    x, rx = card(reef)
    # as after a game: the launcher holds that game's result, not yet seen
    played = {"session": "s-played", "reason": "exited", "message": "", "code": 0, "signal": None, "detail": "",
              "card_id": "mun.test", "title": "Reef", "version": "1.0.0", "ended_at": 1, "acknowledged": False}
    cardd, launchd = serve(f"motion-home-return-{tag}", [[(0, snapshot(game(rx)))]],
                           launcher=[[(0, {"type": "snapshot", "state": "idle", "last_result": played})]])
    out, phases, _ = run(f"motion-home-return-{tag}", cardd, launchd,
                         [{"at": 6000, "actor": "fish-near", "label": "back"}, {"at": 6100, "probe": "end"}], end=6400)
    back = next(iter(actors(out, "back")), {})
    checks.expect(f"motion-home-return-{tag}", "coming back after a game reaches the motion as return",
                  len(applied(out, "return")) == 1 and not applied(out, "arrive"), (applied(out, "return"), applied(out, "arrive")))
    checks.expect(f"motion-home-return-{tag}", "the school is out, swimming", back.get("hidden") is False, back)
    checks.expect(f"motion-home-return-{tag}", "the game's result, found at start, gives nothing back",
                  not applied(out, "launch.refused"), applied(out, "launch.refused"))

    # Released (Eject safely): leave. Removed while Play's reaction runs:
    # the identity goes, and Home with it to MUN's.
    x, rx = card(reef)
    cardd = Service(shell.work / f"motion-home-leave-{tag}.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)})]])

    def release(service, message):
        if message.get("type") == "release":
            time.sleep(0.3)
            cardd.push({"type": "card", "card": dict(rx, state="released", info=None)})
            service.push({"type": "released", "ok": True, "slot": "c1", "serial": rx["serial"]})
    launchd = Service(shell.work / f"motion-home-leave-{tag}-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]],
                      release)
    out, phases, _ = run(f"motion-home-leave-{tag}", cardd, launchd, [{"at": 6500, "call": "eject"}, {"at": 10500, "probe": "end"}],
                         end=10800)
    end = first(phases, label="end")
    checks.expect(f"motion-home-leave-{tag}", "released: leave reaches the motion, and the identity goes",
                  len(applied(out, "leave")) == 1 and end is not None and end["phase"] == "none" and not end["hasMotion"],
                  (applied(out, "leave"), end))

    x, rx = card(reef)
    cardd = Service(shell.work / f"motion-home-removed-{tag}.sock",
                    [[(0, snapshot()), (0.6, {"type": "card", "card": game(rx)}),
                      (9.5, {"type": "removed", "slot": "c1", "insertion": x})]])
    launchd = Service(shell.work / f"motion-home-removed-{tag}-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]])
    out, phases, _ = run(f"motion-home-removed-{tag}", cardd, launchd,
                         [{"at": 6500, "call": "enter"}, {"at": 13000, "probe": "end"}], end=13300)
    end, leaving = first(phases, label="end"), first(phases, phase="leaving")
    checks.expect(f"motion-home-removed-{tag}", "removed during Play's reaction: the identity leaves (removal), "
                  "MUN's Home after it, the shell still running",
                  leaving is not None and leaving["exit"] == "removal" and end is not None and end["phase"] == "none"
                  and not end["hasMotion"] and "(exit status" not in out and not applied(out, "leave"),
                  (leaving, end, applied(out, "leave")))

    # MUN's panel over the motion: on its plate, as over a world. A white
    # plane, the focus on an entry that is not the card's.
    x, rx = card(motion_home_package(packages, f"motion-white-{tag}"))
    cardd, launchd = serve(f"motion-home-panel-{tag}", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run(f"motion-home-panel-{tag}", cardd, launchd,
                             [{"at": 6000, "set": {"mainIndex": 1}}, {"at": 7500, "probe": "end"}, {"at": 7600, "grab": "panel"}],
                             end=8200)
    end, grab = first(phases, label="end"), grabs.get("panel")
    contrast = None
    if grab and grab.get("path") and grab["regions"]["panel"]:
        contrast = text_contrast(read_ppm(Path(grab["path"])), grab["regions"]["panel"]["text"])
    checks.expect(f"motion-home-panel-{tag}", "over the motion, the focus off the card: the panel on its plate",
                  end is not None and end["motionVisible"] and end["mainIndex"] == 1 and end["panelOverWorld"], end)
    checks.expect(f"motion-home-panel-{tag}", "its text reads on the white motion (4.5:1 at least)",
                  contrast is not None and contrast["ratio"] >= 4.5, contrast)

    # A motion that cannot be loaded (a sheet that does not decode): the
    # identity comes in with its v1 world instead, and the journal says why.
    x, rx = card(motion_home_package(packages, f"motion-broken-{tag}", sheet=short_png()))
    cardd, launchd = serve(f"motion-home-broken-{tag}", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run(f"motion-home-broken-{tag}", cardd, launchd, [{"at": 6500, "probe": "end"}], end=6800)
    end = first(phases, label="end")
    checks.expect(f"motion-home-broken-{tag}", "a sheet that does not decode: no motion drawn, the v1 world instead, "
                  "the identity present",
                  end is not None and end["phase"] == "present" and end["motionFailed"] and not end["motionDrawn"]
                  and end["worldDrawn"], end)
    checks.expect(f"motion-home-broken-{tag}", "the journal says which image and that the v1 look is drawn",
                  any("sheets/hero.png could not be decoded" in line and "v1 look instead" in line for line in out.splitlines()),
                  [line for line in out.splitlines() if "motion:" in line][:4])

    # The last start ended while this insertion's motion was decoded: its
    # motion is skipped, its v1 look shown.
    x, rx = card(motion_home_package(packages, f"motion-crashed-{tag}", plane_rgb=(30, 60, 90)))
    cardd, launchd = serve(f"motion-home-crashed-{tag}", [[(0, snapshot(rx))]])
    runtime = shell.work / f"motion-home-crashed-{tag}" / "runtime"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    (runtime / "shape-decoding-motion").write_text(x)
    out, phases, _ = run(f"motion-home-crashed-{tag}", cardd, launchd, [{"at": 6000, "probe": "end"}], end=6300)
    end = first(phases, label="end")
    checks.expect(f"motion-home-crashed-{tag}", "the motion whose decoding took the shell down is skipped; the v1 world "
                  "is drawn", end is not None and not end["hasMotion"] and end["worldDrawn"]
                  and not (runtime / "shape-decoding-motion").exists(), end)
    checks.expect(f"motion-home-crashed-{tag}", "the journal says so",
                  any("its motion is skipped" in line for line in out.splitlines()), out[-600:])

    # The shell taken down while it decodes a motion (killed, as a crash
    # would be): the marker named the insertion then. Started again with
    # that card in, it shows the insertion's v1 look; in that same start,
    # another insertion's motion plays, its own decoding guarded by a marker
    # naming it, which is gone once it is decoded.
    heavy = heavy_motion_package(packages, f"motion-heavy-{tag}")
    a, ra = card(heavy)
    b, rb = card(heavy)
    runtime = shell.work / f"motion-home-interrupted-{tag}" / "runtime"
    marker = runtime / "shape-decoding-motion"
    caught = []

    def decoding():
        # whole: a marker caught as it is written reads empty
        try:
            text = marker.read_text()
        except OSError:
            return False
        if text:
            caught.append(text)
        return bool(text)
    name = f"motion-home-interrupted-{tag}"
    cardd, launchd = serve(name, [[(0, snapshot()), (0.6, {"type": "card", "card": ra})]])
    out, phases, _ = run(name, cardd, launchd, [{"at": 9000, "probe": "end"}], end=9300, runtime=runtime,
                         kill_when=decoding)
    left = marker.read_text() if marker.is_file() else None
    checks.expect(name, "taken down while decoding: the marker named the insertion, and stays",
                  "(killed)" in out and caught[:1] == [a] and left == a, (caught[:1], left, a, out[-300:]))

    name = f"motion-home-after-crash-{tag}"
    seen, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            try:
                seen.append(marker.read_text())
            except OSError:
                pass
            time.sleep(0.002)
    watcher = threading.Thread(target=watch, daemon=True)
    cardd, launchd = serve(name, [[(0, snapshot(ra)), (6.5, {"type": "removed", "slot": "c1", "insertion": a}),
                                   (8.0, {"type": "card", "card": rb})]])
    watcher.start()
    try:
        out, phases, _ = run(name, cardd, launchd, [{"at": 6000, "probe": "skipped"}, {"at": 15500, "probe": "end"}],
                             end=15800, runtime=runtime)
    finally:
        stop.set()
        watcher.join()
    skipped, end = first(phases, label="skipped"), first(phases, label="end")
    checks.expect(name, "started again with that card in: its motion skipped, its v1 world drawn",
                  skipped is not None and skipped["insertion"] == a and not skipped["hasMotion"] and skipped["worldDrawn"],
                  skipped)
    checks.expect(name, "the journal says which insertion's motion is skipped",
                  any("motion of insertion " + a in line and "its motion is skipped" in line for line in out.splitlines()),
                  [line for line in out.splitlines() if "shape:" in line][:3])
    checks.expect(name, "another insertion in that start: its motion decoded and drawn",
                  end is not None and end["insertion"] == b and end["hasMotion"] and end["motionDrawn"]
                  and not end["motionFailed"], end)
    checks.expect(name, "its decoding guarded by a marker naming it, removed once decoded",
                  b in seen and not marker.exists(), (sorted(set(seen)), marker.exists()))

    # The marker cannot be written (a directory stands where it goes): the
    # motion is not decoded, unguarded; the v1 world is drawn.
    x, rx = card(motion_home_package(packages, f"motion-unguarded-{tag}", plane_rgb=(30, 60, 90)))
    name = f"motion-home-unguarded-{tag}"
    runtime = shell.work / name / "runtime"
    (runtime / "shape-decoding-motion").mkdir(parents=True)
    cardd, launchd = serve(name, [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run(name, cardd, launchd, [{"at": 6500, "probe": "end"}], end=6800, runtime=runtime)
    end = first(phases, label="end")
    checks.expect(name, "the marker cannot be written: no motion decoded, the v1 world drawn, the identity present",
                  end is not None and end["phase"] == "present" and end["motionFailed"] and not end["motionDrawn"]
                  and end["worldDrawn"], end)
    checks.expect(name, "the journal says why, and no start is taken for a crash",
                  any("decoding marker could not be written" in line for line in out.splitlines())
                  and not any("its motion is skipped" in line for line in out.splitlines()),
                  [line for line in out.splitlines() if "motion" in line][:4])


def boot_cases(shell: Shell, checks: Checks):
    """The start-up as the first start of a boot plays it (boot.qml)."""
    def run(name, ready_at=None, steps=(), end=16000, settings="", env=None):
        cardd = launchd = None
        if ready_at is not None:
            cardd = Service(shell.work / f"{name}.sock", [[(ready_at, snapshot())]])
            launchd = Service(shell.work / f"{name}-launch.sock", [[(ready_at, {"type": "snapshot", "state": "idle"})]])
        try:
            output = shell.run(name, "boot.qml", {"steps": list(steps) + [{"at": end, "quit": True}]},
                               socket_path=cardd.path if cardd else None, launcher=launchd.path if launchd else None,
                               settings=settings, timeout=end / 1000 + 60, env=env)
        finally:
            for service in (cardd, launchd):
                if service:
                    service.close()
        clocks[name] = re.findall(r"mun-shell: start-up: the picture (follows the sound|runs on its own[^\n]*)", output)
        waits[name] = re.findall(r"mun-shell: start-up: waited ([0-9.]+) s for the sound device: ([^\n]*)", output)
        sounds_off[name] = output.count("mun-shell: interface sounds off")
        alsa_after[name] = output.partition("mun-shell: interface sounds off")[2].count("ALSA lib ")
        return lines(output, "BOOT"), lines(output, "KEY")

    def first(states, **match):
        return next((s for s in states if all(s[k] == v for k, v in match.items())), None)

    clocks = {}   # what each run's start-up said about its clock
    waits = {}    # what it said about waiting for the sound device
    sounds_off = {}  # how many times it said the interface sounds are off
    alsa_after = {}  # ALSA's own lines about the device after the shell said so

    # Both services answer at once: the hint shows; Down does nothing, A ends it.
    states, keys = run("boot-ready", ready_at=0, steps=[{"at": 2000, "key": "Down"}, {"at": 3000, "key": "A"}], end=5000)
    hinted = first(states, hint=1)
    checks.expect("boot-ready", "the system ready, the Skip hint shows before any key",
                  hinted is not None and hinted["at"] < 2000 and hinted["canSkip"], hinted)
    checks.expect("boot-ready", "another key (Down) does nothing: the start-up plays on",
                  all(s["playing"] and not s["powered"] for s in states if 2000 <= s["at"] < 3000), keys)
    home = first(states, powered=True)
    checks.expect("boot-ready", "A skips it: Home within 0.7 s, the layer leaving",
                  home is not None and 3000 <= home["at"] < 3700 and not home["playing"] and not home["shown"], home)
    played = [s["t"] for s in states if s["at"] < 3000 and s["playing"]]
    checks.expect("boot-ready", "its clock ran before the skip", played and max(played) > 1.0, played[-3:])
    gone = [s for s in states if home and s["at"] >= home["at"] + 1000]
    checks.expect("boot-ready", "once it has faded out, its drawing is let go",
                  gone and all(s["hint"] == -1 for s in gone), gone[:2])
    checks.expect("boot-ready", "the memory settles once it is over, not while it plays",
                  home is not None and not any(s["settled"] for s in states if s["at"] < home["at"] and s["playing"])
                  and all(s["settled"] for s in states if s["at"] >= home["at"]), home)
    early = next((s for s in states if s["at"] >= 1500), None)
    checks.expect("boot-ready", "without a sound device the picture does not wait for one",
                  early is not None and early["t"] >= 0.5, early)
    checks.expect("boot-ready", "none expected: the LED never waits for one before the start-up",
                  not any(s["warming"] for s in states) and not waits.get("boot-ready"), waits.get("boot-ready"))
    checks.expect("boot-ready", "standby from the first frame: the LED lit at once, as the system's picture has it",
                  states and states[0]["standby"] and states[0]["led"] == 1 and states[0]["t"] < 0, states[:1])
    running = next((s for s in states if s["t"] >= 0.7), None)
    checks.expect("boot-ready", "standby ends as the start-up begins: its LED faded within 0.7 s of the picture",
                  running is not None and not running["standby"] and running["led"] == 0, running)

    checks.expect("boot-ready", "the journal says which clock the picture follows", bool(clocks.get("boot-ready")),
                  clocks.get("boot-ready"))

    # The services answer at 15 s: nothing skips it before; the picture
    # fades (11.75 s of the sound) and the LED waits for them; Home follows.
    states, keys = run("boot-late", ready_at=15.0, steps=[{"at": 5000, "key": "A"}, {"at": 14000, "key": "A"}], end=17000)
    checks.expect("boot-late", "before the system is ready no Skip shows and A does nothing",
                  all(not s["canSkip"] and s["hint"] == 0 for s in states if s["at"] < 15000)
                  and all(s["playing"] for s in states if 5000 <= s["at"] < 14900), keys)
    waiting = first(states, waiting=True)
    checks.expect("boot-late", "once the picture has faded, the LED waits for the system",
                  waiting is not None and waiting["t"] >= 11.7 and waiting["playing"] and not waiting["powered"], waiting)
    home = first(states, powered=True)
    checks.expect("boot-late", "the memory settles once Home comes, not while the LED waits",
                  home is not None and not any(s["settled"] for s in states if s["at"] < home["at"])
                  and all(s["settled"] for s in states if s["at"] >= home["at"]), home)
    checks.expect("boot-late", "Home follows the system's answer",
                  home is not None and 15000 <= home["at"] < 16500, home)
    # No sound device for 17 s, the interface awake: it is tried again every
    # 5 s, and only the first failure is explained, by ALSA and by the shell.
    checks.expect("boot-late", "a missing sound device is said once, and its retries say nothing, ALSA's lines included",
                  sounds_off.get("boot-late") == 1 and alsa_after.get("boot-late") == 0,
                  (sounds_off.get("boot-late"), alsa_after.get("boot-late")))

    # Neither answers: Home after the start-up's 20 s, whatever they do.
    states, _ = run("boot-never", steps=[], end=23000)
    home = first(states, powered=True)
    checks.expect("boot-never", "no Skip ever shows: hidden while the start-up is on screen, gone after",
                  all(not s["canSkip"] and (s["hint"] == 0 if s["shown"] else s["hint"] <= 0) for s in states), "")
    checks.expect("boot-never", "the memory settles once Home comes, not before",
                  home is not None and not any(s["settled"] for s in states if s["at"] < home["at"])
                  and all(s["settled"] for s in states if s["at"] >= home["at"]), home)
    checks.expect("boot-never", "Home after 20 s without the system", home is not None and 20000 <= home["at"] < 21500, home)

    # The interface sounds off: the picture's clock runs from the start.
    states, _ = run("boot-silent", ready_at=0, steps=[], end=3000, settings="[audio]\nsystem-sounds=off\n")
    early = next((s for s in states if s["at"] >= 1300), None)
    checks.expect("boot-silent", "without the sound, its clock runs from the start (no wait for a device)",
                  early is not None and early["t"] >= 0.8, early)
    checks.expect("boot-silent", "standby still from the first frame, the sounds off",
                  states and states[0]["standby"] and states[0]["led"] == 1, states[:1])

    # A sound device expected within 4 s of the shell's start (a board's
    # sound card comes up after the shell starts) that never comes: the LED
    # breathes in the dark until the wait is over, then the picture runs on
    # its own. The scene's clock starts once the interface has loaded.
    states, _ = run("boot-expected", ready_at=0, steps=[], end=7000, env={"MUN_SHELL_SOUND_WAIT": "4"})
    begun = first(states, playing=True)
    held = [s for s in states if begun and begun["at"] <= s["at"] < min(begun["at"] + 1000, 2500)]
    checks.expect("boot-expected", "while the device is expected the start-up waits, the LED breathing",
                  held and all(s["warming"] and s["standby"] and s["led"] == 1 and s["t"] < 0 and s["playing"]
                               for s in held), held[-2:])
    late = next((s for s in states if s["at"] >= 6000), None)
    checks.expect("boot-expected", "once none is expected any more, the picture runs on its own",
                  late is not None and not late["warming"] and late["t"] >= 1.0, late)
    said = waits.get("boot-expected") or []
    checks.expect("boot-expected", "the journal says how long it waited and that none came",
                  len(said) == 1 and 1.5 <= float(said[0][0]) <= 4.2 and said[0][1] == "none came", said)
    checks.expect("boot-expected", "one player of sounds: the device's absence is said once, after the wait",
                  sounds_off.get("boot-expected") == 1, sounds_off.get("boot-expected"))


class Xvfb:
    """An X server of its own for the OpenGL path, 1920x1080, or None where
    Xvfb is not installed."""

    def __init__(self):
        self.process, self.display = None, None

    def __enter__(self):
        if not shutil.which("Xvfb"):
            return None
        read, write = os.pipe()
        self.process = subprocess.Popen(["Xvfb", "-displayfd", str(write), "-screen", "0", "1920x1080x24",
                                         "-nolisten", "tcp"], pass_fds=(write,), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        os.close(write)
        with os.fdopen(read) as numbers:
            number = numbers.readline().strip()
        if not number.isdigit():
            self.process.kill()
            return None
        self.display = f":{number}"
        return self

    def __exit__(self, *exc):
        if self.process:
            self.process.kill()
            self.process.wait()


def difference(a, b, box=None):
    """Mean absolute channel difference of two same-size frames (within
    `box`, x0, y0, x1, y1) and the share of channels that differ by more
    than 16 levels."""
    width, height, da = a
    _, _, db = b
    x0, y0, x1, y1 = box or (0, 0, width, height)
    total = large = count = 0
    for y in range(y0, y1):
        row = (y * width + x0) * 3
        for i in range(row, row + (x1 - x0) * 3):
            d = abs(da[i] - db[i])
            total += d
            large += d > 16
            count += 1
    return total / count, large / count


def motion_package(packages: Path, name: str, busy: bool, bend: bool = False) -> Path:
    """A mun-shape/1.1 package with motion (pixel look): a striped plane, a
    character of four 32 x 32 frames that answers Play in focus by going to
    the right, the disc's glow and, `busy`, an emitter whose particles keep
    appearing (60 at most)."""
    def rgba_rows(width, height, colour_at):
        return [b"".join(bytes(colour_at(x, y)) for x in range(width)) for y in range(height)]
    plane = shapetools.encode_png(640, 360, rgba_rows(640, 360, lambda x, y: (30, 60, 90, 255) if (x // 40) % 2
                                                      else (60, 110, 140, 255)), alpha=True)
    tones = [(250, 200, 40, 255), (240, 120, 40, 255), (200, 60, 60, 255), (240, 120, 40, 255)]
    hero = shapetools.encode_png(128, 32, rgba_rows(128, 32, lambda x, y: tones[x // 32] if 4 <= x % 32 < 28 and 4 <= y < 28
                                                    else (0, 0, 0, 0)), alpha=True)
    dot = shapetools.encode_png(4, 4, rgba_rows(4, 4, lambda x, y: (255, 255, 255, 220)), alpha=True)
    document = {
        "format": "mun-shape/1.1", "motion_draft": 1,
        "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
                    "accent": "#F2B85C"},
        "scene": {"look": "pixel", "planes": [{"image": "backgrounds/stripes.png"}]},
        "actors": {"hero": {"sheet": {"image": "actors/hero.png", "frame": [32, 32]}, "at": [480, 540], "z": 0.3}},
        "paths": {"visit": {"points": [[1440, 540]], "seconds": 1.0, "ease": "linear"}},
        "states": {"hero.visit": {"path": "visit"}},
        "lights": {"glow": {"effect": "glow", "at": "disc", "radius": 200, "opacity": 0.5}},
        "react": {"focus.play": {"settle": 0.2, "cooldown": 0, "do": {"hero": "visit"}}},
    }
    files = {"backgrounds/stripes.png": plane, "actors/hero.png": hero}
    if bend:
        # a plant of 48 rows bending from its foot (8 bands)
        stem = shapetools.encode_png(16, 48, rgba_rows(16, 48, lambda x, y: (60, 160, 90, 255) if 5 <= x < 11
                                                       else (0, 0, 0, 0)), alpha=True)
        files["actors/stem.png"] = stem
        document["actors"]["stem"] = {"sheet": {"image": "actors/stem.png", "frame": [16, 48]}, "at": [960, 860],
                                      "z": 0.1, "bend": {"amount": 30, "period": 2}}
    if busy:
        document["emitters"] = {"sparks": {"sheet": {"image": "effects/dot.png"}, "region": [0, 0, 1920, 1080],
                                           "rate": 30, "life": [5, 6], "max": 60, "z": 0.2}}
        files["effects/dot.png"] = dot
    return write_package(packages / name, document, files)


def motion_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, report: dict):
    def motion_of(shape_record):
        document = json.loads((Path(shape_record["path"]) / shape.MANIFEST).read_text(encoding="utf-8"))
        return document["motion"], shape_record["path"]

    busy, busy_root = motion_of(export(exports, insertion(0x900), motion_package(packages, "motion-busy", True)))
    calm, calm_root = motion_of(export(exports, insertion(0x901), motion_package(packages, "motion-calm", False)))
    plant, plant_root = motion_of(export(exports, insertion(0x902), motion_package(packages, "motion-plant", False, True)))
    out_dir = shell.work / "motion-grabs"
    out_dir.mkdir(exist_ok=True)

    def run(name, backend, steps, env=None):
        output = shell.run(name, "motion.qml", {"steps": steps, "out": str(out_dir)}, exports=exports,
                           env={"MUN_SHELL_MOTION_BACKEND": backend, "MUN_SHELL_MOTION_TRACE": "1"} if env is None else env)
        return output, {m["label"]: m for m in lines(output, "MOTION")}, {g["name"]: g for g in lines(output, "GRAB")}

    # A console package's motion is loaded from its own insertion's export
    # (<insertion>.<attempt>, as the card service names it), its marker
    # written first and gone once decoded. Another insertion's export, or a
    # folder named otherwise, is not decoded.
    unnamed = exports / "calm-unnamed"
    shutil.copytree(calm_root, unnamed)
    for case, root, own in (("motion-export-own", calm_root, True), ("motion-export-another", busy_root, False),
                            ("motion-export-unnamed", str(unnamed), False)):
        out, probes, _ = run(case, "graph", [
            {"at": 0, "insertion": insertion(0x901), "root": root, "motion": calm, "running": True},
            {"at": 1500, "probe": "end"}, {"at": 1600, "quit": True}])
        end, marker = probes.get("end", {}), shell.work / case / "runtime" / "shape-decoding-motion"
        if own:
            checks.expect(case, "its own insertion's export: decoded and drawn, its marker gone once decoded",
                          end.get("drawn") and not end.get("failed") and not marker.exists(), end)
        else:
            checks.expect(case, "not its insertion's export: nothing decoded or drawn, and the journal says whose it is "
                          "not", end.get("failed") and not end.get("drawn") and not marker.exists()
                          and any("is not insertion " + insertion(0x901) in line for line in out.splitlines()),
                          (end, [line for line in out.splitlines() if "motion:" in line][:2]))

    # A motion with nothing to draw (the checker refuses it): not drawn in
    # the v1 world's place.
    empty = dict(calm, planes=[], actors={}, emitters={}, lights={})
    out, probes, _ = run("motion-empty", "graph", [
        {"at": 0, "root": calm_root, "motion": empty, "running": True}, {"at": 1200, "probe": "end"}, {"at": 1300, "quit": True}])
    end = probes.get("end", {})
    checks.expect("motion-empty", "nothing to draw: not drawn, failed (the v1 world drawn instead), the journal says so",
                  end.get("failed") and not end.get("drawn")
                  and any("nothing to draw" in line for line in out.splitlines()), end)

    grabs_by_backend = {}
    for backend in ("graph", "cpu"):
        # What it draws grows while it is on screen: particles keep appearing
        # (a node added to a live scene graph must be drawable at once).
        case = f"motion-grow-{backend}"
        out, probes, _ = run(case, backend, [
            {"at": 0, "root": busy_root, "motion": busy, "running": True}, {"at": 400, "probe": "start"},
            {"at": 2600, "probe": "grown"}, {"at": 2700, "quit": True}])
        start, grown = probes.get("start", {}), probes.get("grown", {})
        checks.expect(case, f"the {backend} way is the one taken", grown.get("backend") == backend, grown)
        checks.expect(case, "it keeps running while what it draws grows (the shell ends by itself)",
                      "(exit status" not in out and "(timed out)" not in out and bool(grown), out[-400:])
        checks.expect(case, "more is drawn as particles appear",
                      grown.get("drawn") and grown.get("things", 0) >= start.get("things", 0) + 20, (start, grown))
        # Steps are the simulation's (its rate); ticks are frames drawn.
        steps_taken = grown.get("steps", 0) - start.get("steps", 0)
        checks.expect(case, "it simulates at its rate: 20 steps a second (2.2 s: 38 to 50)",
                      38 <= steps_taken <= 50, (start, grown))
        checks.expect(case, "it draws on every frame, at least once a step",
                      grown.get("ticks", 0) - start.get("ticks", 0) >= steps_taken, (start, grown))

        # A still frame (Reduce motion) of the calm package, for comparing the two ways.
        case = f"motion-still-{backend}"
        out, probes, grabs = run(case, backend, [
            {"at": 0, "root": calm_root, "motion": calm, "still": True, "running": True},
            {"at": 1200, "grab": case}, {"at": 2700, "grab": f"{case}-later"}, {"at": 2800, "probe": "end"},
            {"at": 2900, "quit": True}])
        grab, later = grabs.get(case, {}), grabs.get(f"{case}-later", {})
        checks.expect(case, "a still frame is drawn and grabbed", probes.get("end", {}).get("drawn") and grab.get("path"),
                      (probes, grab))
        if grab.get("path") and later.get("path"):
            first_frame, second_frame = read_ppm(Path(grab["path"])), read_ppm(Path(later["path"]))
            checks.expect(case, "held still: 1.5 s later the same frame, pixel for pixel",
                          difference(first_frame, second_frame) == (0, 0), difference(first_frame, second_frame))
            hero, _ = coloured(first_frame, (250, 200, 40), (430, 490, 530, 590), tolerance=30)
            checks.expect(case, "the character is in it (its first frame, warm, at its place)", hero > 2000, hero)
            grabs_by_backend[backend] = first_frame

    if len(grabs_by_backend) == 2:
        mean, large = difference(grabs_by_backend["graph"], grabs_by_backend["cpu"])
        report.setdefault("motion", {})["still_difference"] = {"mean": round(mean, 3), "large_share": round(large, 5)}
        # Measured on the software renderer (2026-11-09): mean 0.015, share
        # 0.0. Both ways place the frames on whole scene pixels and sample
        # without smoothing; the glow is drawn from the same image.
        checks.expect("motion-still", "the two ways draw the same still frame (mean channel difference under 1.5, "
                      "under 1% of channels off by more than 16)", mean < 1.5 and large < 0.01, (mean, large))
        # The whole frame is mostly the plane: the character and the glow,
        # small, are compared where they are.
        for what, box in (("the character", (430, 490, 530, 590)), ("the glow", (760, 340, 1160, 740))):
            mean, large = difference(grabs_by_backend["graph"], grabs_by_backend["cpu"], box)
            checks.expect("motion-still", f"the two ways draw {what} alike (within its box: mean under 3, under 3% "
                          "of channels off by more than 16)", mean < 3 and large < 0.03, (mean, large))

    # A plant bends from its foot: drawn in bands, the same both ways.
    plant_grabs = {}
    for backend in ("graph", "cpu"):
        case = f"motion-bend-{backend}"
        out, probes, grabs = run(case, backend, [
            {"at": 0, "root": plant_root, "motion": plant, "running": True}, {"at": 900, "still": True},
            {"at": 1500, "grab": case}, {"at": 1600, "probe": "end"}, {"at": 1700, "quit": True}])
        end = probes.get("end", {})
        checks.expect(case, "the plant is drawn in its 8 bands (with the plane, the character and the glow: 11)",
                      end.get("things") == 11, end)
        if grabs.get(case, {}).get("path"):
            plant_grabs[backend] = read_ppm(Path(grabs[case]["path"]))
            stem, _ = coloured(plant_grabs[backend], (60, 160, 90), (840, 620, 1080, 980), tolerance=30)
            checks.expect(case, "the plant is in the frame", stem > 1500, stem)
    if len(plant_grabs) == 2:
        mean, large = difference(plant_grabs["graph"], plant_grabs["cpu"])
        checks.expect("motion-bend", "the two ways bend it alike (mean under 1.5, under 1% of channels off by more than 16)",
                      mean < 1.5 and large < 0.01, (mean, large))
        mean, large = difference(plant_grabs["graph"], plant_grabs["cpu"], (840, 620, 1080, 980))
        checks.expect("motion-bend", "and where the plant is (mean under 3, under 3% of channels off by more than 16)",
                      mean < 3 and large < 0.03, (mean, large))

    # Play in focus: after its settle the character goes along its path and
    # stays at its end while the focus does.
    case = "motion-react"
    out, probes, grabs = run(case, "graph", [
        {"at": 0, "root": calm_root, "motion": calm, "running": True}, {"at": 500, "grab": "react-before"},
        {"at": 600, "event": "focus.play"}, {"at": 2600, "grab": "react-after"}, {"at": 2700, "probe": "end"},
        {"at": 2800, "quit": True}])
    before, after = grabs.get("react-before", {}), grabs.get("react-after", {})
    if before.get("path") and after.get("path"):
        a, b = read_ppm(Path(before["path"])), read_ppm(Path(after["path"]))
        def at(image, x, y):
            i = (y * image[0] + x) * 3
            return tuple(image[2][i:i + 3])
        hero = lambda pixel: pixel[0] > 180 and pixel[2] < 120    # a warm frame over the cool stripes
        checks.expect(case, "before: the character at its place, not at the path's end",
                      hero(at(a, 480, 540)) and not hero(at(a, 1440, 540)), (at(a, 480, 540), at(a, 1440, 540)))
        checks.expect(case, "after Play in focus: at the path's end, gone from its place",
                      hero(at(b, 1440, 540)) and not hero(at(b, 480, 540)), (at(b, 480, 540), at(b, 1440, 540)))
    else:
        checks.expect(case, "frames grabbed before and after", False, (before, after))

    depth_cases(shell, checks, exports, packages, out_dir, run, report)
    away_cases(checks, exports, packages, run)
    loading_cases(shell, checks, exports, packages, calm, calm_root, run)
    event_cases(checks, exports, packages, calm, calm_root, run)


def loading_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, calm: dict, calm_root: str, run):
    """What the player loads, and when it will not: an export that is gone,
    an image missing from it, images over their decoded budget, a list of
    things over its bound, and the backend chosen by the test, not by the
    environment it inherits."""
    case = "motion-export-gone"
    out, probes, _ = run(case, "graph", [{"at": 0, "root": str(shell.work / "no-such-export"), "motion": calm, "running": True},
                                         {"at": 1500, "probe": "end"}, {"at": 1600, "quit": True}])
    end = probes.get("end", {})
    checks.expect(case, "its export gone: nothing drawn, and the scene says it failed",
                  end.get("failed") is True and end.get("drawn") is False, end)
    checks.expect(case, "the journal says why", "is not there; nothing of it is drawn" in out, out[-400:])

    case = "motion-image-missing"
    gone = shell.work / "motion-image-missing-export"
    shutil.copytree(calm_root, gone)
    (gone / "actors" / "hero.png").unlink()
    out, probes, _ = run(case, "graph", [{"at": 0, "root": str(gone), "motion": calm, "running": True},
                                         {"at": 1500, "probe": "end"}, {"at": 1600, "quit": True}])
    end = probes.get("end", {})
    checks.expect(case, "an image missing: nothing drawn (not a scene without it), the scene failed",
                  end.get("failed") is True and end.get("drawn") is False, end)
    checks.expect(case, "the journal names it", "actors/hero.png is not in its export" in out, out[-400:])

    # Five flat 2048 x 2048 images: small files, 80 MiB decoded.
    case = "motion-decoded-budget"
    big = shell.work / "motion-big-export"
    (big / "planes").mkdir(parents=True)
    flat = shapetools.encode_png(2048, 2048, [bytes((20, 40, 60, 255)) * 2048] * 2048, alpha=True)
    planes = []
    for i in range(5):
        (big / "planes" / f"p{i}.png").write_bytes(flat)
        planes.append({"image": f"planes/p{i}.png", "z": 0.5, "at": [0, 0], "drift": {"x": 0, "y": 0}, "vary": {}})
    heavy = dict(calm, planes=planes, sheets={}, actors={}, states={}, react={}, lights={})
    out, probes, _ = run(case, "graph", [{"at": 0, "root": str(big), "motion": heavy, "running": True},
                                         {"at": 6000, "probe": "end"}, {"at": 6100, "quit": True}])
    end = probes.get("end", {})
    checks.expect(case, "images over their decoded budget (64 MiB): nothing drawn, the scene failed, the journal "
                  "says why", end.get("failed") is True and end.get("drawn") is False
                  and "its images decode to more than 67108864 bytes" in out, (end, out[-300:]))

    # A list over its bound: twelve drifting things one pixel wide at the
    # native look (the checker refuses them; the player bounds them anyway).
    case = "motion-bound"
    narrow = shell.work / "motion-narrow-export"
    (narrow / "planes").mkdir(parents=True)
    (narrow / "planes" / "dot.png").write_bytes(shapetools.encode_png(1, 1, [bytes((255, 255, 255, 255))], alpha=True))
    rows = [{"image": "planes/dot.png", "z": 0.5, "at": [0, 10 * i], "drift": {"x": 10, "y": 0}, "vary": {}} for i in range(12)]
    native = dict(calm, look={"resolution": None, "colour": 24, "dither": "none", "sampling": "smooth"}, planes=rows,
                  sheets={}, actors={}, states={}, react={}, lights={})
    out, probes, _ = run(case, "graph", [{"at": 0, "root": str(narrow), "motion": native, "running": True},
                                         {"at": 1500, "probe": "end"}, {"at": 1600, "quit": True}])
    end = probes.get("end", {})
    checks.expect(case, "a frame lists 8192 things at most, and the journal says the rest are not drawn",
                  end.get("drawn") and 0 < end.get("things", 0) <= 8192 and "things in a frame" in out, end)

    # The backend is the one the test gives: an inherited choice is not
    # taken (Shell.run clears it).
    case = "motion-inherited"
    os.environ["MUN_SHELL_MOTION_BACKEND"] = "cpu"
    try:
        out, probes, _ = run(case, "graph", [{"at": 0, "root": calm_root, "motion": calm, "running": True},
                                             {"at": 800, "probe": "end"}, {"at": 900, "quit": True}], env={})
    finally:
        del os.environ["MUN_SHELL_MOTION_BACKEND"]
    checks.expect(case, "an inherited MUN_SHELL_MOTION_BACKEND is not passed on (the default, graph, is taken)",
                  probes.get("end", {}).get("backend") == "graph", probes.get("end"))


def event_cases(checks: Checks, exports: Path, packages: Path, calm: dict, calm_root: str, run):
    """Events while held still, events in quick succession, and the same
    events at the same steps giving the same simulation whichever way it is
    drawn (with particles, lights and reactions)."""
    def applied(out):
        return [line for line in out.splitlines() if re.search(r"motion: [a-z.]+ at step", line)]

    # Held still (Reduce motion), the console's events do not pile up: once
    # it moves again only where the focus is applies.
    case = "motion-still-events"
    burst = ["focus.play", "focus.away", "arrive", "launch"] * 100 + ["focus.play"]
    out, probes, _ = run(case, "graph", [{"at": 0, "root": calm_root, "motion": calm, "running": True},
                                         {"at": 800, "still": True}, {"at": 1000, "events": burst},
                                         {"at": 1200, "still": False}, {"at": 2500, "probe": "end"}, {"at": 2600, "quit": True}])
    checks.expect(case, "400 events while still: one applied once it moves again, the focus's latest",
                  [re.search(r"motion: ([a-z.]+) at", line).group(1) for line in applied(out)] == ["focus.play"],
                  applied(out)[:6])

    # The focus coming and going quickly, ending away: the character is
    # back at its place, and nothing piles up.
    case = "motion-quick-focus"
    out, probes, _ = run(case, "graph", [{"at": 0, "root": calm_root, "motion": calm, "running": True}]
                         + [{"at": 600 + 30 * i, "event": "focus.play" if i % 2 == 0 else "focus.away"} for i in range(21)]
                         + [{"at": 1300, "event": "focus.away"}, {"at": 4500, "actor": "hero", "label": "after"},
                            {"at": 4600, "probe": "end"}, {"at": 4700, "quit": True}])
    after = next((a for a in lines(out, "ACTOR") if a.get("label") == "after"), {})
    checks.expect(case, "at most one focus event a step, the last one away", len(applied(out)) <= 22
                  and applied(out) and "focus.away" in applied(out)[-1], applied(out)[-3:])
    checks.expect(case, "the character back at its place, at rest",
                  abs(after.get("x", 0) - 480) < 3 and abs(after.get("y", 0) - 540) < 3, after)

    # The same events at the same steps: the same simulation, drawn either
    # way, with particles coming and going, a light and three reactions.
    document = json.loads((Path(export(exports, insertion(0x906), away_package(packages, "motion-trace-events"))["path"])
                           / shape.MANIFEST).read_text(encoding="utf-8"))
    root = str(exports / f"{insertion(0x906)}.1")
    traces, events = {}, {}
    for backend in ("graph", "cpu"):
        out, probes, _ = run(f"motion-trace-events-{backend}", backend,
                             [{"at": 0, "root": root, "motion": document["motion"], "running": True},
                              {"at": 300, "eventsAt": [["focus.play", 20], ["focus.away", 70], ["focus.play", 110],
                                                       ["launch", 150]]},
                              {"at": 9500, "quit": True}])
        traces[backend] = {m.group(1): m.group(2) for m in re.finditer(r"motion: trace step (\d+) ([0-9a-f]{16})", out)}
        events[backend] = [re.search(r"(motion: [a-z.]+ at step \d+)", line).group(1) for line in applied(out)]
    common = sorted(set(traces["graph"]) & set(traces["cpu"]), key=int)
    checks.expect("motion-trace-events", "the same events at the same steps, either way",
                  len(events["graph"]) == 4 and events["graph"] == events["cpu"], events)
    checks.expect("motion-trace-events", "the same simulation step by step (particles, a light, reactions), either way",
                  len(common) >= 7 and all(traces["graph"][k] == traces["cpu"][k] for k in common),
                  {k: (traces["graph"].get(k), traces["cpu"].get(k)) for k in common})


def away_package(packages: Path, name: str) -> Path:
    """A mun-shape/1.1 package whose Play reaction moves an actor, brightens a
    light and starts an emitter, and whose focus.away reaction puts the light
    and the emitter back (the reef's pattern): over a dark plane, MUN's glow
    at the disc (0.15, bright 0.9), sparks over a square at the top left, a
    warm block that goes 240 pixels right."""
    def rgba_rows(width, height, colour_at):
        return [b"".join(bytes(colour_at(x, y)) for x in range(width)) for y in range(height)]
    plane = shapetools.encode_png(640, 360, rgba_rows(640, 360, lambda x, y: (20, 20, 30, 255)), alpha=True)
    block = shapetools.encode_png(16, 16, rgba_rows(16, 16, lambda x, y: (240, 150, 40, 255)), alpha=True)
    dot = shapetools.encode_png(2, 2, rgba_rows(2, 2, lambda x, y: (255, 255, 255, 255)), alpha=True)
    document = {
        "format": "mun-shape/1.1", "motion_draft": 1,
        "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
                    "accent": "#F2B85C"},
        "scene": {"look": "pixel", "planes": [{"image": "planes/dark.png", "z": 1}]},
        "sheets": {"block": {"image": "sheets/block.png"}, "dot": {"image": "sheets/dot.png"}},
        "actors": {"hero": {"sheet": "block", "at": [480, 800], "z": 0.3}},
        "paths": {"visit": {"points": [[720, 800]], "seconds": 1.0, "ease": "linear"}},
        "states": {"hero.visit": {"path": "visit"}, "glow.bright": {"opacity": 0.9}, "sparks.flow": {"on": True}},
        "emitters": {"sparks": {"sheet": "dot", "region": [200, 200, 200, 200], "rate": 40, "life": [0.4, 0.5],
                                "max": 30, "z": 0.2, "on": False}},
        "lights": {"glow": {"effect": "glow", "at": "disc", "radius": 200, "opacity": 0.15, "z": 0.5}},
        "react": {"focus.play": {"settle": 0.2, "cooldown": 0, "do": {"hero": "visit", "glow": "bright", "sparks": "flow"}},
                  "focus.away": {"glow": "on", "sparks": "off"}},
    }
    files = {"planes/dark.png": plane, "sheets/block.png": block, "sheets/dot.png": dot}
    return write_package(packages / name, document, files)


def away_cases(checks: Checks, exports: Path, packages: Path, run):
    """Play in focus and away, twice: the declared focus.away reaction puts
    the light back to its own opacity and stops the emitter, and the actor
    still goes back to rest."""
    document = json.loads((Path(export(exports, insertion(0x905), away_package(packages, "motion-away"))["path"])
                           / shape.MANIFEST).read_text(encoding="utf-8"))
    away, away_root = document["motion"], str(exports / f"{insertion(0x905)}.1")
    case = "motion-away"
    out, probes, grabs = run(case, "graph", [
        {"at": 0, "root": away_root, "motion": away, "running": True}, {"at": 700, "grab": "away-base"},
        {"at": 900, "event": "focus.play"}, {"at": 2900, "grab": "away-play-1"},
        {"at": 3000, "event": "focus.away"}, {"at": 6200, "grab": "away-away-1"},
        {"at": 6400, "event": "focus.play"}, {"at": 8400, "grab": "away-play-2"},
        {"at": 8500, "event": "focus.away"}, {"at": 11700, "grab": "away-away-2"},
        {"at": 11800, "probe": "end"}, {"at": 11900, "quit": True}])
    frames = {name: read_ppm(Path(g["path"])) for name, g in grabs.items() if g.get("path")}
    if len(frames) != 5:
        checks.expect(case, "five frames grabbed", False, sorted(frames))
        return

    def glow(image):
        width, _, data = image
        i = (540 * width + 960) * 3
        return data[i]

    def sparks(image):
        return coloured(image, (255, 255, 255), (190, 190, 410, 410), tolerance=30)[0]

    def hero_at(image):
        _, box = coloured(image, (240, 150, 40), (300, 740, 1000, 860), tolerance=30)
        return (box[0] + box[2]) / 2 if box[2] >= box[0] else None

    seen = {name: (glow(f), sparks(f), hero_at(f)) for name, f in frames.items()}
    base = seen["away-base"]
    checks.expect(case, "at rest: the light dim, no sparks, the actor at its place",
                  base[0] < 80 and base[1] == 0 and base[2] is not None and abs(base[2] - 480) < 20, base)
    for n in (1, 2):
        play, back = seen[f"away-play-{n}"], seen[f"away-away-{n}"]
        checks.expect(case, f"Play in focus ({n}): the light bright, sparks flowing, the actor at its path's end",
                      play[0] > 140 and play[1] > 0 and play[2] is not None and abs(play[2] - 720) < 20, play)
        checks.expect(case, f"focus away ({n}): the light back to its own opacity and the sparks stopped, "
                      "as the package's focus.away says", back[0] < 80 and back[1] == 0, back)
        checks.expect(case, f"focus away ({n}): the actor back at its place", back[2] is not None and abs(back[2] - 480) < 20,
                      back)


def depth_package(packages: Path, name: str) -> Path:
    """A mun-shape/1.1 package for depth (pixel look, scene.depth far 0.25):
    a sheet of four headings, a square of one colour each (red going right,
    green going away, blue going left, yellow coming near); one actor going
    right, one going away from depth 0 to 1 in 1.2 s; a pair of copies at
    their own places, depths and clips (magenta near, cyan deeper); a white
    block that keeps away from another standing beside it."""
    def rgba_rows(width, height, colour_at):
        return [b"".join(bytes(colour_at(x, y)) for x in range(width)) for y in range(height)]
    plane = shapetools.encode_png(640, 360, rgba_rows(640, 360, lambda x, y: (20, 20, 30, 255)), alpha=True)
    headings = [(220, 40, 40, 255), (40, 200, 60, 255), (40, 80, 230, 255), (230, 210, 40, 255)]
    marks = shapetools.encode_png(32, 64, rgba_rows(32, 64, lambda x, y: headings[y // 16]), alpha=True)
    duo = shapetools.encode_png(32, 16, rgba_rows(32, 16, lambda x, y: (220, 40, 220, 255) if x < 16 else (40, 220, 220, 255)),
                                alpha=True)
    block = shapetools.encode_png(16, 16, rgba_rows(16, 16, lambda x, y: (235, 235, 235, 255)), alpha=True)
    document = {
        "format": "mun-shape/1.1", "motion_draft": 1,
        "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E", "plate": "#04283A", "text": "#EFFFFF",
                    "accent": "#F2B85C"},
        "scene": {"look": "pixel", "depth": {"far": 0.25, "span": 2000}, "planes": [{"image": "planes/dark.png", "z": 1}]},
        "sheets": {"marks": {"image": "sheets/marks.png", "frame": [16, 16], "views": 4, "clips": {"go": [0, 2, 4]}},
                   "duo": {"image": "sheets/duo.png", "frame": [16, 16], "clips": {"a": [0, 1, 1], "b": [1, 1, 1]}},
                   "block": {"image": "sheets/block.png"}},
        "actors": {
            "across": {"sheet": "marks", "face": "views", "at": [200, 200], "z": 0, "rest": "east"},
            "away": {"sheet": "marks", "face": "views", "at": [960, 700], "z": 0, "rest": "recede"},
            "pair": {"sheet": "duo", "at": [960, 420], "z": 0.2,
                     "copies": {"places": [[-200, 0, 0], [200, 0, 0.5]], "clips": ["a", "b"]}},
            "shy": {"sheet": "block", "at": [600, 920], "z": 0.3, "avoid": {"actor": "bully", "radius": 300, "push": 200}},
            "bully": {"sheet": "block", "at": [700, 920], "z": 0.3},
        },
        "paths": {"east": {"points": [[1700, 200, 0]], "seconds": 60, "ease": "linear"},
                  "recede": {"points": [[960, 700, 1]], "seconds": 1.2, "ease": "linear"}},
        "states": {"across.east": {"path": "east"}, "away.recede": {"path": "recede", "then": "stay"}, "away.stay": {}},
    }
    files = {"planes/dark.png": plane, "sheets/marks.png": marks, "sheets/duo.png": duo, "sheets/block.png": block}
    return write_package(packages / name, document, files)


def coloured(image, colour, box, tolerance=40):
    """The pixels of `colour` within `box` (x0, y0, x1, y1): their count and
    their bounding box."""
    width, _, data = image
    x0, y0, x1, y1 = box
    count, found = 0, [x1, y1, x0, y0]
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * width + x) * 3
            if all(abs(data[i + c] - colour[c]) <= tolerance for c in range(3)):
                count += 1
                found = [min(found[0], x), min(found[1], y), max(found[2], x), max(found[3], y)]
    return count, found


def depth_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, out_dir: Path, run, report: dict):
    """Depth: headings shown from the sheet's rows, perspective as a course
    goes deeper, copies at their own places and clips, keeping away; and the
    graph backend's batches on the graphics path (OpenGL through Xvfb, or a
    board's GPU) drawing the reef sample as the composed way does."""
    document = json.loads((Path(export(exports, insertion(0x903), depth_package(packages, "motion-depth"))["path"])
                           / shape.MANIFEST).read_text(encoding="utf-8"))
    depth, depth_root = document["motion"], str(exports / f"{insertion(0x903)}.1")
    traces = {}
    for backend in ("graph", "cpu"):
        case = f"motion-depth-{backend}"
        out, probes, grabs = run(case, backend, [
            {"at": 0, "root": depth_root, "motion": depth, "running": True}, {"at": 2600, "grab": case},
            {"at": 2700, "probe": "end"}, {"at": 2800, "quit": True}])
        traces[backend] = dict(re.findall(r"motion: trace step (\d+) ([0-9a-f]{16})", out))
        if not grabs.get(case, {}).get("path"):
            checks.expect(case, "a frame grabbed", False, (probes, grabs))
            continue
        image = read_ppm(Path(grabs[case]["path"]))
        red, _ = coloured(image, (220, 40, 40), (0, 140, 1920, 260))
        others = sum(coloured(image, c, (0, 140, 1920, 260))[0] for c in ((40, 200, 60), (40, 80, 230), (230, 210, 40)))
        checks.expect(case, "going right it shows the first row (red), no other", red > 400 and others == 0, (red, others))
        green, box = coloured(image, (40, 200, 60), (860, 600, 1060, 800))
        side = box[2] - box[0] + 1
        checks.expect(case, "gone away it shows the row turned away (green), at a quarter of its size at depth 0 "
                      "(16 scene pixels: 48 on the canvas, 12 at depth 1)", green > 0 and 9 <= side <= 15, (green, box))
        magenta, near = coloured(image, (220, 40, 220), (600, 340, 960, 500))
        cyan, deep = coloured(image, (40, 220, 220), (960, 340, 1320, 500))
        checks.expect(case, "each copy at its place with its clip, at its own size where it rests (48 pixels)",
                      magenta > 0 and cyan > 0 and 45 <= near[2] - near[0] + 1 <= 51 and 45 <= deep[2] - deep[0] + 1 <= 51,
                      (near, deep))
        _, shy = coloured(image, (235, 235, 235), (300, 860, 660, 980), tolerance=12)
        checks.expect(case, "kept away from the one beside it (pushed left of its place by over 80 pixels)",
                      shy[2] >= shy[0] and (shy[0] + shy[2]) / 2 < 520, shy)

    # The simulation is the backends' common part: its trace (a digest of
    # every actor, particle and light each second of scene time) is the same.
    common = sorted(set(traces.get("graph", {})) & set(traces.get("cpu", {})), key=int)
    checks.expect("motion-trace", "the scene's trace is the same, step by step, whichever way it is drawn",
                  len(common) >= 2 and all(traces["graph"][k] == traces["cpu"][k] for k in common),
                  {k: (traces["graph"].get(k), traces["cpu"].get(k)) for k in common})

    # The reef sample on the graphics path: its things drawn in a few runs,
    # the still frame as the composed way draws it.
    reef = SAMPLES / "reef"
    if not (reef / shape.MANIFEST).is_file():
        checks.expect("motion-batches", "the reef sample is here", False, str(reef))
        return
    document = json.loads((Path(export(exports, insertion(0x904), reef)["path"]) / shape.MANIFEST).read_text(encoding="utf-8"))
    reef_motion, reef_root = document["motion"], str(exports / f"{insertion(0x904)}.1")
    # (on the graphics path a grab comes with the next frame drawn: time for it)
    steps = [{"at": 0, "root": reef_root, "motion": reef_motion, "still": True, "running": True},
             {"at": 2500, "grab": "STILL"}, {"at": 2600, "probe": "end"}, {"at": 5000, "quit": True}]
    native = shell.platform == "eglfs"
    frames = {}
    with contextlib.nullcontext(False) if native else Xvfb() as server:
        if server is None:
            checks.expect("motion-batches", "Xvfb gives the OpenGL path a display (it is required)", False,
                          "Xvfb is not installed: the graphics path was not drawn")
            return
        for route, backend in (("opengl", "graph"), ("software", "graph"), ("software", "cpu")):
            case = f"motion-batches-{route}-{backend}"
            config = {"steps": [dict(s, grab=case) if s.get("grab") else s for s in steps], "out": str(out_dir)}
            if route == "opengl":
                route_args = {"display": server.display} if server else {}
            else:
                route_args = {"software": True}
            output = shell.run(case, "motion.qml", config, exports=exports, env={"MUN_SHELL_MOTION_BACKEND": backend}, **route_args)
            probes = {m["label"]: m for m in lines(output, "MOTION")}
            grabs = {g["name"]: g for g in lines(output, "GRAB")}
            end = probes.get("end", {})
            checks.expect(case, "the reef is drawn, its hundreds of things listed", end.get("drawn") and end.get("things", 0) > 500, end)
            if route == "opengl":
                checks.expect(case, "on the graphics path its things are drawn in runs of one image (far fewer nodes)",
                              0 < end.get("runs", 0) < end.get("things", 0) / 4, end)
                report.setdefault("motion", {})["reef_runs"] = {"things": end.get("things"), "runs": end.get("runs")}
            checks.expect(case, "its still frame grabbed", bool(grabs.get(case, {}).get("path")), grabs)
            if grabs.get(case, {}).get("path"):
                frames[case] = read_ppm(Path(grabs[case]["path"]))
    gl, sw, cpu = (frames.get(f"motion-batches-{r}") for r in ("opengl-graph", "software-graph", "software-cpu"))
    if sw and cpu:
        mean, large = difference(sw, cpu)
        report.setdefault("motion", {})["reef_nodes_vs_composed"] = {"mean": round(mean, 3), "large_share": round(large, 5)}
    if gl and sw:
        mean, large = difference(gl, sw)
        report.setdefault("motion", {})["reef_batches_vs_nodes"] = {"mean": round(mean, 3), "large_share": round(large, 5)}
        checks.expect("motion-batches", "the batches draw what a node each draws (mean under 1.5, under 1% of channels "
                      "off by more than 16)", mean < 1.5 and large < 0.01, (mean, large))
    if gl and cpu:
        mean, large = difference(gl, cpu)
        report.setdefault("motion", {})["reef_batches_vs_composed"] = {"mean": round(mean, 3), "large_share": round(large, 5)}
        checks.expect("motion-batches", "the batches draw what the composed way draws (mean under 3, under 4% of "
                      "channels off by more than 16: turned things are sampled at different sizes)",
                      mean < 3 and large < 0.04, (mean, large))


def paths_cases(shell: Shell, checks: Checks, report: dict):
    """The software renderer and OpenGL draw the same still screen. OpenGL
    is llvmpipe on an X server from Xvfb, or, with --platform eglfs, a
    board's own GPU."""
    steps = lambda icon: {"icon": "card", "steps": [{"at": 1500, "icon": icon}, {"at": 6000, "grab": icon},
                                                    {"at": 9000, "quit": True}]}
    native = shell.platform == "eglfs"
    with contextlib.nullcontext(False) if native else Xvfb() as server:
        if server is None:
            checks.expect("paths", "Xvfb gives the OpenGL path a display (it is required)", False,
                          "Xvfb is not installed: the OpenGL path was not drawn")
            return
        route_args = {"software": {"software": True}, "opengl": {"display": server.display} if server else {}}
        frames = {}
        for route in ("software", "opengl"):
            for icon in ("none", "settings", "card"):
                name = f"paths-{route}-{icon}"
                config = dict(steps(icon), out=str(shell.work / name))
                (shell.work / name).mkdir(parents=True, exist_ok=True)
                output = shell.run(name, "paths.qml", config, **route_args[route])
                said = [p for p in lines(output, "PATHS") if p["label"] == "renderer"]
                if icon == "none":
                    if route == "software":
                        checks.expect(name, "the software renderer says so, its clock a timer",
                                      bool(said) and said[0]["api"] == "software" and not said[0]["rhi"]
                                      and "motion: on a timer" in output, said)
                    elif native:
                        checks.expect(name, "OpenGL says so, accelerated, its clock Qt's animation driver",
                                      bool(said) and said[0]["api"] == "opengl" and said[0]["rhi"]
                                      and said[0]["accelerated"] and "motion: on the frames" in output, said)
                    else:
                        checks.expect(name, "OpenGL says so and that llvmpipe is not accelerated, its clock a timer",
                                      bool(said) and said[0]["api"] == "opengl" and said[0]["rhi"]
                                      and not said[0]["accelerated"] and "motion: on a timer" in output, said)
                path = shell.work / name / f"{icon}.ppm"
                frames[route, icon] = read_ppm(path) if path.exists() else None
                checks.expect(name, "the still screen is grabbed", frames[route, icon] is not None)
        # Measured with this scene (levels in 255, on average): the network
        # 0.05, the objects 0.03 to 0.09, the panel 0.09, the bar and knob
        # 0.22; the bounds leave a margin, not room for another look.
        regions = {"the whole screen": (None, 0.15, 0.0005),
                   "the panel's surface and shadow": ((1100, 120, 1920, 760), 0.3, 0.0005),
                   "the chosen bar, its glow and the knob": ((940, 700, 1660, 920), 0.6, 0.001),
                   "the object": ((150, 250, 790, 890), 0.3, 0.0005)}
        for icon in ("none", "settings", "card"):
            a, b = frames.get(("software", icon)), frames.get(("opengl", icon))
            if not a or not b or a[:2] != b[:2]:
                continue
            for what, (box, mean, share) in regions.items():
                if icon != "none" and box is None:
                    continue
                if icon == "none" and what == "the object":
                    continue
                got_mean, got_share = difference(a, b, box)
                report.setdefault("paths", {})[f"{icon}: {what}"] = {"mean": round(got_mean, 3), "over16": round(got_share, 5)}
                checks.expect(f"paths-{icon}", f"{what} matches (mean {got_mean:.3f} <= {mean}, over 16 levels "
                              f"{got_share:.4%} <= {share:.2%})", got_mean <= mean and got_share <= share)



def settings_cases(shell: Shell, checks: Checks):
    """The player's settings file (settings.qml)."""
    defaults = {"language": "en", "clockFormat": "24h", "safeArea": 100, "systemSounds": True, "gameSounds": True,
                "shapeMode": "full", "reduceMotion": False, "autoPowerOffHours": 1}

    def run(name, settings, steps, before=None):
        state = shell.work / name / "state"
        state.mkdir(parents=True, exist_ok=True)
        if before:
            before(state)
        output = shell.run(name, "settings.qml", {"steps": steps}, settings=settings)
        return {r["label"]: r for r in lines(output, "SETTINGS")}, state / "settings.ini"

    def values(state):
        return {k: state[k] for k in defaults} if state else None

    def ini(path):
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, configparser.Error):
            return None
        return {f"{section}/{key}": value for section in parser.sections() for key, value in parser[section].items()}

    # A damaged file: the defaults, an error said once, and the next choice
    # writes a valid file with it.
    damaged = b"\x00\xff\xfe[display\nsafe-area=\x01\x02\n\xc3\x28=\n[interface\nlanguage"
    states, path = run("settings-damaged", damaged, [{"set": {"language": "es"}}, {"report": "chosen"}])
    start = states.get("start")
    checks.expect("settings-damaged", "a damaged file reads as the defaults", values(start) == defaults, start)
    written = ini(path)
    checks.expect("settings-damaged", "the next choice writes a valid file that holds it",
                  written is not None and written.get("interface/language") == "es"
                  and states.get("chosen", {}).get("lastError") == "", (written, states.get("chosen")))

    # Values outside the allowed set read as their defaults.
    states, _ = run("settings-out-of-range", "[display]\nsafe-area=55\nreduce-motion=maybe\n"
                    "[interface]\nlanguage=fr\nclock=13h\n[power]\nauto-off-hours=2\n[shape]\nmode=loud\n", [])
    checks.expect("settings-out-of-range", "a value outside the allowed set reads as its default",
                  values(states.get("start")) == defaults, states.get("start"))

    # A change replaces the file whole: a new file renamed over the old one
    # (another inode), so a write cut short leaves the old file as it was. A
    # temporary file such a write left behind is not read.
    opened = {}
    def prepare(state):
        (state / "settings.ini").write_text("[interface]\nclock=12h\n", encoding="utf-8")
        (state / "settings.ini.Xq3Lp9").write_text("[interface]\nlanguage=es\n", encoding="utf-8")
        opened["inode"] = (state / "settings.ini").stat().st_ino
    states, path = run("settings-atomic", None, [{"report": "read"}, {"set": {"safeArea": 94}}, {"report": "chosen"}],
                       before=prepare)
    read = states.get("read", {})
    checks.expect("settings-atomic", "a temporary file left by an interrupted write is ignored",
                  read.get("language") == "en" and read.get("clockFormat") == "12h", read)
    written = ini(path)
    checks.expect("settings-atomic", "a change keeps the rest of the file and adds itself",
                  written == {"interface/clock": "12h", "display/safe-area": "94"}, written)
    checks.expect("settings-atomic", "the change is a new file renamed over the old one, never written in place",
                  path.exists() and path.stat().st_ino != opened.get("inode"), opened)

    # Reset settings: every choice back to its default but the language;
    # the resolution's key goes too (Automatic).
    chosen = ("[interface]\nlanguage=es\nclock=12h\n[display]\nsafe-area=91\nreduce-motion=on\n"
              "resolution=1920x1080\n[audio]\nsystem-sounds=off\ngame-sounds=off\n[power]\nauto-off-hours=6\n"
              "[shape]\nmode=off\n")
    states, path = run("settings-reset", chosen, [{"report": "before"}, {"call": "resetKeepingLanguage"},
                                                  {"report": "after"}])
    before = states.get("before")
    checks.expect("settings-reset", "the choices are read before the reset",
                  before is not None and before["language"] == "es" and before["safeArea"] == 91
                  and before["shapeMode"] == "off" and not before["systemSounds"], before)
    after = values(states.get("after"))
    checks.expect("settings-reset", "Reset settings keeps the language and returns everything else to its default",
                  after == dict(defaults, language="es"), after)
    checks.expect("settings-reset", "the file keeps the language alone", ini(path) == {"interface/language": "es"},
                  ini(path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--work", type=Path, help="keep the scenes, logs, grabs and report here")
    parser.add_argument("--platform", choices=("offscreen", "eglfs"), default="offscreen",
                        help="offscreen with the software renderer (default), or eglfs: a board's display and GPU, "
                             "with the console's shell service stopped first")
    parser.add_argument("--only", choices=("card", "cue", "surfaces", "presence", "world", "memory", "boot", "home", "settings",
                                           "motion", "paths"),
                        action="append")
    parser.add_argument("--timing", choices=("check", "record"), default="check",
                        help="judge how long things take (check, the default, on a steady machine), or measure and "
                             "record them without judging (record: a busy builder); what happens is always judged")
    args = parser.parse_args(argv)
    binary = args.binary.resolve()
    if not os.access(binary, os.X_OK):
        print(f"{binary} is not an executable", file=sys.stderr)
        return 2
    work = args.work.resolve() if args.work else Path(tempfile.mkdtemp(prefix="mun-shell-behaviour."))
    if work.exists() and any(work.iterdir()):
        print(f"{work} is not empty", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    exports, packages = work / "exports", work / "packages"
    exports.mkdir()
    packages.mkdir()
    shell, checks, report = Shell(binary, work, args.platform), Checks(), {}
    checks.timing = args.timing
    only = set(args.only or ("card", "cue", "surfaces", "presence", "world", "memory", "boot", "home", "settings", "motion",
                             "paths"))
    try:
        if "card" in only:
            print("MUN Shell behaviour: the card object")
            card_cases(shell, checks, exports, packages)
        if "cue" in only:
            print("MUN Shell behaviour: the insertion cue")
            cue_cases(shell, checks, exports, packages)
        if "surfaces" in only:
            print("MUN Shell behaviour: the dressed surfaces' contrast")
            surface_cases(shell, checks, exports, packages, report)
        if "presence" in only:
            print("MUN Shell behaviour: presence (phases, reasons, what waits)")
            presence_cases(shell, checks, exports, packages)
        if "world" in only:
            print("MUN Shell behaviour: the world")
            world_cases(shell, checks, exports, packages)
        if "memory" in only:
            print("MUN Shell behaviour: the world's memory at 1080p and 1440p")
            memory_cases(shell, checks, exports, packages, report)
        if "boot" in only:
            print("MUN Shell behaviour: the start-up, as a boot plays it")
            boot_cases(shell, checks)
        if "home" in only:
            # The same moments three ways, each required: the previous design
            # on the software renderer, kept as the reference it is compared
            # with; the orbital design on the software renderer, as the
            # virtual console draws it; and the orbital design through
            # OpenGL, as a console with a GPU draws it (an X server of its
            # own here, a board's display and GPU there). A way that cannot
            # run fails: it does not count as passed.
            print("MUN Shell behaviour: Home, as a player meets it (the previous design, software renderer)")
            home_cases(shell, checks, exports, packages, report)
            for tag, api, title in (("orbital-sw", "software", "the orbital design, software renderer"),
                                    ("orbital-gl", "opengl", "the orbital design, OpenGL")):
                print(f"MUN Shell behaviour: Home, as a player meets it ({title})")
                folder = work / tag
                (folder / "exports").mkdir(parents=True)
                (folder / "packages").mkdir()
                checks.tag = tag + "/"
                try:
                    if api == "software" or args.platform == "eglfs":
                        home_cases(Shell(binary, folder, args.platform), checks, folder / "exports", folder / "packages",
                                   report.setdefault(tag, {}), design="orbital", api=api)
                    else:
                        with Xvfb() as server:
                            if server is None:
                                checks.expect("home", "Xvfb gives the OpenGL way a display (it is required)", False,
                                              "Xvfb is not installed: Home in the orbital design through OpenGL was not run")
                            else:
                                home_cases(Shell(binary, folder, args.platform), checks, folder / "exports",
                                           folder / "packages", report.setdefault(tag, {}), design="orbital", api=api,
                                           display=server.display)
                finally:
                    checks.tag = ""
        if "settings" in only:
            print("MUN Shell behaviour: the player's settings file")
            settings_cases(shell, checks)
        if "motion" in only:
            print("MUN Shell behaviour: the Shape motion player (draft)")
            motion_cases(shell, checks, exports, packages, report)
        if "paths" in only:
            print("MUN Shell behaviour: the software and OpenGL drawing paths")
            paths_cases(shell, checks, report)
        report["durations"] = {"timing": checks.timing, "recorded": checks.timings}
        (work / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        for folder in checks.failing:
            log = work / folder / "output.log"
            if log.exists():
                print(f"--- {folder}: the shell's output (last 40 lines)")
                print("\n".join(log.read_text(encoding="utf-8").splitlines()[-40:]))
    finally:
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)
    outside = sum(not t["within"] for t in checks.timings)
    print(f"MUN Shell behaviour: {checks.passed} passed, {checks.failed} failed"
          + (f", {checks.skipped} skipped" if checks.skipped else "")
          + (f"; {len(checks.timings)} durations recorded, {outside} outside their bounds (not judged here)"
             if checks.timing == "record" else ""))
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
