#!/usr/bin/env python3
"""The laboratory's virtual display, as the console sees a real one: an EDID.

QEMU's virtio-gpu describes itself with an EDID of its own making whose
modes follow its window (`xres`/`yres`); at 1920x1080 it lists neither
2560x1440 nor 1280x720. The development image gives the kernel this EDID
instead (`drm.edid_firmware=`, qemu-dev profile): a display that prefers
1920x1080 and also takes 2560x1440 and 1280x720, the three resolutions MUN
Shell offers. Its modes are then the connector's own, as a television's
are, so the shell sets them from the list and a game that asks for the
current mode finds it there (docs/runtime.md, "Display").

    lab_edid.py OUT     write the 128-byte EDID 1.3 block
"""

import sys
from pathlib import Path

# (pixel clock kHz, h active, h front porch, h sync, h back porch,
#  v active, v front porch, v sync, v back porch, +hsync, +vsync)
PREFERRED_1080P = (148500, 1920, 88, 44, 148, 1080, 4, 5, 36, True, True)    # CEA-861 VIC 16
QHD_1440P = (241500, 2560, 48, 32, 80, 1440, 3, 5, 33, True, False)          # VESA CVT reduced blanking
HD_720P = (74250, 1280, 110, 40, 220, 720, 5, 5, 20, True, True)             # CEA-861 VIC 4
SIZE_MM = (597, 336)                                                         # a 27-inch 16:9 panel


def detailed_timing(timing) -> bytes:
    clock, ha, hfp, hs, hbp, va, vfp, vs, vbp, hpos, vpos = timing
    hb, vb = hfp + hs + hbp, vfp + vs + vbp
    w, h = SIZE_MM
    flags = 0x18 | (0x04 if vpos else 0) | (0x02 if hpos else 0)    # digital separate sync
    return bytes([
        (clock // 10) & 0xFF, (clock // 10) >> 8,
        ha & 0xFF, hb & 0xFF, ((ha >> 8) << 4) | (hb >> 8),
        va & 0xFF, vb & 0xFF, ((va >> 8) << 4) | (vb >> 8),
        hfp & 0xFF, hs & 0xFF, ((vfp & 0x0F) << 4) | (vs & 0x0F),
        ((hfp >> 8) << 6) | ((hs >> 8) << 4) | ((vfp >> 4) << 2) | (vs >> 4),
        w & 0xFF, h & 0xFF, ((w >> 8) << 4) | (h >> 8),
        0, 0, flags,
    ])


def name_descriptor(name: str) -> bytes:
    text = name.encode("ascii")[:13]
    return bytes([0, 0, 0, 0xFC, 0]) + (text + b"\n" + b" " * 13)[:13]


def edid() -> bytes:
    maker = (ord("M") - 64) << 10 | (ord("U") - 64) << 5 | (ord("N") - 64)
    block = bytearray()
    block += bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    block += maker.to_bytes(2, "big") + (1).to_bytes(2, "little") + (0).to_bytes(4, "little")
    block += bytes([0, 2026 - 1990, 1, 3])                   # no week, 2026, EDID 1.3
    block += bytes([0x80, SIZE_MM[0] // 10, SIZE_MM[1] // 10, 120, 0x0A])   # digital, cm, gamma 2.2, RGB + preferred in DTD 1
    block += bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])   # sRGB chromaticity
    block += bytes([0, 0, 0])                                # no established timings
    block += bytes([0x01, 0x01] * 8)                         # no standard timings
    block += detailed_timing(PREFERRED_1080P) + detailed_timing(QHD_1440P) + detailed_timing(HD_720P)
    block += name_descriptor("MUN lab")
    block += bytes([0])                                      # no extensions
    block += bytes([(-sum(block)) & 0xFF])
    assert len(block) == 128
    return bytes(block)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        raise SystemExit(64)
    Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1]).write_bytes(edid())
