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
  option is the only focus.

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

    def run(self, name: str, scene: str, config: dict, runtime: Path = None, socket_path: Path = None,
            exports: Path = None, timeout: float = 90) -> str:
        case = self.work / name
        qml = case / "qml"
        qml.mkdir(parents=True, exist_ok=True)
        text = (HERE / "scenes" / scene).read_text(encoding="utf-8")
        (qml / "Main.qml").write_text(text.replace("CONFIG", json.dumps(config)), encoding="utf-8")
        runtime = runtime or case / "runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_SCALE_FACTOR="1",
                   QT_FORCE_STDERR_LOGGING="1", LANG="C.UTF-8", HOME=str(case / "home"),
                   XDG_CONFIG_HOME=str(case / "config"), XDG_RUNTIME_DIR=str(runtime),
                   MUN_SHELL_QML_DIR=str(qml), MUN_SHAPE_ROOT=str(exports or self.work / "exports"),
                   MUN_CARDD_SOCKET=str(socket_path or case / "no-service.sock"))
        for key in ("STATE_DIRECTORY", "QT_QPA_KMS_CONFIG", "QT_QPA_EGLFS_KMS_CONFIG", "WAYLAND_DISPLAY", "DISPLAY"):
            env.pop(key, None)
        try:
            done = subprocess.run([str(self.binary)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=timeout, cwd=case)
            output = done.stdout.decode("utf-8", "replace")
            if done.returncode:
                output += f"\n(exit status {done.returncode})"
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode("utf-8", "replace") + "\n(timed out)"
        (case / "output.log").write_text(output, encoding="utf-8")
        return output


def lines(output: str, tag: str) -> list:
    return [json.loads(m.group(1)) for m in re.finditer(rf"{tag} (\{{.*\}})\s*$", output, re.M)]


class Service:
    """A stand-in for the card service on its socket: one script per
    connection, each a list of (seconds after the connection, message);
    a message "close" ends that connection."""

    def __init__(self, path: Path, scripts: list):
        self.path, self.scripts = path, scripts
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.stopping = False
        self.held = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        for script in self.scripts:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            start = time.monotonic()
            for at, message in script:
                time.sleep(max(0.0, start + at - time.monotonic()))
                if self.stopping:
                    return
                if message == "close":
                    conn.close()
                    break
                conn.sendall((json.dumps(message) + "\n").encode("utf-8"))
            else:
                self.held.append(conn)

    def close(self):
        self.stopping = True
        self.server.close()
        for conn in self.held:
            conn.close()


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
        for world in ("white", "black"):
            before, after = results.get(f"{world}-home"), results.get(f"{world}-home-again")
            if before and after:
                same = all(abs(before["entries"][k]["ratio"] - after["entries"][k]["ratio"]) < 0.05
                           for k in before["entries"])
                checks.expect(f"surfaces-{name} {world}", "leaving the options restores Home as it was", same,
                              (before["entries"], after["entries"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--work", type=Path, help="keep the scenes, logs, grabs and report here")
    parser.add_argument("--only", choices=("card", "cue", "surfaces"), action="append")
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
    only = set(args.only or ("card", "cue", "surfaces"))
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
