"""MUN Shape motion (format mun-shape/1.1, draft): the blocks that make a
package's scene move, checked and normalised for the shell.

docs/shape-motion.md is the contract. Everything here is provisional until
it is measured on the console: the limits and the budget are starting
points, not promises.

A package that declares motion keeps its v1 blocks as its look on consoles
that only read v1; those consoles ignore the blocks below (a later minor
version) and never read their files. This module is the reading of a
console that knows them. The motion is one aspect: a defect anywhere in it
drops all of it, with a note, and the v1 look stays. It never raises for a
defect, and it reads headers only, like the rest of the checker.

The normalised result has every default filled in, every inline sheet
hoisted into `sheets`, and every reference resolved, so the shell's player
needs no rule of its own to read it.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from . import shape

BLOCKS = ("scene", "sheets", "actors", "paths", "states", "emitters", "lights", "beats", "react")
# The draft's revision. A package with motion says which revision of this
# draft it was written for (`motion_draft`, a top-level integer, outside the
# motion blocks so that consoles reading only v1 ignore it as an unknown
# field); this checker reads the motion only when it is this one, and drops
# it otherwise, the v1 look staying. The final 1.1 will read motion only
# from packages without the key, so no draft package is read by rules it was
# not written for. Raise it whenever a change would read a draft package
# differently.
DRAFT_KEY = "motion_draft"
DRAFT = 1

# Provisional limits (docs/shape-motion.md, "Limits").
MAX_PLANES = 8
MAX_SHEETS = 16
MAX_ACTORS = 24
MAX_COPIES = 160
MAX_COPIES_EACH = 64
MAX_PATHS = 24
MAX_POINTS = 32
MAX_STATES = 48
MAX_EMITTERS = 4
MAX_PARTICLES = 320
MAX_VIEWS = 32
MAX_LIGHTS = 4
MAX_BEATS = 12
MAX_TARGETS = 4
MOTION_MAX_BYTES = 32 * 1024 * 1024
# What the console can load. The player decodes every image the motion
# draws (not the poster) once, keeps them for the whole visit at 4 bytes a
# pixel, and draws nothing until all are decoded: together they stay within
# MOTION_MAX_DECODED (the shell refuses more: src/motionscene.cpp,
# kMotionDecodedMaxBytes). Each one, while Qt decodes it, is held in the
# format it reads it into (16-bit colour at 8 bytes a pixel), within the
# shell's limit for any one decode (src/shape.cpp, kAllocationLimitMiB).
MOTION_MAX_DECODED = 64 * 1024 * 1024
DECODE_MAX_BYTES = 16 * 1024 * 1024
# A plane or a light that drifts sideways repeats across the screen, one
# copy for each of its widths: at least this wide in canvas pixels, it is
# drawn ten times at most.
TILE_MIN_WIDTH = 240

LOOKS = {
    "pixel": {"resolution": [640, 360], "colour": 15, "dither": "bayer4", "sampling": "nearest"},
    "native": {"resolution": None, "colour": 24, "dither": "none", "sampling": "smooth"},
}
EVENTS = ("arrive", "focus.play", "focus.away", "launch", "return", "leave")
FOCUS_DEFAULTS = {"settle": 0.6, "cooldown": 20.0, "once": "per-visit"}
VARY_PROPERTIES = ("x", "y", "rotate", "scale", "opacity")
KEEP = ("identity", "ambient", "detail")
ANCHORS = ("disc",)
# Canvas pixels (1920 x 1080), with room off screen for entrances and exits.
X_RANGE = (-960.0, 2880.0)
Y_RANGE = (-540.0, 1620.0)

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

# What this draft reads and its player does not draw yet, by class: each
# class a package uses gets one note (shape_motion_not_drawn, information
# only: the motion is used), saying where, so that its author sees it in
# the checker and not only in this page's prose.
NOT_DRAWN = {
    "look": "la reducción de color y el tramado del aspecto (se compone a 24 bits, sin tramado)",
    "blend": "las mezclas screen y add (se dibujan como alpha)",
    "vary": "rotate y scale en planos y luces",
    "bend": "rotate y las animaciones blend en un personaje que se dobla",
    "colour": "el color de un brillo (el brillo de MUN es blanco)",
    "glow": "la deriva y la variación en x, y de un brillo",
    "light-state": "vary en el estado de una luz",
    "keep": "renunciar a cosas por lo que conservan (keep): la consola aún no renuncia a nada",
    "once": "once per-insertion (la reacción vuelve en cada visita)",
    "every": "el segundo número de every de un momento (cuenta el primero: el tiempo mínimo entre dos)",
    "poster": "el póster: con Reducir movimiento la consola deja quieta la escena; las consolas que solo leen v1 "
              "muestran world.backdrop",
    "toward": "velocity, gravity y sway de un emisor con toward (sus partículas van al disco a velocidad fija)",
    "emitter-clip": "las animaciones once y blend en un emisor (se repiten en bucle)",
}


def _fail(code: str, detail: str, where: str):
    raise shape._Invalid(code, detail, where)


def _type(value, kind, where: str, what: str):
    ok = isinstance(value, kind) and not (kind in (int, float, (int, float)) and isinstance(value, bool))
    if not ok:
        _fail("shape_motion_field", f"debe ser {what}", where)
    return value


def _number(value, low: float, high: float, where: str) -> float:
    _type(value, (int, float), where, "un número")
    if not low <= value <= high:
        _fail("shape_range", f"{value} fuera de {low:g}–{high:g}", where)
    return float(value)


def _integer(value, low: int, high: int, where: str) -> int:
    _type(value, int, where, "un número entero")
    if not low <= value <= high:
        _fail("shape_range", f"{value} fuera de {low}–{high}", where)
    return value


def _enum(value, allowed, where: str):
    _type(value, str, where, "un texto")
    if value not in allowed:
        _fail("shape_enum", f"{value[:40]}; admitidos: {', '.join(allowed)}", where)
    return value


def _name(value, where: str) -> str:
    _type(value, str, where, "un nombre")
    if not _NAME.fullmatch(value):
        _fail("shape_motion_field", f"«{value[:40]}»: minúsculas, cifras y guiones, hasta 32", where)
    return value


def _keys(value, allowed, where: str) -> Dict[str, Any]:
    _type(value, dict, where, "un objeto")
    extra = sorted(set(value) - set(allowed))
    if extra:
        _fail("shape_motion_field", f"claves desconocidas: {', '.join(extra)}", where)
    return value


def _point(value, where: str, depth: bool = False) -> List[float]:
    _type(value, list, where, "[x, y]" + (" o [x, y, z]" if depth else ""))
    if len(value) not in ((2, 3) if depth else (2,)):
        _fail("shape_motion_field", "debe ser [x, y]" + (" o [x, y, z]" if depth else ""), where)
    point = [_number(value[0], *X_RANGE, f"{where}[0]"), _number(value[1], *Y_RANGE, f"{where}[1]")]
    if len(value) == 3:
        point.append(_number(value[2], 0, 1, f"{where}[2]"))
    return point


def _offset(value, where: str) -> List[float]:
    """A point measured from an anchor: [dx, dy] or [dx, dy, z], z a depth."""
    _type(value, list, where, "[dx, dy] o [dx, dy, z]")
    if len(value) not in (2, 3):
        _fail("shape_motion_field", "debe ser [dx, dy] o [dx, dy, z]", where)
    point = [_number(value[0], -1920, 1920, f"{where}[0]"), _number(value[1], -1080, 1080, f"{where}[1]")]
    if len(value) == 3:
        point.append(_number(value[2], 0, 1, f"{where}[2]"))
    return point


def _pair(value, low: float, high: float, where: str, ordered: bool = False) -> List[float]:
    _type(value, list, where, "[a, b]")
    if len(value) != 2:
        _fail("shape_motion_field", "debe ser [a, b]", where)
    pair = [_number(value[0], low, high, f"{where}[0]"), _number(value[1], low, high, f"{where}[1]")]
    if ordered and pair[0] > pair[1]:
        _fail("shape_range", f"{pair[0]:g} es mayor que {pair[1]:g}", where)
    return pair


def _vary(value, where: str) -> Dict[str, Dict[str, Any]]:
    """{property: [min, max, period, shape?, phase?]}: the property between
    min and max over period seconds, as a sine (default), a saw, a step or a
    fade (at max but for the cycle's first and last tenth, rising from and
    falling to min), starting `phase` (0-1) into its cycle. A saw and a fade
    of the same period and phase go together: what rises fades in and out."""
    _keys(value, VARY_PROPERTIES, where)
    out = {}
    for prop, curve in value.items():
        inner = f"{where}.{prop}"
        _type(curve, list, inner, "[mín, máx, periodo, forma?, fase?]")
        if len(curve) not in (3, 4, 5):
            _fail("shape_motion_field", "debe ser [mín, máx, periodo, forma?, fase?]", inner)
        span = {"x": 400, "y": 400, "rotate": 360, "scale": 4, "opacity": 1}[prop]
        low = 0 if prop in ("scale", "opacity") else -span
        out[prop] = {"min": _number(curve[0], low, span, f"{inner}[0]"), "max": _number(curve[1], low, span, f"{inner}[1]"),
                     "period": _number(curve[2], 0.2, 600, f"{inner}[2]"),
                     "shape": _enum(curve[3], ("sine", "saw", "step", "fade"), f"{inner}[3]") if len(curve) >= 4 else "sine",
                     "phase": _number(curve[4], 0, 1, f"{inner}[4]") if len(curve) == 5 else 0.0}
    return out


def _drift(value, where: str) -> Dict[str, float]:
    _keys(value, ("x", "y"), where)
    return {axis: _number(value.get(axis, 0), -240, 240, f"{where}.{axis}") for axis in ("x", "y")}


def _surge(value, where: str) -> Dict[str, float]:
    """A stroke's pulse: an amount (0..1) of speed coming and going with each
    cycle of the clip, fastest at `at` (0..1 of the cycle, 0.25 by default);
    a number is the amount."""
    if isinstance(value, dict):
        _keys(value, ("amount", "at"), where)
        return {"amount": _number(value.get("amount", 0), 0, 1, f"{where}.amount"),
                "at": _number(value.get("at", 0.25), 0, 1, f"{where}.at")}
    return {"amount": _number(value, 0, 1, where), "at": 0.25}


class _Files:
    """The images the motion names: checked from their entries, then their
    headers, against their role's sides, within the motion's own budget."""

    def __init__(self, package, stats, cache, v1_files):
        self.package, self.stats, self.cache, self.v1 = package, stats, cache, v1_files
        self.found: Dict[str, Dict[str, Any]] = {}
        self.total = 0
        self.decoded = 0
        self.drawn: set = set()

    def image(self, value, role: str, where: str) -> Dict[str, Any]:
        _type(value, str, where, "una ruta")
        try:
            shape.safe_path(value, where)
        except shape.CardError as exc:
            _fail("shape_path_unsafe", f"{value[:80]}: {exc.message}", where)
        reference = shape._Reference(value, role, where)
        size = shape._stat_file(self.package, reference, self.stats)
        details = shape._read_file(self.package, reference, size, self.cache)
        if value not in self.found and value not in self.v1:
            self.total += size
            if self.total > MOTION_MAX_BYTES:
                _fail("shape_motion_budget", f"{self.total} bytes > {MOTION_MAX_BYTES}", where)
        # The poster is the still composition for consoles that read v1;
        # the player decodes everything else it names.
        if role != "poster" and value not in self.drawn:
            self.drawn.add(value)
            reading = decode_bytes(details)
            if reading > DECODE_MAX_BYTES:
                _fail("shape_motion_budget", f"{value}: decodificarla ocupa {reading} bytes > {DECODE_MAX_BYTES}", where)
            self.decoded += details["width"] * details["height"] * 4
            if self.decoded > MOTION_MAX_DECODED:
                _fail("shape_motion_budget", f"{self.decoded} bytes decodificados > {MOTION_MAX_DECODED}", where)
        self.found[value] = details
        return details


def decode_bytes(details: Dict[str, Any]) -> int:
    """One PNG while Qt decodes it: in the format it reads its colour type
    and depth into (16-bit colour 64 bits a pixel, 16-bit grey 16, 8-bit
    colour 32, palette and 8-bit grey 8), its lines padded to 32 bits."""
    depth, colour = details["depth"], details["colour"]
    if depth == 16:
        bits = 64 if colour in (2, 4, 6) else 16
    else:
        bits = 32 if colour in (2, 4, 6) else 8
    return (details["width"] * bits + 31) // 32 * 4 * details["height"]


def _sheet(value, files: _Files, where: str) -> Dict[str, Any]:
    """A sheet: frames in a grid. Without `frame`, square frames of the
    image's height; without `clips`, one clip of every frame, looping at 8
    frames a second. A clip loops, plays `once`, or loops with each frame
    dissolving into the next (`blend`: light, water).

    With `views` (2 to 32), the sheet is one row of frames per heading, the
    thing drawn the same way in each: the first row facing right, each next
    one turned further away from the viewer, round through facing left and
    back towards the viewer (views evenly spaced). Its clips then name
    columns, the same in every row; an actor that faces `views` shows the
    row of the heading it moves in."""
    _keys(value, ("image", "frame", "clips", "views"), where)
    if "image" not in value:
        _fail("shape_motion_field", "falta image", where)
    details = files.image(value["image"], "sheet", f"{where}.image")
    width, height = details["width"], details["height"]
    frame = value.get("frame", [height, height])
    _type(frame, list, f"{where}.frame", "[ancho, alto]")
    if len(frame) != 2:
        _fail("shape_motion_field", "debe ser [ancho, alto]", f"{where}.frame")
    fw = _integer(frame[0], 1, 2048, f"{where}.frame[0]")
    fh = _integer(frame[1], 1, 2048, f"{where}.frame[1]")
    if width % fw or height % fh:
        _fail("shape_motion_field", f"{value['image']} mide {width}×{height}: no se divide en fotogramas de {fw}×{fh}",
              f"{where}.frame")
    count = (width // fw) * (height // fh)
    views = _integer(value.get("views", 1), 1, MAX_VIEWS, f"{where}.views")
    if views > 1:
        if height // fh != views:
            _fail("shape_motion_field", f"{value['image']} tiene {height // fh} filas; con views {views}, una fila por rumbo",
                  f"{where}.views")
        count = width // fw          # clips name columns, the same in every row
    clips = {}
    raw = value.get("clips", {"loop": [0, count, 8]})
    _type(raw, dict, f"{where}.clips", "un objeto")
    if not raw:
        _fail("shape_motion_field", "sin animaciones", f"{where}.clips")
    for name, spec in raw.items():
        inner = f"{where}.clips.{_name(name, f'{where}.clips')}"
        _type(spec, list, inner, "[primero, cuántos, fps, modo?]")
        if len(spec) not in (3, 4):
            _fail("shape_motion_field", "debe ser [primero, cuántos, fps, modo?]", inner)
        first = _integer(spec[0], 0, count - 1, f"{inner}[0]")
        frames = _integer(spec[1], 1, count, f"{inner}[1]")
        if first + frames > count:
            _fail("shape_range", f"pide los fotogramas {first}–{first + frames - 1} y la hoja tiene {count}", inner)
        clips[name] = {"first": first, "count": frames, "fps": _number(spec[2], 0.5, 60, f"{inner}[2]"),
                       "mode": _enum(spec[3], ("loop", "once", "blend"), f"{inner}[3]") if len(spec) == 4 else "loop"}
    return {"image": value["image"], "size": [width, height], "frame": [fw, fh], "frames": count, "clips": clips,
            "views": views}


def _tiles(details: Dict[str, Any], drift: Dict[str, float], look: Dict[str, Any], where: str) -> None:
    """A thing drifting sideways is drawn once for each of its widths across
    the screen: it must be wide enough to be drawn a few times only."""
    if drift["x"] == 0:
        return
    scale = 1920 / look["resolution"][0] if look["resolution"] else 1
    wide = details["width"] * scale
    if wide < TILE_MIN_WIDTH:
        _fail("shape_range", f"mide {wide:g} píxeles de lienzo y se repite al derivar; al menos {TILE_MIN_WIDTH}", where)


def _look(value, where: str) -> Dict[str, Any]:
    if isinstance(value, str):
        return dict(_enum(value, tuple(LOOKS), where) and LOOKS[value])
    _keys(value, ("resolution", "colour", "dither"), where)
    if "resolution" not in value:
        _fail("shape_motion_field", "falta resolution", where)
    res = value["resolution"]
    _type(res, list, f"{where}.resolution", "[ancho, alto]")
    if len(res) != 2:
        _fail("shape_motion_field", "debe ser [ancho, alto]", f"{where}.resolution")
    w = _integer(res[0], 320, 1920, f"{where}.resolution[0]")
    h = _integer(res[1], 180, 1080, f"{where}.resolution[1]")
    if abs(w * 9 - h * 16) > w * 9 * 0.01:
        _fail("shape_range", f"{w}×{h} no es 16:9", f"{where}.resolution")
    colour = value.get("colour", 24)
    if colour not in (15, 24) or isinstance(colour, bool):
        _fail("shape_enum", f"{colour}; admitidos: 15, 24", f"{where}.colour")
    dither = _enum(value.get("dither", "none"), ("none", "bayer4"), f"{where}.dither")
    return {"resolution": [w, h], "colour": colour, "dither": dither, "sampling": "nearest"}


def check(document: Dict[str, Any], package, stats, cache,
          v1_files) -> Tuple[Dict[str, Any], Dict[str, Any], List[Tuple[str, str, str]]]:
    """The normalised motion, its files, and what it uses that the player
    does not draw yet ((class, where, detail) for shape_motion_not_drawn,
    one for each class used). Raises shape._Invalid on the first defect:
    the caller drops the motion as a whole."""
    files = _Files(package, stats, cache, v1_files)
    unseen: Dict[str, List[str]] = {}

    def not_drawn(kind: str, where: str) -> None:
        unseen.setdefault(kind, []).append(where)

    draft = document.get(DRAFT_KEY)
    if draft is None:
        _fail("shape_motion_draft", f"sin {DRAFT_KEY}: esta consola lee el borrador {DRAFT}", DRAFT_KEY)
    if not isinstance(draft, int) or isinstance(draft, bool) or draft != DRAFT:
        _fail("shape_motion_draft", f"{str(draft)[:20]}; esta consola lee el borrador {DRAFT}", DRAFT_KEY)
    out: Dict[str, Any] = {"draft": DRAFT}

    scene = _keys(document.get("scene", {}), ("look", "rate", "poster", "planes", "current", "depth", "sway"), "scene")
    out["look"] = _look(scene.get("look", "native"), "scene.look")
    if out["look"]["colour"] != 24 or out["look"]["dither"] != "none":
        not_drawn("look", "scene.look")
    out["rate"] = _integer(scene.get("rate", 20), 10, 30, "scene.rate")
    # The water's current: canvas pixels a second, with gusts (a share of
    # it, over about `period` seconds). Particles drift with it; things that
    # bend lean with it.
    current = _keys(scene.get("current", {}), ("x", "y", "gust", "period"), "scene.current")
    out["current"] = {"x": _number(current.get("x", 0), -200, 200, "scene.current.x"),
                      "y": _number(current.get("y", 0), -200, 200, "scene.current.y"),
                      "gust": _number(current.get("gust", 0), 0, 1, "scene.current.gust"),
                      "period": _number(current.get("period", 12), 2, 120, "scene.current.period")}
    # Perspective: a thing at depth z is drawn 1 / (1 + z (1/far - 1)) times
    # the size it has at depth 0, `far` being that share at depth 1 (so far
    # 0.25: four times as far); `span` is how far, in canvas pixels at depth
    # 0, the whole depth from 0 to 1 is, for headings and distances that
    # take depth in. Without it, depth only orders what is drawn.
    out["depth"] = None
    if "depth" in scene:
        depth = _keys(scene["depth"], ("far", "span"), "scene.depth")
        out["depth"] = {"far": _number(depth.get("far", 0.25), 0.05, 1, "scene.depth.far"),
                        "span": _number(depth.get("span", 2000), 100, 20000, "scene.depth.span")}
    # The viewpoint drifting: x and y canvas pixels, over their periods,
    # moving what is near (depth 0) so far, what is at `pivot` not at all,
    # what is beyond the other way, by perspective (or in proportion, with
    # no `depth`): the scene's parallax.
    out["sway"] = None
    if "sway" in scene:
        sway = _keys(scene["sway"], ("x", "y", "period", "pivot"), "scene.sway")
        out["sway"] = {"x": _number(sway.get("x", 0), 0, 200, "scene.sway.x"),
                       "y": _number(sway.get("y", 0), 0, 200, "scene.sway.y"),
                       "period": _pair(sway.get("period", [56, 69]), 4, 600, "scene.sway.period"),
                       "pivot": _number(sway.get("pivot", 0.5), 0.01, 1, "scene.sway.pivot")}
    out["poster"] = None
    if "poster" in scene:
        files.image(scene["poster"], "poster", "scene.poster")
        out["poster"] = scene["poster"]
        backdrop = document.get("world", {}).get("backdrop") if isinstance(document.get("world"), dict) else None
        if not isinstance(backdrop, dict) or backdrop.get("image") != scene["poster"]:
            not_drawn("poster", "scene.poster")
    planes = scene.get("planes", [])
    _type(planes, list, "scene.planes", "una lista")
    if len(planes) > MAX_PLANES:
        _fail("shape_motion_count", f"{len(planes)} planos; máximo {MAX_PLANES}", "scene.planes")
    out["planes"] = []
    for i, plane in enumerate(planes):
        where = f"scene.planes[{i}]"
        _keys(plane, ("id", "image", "z", "at", "drift", "vary"), where)
        if "image" not in plane:
            _fail("shape_motion_field", "falta image", where)
        if "id" in plane:
            _name(plane["id"], f"{where}.id")
        details = files.image(plane["image"], "plane", f"{where}.image")
        # `at`: where its top left corner goes (a plane wider than the
        # screen starts left of it, so that sway never shows its edge)
        entry = {"image": plane["image"], "z": _number(plane.get("z", 0.5), 0, 1, f"{where}.z"),
                 "at": _point(plane.get("at", [0, 0]), f"{where}.at"),
                 "drift": _drift(plane.get("drift", {}), f"{where}.drift"),
                 "vary": _vary(plane.get("vary", {}), f"{where}.vary")}
        _tiles(details, entry["drift"], out["look"], f"{where}.drift")
        for prop in ("rotate", "scale"):
            if prop in entry["vary"]:
                not_drawn("vary", f"{where}.vary.{prop}")
        out["planes"].append(entry)

    sheets: Dict[str, Any] = {}
    named = _type(document.get("sheets", {}), dict, "sheets", "un objeto")
    for name, sheet in named.items():
        sheets[_name(name, "sheets")] = _sheet(sheet, files, f"sheets.{name}")

    def sheet_of(value, owner: str, where: str) -> str:
        if isinstance(value, str):
            if value not in sheets:
                _fail("shape_motion_reference", f"la hoja «{value}» no está en sheets", where)
            return value
        key = f"{owner}~"
        sheets[key] = _sheet(value, files, where)
        if len(sheets) > MAX_SHEETS:
            _fail("shape_motion_count", f"{len(sheets)} hojas; máximo {MAX_SHEETS}", where)
        return key

    if len(sheets) > MAX_SHEETS:
        _fail("shape_motion_count", f"{len(sheets)} hojas; máximo {MAX_SHEETS}", "sheets")

    raw_actors = _type(document.get("actors", {}), dict, "actors", "un objeto")
    raw_react = _type(document.get("react", {}), dict, "react", "un objeto")
    answering = set()
    focus = raw_react.get("focus.play")
    if isinstance(focus, dict):
        answering = set((focus.get("do") if isinstance(focus.get("do"), dict) else focus).keys())
    if len(raw_actors) > MAX_ACTORS:
        _fail("shape_motion_count", f"{len(raw_actors)} personajes; máximo {MAX_ACTORS}", "actors")
    actors: Dict[str, Any] = {}
    copies_total = 0
    for name, actor in raw_actors.items():
        where = f"actors.{_name(name, 'actors')}"
        _keys(actor, ("sheet", "clip", "at", "z", "face", "bank", "copies", "keep", "rest", "turn", "bend", "avoid"), where)
        if "sheet" not in actor or "at" not in actor:
            _fail("shape_motion_field", "un personaje necesita sheet y at", where)
        key = sheet_of(actor["sheet"], f"actor:{name}", f"{where}.sheet")
        clips = sheets[key]["clips"]
        clip = _type(actor.get("clip", next(iter(clips))), str, f"{where}.clip", "el nombre de una animación")
        if clip not in clips:
            _fail("shape_motion_reference", f"la animación «{clip}» no está en su hoja", f"{where}.clip")
        turn = None
        if "turn" in actor:
            turn = _type(actor["turn"], str, f"{where}.turn", "el nombre de una animación")
            if turn not in clips:
                _fail("shape_motion_reference", f"la animación «{turn}» no está en su hoja", f"{where}.turn")
        bend = None
        if "bend" in actor:
            b = _keys(actor["bend"], ("amount", "period", "wave", "follow"), f"{where}.bend")
            bend = {"amount": _number(b.get("amount", 12), 0, 200, f"{where}.bend.amount"),
                    "period": _number(b.get("period", 5), 0.5, 60, f"{where}.bend.period"),
                    "wave": _number(b.get("wave", 0.4), -3, 3, f"{where}.bend.wave"),
                    "follow": _number(b.get("follow", 0.5), 0, 5, f"{where}.bend.follow")}
        # Copies: a formation round the actor. Either `count` figures
        # scattered (seeded) within `spread` [x, y] canvas pixels and `depth`
        # either side, or one at each of `places` ([dx, dy] or [dx, dy, dz]
        # from the actor). Each may sway up and down (`wobble` [pixels,
        # seconds]), runs its clip `lag` seconds after the one before, and
        # with `clips` shows them in turn (still things of many looks: the
        # kelp of a bed, the rays of a sea).
        copies = None
        if "copies" in actor:
            c = _keys(actor["copies"], ("count", "spread", "depth", "places", "lag", "wobble", "clips"), f"{where}.copies")
            places = None
            if "places" in c:
                raw_places = _type(c["places"], list, f"{where}.copies.places", "una lista de [dx, dy, dz?]")
                if not 1 <= len(raw_places) <= MAX_COPIES_EACH:
                    _fail("shape_motion_count", f"{len(raw_places)} sitios; de 1 a {MAX_COPIES_EACH}", f"{where}.copies.places")
                places = []
                for j, place in enumerate(raw_places):
                    inner = f"{where}.copies.places[{j}]"
                    _type(place, list, inner, "[dx, dy] o [dx, dy, dz]")
                    if len(place) not in (2, 3):
                        _fail("shape_motion_field", "debe ser [dx, dy] o [dx, dy, dz]", inner)
                    places.append([_number(place[0], -1920, 1920, f"{inner}[0]"), _number(place[1], -1080, 1080, f"{inner}[1]"),
                                   _number(place[2], -1, 1, f"{inner}[2]") if len(place) == 3 else 0.0])
                if "count" in c and c["count"] != len(places):
                    _fail("shape_motion_field", f"count {c['count']} y {len(places)} sitios", f"{where}.copies.count")
            count = len(places) if places else _integer(c.get("count", 1), 1, MAX_COPIES_EACH, f"{where}.copies.count")
            copy_clips = None
            if "clips" in c:
                copy_clips = _type(c["clips"], list, f"{where}.copies.clips", "una lista de animaciones")
                if not 1 <= len(copy_clips) <= MAX_COPIES_EACH:
                    _fail("shape_motion_count", f"{len(copy_clips)} animaciones; de 1 a {MAX_COPIES_EACH}", f"{where}.copies.clips")
                for j, name_ in enumerate(copy_clips):
                    if _type(name_, str, f"{where}.copies.clips[{j}]", "el nombre de una animación") not in clips:
                        _fail("shape_motion_reference", f"la animación «{name_}» no está en su hoja", f"{where}.copies.clips[{j}]")
            wobble = _pair(c.get("wobble", [0, 1]), 0, 200, f"{where}.copies.wobble")
            if wobble[1] < 0.2:
                _fail("shape_range", f"{wobble[1]:g} s; al menos 0.2", f"{where}.copies.wobble[1]")
            copies = {"count": count, "spread": _pair(c.get("spread", [0, 0]), 0, 1920, f"{where}.copies.spread"),
                      "depth": _number(c.get("depth", 0), 0, 1, f"{where}.copies.depth"), "places": places,
                      "lag": _number(c.get("lag", 0), 0, 1, f"{where}.copies.lag"),
                      "wobble": wobble, "clips": copy_clips}
            copies_total += copies["count"]
        else:
            copies_total += 1
        face = _enum(actor.get("face", "fixed"), ("fixed", "mirror", "views"), f"{where}.face")
        if face == "views" and sheets[key]["views"] < 2:
            _fail("shape_motion_field", "face views necesita una hoja con views", f"{where}.face")
        avoid = None
        if "avoid" in actor:
            a = _keys(actor["avoid"], ("actor", "radius", "push"), f"{where}.avoid")
            if not isinstance(a.get("actor"), str) or a["actor"] not in raw_actors or a["actor"] == name:
                _fail("shape_motion_reference", f"«{a.get('actor')}» no es otro personaje", f"{where}.avoid.actor")
            avoid = {"actor": a["actor"], "radius": _number(a.get("radius", 400), 1, 3840, f"{where}.avoid.radius"),
                     "push": _number(a.get("push", 200), 0, 1920, f"{where}.avoid.push")}
        if "keep" in actor:
            not_drawn("keep", f"{where}.keep")
        if bend is not None and any(c["mode"] == "blend" for c in clips.values()):
            not_drawn("bend", f"{where}.bend")
        actors[name] = {"sheet": key, "clip": clip, "at": _point(actor["at"], f"{where}.at"),
                        "z": _number(actor.get("z", 0.5), 0, 1, f"{where}.z"),
                        "face": face, "avoid": avoid,
                        "bank": _number(actor.get("bank", 0), 0, 1, f"{where}.bank"),
                        "keep": _enum(actor.get("keep", "identity" if name in answering else "ambient"), KEEP, f"{where}.keep"),
                        "rest": _name(actor.get("rest", "idle"), f"{where}.rest"), "copies": copies,
                        "turn": turn, "bend": bend}
    if copies_total > MAX_COPIES:
        _fail("shape_motion_count", f"{copies_total} figuras de personajes; máximo {MAX_COPIES}", "actors")

    raw_paths = _type(document.get("paths", {}), dict, "paths", "un objeto")
    if len(raw_paths) > MAX_PATHS:
        _fail("shape_motion_count", f"{len(raw_paths)} recorridos; máximo {MAX_PATHS}", "paths")
    paths: Dict[str, Any] = {}
    for name, path in raw_paths.items():
        where = f"paths.{_name(name, 'paths')}"
        _keys(path, ("from", "to", "points", "origin", "around", "radius", "turns", "seconds", "speed", "loop", "ease",
                     "smooth", "pace"), where)
        if not any(k in path for k in ("points", "to", "around")):
            _fail("shape_motion_field", "un recorrido necesita points, to o around", where)
        # Fields another option leaves unused are refused, not ignored.
        for field in ("radius", "turns"):
            if field in path and "around" not in path:
                _fail("shape_motion_field", "solo con around", f"{where}.{field}")
        if "seconds" in path and "speed" in path:
            _fail("shape_motion_field", "seconds o speed, uno solo", f"{where}.speed")
        points = path.get("points", [])
        _type(points, list, f"{where}.points", "una lista de puntos")
        if len(points) > MAX_POINTS:
            _fail("shape_motion_count", f"{len(points)} puntos; máximo {MAX_POINTS}", f"{where}.points")
        # `origin`: where the points are measured from: the canvas's top
        # left (by default), or an anchor, where it is when the course
        # starts, so that it keeps its shape wherever the console draws it.
        origin = _enum(path.get("origin", "canvas"), ("canvas",) + ANCHORS, f"{where}.origin")
        if origin != "canvas" and not points:
            _fail("shape_motion_field", "origin mide points, y el recorrido no tiene", f"{where}.origin")
        measured = [(_offset(p, f"{where}.points[{j}]") if origin != "canvas" else _point(p, f"{where}.points[{j}]", depth=True))
                    for j, p in enumerate(points)]
        entry = {"points": measured, "origin": origin,
                 "from": _enum(path.get("from", "here"), ("here",) + ANCHORS, f"{where}.from"),
                 "to": _enum(path["to"], ANCHORS, f"{where}.to") if "to" in path else None,
                 "around": None, "loop": _type(path.get("loop", False), bool, f"{where}.loop", "true o false"),
                 "ease": _enum(path.get("ease", "inout"), ("linear", "in", "out", "inout"), f"{where}.ease"),
                 "smooth": _type(path.get("smooth", True), bool, f"{where}.smooth", "true o false"),
                 # `even`: every point reached in the same time (a motion sampled
                 # at even times keeps its rhythm); `speed`: an even speed
                 "pace": _enum(path.get("pace", "speed"), ("speed", "even"), f"{where}.pace")}
        if "around" in path:
            entry["around"] = {"anchor": _enum(path["around"], ANCHORS, f"{where}.around"),
                               "radius": _pair(path.get("radius", [240, 180]), 0, 1920, f"{where}.radius"),
                               "turns": _number(path.get("turns", 1), -8, 8, f"{where}.turns")}
        if "seconds" in path:
            entry["seconds"], entry["speed"] = _number(path["seconds"], 0.1, 600, f"{where}.seconds"), None
        else:
            entry["seconds"], entry["speed"] = None, _number(path.get("speed", 120), 1, 2000, f"{where}.speed")
        paths[name] = entry

    raw_emitters = _type(document.get("emitters", {}), dict, "emitters", "un objeto")
    raw_lights = _type(document.get("lights", {}), dict, "lights", "un objeto")
    if len(raw_emitters) > MAX_EMITTERS:
        _fail("shape_motion_count", f"{len(raw_emitters)} emisores; máximo {MAX_EMITTERS}", "emitters")
    if len(raw_lights) > MAX_LIGHTS:
        _fail("shape_motion_count", f"{len(raw_lights)} luces; máximo {MAX_LIGHTS}", "lights")
    things = set(raw_actors) | set(raw_emitters) | set(raw_lights)
    if len(things) != len(raw_actors) + len(raw_emitters) + len(raw_lights):
        _fail("shape_motion_field", "un mismo nombre para un personaje, un emisor o una luz", "actors")

    emitters: Dict[str, Any] = {}
    particles = 0
    for name, emitter in raw_emitters.items():
        where = f"emitters.{_name(name, 'emitters')}"
        _keys(emitter, ("sheet", "clip", "region", "ring", "at", "toward", "swirl", "rate", "burst", "life", "velocity",
                        "spread", "gravity", "sway", "grow", "fade", "z", "max", "keep", "on", "pull"), where)
        sources = [k for k in ("region", "ring", "at") if k in emitter]
        if "sheet" not in emitter or len(sources) != 1:
            _fail("shape_motion_field", "un emisor necesita sheet y una sola fuente: region, ring o at", where)
        key = sheet_of(emitter["sheet"], f"emitter:{name}", f"{where}.sheet")
        clip = _type(emitter.get("clip", next(iter(sheets[key]["clips"]))), str, f"{where}.clip", "el nombre de una animación")
        if clip not in sheets[key]["clips"]:
            _fail("shape_motion_reference", f"la animación «{clip}» no está en su hoja", f"{where}.clip")
        source: Dict[str, Any]
        if "region" in emitter:
            r = emitter["region"]
            _type(r, list, f"{where}.region", "[x, y, ancho, alto]")
            if len(r) != 4:
                _fail("shape_motion_field", "debe ser [x, y, ancho, alto]", f"{where}.region")
            source = {"region": [_number(r[0], *X_RANGE, f"{where}.region[0]"), _number(r[1], *Y_RANGE, f"{where}.region[1]"),
                                 _number(r[2], 0, 3840, f"{where}.region[2]"), _number(r[3], 0, 2160, f"{where}.region[3]")]}
        elif "ring" in emitter:
            r = emitter["ring"]
            _type(r, list, f"{where}.ring", "[ancla, radio interior, radio exterior]")
            if len(r) != 3:
                _fail("shape_motion_field", "debe ser [ancla, radio interior, radio exterior]", f"{where}.ring")
            source = {"ring": {"anchor": _enum(r[0], ANCHORS, f"{where}.ring[0]"),
                               "radii": _pair(r[1:], 0, 1920, f"{where}.ring", ordered=True)}}
        else:
            source = {"at": _point(emitter["at"], f"{where}.at")}
        # `pull`: the disc draws in what comes within `radius` canvas pixels,
        # the more the nearer (up to `speed` pixels a second, turning round
        # it by `swirl`), and swallows it within `swallow`.
        pull = None
        if "pull" in emitter:
            q = _keys(emitter["pull"], ("radius", "speed", "swirl", "swallow"), f"{where}.pull")
            pull = {"radius": _number(q.get("radius", 600), 1, 1920, f"{where}.pull.radius"),
                    "speed": _number(q.get("speed", 120), 0, 1000, f"{where}.pull.speed"),
                    "swirl": _number(q.get("swirl", 0.5), -2, 2, f"{where}.pull.swirl"),
                    "swallow": _number(q.get("swallow", 60), 0, 960, f"{where}.pull.swallow")}
        limit = _integer(emitter.get("max", 16), 1, MAX_PARTICLES, f"{where}.max")
        particles += limit
        if "toward" in emitter:
            for field in ("velocity", "gravity", "sway"):
                if field in emitter:
                    not_drawn("toward", f"{where}.{field}")
        if sheets[key]["clips"][clip]["mode"] != "loop":
            not_drawn("emitter-clip", f"{where}.clip")
        if "keep" in emitter:
            not_drawn("keep", f"{where}.keep")
        emitters[name] = {"sheet": key, "clip": clip, **source,
                          "toward": _enum(emitter["toward"], ANCHORS, f"{where}.toward") if "toward" in emitter else None,
                          "pull": pull,
                          "swirl": _number(emitter.get("swirl", 0), -2, 2, f"{where}.swirl"),
                          "rate": _number(emitter.get("rate", 0), 0, 60, f"{where}.rate"),
                          "burst": _integer(emitter.get("burst", 0), 0, 64, f"{where}.burst"),
                          "life": _pair(emitter.get("life", [4, 6]), 0.1, 60, f"{where}.life", ordered=True),
                          "velocity": _pair(emitter.get("velocity", [0, 0]), -1000, 1000, f"{where}.velocity"),
                          "spread": _number(emitter.get("spread", 0), 0, 360, f"{where}.spread"),
                          "gravity": _number(emitter.get("gravity", 0), -2000, 2000, f"{where}.gravity"),
                          "sway": _number(emitter.get("sway", 0), 0, 200, f"{where}.sway"),
                          "grow": _number(emitter.get("grow", 0), 0, 8, f"{where}.grow"),
                          "fade": _number(emitter.get("fade", 0), 0, 1, f"{where}.fade"),
                          "z": _number(emitter.get("z", 0.5), 0, 1, f"{where}.z"), "max": limit,
                          "keep": _enum(emitter.get("keep", "detail"), KEEP, f"{where}.keep"),
                          "on": _type(emitter.get("on", True), bool, f"{where}.on", "true o false")}
    if particles > MAX_PARTICLES:
        _fail("shape_motion_count", f"{particles} partículas a la vez; máximo {MAX_PARTICLES}", "emitters")

    lights: Dict[str, Any] = {}
    for name, light in raw_lights.items():
        where = f"lights.{_name(name, 'lights')}"
        _keys(light, ("image", "effect", "blend", "at", "colour", "radius", "z", "drift", "vary", "opacity", "keep", "on"), where)
        if ("image" in light) == ("effect" in light):
            _fail("shape_motion_field", "una luz tiene image o effect, uno solo", where)
        entry = {"image": None, "effect": None, "blend": _enum(light.get("blend", "alpha"), ("screen", "add", "alpha"), f"{where}.blend"),
                 "z": _number(light.get("z", 0.5), 0, 1, f"{where}.z"),
                 "opacity": _number(light.get("opacity", 1), 0, 1, f"{where}.opacity"),
                 "drift": _drift(light.get("drift", {}), f"{where}.drift"), "vary": _vary(light.get("vary", {}), f"{where}.vary"),
                 "keep": _enum(light.get("keep", "ambient"), KEEP, f"{where}.keep"),
                 "on": _type(light.get("on", True), bool, f"{where}.on", "true o false"), "at": None}
        if "image" in light:
            for field in ("colour", "radius"):
                if field in light:
                    _fail("shape_motion_field", "solo en un brillo (effect)", f"{where}.{field}")
            details = files.image(light["image"], "light", f"{where}.image")
            entry["image"] = light["image"]
            _tiles(details, entry["drift"], out["look"], f"{where}.drift")
        else:
            entry["effect"] = _enum(light["effect"], ("glow",), f"{where}.effect")
            entry["colour"] = shape._colour(light.get("colour", "#FFFFFF"), f"{where}.colour", None)
            entry["radius"] = _number(light.get("radius", 220), 1, 1920, f"{where}.radius")
            if "at" not in light:
                _fail("shape_motion_field", "un brillo necesita at: un punto o un ancla", f"{where}.at")
            if entry["colour"] != "#FFFFFF":
                not_drawn("colour", f"{where}.colour")
            if entry["drift"]["x"] or entry["drift"]["y"]:
                not_drawn("glow", f"{where}.drift")
            for prop in ("x", "y"):
                if prop in entry["vary"]:
                    not_drawn("glow", f"{where}.vary.{prop}")
        if "at" in light:
            entry["at"] = light["at"] if light["at"] in ANCHORS else _point(light["at"], f"{where}.at")
        if entry["blend"] != "alpha":
            not_drawn("blend", f"{where}.blend")
        for prop in ("rotate", "scale"):
            if prop in entry["vary"]:
                not_drawn("vary", f"{where}.vary.{prop}")
        if "keep" in light:
            not_drawn("keep", f"{where}.keep")
        lights[name] = entry

    # States, for every kind of thing (docs/shape-motion.md, "States").
    raw_states = _type(document.get("states", {}), dict, "states", "un objeto")
    if len(raw_states) > MAX_STATES:
        _fail("shape_motion_count", f"{len(raw_states)} estados; máximo {MAX_STATES}", "states")
    states: Dict[str, Any] = {}
    for key, state in raw_states.items():
        where = f"states.{key}"
        owner, _, label = key.partition(".")
        if not label:
            _fail("shape_motion_field", "un estado se nombra «personaje.estado»", where)
        _name(label, where)
        if owner in actors:
            _keys(state, ("path", "clip", "vary", "then", "hidden", "only", "surge", "glide", "seconds"), where)
            if "path" in state and _type(state["path"], str, f"{where}.path", "el nombre de un recorrido") not in paths:
                _fail("shape_motion_reference", f"el recorrido «{state['path']}» no existe", f"{where}.path")
            clips = sheets[actors[owner]["sheet"]]["clips"]
            if "clip" in state and _type(state["clip"], str, f"{where}.clip", "el nombre de una animación") not in clips:
                _fail("shape_motion_reference", f"la animación «{state['clip']}» no está en la hoja de {owner}", f"{where}.clip")
            glide = None
            if "glide" in state:
                g = _keys(state["glide"], ("clip", "every", "seconds"), f"{where}.glide")
                if _type(g.get("clip"), str, f"{where}.glide.clip", "el nombre de una animación") not in clips:
                    _fail("shape_motion_reference", f"la animación «{g.get('clip')}» no está en la hoja de {owner}",
                          f"{where}.glide.clip")
                every = g.get("every", [2, 3])
                _type(every, list, f"{where}.glide.every", "[mín, máx] brazadas")
                if len(every) != 2:
                    _fail("shape_motion_field", "debe ser [mín, máx] brazadas", f"{where}.glide.every")
                every = [_integer(every[0], 1, 20, f"{where}.glide.every[0]"), _integer(every[1], 1, 20, f"{where}.glide.every[1]")]
                if every[0] > every[1]:
                    _fail("shape_range", f"{every[0]} es mayor que {every[1]}", f"{where}.glide.every")
                glide = {"clip": g["clip"], "every": every,
                         "seconds": _pair(g.get("seconds", [1, 2]), 0.2, 10, f"{where}.glide.seconds", ordered=True)}
            vary = _vary(state.get("vary", {}), f"{where}.vary")
            if actors[owner]["bend"] is not None and "rotate" in vary:
                not_drawn("bend", f"{where}.vary.rotate")
            states[key] = {"kind": "actor", "path": state.get("path"), "clip": state.get("clip"),
                           "vary": vary, "then": state.get("then"),
                           "surge": _surge(state.get("surge", 0), f"{where}.surge"), "glide": glide,
                           "seconds": _number(state["seconds"], 0.2, 600, f"{where}.seconds") if "seconds" in state else None,
                           "hidden": _type(state.get("hidden", False), bool, f"{where}.hidden", "true o false"),
                           "only": _enum(state["only"], ("visible",), f"{where}.only") if "only" in state else None}
        elif owner in emitters:
            _keys(state, ("on", "burst", "rate"), where)
            states[key] = {"kind": "emitter", "on": _type(state.get("on", True), bool, f"{where}.on", "true o false"),
                           "burst": _integer(state.get("burst", 0), 0, 64, f"{where}.burst"),
                           "rate": _number(state.get("rate", 1), 0, 4, f"{where}.rate")}
        elif owner in lights:
            _keys(state, ("on", "opacity", "vary"), where)
            if state.get("vary"):
                not_drawn("light-state", f"{where}.vary")
            states[key] = {"kind": "light", "on": _type(state.get("on", True), bool, f"{where}.on", "true o false"),
                           "opacity": _number(state["opacity"], 0, 1, f"{where}.opacity") if "opacity" in state else None,
                           "vary": _vary(state.get("vary", {}), f"{where}.vary")}
        else:
            _fail("shape_motion_reference", f"no hay ningún personaje, emisor o luz «{owner}»", where)

    def target(who: str, what, where: str) -> None:
        """A state change: an actor to one of its states (or `idle`), an
        emitter to on, off, burst or one of its states, a light to on, off or
        one of its states."""
        _type(what, str, where, "el nombre de un estado")
        builtin = {"idle"} if who in actors else {"on", "off", "burst"} if who in emitters else {"on", "off"} if who in lights else None
        if builtin is None:
            _fail("shape_motion_reference", f"no hay ningún personaje, emisor o luz «{who}»", where)
        if what not in builtin and f"{who}.{what}" not in states:
            _fail("shape_motion_reference", f"«{who}» no tiene el estado «{what}»", where)
        if what == "burst" and who in emitters and emitters[who]["burst"] < 1:
            _fail("shape_motion_field", f"«{who}» no dice cuántas partículas lanza (burst)", where)

    for key, state in states.items():
        owner = key.partition(".")[0]
        if state["kind"] == "actor" and state["then"] is not None:
            target(owner, state["then"], f"states.{key}.then")
    for name, actor in actors.items():
        if actor["rest"] != "idle":
            target(name, actor["rest"], f"actors.{name}.rest")

    out["beats"] = None
    if "beats" in document:
        beats = _keys(document["beats"], ("gap", "list"), "beats")
        items = _type(beats.get("list", []), list, "beats.list", "una lista")
        if len(items) > MAX_BEATS:
            _fail("shape_motion_count", f"{len(items)} momentos; máximo {MAX_BEATS}", "beats.list")
        normal = []
        for i, item in enumerate(items):
            where = f"beats.list[{i}]"
            _keys(item, ("do", "weight", "every"), where)
            moves = _type(item.get("do"), dict, f"{where}.do", "un objeto")
            if not 1 <= len(moves) <= MAX_TARGETS:
                _fail("shape_motion_count", f"{len(moves)} destinos; de 1 a {MAX_TARGETS}", f"{where}.do")
            for who, what in moves.items():
                target(who, what, f"{where}.do.{who}")
            every = _pair(item["every"], 0, 3600, f"{where}.every", ordered=True) if "every" in item else None
            if every and every[1] != every[0]:
                not_drawn("every", f"{where}.every")
            normal.append({"do": dict(moves), "weight": _integer(item.get("weight", 1), 1, 10, f"{where}.weight"),
                           "every": every})
        out["beats"] = {"gap": _pair(beats.get("gap", [6, 18]), 0, 600, "beats.gap", ordered=True), "list": normal}

    react = {}
    for event, value in raw_react.items():
        where = f"react.{event}"
        _enum(event, EVENTS, where)
        _type(value, dict, where, "un objeto")
        full = dict(FOCUS_DEFAULTS) if event == "focus.play" else {"settle": 0.0, "cooldown": 0.0, "once": None}
        if "do" in value:
            _keys(value, ("settle", "cooldown", "once", "do"), where)
            timed = [k for k in ("settle", "cooldown", "once") if k in value]
            if timed and event != "focus.play":
                # The player applies every other event at its next step.
                _fail("shape_motion_field", f"{', '.join(timed)}: solo en focus.play", where)
            if "settle" in value:
                full["settle"] = _number(value["settle"], 0, 5, f"{where}.settle")
            if "cooldown" in value:
                full["cooldown"] = _number(value["cooldown"], 0, 600, f"{where}.cooldown")
            if "once" in value:
                full["once"] = _enum(value["once"], ("per-visit", "per-insertion"), f"{where}.once")
                if full["once"] == "per-insertion":
                    not_drawn("once", f"{where}.once")
            moves = _type(value["do"], dict, f"{where}.do", "un objeto")
        else:
            moves = value
        if not 1 <= len(moves) <= MAX_TARGETS:
            _fail("shape_motion_count", f"{len(moves)} destinos; de 1 a {MAX_TARGETS}", where)
        for who, what in moves.items():
            target(who, what, f"{where}.{who}")
        full["do"] = dict(moves)
        react[event] = full

    # Something to draw: the player draws the motion instead of the v1
    # world, so a motion of nothing would leave Home over an empty scene.
    if not (out["planes"] or actors or emitters or lights):
        _fail("shape_motion_empty", "no declara nada que dibujar: ni planos, ni personajes, ni emisores, ni luces", "motion")
    out.update({"planes": out["planes"], "sheets": sheets, "actors": actors, "paths": paths, "states": states,
                "emitters": emitters, "lights": lights, "react": react})
    findings = []
    for kind, places in unseen.items():
        more = f" y {len(places) - 3} más" if len(places) > 3 else ""
        findings.append((kind, places[0], f"{NOT_DRAWN[kind]}: {', '.join(places[:3])}{more}"))
    return out, files.found, findings


def named_files(document: Dict[str, Any]) -> List[str]:
    """Every file the motion blocks of a document name, whatever the
    checker's verdict on them: the poster, the planes, the sheets (named or
    inline) and the lights' images. The authoring tool copies them beside
    the v1 files; a console that only reads v1 never looks at them."""
    names: List[str] = []

    def take(value) -> None:
        if isinstance(value, str):
            names.append(value)

    def sheet(value) -> None:
        if isinstance(value, dict):
            take(value.get("image"))

    def each(value) -> List[Any]:
        if isinstance(value, dict):
            return list(value.values())
        return value if isinstance(value, list) else []

    scene = document.get("scene")
    if isinstance(scene, dict):
        take(scene.get("poster"))
        for plane in each(scene.get("planes")):
            if isinstance(plane, dict):
                take(plane.get("image"))
    for value in each(document.get("sheets")):
        sheet(value)
    for block in ("actors", "emitters"):
        for thing in each(document.get(block)):
            if isinstance(thing, dict):
                sheet(thing.get("sheet"))
    for light in each(document.get("lights")):
        if isinstance(light, dict):
            take(light.get("image"))
    return names


def estimate(motion: Dict[str, Any], files: Dict[str, Dict[str, Any]], display=(1920, 1080),
             display_rate: int = 60) -> Dict[str, Any]:
    """What the scene costs, before decoding: its images decoded (4 bytes a
    pixel) and the pixels drawn per frame at the look's resolution (planes,
    every actor figure, the particles at their limit, the lights), and per
    second at the display's rate: the player draws on every display frame,
    while it simulates `rate` steps a second. It counts what is listed to
    draw, not what a backend or a GPU costs: an estimate to compare
    packages; the budgets it is to be held against are still to be
    measured."""
    res = motion["look"]["resolution"] or list(display)
    scale = res[0] / display[0]
    decoded = sum(files[name]["width"] * files[name]["height"] * 4
                  for name in {p["image"] for p in motion["planes"]} | {s["image"] for s in motion["sheets"].values()}
                  | {l["image"] for l in motion["lights"].values() if l["image"]} if name in files)
    drawn = 0.0
    for plane in motion["planes"]:
        d = files[plane["image"]]
        drawn += min(d["width"], res[0]) * min(d["height"], res[1])
    for actor in motion["actors"].values():
        fw, fh = motion["sheets"][actor["sheet"]]["frame"]
        drawn += fw * fh * (actor["copies"]["count"] if actor["copies"] else 1)
    for emitter in motion["emitters"].values():
        fw, fh = motion["sheets"][emitter["sheet"]]["frame"]
        drawn += fw * fh * emitter["max"]
    for light in motion["lights"].values():
        if light["image"]:
            d = files[light["image"]]
            drawn += min(d["width"] * scale, res[0]) * min(d["height"] * scale, res[1])
        else:
            drawn += (2 * light["radius"] * scale) ** 2
    return {"resolution": res, "decoded_bytes": decoded, "drawn_per_frame": round(drawn),
            "drawn_per_second": round(drawn * display_rate), "display_rate": display_rate,
            "steps_per_second": motion["rate"],
            "keep": {cls: sorted([n for n, a in motion["actors"].items() if a["keep"] == cls]
                                 + [n for n, e in motion["emitters"].items() if e["keep"] == cls]
                                 + [n for n, l in motion["lights"].items() if l["keep"] == cls]) for cls in KEEP}}
