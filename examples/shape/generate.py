#!/usr/bin/env python3
"""Generate the two sample MUN Shape packages, sea and paper, from code.

Every image and sound of both packages is drawn or synthesised here, with
Python's standard library and fixed seeds, so the art is this repository's
own and anyone can remake it:

    python3 examples/shape/generate.py            # (re)write sea/ and paper/
    python3 examples/shape/generate.py --check    # compare with the files in the repository

`--check` compares decoded pixels, not PNG bytes: zlib builds may compress
the same pixels differently. Sounds and shape.json are compared byte for byte.
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools" / "mun-card"))

from mun_card import shape, shapetools  # noqa: E402

RATE = shape.SOUND_RATE
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def clamp(value, low=0, high=255):
    return low if value < low else high if value > high else value


def hex_rgb(colour):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_colour(stops, t):
    """Colour at t in 0–1 along evenly spaced stops."""
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    index = min(int(t), len(stops) - 2)
    local = t - index
    return tuple(lerp(a, b, local) for a, b in zip(stops[index], stops[index + 1]))


def profile(width, base, waves, seed):
    """A horizon that tiles across `width`: a base height plus sines of whole
    periods (count, amplitude) with seeded phases."""
    rng = random.Random(seed)
    phases = [(count, amplitude, rng.uniform(0, 2 * math.pi)) for count, amplitude in waves]
    return [base + sum(a * math.sin(2 * math.pi * k * x / width + p) for k, a, p in phases) for x in range(width)]


def rgb_rows(width, height, pixel):
    """Rows of RGB bytes from pixel(x, y) -> (r, g, b) floats, dithered by
    an ordered 4×4 matrix so gradients do not band."""
    rows = []
    for y in range(height):
        row = bytearray(width * 3)
        dither = BAYER[y & 3]
        for x in range(width):
            r, g, b = pixel(x, y)
            d = dither[x & 3] / 16 - 0.47
            row[x * 3] = clamp(int(r + d + 0.5))
            row[x * 3 + 1] = clamp(int(g + d + 0.5))
            row[x * 3 + 2] = clamp(int(b + d + 0.5))
        rows.append(bytes(row))
    return rows


def rgba_rows(width, height, pixel):
    """Rows of RGBA bytes from pixel(x, y) -> (r, g, b, a) floats; colour is
    kept constant under transparent pixels so rows compress."""
    rows = []
    for y in range(height):
        row = bytearray(width * 4)
        for x in range(width):
            r, g, b, a = pixel(x, y)
            a = clamp(int(a + 0.5))
            if a:
                row[x * 4:x * 4 + 4] = bytes((clamp(int(r + 0.5)), clamp(int(g + 0.5)), clamp(int(b + 0.5)), a))
        rows.append(bytes(row))
    return rows


def supersampled(width, height, inside, colour, samples=4):
    """An RGBA sprite: coverage of inside(x, y) (in sprite pixels) averaged
    over samples×samples points per pixel; colour(x, y) -> (r, g, b, a)."""
    def pixel(x, y):
        hits = 0
        for sy in range(samples):
            for sx in range(samples):
                if inside(x + (sx + 0.5) / samples, y + (sy + 0.5) / samples):
                    hits += 1
        if not hits:
            return (0, 0, 0, 0)
        r, g, b, a = colour(x + 0.5, y + 0.5)
        return (r, g, b, a * hits / (samples * samples))
    return rgba_rows(width, height, pixel)


# ------------------------------------------------------------------- sea

SEA_STOPS = [hex_rgb(c) for c in ("#8FDDEB", "#2A9DBA", "#0B5A78", "#03263A", "#02141E")]


def sea_backdrop(width=1920, height=1080):
    # Light from the surface, a little left of centre: a separable glow.
    gx = [math.exp(-((x - width * 0.38) / (width * 0.34)) ** 2) for x in range(width)]
    gy = [math.exp(-(y / (height * 0.42)) ** 2) for y in range(height)]
    rows_colour = [lerp_colour(SEA_STOPS, (y / (height - 1)) ** 0.9) for y in range(height)]
    glow = (60, 50, 34)
    return rgb_rows(width, height, lambda x, y: tuple(
        base + amount * gx[x] * gy[y] for base, amount in zip(rows_colour[y], glow)))


def sea_reliefs(width, height, base, waves, seed, body, edge, alpha, strands=0):
    """Rock silhouettes that tile across the width, lit along their ridge,
    darker with depth, with optional weed strands swaying up from them."""
    horizon = profile(width, base, waves, seed)
    rng = random.Random(seed + 1)
    weeds = [(rng.uniform(0, width), rng.uniform(90, 260), rng.uniform(40, 90), rng.uniform(0, 6.28), rng.uniform(9, 16))
             for _ in range(strands)]
    body, edge = hex_rgb(body), hex_rgb(edge)
    top = int(min(horizon) - max((w[1] for w in weeds), default=0)) - 2
    rows = []
    for y in range(height):
        row = bytearray(width * 4)
        if y >= top:
            shade = 1 - min(0.35, (y - base) / height * 0.8) if y > base else 1
            dark = bytes(clamp(int(c * shade + 0.5)) for c in body)
            lit = bytes(clamp(int(c * shade + 0.5)) for c in edge)
            for root, tall, wavelength, phase, thick in weeds:
                ground = horizon[int(root) % width]
                if ground - tall <= y < ground:
                    rise = (ground - y) / tall
                    centre = root + 10 * math.sin(y / wavelength + phase) * rise
                    half = thick * (1 - rise * 0.7) / 2
                    for x in range(int(centre - half - 1), int(centre + half + 2)):
                        cover = min(1.0, half + 0.5 - abs(x + 0.5 - centre))
                        if cover > 0:
                            i = (x % width) * 4
                            row[i:i + 3] = bytes(body)
                            row[i + 3] = max(row[i + 3], clamp(int(alpha * 0.9 * cover + 0.5)))
            for x in range(width):
                depth = y - horizon[x]
                if depth >= -0.5:
                    i = x * 4
                    row[i:i + 3] = lit if depth < 7 else dark
                    row[i + 3] = max(row[i + 3], clamp(int(alpha * min(1.0, depth + 0.5) + 0.5)))
        rows.append(bytes(row))
    return rows


def sea_rays(width=960, height=540):
    ox, oy = width * 0.36, -height * 0.35
    rng = random.Random(7)
    beams = [(math.pi / 2 + (k - 3) * 0.16 + rng.uniform(-0.03, 0.03), 0.018 + (k % 3) * 0.008) for k in range(7)]

    def pixel(x, y):
        angle = math.atan2(y - oy, x - ox)
        light = sum(math.exp(-((angle - centre) / spread) ** 2) for centre, spread in beams)
        fade = (1 - y / height) ** 1.6
        return (230, 255, 255, min(1.0, light) * fade * 200)
    return rgba_rows(width, height, pixel)


def sea_caustics(width=480, height=270):
    a, b, c = 2 * math.pi * 5 / width, 2 * math.pi * 3 / height, 2 * math.pi * 4 / width
    d, e = 2 * math.pi * 3 / width, 2 * math.pi * 2 / height

    def pixel(x, y):
        s = math.sin(a * x + 1.6 * math.sin(b * y)) + math.sin(b * y + 1.4 * math.sin(c * x)) \
            + math.sin(d * x + e * y)
        line = math.exp(-(s * s) * 2.2)
        return (220, 250, 255, line * line * 190)
    return rgba_rows(width, height, pixel)


def sea_fish(width=64, height=24):
    def inside(x, y):
        body = ((x - 36) / 24) ** 2 + ((y - 12) / 8) ** 2 <= 1
        tail = 4 <= x <= 16 and abs(y - 12) <= (16 - x) * 0.75 + 1 and abs(y - 12) >= (16 - x) * 0.1
        return body or tail

    def colour(x, y):
        if (x - 52) ** 2 + (y - 10) ** 2 < 3:
            return (10, 40, 55, 255)
        back = max(0.0, min(1.0, (14 - y) / 8))
        return (lerp(210, 120, back), lerp(246, 205, back), lerp(250, 222, back), 235)
    return supersampled(width, height, inside, colour)


def sea_bubble(size=24):
    centre = size / 2

    def inside(x, y):
        r = math.hypot(x - centre, y - centre)
        return size * 0.30 <= r <= size * 0.42 or math.hypot(x - centre * 0.75, y - centre * 0.7) <= size * 0.08

    return supersampled(size, size, inside, lambda x, y: (225, 252, 255, 200))


def sea_window(size=512):
    rays = []
    rng = random.Random(11)
    for k in range(5):
        rays.append((math.pi / 2 + (k - 2) * 0.22 + rng.uniform(-0.04, 0.04), 0.05))
    fish = [(rng.uniform(80, 430), rng.uniform(170, 330), rng.uniform(0.6, 1.0)) for _ in range(9)]

    def pixel(x, y):
        r, g, b = lerp_colour(SEA_STOPS, (y / (size - 1)) ** 0.8)
        angle = math.atan2(y + size * 0.3, x - size * 0.45)
        light = sum(math.exp(-((angle - c) / s) ** 2) for c, s in rays) * (1 - y / size) ** 1.4
        r, g, b = r + 90 * light, g + 80 * light, b + 60 * light
        for fx, fy, scale in fish:
            if ((x - fx) / (22 * scale)) ** 2 + ((y - fy) / (7 * scale)) ** 2 <= 1 or \
                    (fx - 34 * scale <= x <= fx - 18 * scale and abs(y - fy) <= (fx - 18 * scale - x) * 0.5 + 1):
                return (lerp(r, 3, 0.75), lerp(g, 30, 0.75), lerp(b, 44, 0.75))
        if y > size * 0.82 + 18 * math.sin(x / 37) + 9 * math.sin(x / 13):
            return (3, 30, 44)
        return (r, g, b)
    return rgb_rows(size, size, pixel)


# ------------------------------------------------------------------ paper

PAPER = hex_rgb("#F2EADB")


def paper_backdrop(width=960, height=540):
    rng = random.Random(21)
    grain = [[rng.uniform(-3.2, 3.2) for _ in range(width // 2 + 1)] for _ in range(height // 2 + 1)]
    fibres = [(rng.randrange(height), rng.randrange(width), rng.randrange(20, 90), rng.uniform(-6, -2))
              for _ in range(420)]
    marks = {}
    for y, x0, length, depth in fibres:
        for x in range(x0, min(width, x0 + length)):
            marks[(x, y)] = depth
    blot_x = [math.sin(2 * math.pi * 2 * x / width + 1.1) + 0.5 * math.sin(2 * math.pi * 5 * x / width) for x in range(width)]
    blot_y = [math.sin(2 * math.pi * 1 * y / height + 0.4) + 0.5 * math.sin(2 * math.pi * 3 * y / height) for y in range(height)]

    def pixel(x, y):
        shade = 2.2 * blot_x[x] * blot_y[y] + grain[y // 2][x // 2] + marks.get((x, y), 0)
        warm = 1.2 * (y / height)
        return (PAPER[0] + shade, PAPER[1] + shade - warm, PAPER[2] + shade - 2 * warm)
    return rgb_rows(width, height, pixel)


def ink_mountains(width, height, base, waves, seed, ink, alpha, mist, dry=0.0):
    horizon = profile(width, base, waves, seed)
    rng = random.Random(seed + 5)
    brush = [rng.uniform(0, 1) for _ in range(width)]
    # Smooth the brush noise along x (tileable) so dry strokes run, not speckle.
    brush = [sum(brush[(x + k) % width] for k in range(-14, 15)) / 29 for x in range(width)]
    ink = hex_rgb(ink)

    def pixel(x, y):
        depth = y - horizon[x]
        if depth < 0:
            return (0, 0, 0, 0)
        coverage = min(1.0, depth + 0.5)
        # Ink is darkest at the ridge and dissolves into mist below it.
        fade = math.exp(-depth / mist)
        stroke = 1 - dry * max(0.0, brush[x] - 0.5) * max(0.0, 1 - depth / 24)
        return (ink[0], ink[1], ink[2], alpha * coverage * (0.25 + 0.75 * fade) * stroke)
    return rgba_rows(width, height, pixel)


def paper_speck(size=12):
    centre = size / 2
    return supersampled(size, size, lambda x, y: math.hypot(x - centre, (y - centre) * 1.2) <= size * 0.33,
                        lambda x, y: (40, 36, 32, 210))


def paper_petal(width=24, height=16):
    def inside(x, y):
        u, v = (x - width / 2) / (width * 0.46), (y - height / 2) / (height * 0.40)
        return abs(v) <= (1 - u * u) ** 1.4 if abs(u) < 1 else False

    return supersampled(width, height, inside,
                        lambda x, y: (lerp(200, 170, y / height), lerp(66, 44, y / height), lerp(46, 34, y / height), 230))


def paper_window(size=512):
    horizon_far = profile(size, size * 0.66, ((1, 18), (3, 10), (7, 4)), 31)
    horizon_near = profile(size, size * 0.80, ((2, 16), (5, 7), (11, 3)), 37)

    def pixel(x, y):
        r, g, b = PAPER
        sun = math.hypot(x - size * 0.62, y - size * 0.36)
        if sun <= size * 0.17:
            edge = min(1.0, size * 0.17 - sun)
            r, g, b = lerp(r, 184, edge), lerp(g, 57, edge), lerp(b, 42, edge)
        for horizon, ink, strength in ((horizon_far, (142, 148, 154), 0.6), (horizon_near, (46, 42, 38), 0.9)):
            depth = y - horizon[x]
            if depth >= 0:
                amount = strength * min(1.0, depth + 0.5) * (0.35 + 0.65 * math.exp(-depth / 60))
                r, g, b = lerp(r, ink[0], amount), lerp(g, ink[1], amount), lerp(b, ink[2], amount)
        return (r, g, b)
    return rgb_rows(size, size, pixel)


# ----------------------------------------------------------------- sounds

def normalise(left, right, peak_dbfs=-3.0):
    top = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1
    gain = 32767 * 10 ** (peak_dbfs / 20) / top
    return shapetools.encode_wav([(int(round(l * gain)), int(round(r * gain))) for l, r in zip(left, right)])


def envelope(n, total, attack, release):
    return min(1.0, n / max(1, attack), (total - n) / max(1, release))


def bloop(seconds, start, end, delay=14):
    total = int(seconds * RATE)
    phase, left = 0.0, []
    for n in range(total):
        t = n / total
        phase += 2 * math.pi * lerp(start, end, t ** 0.6) / RATE
        left.append(math.sin(phase) * math.exp(-4.5 * t) * envelope(n, total, 96, 480))
    right = [0.0] * delay + left[:-delay]
    return left, right


def mix(*parts):
    length = max(len(p[0]) + offset for p, offset in parts)
    left, right = [0.0] * length, [0.0] * length
    for (l, r), offset in parts:
        for i, (a, b) in enumerate(zip(l, r)):
            left[offset + i] += a
            right[offset + i] += b
    return left, right


def chime(seconds, frequencies, decay, pan=0.0):
    total = int(seconds * RATE)
    left, right = [], []
    for n in range(total):
        t = n / RATE
        value = sum(math.sin(2 * math.pi * f * t) / (i + 1) for i, f in enumerate(frequencies))
        value *= math.exp(-t / decay) * envelope(n, total, 48, 960)
        left.append(value * (1 - pan))
        right.append(value * (1 + pan))
    return left, right


def sea_insert():
    total = int(2.4 * RATE)
    rng = random.Random(41)
    low, left, right = 0.0, [], []
    for n in range(total):
        t = n / RATE
        swell = min(1.0, t / 0.7) * math.exp(-max(0.0, t - 0.7) / 0.8)
        low = low * 0.985 + rng.uniform(-1, 1) * 0.015            # a slow wash of filtered noise
        tone = 0.55 * math.sin(2 * math.pi * 110 * t) + 0.35 * math.sin(2 * math.pi * 165 * t + 0.6)
        value = swell * (tone + 2.2 * low) * envelope(n, total, 1, 2400)
        left.append(value)
        right.append(value * 0.94 + 0.06 * tone * swell)
    sound = (left, right)
    parts = [(sound, 0)]
    for k in range(9):
        start = rng.uniform(900, 2400)
        parts.append((tuple([v * 0.35 for v in channel] for channel in bloop(0.07, start, start * 1.6, rng.randrange(4, 30))),
                      int(rng.uniform(0.5, 1.9) * RATE)))
    parts.append((chime(1.2, (1318.5, 1975.5), 0.45, pan=0.2), int(0.9 * RATE)))
    return mix(*parts)


def noise_burst(seconds, bright, seed, swell=False):
    rng = random.Random(seed)
    total = int(seconds * RATE)
    slow = fast = 0.0
    left, right = [], []
    for n in range(total):
        sample = rng.uniform(-1, 1)
        slow += (sample - slow) * 0.08
        fast += (sample - fast) * bright
        band = fast - slow
        t = n / total
        shape_ = math.sin(math.pi * t) ** 1.5 if swell else math.exp(-7 * t)
        value = band * shape_ * envelope(n, total, 24, 240)
        left.append(value)
        right.append(value * (0.9 if n % 2 else 1.0))
    return left, right


def pluck(frequency, seconds, seed, brightness=0.5):
    """Karplus–Strong: a noise burst circulating in a damped delay line."""
    rng = random.Random(seed)
    period = int(RATE / frequency)
    line = [rng.uniform(-1, 1) for _ in range(period)]
    out = []
    for n in range(int(seconds * RATE)):
        value = line[n % period]
        following = line[(n + 1) % period]
        line[n % period] = 0.996 * (brightness * value + (1 - brightness) * following)
        out.append(value)
    return out


def paper_insert():
    notes = [(293.66, 0.0, 0.2), (440.0, 0.28, -0.2), (587.33, 0.62, 0.05), (392.0, 0.95, -0.1)]
    parts = []
    for index, (frequency, start, pan) in enumerate(notes):
        tone = pluck(frequency, 1.5, 50 + index)
        total = len(tone)
        tone = [v * envelope(n, total, 1, 4800) for n, v in enumerate(tone)]
        parts.append((([v * (1 - pan) for v in tone], [v * (1 + pan) for v in tone]), int(start * RATE)))
    parts.append((noise_burst(0.5, 0.35, 59, swell=True), 0))
    return mix(*parts)


# --------------------------------------------------------------- packages

def sea():
    return {
        "shape.json": {
            "format": "mun-shape/1",
            "palette": {"light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E",
                        "plate": "#04283A", "text": "#EFFFFF", "accent": "#F2B85C"},
            "card": {"window": "card/window.png", "shape": "organic", "morph": 0.6, "glow": "#9FE7F2"},
            "world": {
                "backdrop": {"image": "world/backdrop.png"},
                "layers": [
                    {"image": "world/reliefs-far.png", "motion": "drift", "speed": 8, "depth": 0.2, "opacity": 0.9},
                    {"image": "world/reliefs-near.png", "motion": "drift", "speed": 20, "depth": 0.6},
                ],
                "emitters": [
                    {"sprite": "world/fish.png", "count": 18, "path": "school", "speed": 45, "band": [0.25, 0.6]},
                    {"sprite": "world/bubble.png", "count": 40, "path": "rise", "speed": 36, "band": [0.1, 1], "scale": 0.8},
                ],
                "light": [
                    {"texture": "world/rays.png", "blend": "screen", "motion": "sway", "opacity": 0.55},
                    {"texture": "world/caustics.png", "blend": "screen", "motion": "ripple", "opacity": 0.35},
                ],
                "rate": 20,
            },
            "surfaces": {"entries": {"material": "glass"}, "panel": {"material": "glass"}},
            "transition": {"in": "tide", "out": "tide", "seconds": 3.2},
            "sounds": {"move": "sfx/move.wav", "enter": "sfx/enter.wav", "back": "sfx/back.wav",
                       "insert": "sfx/insert.wav"},
        },
        "world/backdrop.png": (sea_backdrop, False),
        "world/reliefs-far.png": (lambda: sea_reliefs(1920, 1080, 760, ((1, 40), (2, 26), (3, 18), (5, 12), (8, 6), (13, 3)),
                                                      3, "#0E5A70", "#1B7A90", 200), True),
        "world/reliefs-near.png": (lambda: sea_reliefs(1920, 1080, 905, ((1, 60), (2, 30), (4, 20), (7, 9), (11, 5), (17, 2)),
                                                       5, "#03283A", "#0B4A5E", 245, strands=14), True),
        "world/fish.png": (sea_fish, True),
        "world/bubble.png": (sea_bubble, True),
        "world/rays.png": (sea_rays, True),
        "world/caustics.png": (sea_caustics, True),
        "card/window.png": (sea_window, False),
        "sfx/move.wav": lambda: normalise(*bloop(0.10, 620, 930)),
        "sfx/enter.wav": lambda: normalise(*mix((bloop(0.08, 520, 1040), 0), (bloop(0.08, 700, 1400, 20), int(0.06 * RATE)),
                                                (chime(0.32, (1318.5, 1975.5), 0.12), int(0.1 * RATE)))),
        "sfx/back.wav": lambda: normalise(*bloop(0.13, 900, 480)),
        "sfx/insert.wav": lambda: normalise(*sea_insert()),
    }


def paper():
    return {
        "shape.json": {
            "format": "mun-shape/1",
            "palette": {"light": "#FBF7EE", "mid": "#BDB3A2", "deep": "#2B2825",
                        "plate": "#F3ECDF", "text": "#1F1C19", "accent": "#B8392A"},
            "card": {"window": "card/window.png", "shape": "card", "morph": 0.15, "glow": "#F0D9B5"},
            "world": {
                "backdrop": {"image": "world/backdrop.png"},
                "layers": [
                    {"image": "world/mountains-far.png", "motion": "parallax", "depth": 0.2},
                    {"image": "world/mountains-near.png", "motion": "drift", "speed": 6, "depth": 0.7},
                ],
                "emitters": [
                    {"sprite": "world/speck.png", "count": 30, "path": "drift", "speed": 12},
                    {"sprite": "world/petal.png", "count": 10, "path": "fall", "speed": 20, "band": [0, 0.9]},
                ],
                "rate": 10,
            },
            "surfaces": {"entries": {"material": "paper"}, "panel": {"material": "paper"}},
            "transition": {"in": "sweep", "out": "fade", "seconds": 1.6},
            "sounds": {"move": "sfx/move.wav", "enter": "sfx/enter.wav", "back": "sfx/back.wav",
                       "insert": "sfx/insert.wav"},
        },
        "world/backdrop.png": (paper_backdrop, False),
        "world/mountains-far.png": (lambda: ink_mountains(1920, 1080, 640, ((1, 70), (2, 45), (3, 30), (6, 12), (9, 6)),
                                                          61, "#8E949A", 150, 170), True),
        "world/mountains-near.png": (lambda: ink_mountains(1920, 1080, 820, ((1, 50), (2, 40), (4, 22), (7, 10), (13, 4)),
                                                           67, "#2E2A26", 235, 90, dry=0.9), True),
        "world/speck.png": (paper_speck, True),
        "world/petal.png": (paper_petal, True),
        "card/window.png": (paper_window, False),
        "sfx/move.wav": lambda: normalise(*mix((noise_burst(0.03, 0.6, 71), 0), (chime(0.03, (1800,), 0.006), 0))),
        "sfx/enter.wav": lambda: normalise(*mix((noise_burst(0.22, 0.3, 73, swell=True), 0),
                                                (chime(0.12, (700, 1050), 0.03), int(0.2 * RATE)))),
        "sfx/back.wav": lambda: normalise(*noise_burst(0.11, 0.75, 79)),
        "sfx/insert.wav": lambda: normalise(*paper_insert()),
    }


PACKAGES = {"sea": sea, "paper": paper}


def build(name):
    """{relative path: bytes} for one package, plus the decoded rows of each
    image for --check."""
    files, pixels = {}, {}
    for relative, recipe in PACKAGES[name]().items():
        if relative == "shape.json":
            files[relative] = shapetools.dump(recipe).encode("utf-8")
        elif relative.endswith(".png"):
            make, alpha = recipe
            rows = make()
            width = len(rows[0]) // (4 if alpha else 3)
            files[relative] = shapetools.encode_png(width, len(rows), rows, alpha)
            pixels[relative] = rows
        else:
            files[relative] = recipe()
    return files, pixels


def _decoded(data):
    width, height, rows = shapetools.decode_png(data)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with the files on disk instead of writing")
    parser.add_argument("names", nargs="*", metavar="NAME", help=f"packages to make: {', '.join(PACKAGES)} (default all)")
    args = parser.parse_args()
    unknown = [name for name in args.names if name not in PACKAGES]
    if unknown:
        parser.error(f"unknown package {', '.join(unknown)}; known: {', '.join(PACKAGES)}")
    failures = 0
    for name in args.names or PACKAGES:
        files, _ = build(name)
        folder = HERE / name
        for relative, data in sorted(files.items()):
            path = folder / relative
            if args.check:
                if not path.is_file():
                    print(f"missing  {name}/{relative}")
                    failures += 1
                    continue
                current = path.read_bytes()
                same = _decoded(current) == _decoded(data) if relative.endswith(".png") else current == data
                print(f"{'same    ' if same else 'DIFFERS '} {name}/{relative}")
                failures += 0 if same else 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                print(f"wrote {name}/{relative} ({len(data)} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
