#!/usr/bin/env python3
"""MUN Shell's standby picture: what the console shows from the moment its
system can draw until the shell does (deploy/mun-standby). It is the shell's
own first frame at the first start of a boot, the start-up's layer in
standby (qml/BootLayer.qml: the dark screen, the LED lit), made by the
shell just built, so that nothing changes on screen when the shell takes
the display over.

    standby-frame.py SHELL OUT

SHELL is the compiled mun-shell; it runs offscreen with Qt Quick's software
renderer and deploy/standby.qml in place of Main.qml. OUT gets 1920x1080
pixels in the framebuffer's XRGB8888 order (blue, green, red, unused byte),
top row first, 7680 bytes a row: 8 294 400 bytes. It fails, writing
nothing, when the shell does not make the frame or the frame is not the
standby one (a dark screen, its LED lit).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 1920, 1080
HERE = Path(__file__).resolve().parent


def read_ppm(path):
    """(width, height, RGB bytes) of a binary PPM (P6, 8 bits a channel)."""
    data = path.read_bytes()
    fields, at = [], 0
    while len(fields) < 4:
        while data[at:at + 1].isspace():
            at += 1
        if data[at:at + 1] == b"#":
            at = data.index(b"\n", at) + 1
            continue
        end = at
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[at:end])
        at = end
    if fields[0] != b"P6" or fields[3] != b"255":
        raise ValueError(f"{path}: not an 8-bit binary PPM")
    width, height = int(fields[1]), int(fields[2])
    pixels = data[at + 1:at + 1 + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError(f"{path}: {len(pixels)} bytes of pixels for {width}x{height}")
    return width, height, pixels


def main(argv):
    if len(argv) != 3:
        print("usage: standby-frame.py SHELL OUT", file=sys.stderr)
        return 64
    shell, out = Path(argv[1]).resolve(), Path(argv[2])
    with tempfile.TemporaryDirectory(prefix="mun-standby-") as work:
        work = Path(work)
        qml, runtime, state = work / "qml", work / "runtime", work / "state"
        qml.mkdir()
        runtime.mkdir(mode=0o700)
        state.mkdir()
        grabbed = work / "standby.ppm"
        scene = (HERE / "standby.qml").read_text(encoding="utf-8")
        (qml / "Main.qml").write_text(scene.replace("OUT", json.dumps(str(grabbed))), encoding="utf-8")
        # Sounds off: the frame is the same, and nothing looks for a device.
        (state / "settings.ini").write_text("[audio]\nsystem-sounds=off\n", encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("QT_", "MUN_")) and key not in ("DISPLAY", "WAYLAND_DISPLAY")}
        environment.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_SCALE_FACTOR="1",
                           QT_FORCE_STDERR_LOGGING="1", LANG="C.UTF-8", HOME=str(work),
                           XDG_CONFIG_HOME=str(work / "config"), XDG_RUNTIME_DIR=str(runtime),
                           STATE_DIRECTORY=str(state), MUN_SHELL_QML_DIR=str(qml),
                           MUN_SHAPE_ROOT=str(work / "exports"), MUN_CARDD_SOCKET=str(work / "no-service.sock"),
                           MUN_LAUNCHD_SOCKET=str(work / "no-launcher.sock"))
        try:
            run = subprocess.run([str(shell)], env=environment, cwd=work, capture_output=True, text=True,
                                 timeout=120)
            output = run.stdout + run.stderr
        except subprocess.TimeoutExpired as expired:
            output = (expired.stdout or b"").decode("utf-8", "replace") if isinstance(expired.stdout, bytes) else ""
            print("standby-frame.py: the shell did not finish in 120 s\n" + output[-2000:], file=sys.stderr)
            return 1
        if "STANDBY-SAVED" not in output or not grabbed.exists():
            print("standby-frame.py: the shell made no standby frame\n" + output[-2000:], file=sys.stderr)
            return 1
        width, height, rgb = read_ppm(grabbed)
    if (width, height) != (WIDTH, HEIGHT):
        print(f"standby-frame.py: the frame is {width}x{height}, not {WIDTH}x{HEIGHT}", file=sys.stderr)
        return 1
    # What a standby frame looks like: dark corners, an amber LED somewhere.
    corners = [rgb[i:i + 3] for i in (0, (WIDTH - 1) * 3, (HEIGHT - 1) * WIDTH * 3, (WIDTH * HEIGHT - 1) * 3)]
    reds, greens, blues = rgb[0::3], rgb[1::3], rgb[2::3]
    lit = max(range(0, WIDTH * HEIGHT, 7), key=lambda i: reds[i])
    if any(max(c) > 24 for c in corners) or reds[lit] < 160 or not reds[lit] > greens[lit] > blues[lit]:
        print(f"standby-frame.py: not a standby frame (corners {[tuple(c) for c in corners]}, "
              f"brightest {tuple(rgb[lit * 3:lit * 3 + 3])})", file=sys.stderr)
        return 1
    xrgb = bytearray(WIDTH * HEIGHT * 4)
    xrgb[0::4], xrgb[1::4], xrgb[2::4] = blues, greens, reds
    partial = out.with_name(out.name + ".partial")
    partial.write_bytes(xrgb)
    partial.replace(out)
    print(f"standby-frame.py: {out}: {WIDTH}x{HEIGHT} XRGB8888, the LED at its brightest "
          f"{tuple(rgb[lit * 3:lit * 3 + 3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
