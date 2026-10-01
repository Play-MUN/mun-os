#!/usr/bin/env python3
"""Behaviour regressions of MUN Shell's MUN Shape code, on the real binary.

    python3 tests/behaviour.py BUILD_DIR/mun-shell [--work DIR] [--only NAME]

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
  frames in motion, still and at rest, its steps down, a layer that does
  not decode.
- memory: what a world takes at its peak, the shell's own resident memory
  measured (wait4) against the engine's estimate at 1080p and 1440p, for
  worlds that keep every limit but scale large or decode larger, and a
  sample stepped down to still; how the extreme ones look.
- home: the shell's own Main.qml (home.qml) against stand-ins for the card
  service and the launcher: an arrival, dialogs, a return, removal, Eject
  safely confirmed and refused, another card, the player's choices, a
  defective world, Settings; and, with the options focused, a package that
  arrives late, Eject safely, a changed choice and another card, frame by
  frame (a dressed entry whole on every frame); an arrival after Settings
  or a dialog only once Home is wholly on screen.

Linux, with the shell's run-time libraries (the image's). The image build
runs this after compiling the shell (os/mkosi/mkosi.build.chroot); exit
status 1 if any expectation fails. --work keeps the scenes, logs, grabs and
the measurements (report.json) there.
"""
import argparse
import base64
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
    def __init__(self, binary: Path, work: Path):
        self.binary, self.work = binary, work
        self.peak_kib = {}   # each run's peak resident memory (the kernel's ru_maxrss), by name

    def run(self, name: str, scene: str, config: dict, runtime: Path = None, socket_path: Path = None,
            exports: Path = None, timeout: float = 90, launcher: Path = None, settings: str = "",
            scale: float = 1) -> str:
        """Runs the shell with `scene` and returns its output. `scale` is the
        display's device pixels per canvas pixel (4/3: 2560x1440)."""
        case = self.work / name
        qml = case / "qml"
        qml.mkdir(parents=True, exist_ok=True)
        text = (HERE / "scenes" / scene).read_text(encoding="utf-8")
        (qml / "Main.qml").write_text(text.replace("CONFIG", json.dumps(config)), encoding="utf-8")
        runtime = runtime or case / "runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = case / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "settings.ini").write_text(settings, encoding="utf-8")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_SCALE_FACTOR=repr(scale),
                   QT_FORCE_STDERR_LOGGING="1", LANG="C.UTF-8", HOME=str(case / "home"),
                   XDG_CONFIG_HOME=str(case / "config"), XDG_RUNTIME_DIR=str(runtime),
                   MUN_SHELL_QML_DIR=str(qml), MUN_SHAPE_ROOT=str(exports or self.work / "exports"),
                   MUN_CARDD_SOCKET=str(socket_path or case / "no-service.sock"),
                   MUN_LAUNCHD_SOCKET=str(launcher or case / "no-launcher.sock"), STATE_DIRECTORY=str(state))
        for key in ("QT_QPA_KMS_CONFIG", "QT_QPA_EGLFS_KMS_CONFIG", "WAYLAND_DISPLAY", "DISPLAY", "MUN_SHELL_TIMING"):
            env.pop(key, None)
        # Reaped with wait4, which also gives this process's own peak memory.
        log = case / "output.log"
        with open(log, "wb") as sink:
            process = subprocess.Popen([str(self.binary)], env=env, stdout=sink, stderr=subprocess.STDOUT, cwd=case)
            deadline, timed_out = time.monotonic() + timeout, False
            while True:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    break
                if time.monotonic() > deadline:
                    process.kill()
                    pid, status, usage = os.wait4(process.pid, 0)
                    timed_out = True
                    break
                time.sleep(0.05)
            process.returncode = os.waitstatus_to_exitcode(status)
        self.peak_kib[name] = usage.ru_maxrss
        output = log.read_text(encoding="utf-8", errors="replace")
        if timed_out:
            output += "\n(timed out)"
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
    connection that is open."""

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
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

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
            start = time.monotonic()
            for at, message in script:
                time.sleep(max(0.0, start + at - time.monotonic()))
                if self.stopping:
                    return
                if message == "close":
                    with self.lock:
                        self.current = None
                    conn.close()
                    break
                self.push(message)
            else:
                self.held.append(conn)

    def close(self):
        self.stopping = True
        self.server.close()
        for conn in self.held:
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
        self.failing = []   # the cases' folders, for their logs

    def expect(self, case: str, what: str, ok: bool, detail=""):
        if ok:
            self.passed += 1
            print(f"  ok    {case}: {what}", flush=True)
        else:
            self.failed += 1
            print(f"  FAIL  {case}: {what} {detail}", flush=True)
            folder = case.split(" ")[0]
            if folder not in self.failing:
                self.failing.append(folder)


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


def home_cases(shell: Shell, checks: Checks, exports: Path, packages: Path, report: dict):
    """The shell's own Main.qml, against stand-ins for the card service and
    the launcher: the moments of docs/shape.md, "How the console uses a
    package"."""
    slow = sample_copy(packages, "home-slow", "sea", change=lambda d: d["transition"].update(seconds=4.0))
    broken = sample_copy(packages, "home-broken", "sea", replace={first_layer("sea"): short_png()})
    counter = iter(range(0x500, 0x600))

    def card(package):
        number = insertion(next(counter))
        return number, record(number, export(exports, number, package))

    def serve(name, scripts, launcher=None, answer=None):
        return Service(shell.work / f"{name}.sock", scripts), \
               Service(shell.work / f"{name}-launch.sock", launcher or [[(0, {"type": "snapshot", "state": "idle"})]], answer)

    def run(name, cardd, launchd, steps, grabs=(), settings="", end=9000, trace=False):
        out_dir = shell.work / name / "grabs"
        out_dir.mkdir(parents=True, exist_ok=True)
        runtime = shell.work / name / "runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        (runtime / "started").write_text("")   # after a game: no start-up
        try:
            output = shell.run(name, "home.qml", {"steps": list(steps) + [{"at": end, "quit": True}], "grabs": list(grabs),
                                                  "out": str(out_dir), "trace": trace},
                               runtime=runtime, socket_path=cardd.path, launcher=launchd.path, exports=exports,
                               settings=settings, timeout=end / 1000 + 60)
        finally:
            cardd.close()
            launchd.close()
        return output, [p for p in lines(output, "PHASE")], {g["name"]: g for g in lines(output, "GRAB")}

    def first(phases, since=None, **match):
        # The first line matching, after `since` (a line) if given.
        for p in phases:
            if since is not None and p["t"] <= since["t"]:
                continue
            if all(p.get(k) == v for k, v in match.items()):
                return p
        return None

    def surfaces_hold(case, grab, name, dressed_only=False):
        # dressed_only: MUN's entries, dimmed while the options have the
        # focus, are MUN's own look, not measured here.
        if not grab or not grab.get("path"):
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
        {"name": "tide-70", "phase": "entering", "at": 0.7}, {"name": "present", "phase": "present"}], end=7500)
    entering, present = first(phases, phase="entering"), first(phases, phase="present")
    checks.expect("home-arrival", "an arrival comes in with the package's tide",
                  entering is not None and entering["entry"] == "arrival" and entering["kind"] == "tide", entering)
    took = present["t"] - entering["t"] if entering and present else None
    checks.expect("home-arrival", "the tide takes the package's 3.2 s", took is not None and 2600 <= took <= 4400, took)
    checks.expect("home-arrival", "adopted as an arrival, its cue due", [a["live"] for a in lines(out, "ADOPTED")] == [True],
                  lines(out, "ADOPTED"))
    cues = [s for s in lines(out, "SOUND") if s["name"] == "insert"]
    checks.expect("home-arrival", "the cue plays once, as the tide begins",
                  len(cues) == 1 and entering is not None and cues[0]["phase"] == "entering"
                  and abs(cues[0]["t"] - entering["t"]) <= 300, (cues, entering and entering["t"]))
    for name in ("tide-20", "tide-45", "tide-70", "present"):
        surfaces_hold("home-arrival", grabs.get(name), name)

    # A dialog open: the arrival waits for Home.
    x, rx = card(SAMPLES / "sea")
    result = {"type": "snapshot", "state": "idle", "last_result": {"session": "s1", "reason": "exited", "title": "Test"}}
    cardd, launchd = serve("home-dialog", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]], [[(0, result)]])
    out, phases, _ = run("home-dialog", cardd, launchd, [{"at": 4000, "call": "dismissResult"}], end=7000)
    ready, entering = first(phases, phase="ready"), first(phases, phase="entering")
    checks.expect("home-dialog", "with the session's dialog open the identity waits, ready",
                  ready is not None and ready["modal"] and entering is not None and entering["t"] >= 4000, (ready, entering))

    # Back from a game under the same dialog: a short fade, at once.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-return", [[(0, snapshot(rx))]], [[(0, result)]])
    out, phases, _ = run("home-return", cardd, launchd, [], end=4000)
    entering, present = first(phases, phase="entering"), first(phases, phase="present")
    checks.expect("home-return", "found at start (after a game): a short fade, even under a dialog",
                  entering is not None and entering["entry"] == "return" and entering["kind"] == "fade" and entering["modal"]
                  and present is not None and present["t"] - entering["t"] <= 1200, (entering, present))
    checks.expect("home-return", "no cue", [a["live"] for a in lines(out, "ADOPTED")] == [False]
                  and not [s for s in lines(out, "SOUND") if s["name"] == "insert"], lines(out, "ADOPTED"))

    # Removed while the tide comes in: back to MUN at once, the way it came.
    x, rx = card(slow)
    cardd, launchd = serve("home-removal", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                             (2.8, {"type": "removed", "slot": "c1", "insertion": x})]])
    out, phases, grabs = run("home-removal", cardd, launchd, [], grabs=[{"name": "leaving", "phase": "leaving", "at": 0.99}],
                             end=6000)
    leaving = first(phases, phase="leaving")
    gone = first(phases, since=leaving, phase="none") if leaving else None
    checks.expect("home-removal", "removed mid-tide: it leaves (removal) by the same tide, from where it was",
                  leaving is not None and leaving["exit"] == "removal" and leaving["kind"] == "tide" and leaving["progress"] < 1,
                  leaving)
    checks.expect("home-removal", "MUN within half a second", gone is not None and leaving is not None
                  and gone["t"] - leaving["t"] <= 800 and gone["source"] == "none", (leaving, gone))

    # Eject safely: the identity stays until the release is confirmed, then leaves.
    def eject(ok):
        x, rx = card(SAMPLES / "sea")
        cardd = Service(shell.work / f"home-eject-{ok}.sock", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])

        def answer(service, message):
            if message.get("type") == "release":
                time.sleep(0.3)
                if ok:
                    cardd.push({"type": "card", "card": dict(rx, state="released", info=None)})
                    service.push({"type": "released", "ok": True, "slot": "c1", "serial": rx["serial"]})
                else:
                    service.push({"type": "released", "ok": False, "slot": "c1", "serial": rx["serial"],
                                  "error": {"code": "card_busy", "message": "La tarjeta sigue en uso; no la retires"}})
        launchd = Service(shell.work / f"home-eject-{ok}-launch.sock", [[(0, {"type": "snapshot", "state": "idle"})]], answer)
        out, phases, _ = run(f"home-eject-{'ok' if ok else 'busy'}", cardd, launchd,
                             [{"at": 5500, "probe": "before"}, {"at": 5600, "call": "eject"}, {"at": 5700, "probe": "asked"},
                              {"at": 8800, "probe": "later"}], end=9200)
        return out, phases, launchd

    out, phases, launchd = eject(True)
    asked, leaving = first(phases, label="asked"), first(phases, phase="leaving")
    gone = first(phases, since=leaving, phase="none") if leaving else None
    checks.expect("home-eject-ok", "asked to eject, the identity stays until the release is confirmed",
                  asked is not None and asked["phase"] == "present" and any(m.get("type") == "release" for m in launchd.received),
                  (asked, launchd.received))
    checks.expect("home-eject-ok", "confirmed: it leaves (release) with the package's out tide, 2.4 s",
                  leaving is not None and leaving["exit"] == "release" and leaving["kind"] == "tide" and gone is not None
                  and 1800 <= gone["t"] - leaving["t"] <= 3400, (leaving, gone))
    out, phases, launchd = eject(False)
    later = first(phases, label="later")
    checks.expect("home-eject-busy", "a release that fails keeps the identity",
                  later is not None and later["phase"] == "present" and first(phases, phase="leaving") is None, later)

    # Another card: the first leaves, the second comes in with its own transition.
    x, rx = card(SAMPLES / "sea")
    y, ry = card(SAMPLES / "paper")
    cardd, launchd = serve("home-change", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                            (5.2, {"type": "removed", "slot": "c1", "insertion": x}),
                                            (5.3, {"type": "card", "card": ry})]])
    out, phases, _ = run("home-change", cardd, launchd, [], end=10000)
    left = [p for p in phases if p["phase"] in ("leaving", "none", "ready", "entering", "present")]
    second = [p for p in left if p["insertion"] == y and p["phase"] == "entering"]
    checks.expect("home-change", "A leaves (removal); B then comes in (arrival, its sweep)",
                  first(phases, phase="leaving", exit="removal") is not None and second and second[0]["entry"] == "arrival"
                  and second[0]["kind"] == "sweep", [(p["phase"], p["insertion"][-3:], p["exit"], p["kind"]) for p in left])

    # The player's choices.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-reduce-motion", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-reduce-motion", cardd, launchd, [{"at": 4000, "probe": "still-1"}, {"at": 5500, "probe": "still-2"}],
                         settings="[display]\nreduce-motion=on\n", end=6000)
    entering = first(phases, phase="entering")
    still = [first(phases, label=f"still-{i}") for i in (1, 2)]
    checks.expect("home-reduce-motion", "Reduce motion: a fade instead of the tide, and a still world",
                  entering is not None and entering["kind"] == "fade" and all(still)
                  and still[0]["still"] and still[1]["frames"] - still[0]["frames"] <= 1, (entering, still))

    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-colours", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-colours", cardd, launchd, [{"at": 5000, "probe": "after"}], settings="[shape]\nmode=colours\n",
                         end=5500)
    after = first(phases, label="after")
    checks.expect("home-colours", "Colours only: the identity comes in without a world",
                  after is not None and after["phase"] == "present" and not after["world"] and not after["worldDrawn"], after)

    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-off", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-off", cardd, launchd, [{"at": 3000, "probe": "after"}], settings="[shape]\nmode=off\n", end=3500)
    checks.expect("home-off", "Off: MUN alone", all(p["phase"] == "none" and p["source"] == "none" for p in phases), phases)

    x, rx = card(broken)
    cardd, launchd = serve("home-broken", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-broken", cardd, launchd, [{"at": 5000, "probe": "after"}], end=5500)
    after = first(phases, label="after")
    checks.expect("home-broken", "a world that does not decode: the identity comes in, the palette over MUN's world",
                  after is not None and after["phase"] == "present" and after["worldFailed"] and not after["worldDrawn"], after)

    # Settings over the world, and the options focused, measured.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-settings", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-settings", cardd, launchd, [
        {"at": 5000, "call": "enter"}, {"at": 6200, "grab": "options"}, {"at": 6500, "call": "back"},
        {"at": 6700, "set": {"mainIndex": 2}}, {"at": 6900, "call": "enter"}, {"at": 8200, "grab": "settings"},
        {"at": 8400, "probe": "settings"}], end=8800)
    surfaces_hold("home-settings", grabs.get("options"), "options focused")
    surfaces_hold("home-settings", grabs.get("settings"), "Settings over the scrimmed world")
    settings = first(phases, label="settings")
    checks.expect("home-settings", "in Settings the world is under MUN's scrim",
                  settings is not None and settings["level"] == 1 and settings["dim"] > 0.7, settings)

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
                                       (1.6, {"type": "card", "card": rx})]])
        grabs_asked = [{"name": "first", "first": "shaped"}] + [{"name": g, "phase": "entering", "at": a} for g, a in grab_points] \
            + [{"name": "present", "phase": "present"}]
        out, phases, grabs = run(name, cardd, launchd, [{"at": 1100, "call": "enter"}, {"at": 1300, "probe": "focused"}],
                                 grabs=grabs_asked, end=7500, trace=True)
        focused, entering = first(phases, label="focused"), first(phases, phase="entering")
        checks.expect(name, "the options focused before the package was ready; then it comes in",
                      focused is not None and focused["level"] == 2 and focused["phase"] == "none"
                      and entering is not None and entering["level"] == 2, (focused, entering))
        whole_when_dressed(name, out)
        for g in ["first"] + [g for g, _ in grab_points] + ["present"]:
            surfaces_hold(name, grabs.get(g), g, dressed_only=True)

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
                             [{"at": 5000, "call": "enter"}, {"at": 5400, "probe": "focused"}, {"at": 5600, "call": "eject"}],
                             grabs=[{"name": f"leaving-{int(a * 100)}", "phase": "leaving", "at": a} for a in (0.8, 0.5, 0.2)],
                             end=9500, trace=True)
    leaving = first(phases, phase="leaving")
    checks.expect("home-leave-options", "Eject safely from the options: it leaves (release)",
                  (first(phases, label="focused") or {}).get("level") == 2 and leaving is not None
                  and leaving["exit"] == "release",
                  (first(phases, label="focused"), leaving))
    whole_when_dressed("home-leave-options", out)
    eases_to_mun("home-leave-options", out)
    for a in (80, 50, 20):
        surfaces_hold("home-leave-options", grabs.get(f"leaving-{a}"), f"leaving-{a}", dressed_only=True)

    # A changed choice with the options focused: colours only, off, full again.
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-mode-options", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]])
    out, phases, grabs = run("home-mode-options", cardd, launchd, [
        {"at": 5000, "call": "enter"}, {"at": 5600, "settings": {"shapeMode": "colours"}}, {"at": 7600, "probe": "colours"},
        {"at": 7700, "settings": {"shapeMode": "off"}}, {"at": 9200, "probe": "off"},
        {"at": 9300, "settings": {"shapeMode": "full"}}, {"at": 11300, "probe": "full"}],
        grabs=[{"name": "leaving", "phase": "leaving", "at": 0.5, "after": 5600},
               {"name": "colours-in", "phase": "entering", "at": 0.3, "after": 5600},
               {"name": "full-in", "phase": "entering", "at": 0.5, "after": 9300}], end=11800, trace=True)
    probes = {k: first(phases, label=k) for k in ("colours", "off", "full")}
    checks.expect("home-mode-options", "colours only, off and full again, the options focused throughout",
                  all(probes.values()) and [probes[k]["level"] for k in probes] == [2, 2, 2]
                  and probes["colours"]["phase"] == "present" and not probes["colours"]["world"]
                  and probes["off"]["source"] == "none" and probes["full"]["phase"] == "present" and probes["full"]["world"],
                  probes)
    whole_when_dressed("home-mode-options", out)
    eases_to_mun("home-mode-options", out)
    for g in ("leaving", "colours-in", "full-in"):
        surfaces_hold("home-mode-options", grabs.get(g), g, dressed_only=True)

    # Another card with the options focused: the first leaves, the second comes.
    x, rx = card(SAMPLES / "sea")
    y, ry = card(SAMPLES / "paper")
    cardd, launchd = serve("home-change-options", [[(0, snapshot()), (0.6, {"type": "card", "card": rx}),
                                                    (5.6, {"type": "removed", "slot": "c1", "insertion": x}),
                                                    (5.7, {"type": "card", "card": ry})]])
    out, phases, grabs = run("home-change-options", cardd, launchd, [{"at": 5000, "call": "enter"}, {"at": 5400, "probe": "focused"}],
                             grabs=[{"name": f"b-{int(a * 100)}", "phase": "entering", "at": a, "after": 5700} for a in (0.3, 0.6)]
                             + [{"name": "b-present", "phase": "present", "after": 7000}], end=9500, trace=True)
    second = [p for p in phases if p["insertion"] == y and p["phase"] == "entering"]
    checks.expect("home-change-options", "from the options: A leaves, B comes in",
                  (first(phases, label="focused") or {}).get("level") == 2 and bool(second),
                  (first(phases, label="focused"), second[:1]))
    whole_when_dressed("home-change-options", out)
    eases_to_mun("home-change-options", out)
    for g in ("b-30", "b-60", "b-present"):
        surfaces_hold("home-change-options", grabs.get(g), g, dressed_only=True)

    # An arrival waits for Home wholly on screen: back from Settings (its arc
    # fading in), a dialog closed (its layer fading out).
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-from-settings", [[(0, snapshot()), (1.5, {"type": "card", "card": rx})]])
    out, phases, _ = run("home-from-settings", cardd, launchd, [
        {"at": 800, "set": {"mainIndex": 2}}, {"at": 900, "call": "enter"}, {"at": 3000, "call": "back"}],
        end=7000, trace=True)
    entering, dressed = first(phases, phase="entering"), first_dressed(out)
    checks.expect("home-from-settings", "inserted in Settings: it comes in once Home's arc is whole (0.45 s after)",
                  entering is not None and entering["t"] >= 3400 and entering["entry"] == "arrival", entering)
    whole_when_dressed("home-from-settings", out)
    x, rx = card(SAMPLES / "sea")
    cardd, launchd = serve("home-dialog-closed", [[(0, snapshot()), (0.6, {"type": "card", "card": rx})]], [[(0, result)]])
    out, phases, _ = run("home-dialog-closed", cardd, launchd, [{"at": 4000, "call": "dismissResult"}], end=7500, trace=True)
    entering, dressed = first(phases, phase="entering"), first_dressed(out)
    checks.expect("home-dialog-closed", "the dialog closed: it comes in once its layer has faded (0.5 s after)",
                  entering is not None and entering["t"] >= 4400 and dressed is not None and not dressed["modalShown"],
                  (entering, dressed))
    whole_when_dressed("home-dialog-closed", out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--work", type=Path, help="keep the scenes, logs, grabs and report here")
    parser.add_argument("--only", choices=("card", "cue", "surfaces", "presence", "world", "memory", "home"), action="append")
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
    shell, checks, report = Shell(binary, work), Checks(), {}
    only = set(args.only or ("card", "cue", "surfaces", "presence", "world", "memory", "home"))
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
        if "home" in only:
            print("MUN Shell behaviour: Home, as a player meets it")
            home_cases(shell, checks, exports, packages, report)
        (work / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        for folder in checks.failing:
            log = work / folder / "output.log"
            if log.exists():
                print(f"--- {folder}: the shell's output (last 40 lines)")
                print("\n".join(log.read_text(encoding="utf-8").splitlines()[-40:]))
    finally:
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)
    print(f"MUN Shell behaviour: {checks.passed} passed, {checks.failed} failed")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
