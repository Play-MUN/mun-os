"""MUN Shape packages (docs/shape.md): strict parsing, schema, files, budgets
and contrast.

A card may carry `<content.root>/mun-shape/shape.json` and the files it names.
This module is the single reading of such a package: the host tool checks a
folder with it, and the card service is to check the copy it makes of a
card's package with it. It reads bytes and headers only. It never inflates an
image or plays a sound: decoding belongs to the unprivileged shell.

Nothing here raises for a defect in the package. A defect drops the aspect it
concerns (the whole package, one block, or the game's surface colours) and is
reported as a note; the card itself is never affected.
"""

import array
import json
import json.decoder
import json.scanner
import math
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .errors import CardError
from .validate import safe_path

PACKAGE_DIR = "mun-shape"
MANIFEST = "shape.json"
FORMAT_NAME = "mun-shape"
FORMAT_MAJOR = 1
FORMAT_MINOR = 0
FORMAT = f"{FORMAT_NAME}/{FORMAT_MAJOR}"

# The strict JSON profile. Enforced limits, not performance objectives.
MANIFEST_MAX_BYTES = 64 * 1024
MAX_DEPTH = 6
MAX_STRING_CHARS = 512
MAX_NUMBER_CHARS = 32

# Files. The package is copied to RAM before the shell reads it, so every
# byte is bounded before it is read.
PACKAGE_MAX_BYTES = 32 * 1024 * 1024
IMAGE_MAX_BYTES = 4 * 1024 * 1024
SOUND_MAX_BYTES = 1024 * 1024
# Largest side of each kind of image, which bounds its decoded size before
# anything decodes it (2048 x 2048 ARGB is 16 MiB).
SIDE_LIMITS = {"backdrop": 2048, "layer": 2048, "light": 2048, "sprite": 256, "window": 1024}
MAX_LAYERS = 3
MAX_EMITTERS = 2
MAX_SPRITES = 128
MAX_LIGHTS = 2

# Sounds: the format the shell's mixer plays, and the menus' loudness rule.
SOUND_RATE = 48000
SOUND_CHANNELS = 2
SOUND_BITS = 16
SOUND_SECONDS = {"move": 1.0, "enter": 1.0, "back": 1.0, "insert": 3.0}
PEAK_MAX_DBFS = -1.0

BLOCKS = ("palette", "card", "world", "surfaces", "transition", "sounds")
# Blocks with files enter the package budget in this order; one that would
# exceed it is dropped and the next is still considered.
FILE_BLOCKS = ("card", "sounds", "world")

# Contrast (docs/shape.md, "Contrast"). Ratios are WCAG 2's.
TEXT_RATIO = 4.5
FOCUS_RATIO = 3.0
# Qt's 8-bit composition may round a channel by one step either way.
ROUNDING = 1 / 255
# How far a material may move its plate's colour: glass adds a sheen
# towards white, paper a grain towards black and white. The shell draws
# them within these bounds, and the proofs include them.
GLASS_SHEEN = 0.06
PAPER_GRAIN = 0.04
# Glass stays recognisably translucent or becomes as opaque as its text needs.
GLASS_MIN_OPACITY = 0.5
MATERIALS = ("solid", "glass", "paper")
# MUN's own colours for the surfaces a game may dress (services/mun-shell
# Theme.qml): text on its plate, the focus ring and a chosen entry's bar and
# label. They are what every failing set falls back to, as a whole.
NEUTRAL = {"plate": "#17181C", "text": "#DAD7D1", "focus": "#E39A63", "bar": "#DAD7D1", "bar_text": "#131417"}
# The status line, the path and the hints sit on a band of MUN's glass.
SURFACES = ("entries", "panel", "bands")
BAND_MATERIAL = "glass"
# Opaque plates tried as the midpoint of a blend whose text colour cannot be
# kept on either side of it (plan "bridge"); the plates are appended.
BRIDGES = ("#000000", "#FFFFFF")

NOTE_CODES = {
    # The package is not used (the card is unaffected).
    "shape_unreadable": "shape.json o la carpeta mun-shape no se pueden leer como archivo y carpeta normales",
    "shape_too_large": "shape.json supera 64 KiB",
    "shape_encoding": "shape.json no es UTF-8 sin BOM",
    "shape_syntax": "shape.json no es JSON válido",
    "shape_duplicate_key": "Una clave aparece dos veces en el mismo objeto",
    "shape_number": "Un número es NaN, infinito o demasiado largo",
    "shape_depth": "El JSON anida más de 6 niveles",
    "shape_string_too_long": "Un texto supera 512 caracteres",
    "shape_structure": "shape.json no es un objeto JSON",
    "shape_format": "Falta format o no tiene la forma mun-shape/N",
    "shape_format_unsupported": "La versión principal del formato no es compatible con esta consola",
    # One block is dropped; the rest of the package is used.
    "shape_field": "Falta un campo obligatorio o su tipo es incorrecto",
    "shape_range": "Un número está fuera de su intervalo",
    "shape_enum": "Un valor no es uno de los que MUN conoce",
    "shape_colour": "Un color no tiene la forma #RRGGBB",
    "shape_count": "Hay más capas, emisores, figuras o luces de las permitidas",
    "shape_path_unsafe": "Una ruta sale del paquete o no es relativa",
    "shape_path_missing": "Un archivo nombrado no existe",
    "shape_path_symlink": "Un archivo nombrado es o atraviesa un enlace simbólico",
    "shape_path_type": "Una ruta nombrada no es un archivo normal",
    "shape_file_too_large": "Un archivo supera su tamaño máximo",
    "shape_budget": "El paquete supera 32 MiB",
    "shape_png": "Una imagen no es un PNG bien formado",
    "shape_png_dimensions": "Una imagen supera sus dimensiones máximas",
    "shape_wav": "Un sonido no es WAV PCM de 48 kHz, 16 bits y estéreo",
    "shape_wav_duration": "Un sonido dura más de lo permitido",
    "shape_wav_peak": "Un sonido supera −1 dBFS",
    # The game's surface colours are replaced by MUN's, as a set.
    "shape_contrast": "Los colores del juego no garantizan el contraste; las superficies usan los de MUN",
    # Information only.
    "shape_unknown_field": "Campo desconocido; se ignora",
    "shape_minor_newer": "Versión menor posterior a la de esta consola; lo que no conoce se ignora",
}

_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
_FORMAT = re.compile(r"mun-shape/([0-9]{1,4})(?:\.([0-9]{1,4}))?")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}


@dataclass
class Note:
    """One finding. `level`: unused (the package), dropped (a block),
    fallback (the surface colours), ignored or info."""
    code: str
    level: str
    detail: str = ""
    block: Optional[str] = None
    where: str = ""

    @property
    def message(self) -> str:
        return NOTE_CODES[self.code]

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "level": self.level, "message": self.message, "detail": self.detail,
                "block": self.block, "where": self.where}


@dataclass
class ShapeResult:
    """What a console would use. `state`: none (no package), ready (every
    declared aspect used), partial (something dropped or replaced) or
    unused (the package as a whole)."""
    state: str
    shape: Optional[Dict[str, Any]] = None
    notes: List[Note] = field(default_factory=list)
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "shape": self.shape, "files": self.files,
                "notes": [note.to_dict() for note in self.notes]}

    def codes(self) -> List[str]:
        return [note.code for note in self.notes]


class _Unusable(Exception):
    def __init__(self, code: str, detail: str = "", where: str = ""):
        super().__init__(code)
        self.code, self.detail, self.where = code, detail, where


class _Invalid(Exception):
    def __init__(self, code: str, detail: str = "", where: str = ""):
        super().__init__(code)
        self.code, self.detail, self.where = code, detail, where


# ---------------------------------------------------------------- strict JSON

def _position(text: str, index: int) -> str:
    line = text.count("\n", 0, index) + 1
    column = index - (text.rfind("\n", 0, index) + 1) + 1
    return f"línea {line}, columna {column}"


def _depth(text: str) -> int:
    """Deepest nesting of objects and arrays, measured before parsing so a
    deep document never reaches the parser's recursion. String boundaries
    follow JSON's rules, so up to the first syntax error they are the
    parser's own."""
    depth = deepest = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > deepest:
                deepest = depth
                if deepest > MAX_DEPTH:
                    return deepest
        elif char in "]}":
            depth -= 1
    return deepest


def _number_int(literal: str) -> int:
    if len(literal) > MAX_NUMBER_CHARS:
        raise _Unusable("shape_number", literal[:40] + "…")
    return int(literal)


def _number_float(literal: str) -> float:
    if len(literal) > MAX_NUMBER_CHARS:
        raise _Unusable("shape_number", literal[:40] + "…")
    value = float(literal)
    if not math.isfinite(value):
        raise _Unusable("shape_number", literal)
    return value


def _constant(name: str):
    raise _Unusable("shape_number", name)


class _StrictDecoder(json.JSONDecoder):
    """The standard decoder, with the pure-Python scanner so that an object
    knows where it starts: a duplicate key is reported at its object."""

    def __init__(self):
        super().__init__(parse_float=_number_float, parse_int=_number_int, parse_constant=_constant, strict=True)

        def parse_object(s_and_end, strict, scan_once, object_hook, object_pairs_hook, memo=None, *rest):
            text, start = s_and_end
            pairs, end = json.decoder.JSONObject(s_and_end, strict, scan_once, None, list, memo)
            seen = set()
            for key, _ in pairs:
                if key in seen:
                    raise _Unusable("shape_duplicate_key", f'"{key[:40]}"', _position(text, start - 1))
                seen.add(key)
            return dict(pairs), end

        self.parse_object = parse_object
        self.scan_once = json.scanner.py_make_scanner(self)


def _long_string(value, where: str = "") -> Optional[str]:
    if isinstance(value, str):
        return where if len(value) > MAX_STRING_CHARS else None
    if isinstance(value, dict):
        for key, item in value.items():
            if len(key) > MAX_STRING_CHARS:
                return f"{where}.{key[:24]}…" if where else f"{key[:24]}…"
            found = _long_string(item, f"{where}.{key}" if where else key)
            if found is not None:
                return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _long_string(item, f"{where}[{index}]")
            if found is not None:
                return found
    return None


def parse_manifest(raw: bytes) -> Dict[str, Any]:
    """Parse shape.json under the strict profile. Raises _Unusable."""
    if len(raw) > MANIFEST_MAX_BYTES:
        raise _Unusable("shape_too_large", f"{len(raw)} bytes > {MANIFEST_MAX_BYTES}")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _Unusable("shape_encoding", "empieza por una marca BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Unusable("shape_encoding", f"byte {exc.start}")
    if _depth(text) > MAX_DEPTH:
        raise _Unusable("shape_depth", f"más de {MAX_DEPTH} niveles")
    try:
        document = _StrictDecoder().decode(text)
    except json.JSONDecodeError as exc:
        raise _Unusable("shape_syntax", exc.msg, f"línea {exc.lineno}, columna {exc.colno}")
    except RecursionError:
        raise _Unusable("shape_depth", f"más de {MAX_DEPTH} niveles")
    if not isinstance(document, dict):
        raise _Unusable("shape_structure", type(document).__name__)
    where = _long_string(document)
    if where is not None:
        raise _Unusable("shape_string_too_long", f"más de {MAX_STRING_CHARS} caracteres", where)
    return document


def format_version(document: Dict[str, Any]) -> Tuple[int, int]:
    value = document.get("format")
    if not isinstance(value, str):
        raise _Unusable("shape_format", "falta format", "format")
    match = _FORMAT.fullmatch(value)
    if not match:
        raise _Unusable("shape_format", value[:40], "format")
    major, minor = int(match.group(1)), int(match.group(2) or 0)
    if major != FORMAT_MAJOR:
        raise _Unusable("shape_format_unsupported", f"{value}; esta consola lee {FORMAT}", "format")
    return major, minor


# ------------------------------------------------------------------- schema

@dataclass
class _Field:
    check: Callable
    required: bool = False
    default: Any = None


@dataclass
class _Reference:
    path: str
    role: str
    where: str


class _Context:
    def __init__(self, block: str, notes: List[Note]):
        self.block = block
        self.notes = notes
        self.references: List[_Reference] = []


def _colour(value, where, context):
    if not isinstance(value, str) or not _COLOUR.fullmatch(value):
        raise _Invalid("shape_colour", repr(value)[:40], where)
    return value.upper()


def _enum(*values):
    def check(value, where, context):
        if not isinstance(value, str):
            raise _Invalid("shape_field", "debe ser un texto", where)
        if value not in values:
            raise _Invalid("shape_enum", f"{value[:40]}; admitidos: {', '.join(values)}", where)
        return value
    return check


def _number(low: float, high: float):
    def check(value, where, context):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _Invalid("shape_field", "debe ser un número", where)
        if not low <= value <= high:
            raise _Invalid("shape_range", f"{value} fuera de {low}–{high}", where)
        return float(value)
    return check


def _integer(*allowed, low: Optional[int] = None, high: Optional[int] = None):
    def check(value, where, context):
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Invalid("shape_field", "debe ser un número entero", where)
        if allowed and value not in allowed:
            raise _Invalid("shape_enum", f"{value}; admitidos: {', '.join(map(str, allowed))}", where)
        if low is not None and not low <= value <= high:
            raise _Invalid("shape_range", f"{value} fuera de {low}–{high}", where)
        return value
    return check


def _path(role: str):
    def check(value, where, context):
        if not isinstance(value, str):
            raise _Invalid("shape_field", "debe ser una ruta", where)
        try:
            safe_path(value, where)
        except CardError as exc:
            raise _Invalid("shape_path_unsafe", f"{value[:80]}: {exc.message}", where)
        context.references.append(_Reference(value, role, where))
        return value
    return check


def _span(value, where, context):
    """[from, to] within 0–1, from below to."""
    if not isinstance(value, list) or len(value) != 2:
        raise _Invalid("shape_field", "debe ser [desde, hasta]", where)
    low, high = (_number(0, 1)(item, f"{where}[{index}]", context) for index, item in enumerate(value))
    if not low < high:
        raise _Invalid("shape_range", f"{low} no es menor que {high}", where)
    return [low, high]


def _object(spec: Dict[str, _Field], after: Optional[Callable] = None):
    def check(value, where, context):
        if not isinstance(value, dict):
            raise _Invalid("shape_field", "debe ser un objeto", where)
        for key in value:
            if key not in spec:
                context.notes.append(Note("shape_unknown_field", "ignored", block=context.block,
                                          where=f"{where}.{key}" if where else key))
        result = {}
        for key, rule in spec.items():
            inner = f"{where}.{key}" if where else key
            if key in value:
                result[key] = rule.check(value[key], inner, context)
            elif rule.required:
                raise _Invalid("shape_field", "falta", inner)
            elif rule.default is not None:
                result[key] = rule.default() if callable(rule.default) else rule.default
        if after is not None:
            after(result, where, context)
        return result
    return check


def _list(item: Callable, low: int, high: int):
    def check(value, where, context):
        if not isinstance(value, list):
            raise _Invalid("shape_field", "debe ser una lista", where)
        if not low <= len(value) <= high:
            code = "shape_count" if len(value) > high else "shape_field"
            raise _Invalid(code, f"{len(value)} elementos; admitidos {low}–{high}", where)
        return [item(entry, f"{where}[{index}]", context) for index, entry in enumerate(value)]
    return check


def _one_backdrop(result, where, context):
    if ("image" in result) == ("gradient" in result):
        raise _Invalid("shape_field", "debe tener image o gradient, uno solo", where)


def _sprite_total(result, where, context):
    total = sum(emitter["count"] for emitter in result.get("emitters", []))
    if total > MAX_SPRITES:
        raise _Invalid("shape_count", f"{total} figuras; máximo {MAX_SPRITES}", f"{where}.emitters")


_MATERIAL = _object({"material": _Field(_enum(*MATERIALS), default="solid")})

SCHEMA: Dict[str, Callable] = {
    "palette": _object({
        "light": _Field(_colour, required=True),
        "mid": _Field(_colour, required=True),
        "deep": _Field(_colour, required=True),
        "plate": _Field(_colour, required=True),
        "text": _Field(_colour, required=True),
        "accent": _Field(_colour, required=True),
    }),
    "card": _object({
        "window": _Field(_path("window")),
        "shape": _Field(_enum("card", "organic"), default="card"),
        "morph": _Field(_number(0, 1), default=0.0),
        "glow": _Field(_colour),
    }),
    "world": _object({
        "backdrop": _Field(_object({
            "image": _Field(_path("backdrop")),
            "gradient": _Field(_list(_colour, 2, 4)),
        }, after=_one_backdrop), required=True),
        "layers": _Field(_list(_object({
            "image": _Field(_path("layer"), required=True),
            "motion": _Field(_enum("still", "drift", "parallax", "sway"), default="still"),
            "speed": _Field(_number(0, 120), default=0.0),
            "depth": _Field(_number(0, 1), default=0.5),
            "opacity": _Field(_number(0, 1), default=1.0),
        }), 0, MAX_LAYERS), default=list),
        "emitters": _Field(_list(_object({
            "sprite": _Field(_path("sprite"), required=True),
            "count": _Field(_integer(low=1, high=MAX_SPRITES), required=True),
            "path": _Field(_enum("rise", "fall", "drift", "school", "orbit"), required=True),
            "speed": _Field(_number(1, 240), default=40.0),
            "band": _Field(_span, default=lambda: [0.0, 1.0]),
            "scale": _Field(_number(0.25, 2), default=1.0),
        }), 0, MAX_EMITTERS), default=list),
        "light": _Field(_list(_object({
            "texture": _Field(_path("light"), required=True),
            "blend": _Field(_enum("screen", "add"), default="screen"),
            "motion": _Field(_enum("still", "sway", "ripple", "pulse"), default="still"),
            "opacity": _Field(_number(0, 1), default=0.5),
        }), 0, MAX_LIGHTS), default=list),
        "rate": _Field(_integer(10, 20), default=10),
    }, after=_sprite_total),
    "surfaces": _object({
        "entries": _Field(_MATERIAL, default=lambda: {"material": "solid"}),
        "panel": _Field(_MATERIAL, default=lambda: {"material": "solid"}),
    }),
    "transition": _object({
        "in": _Field(_enum("tide", "fade", "sweep"), default="fade"),
        "out": _Field(_enum("tide", "fade", "sweep"), default="fade"),
        "seconds": _Field(_number(0.8, 4), default=1.6),
    }),
    "sounds": _object({
        "move": _Field(_path("move"), required=True),
        "enter": _Field(_path("enter"), required=True),
        "back": _Field(_path("back"), required=True),
        "insert": _Field(_path("insert")),
    }),
}

_ROLE_KIND = {"window": "png", "backdrop": "png", "layer": "png", "sprite": "png", "light": "png",
              "move": "wav", "enter": "wav", "back": "wav", "insert": "wav"}


# ------------------------------------------------------------ file structure

def png_header(data: bytes) -> Dict[str, int]:
    """Check a PNG's chunk structure without inflating it: signature, IHDR
    first and well formed, every chunk's length and CRC, the critical chunks
    in order, a zlib header on the image data, IEND last with nothing after.
    Raises _Invalid("shape_png")."""
    if not data.startswith(_PNG_SIGNATURE):
        raise _Invalid("shape_png", "sin firma PNG")
    offset, index = len(_PNG_SIGNATURE), 0
    header: Dict[str, int] = {}
    seen_idat = ended_idat = seen_plte = False
    first_idat = b""
    while True:
        if offset + 12 > len(data):
            raise _Invalid("shape_png", "truncado" if index else "sin IHDR")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if length > 0x7FFFFFFF or offset + 12 + length > len(data):
            raise _Invalid("shape_png", f"bloque {kind!r} truncado")
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            raise _Invalid("shape_png", f"tipo de bloque no válido {kind!r}")
        body = data[offset + 8:offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise _Invalid("shape_png", f"CRC incorrecto en {kind.decode('ascii')}")
        if index == 0:
            if kind != b"IHDR" or length != 13:
                raise _Invalid("shape_png", "el primer bloque no es IHDR")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if not (0 < width <= 0x7FFFFFFF and 0 < height <= 0x7FFFFFFF):
                raise _Invalid("shape_png", f"dimensiones {width}×{height}")
            if depth not in _PNG_DEPTHS.get(colour, ()) or compression or filtering or interlace > 1:
                raise _Invalid("shape_png", f"IHDR no válido (profundidad {depth}, color {colour})")
            header = {"width": width, "height": height, "depth": depth, "colour": colour, "interlace": interlace}
        elif kind == b"IHDR":
            raise _Invalid("shape_png", "IHDR repetido")
        elif kind == b"PLTE":
            if seen_plte or seen_idat or header["colour"] in (0, 4) or length % 3 or not 3 <= length <= 768:
                raise _Invalid("shape_png", "PLTE no válido")
            seen_plte = True
        elif kind == b"IDAT":
            if ended_idat:
                raise _Invalid("shape_png", "bloques IDAT no consecutivos")
            if not seen_idat and header["colour"] == 3 and not seen_plte:
                raise _Invalid("shape_png", "imagen con paleta sin PLTE")
            seen_idat = True
            first_idat += body[:2 - len(first_idat)]
        elif kind == b"IEND":
            if length or not seen_idat:
                raise _Invalid("shape_png", "IEND antes de los datos")
            if offset + 12 != len(data):
                raise _Invalid("shape_png", "bytes tras IEND")
            break
        else:
            if seen_idat:
                ended_idat = True
            if 65 <= kind[0] <= 90:
                raise _Invalid("shape_png", f"bloque crítico desconocido {kind.decode('ascii')}")
        offset += 12 + length
        index += 1
    # The image data must at least start as a zlib stream: deflate, a legal
    # window and no preset dictionary.
    if len(first_idat) < 2 or first_idat[0] & 0x0F != 8 or first_idat[0] >> 4 > 7 \
            or (first_idat[0] << 8 | first_idat[1]) % 31 or first_idat[1] & 0x20:
        raise _Invalid("shape_png", "los datos no empiezan como zlib")
    return header


def wav_header(data: bytes) -> Dict[str, Any]:
    """Accept exactly RIFF/WAVE with a 16-byte PCM `fmt ` chunk (48 kHz,
    16-bit, stereo) followed by one `data` chunk and nothing else: the shell's
    reader needs no other case. Returns the duration and the peak in dBFS,
    which is None for silence (every sample zero): a finite value in any
    report, and a silent sound is valid. Raises _Invalid."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise _Invalid("shape_wav", "no es RIFF/WAVE")
    (riff_size,) = struct.unpack("<I", data[4:8])
    if riff_size != len(data) - 8:
        raise _Invalid("shape_wav", "el tamaño RIFF no coincide con el archivo")
    if data[12:16] != b"fmt " or struct.unpack("<I", data[16:20])[0] != 16:
        raise _Invalid("shape_wav", "el primer bloque no es fmt de 16 bytes (quita metadatos)")
    kind, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", data[20:36])
    if (kind, channels, rate, bits) != (1, SOUND_CHANNELS, SOUND_RATE, SOUND_BITS) \
            or byte_rate != SOUND_RATE * 4 or align != 4:
        raise _Invalid("shape_wav", f"formato {kind}, {channels} canales, {rate} Hz, {bits} bits")
    if data[36:40] != b"data":
        raise _Invalid("shape_wav", "tras fmt debe venir data (quita metadatos)")
    (size,) = struct.unpack("<I", data[40:44])
    if size != len(data) - 44 or size == 0 or size % 4:
        raise _Invalid("shape_wav", "el bloque data no ocupa el resto del archivo en muestras enteras")
    samples = array.array("h")
    samples.frombytes(data[44:])
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(max(samples), -min(samples))
    peak_dbfs = 20 * math.log10(peak / 32768) if peak else None
    return {"seconds": size / (SOUND_RATE * 4), "peak_dbfs": peak_dbfs}


# ------------------------------------------------------------------ contrast

def _rgb(colour: str) -> Tuple[float, float, float]:
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(colour) -> float:
    """Relative luminance of an sRGB colour (#RRGGBB or channels 0–1)."""
    r, g, b = _rgb(colour) if isinstance(colour, str) else colour
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)))
    return (lb + 0.05) / (la + 0.05)


def _mix(colour, towards, amount):
    return tuple(c + (t - c) * amount for c, t in zip(colour, towards))


def plate_vertices(colour: str, material: str) -> List[Tuple[float, float, float]]:
    """The colours a plate of this material can take; the proofs use their
    per-channel extremes."""
    base = _rgb(colour)
    if material == "glass":
        return [base, _mix(base, (1, 1, 1), GLASS_SHEEN)]
    if material == "paper":
        return [_mix(base, (0, 0, 0), PAPER_GRAIN), _mix(base, (1, 1, 1), PAPER_GRAIN)]
    return [base]


def composite_bounds(opacity: float, plates: Sequence[Tuple[float, float, float]]) -> Tuple[float, float]:
    """Luminance bounds of plate·opacity + world·(1 − opacity) for any world
    colour, any plate within `plates`' channel ranges and any opacity from
    `opacity` up. Each composed channel is multilinear in opacity, plate and
    world, so its extremes lie at their bounds; luminance grows with every
    channel, so the channel extremes bound it. One rounding step either way
    is included."""
    low, high = [], []
    for i in range(3):
        least = min(p[i] for p in plates)
        most = max(p[i] for p in plates)
        low.append(min(1.0, max(0.0, opacity * least - ROUNDING)))
        high.append(min(1.0, max(0.0, 1 - opacity * (1 - most) + ROUNDING)))
    return luminance(tuple(low)), luminance(tuple(high))


def proven(foregrounds: Sequence[Tuple[str, float]], opacity: float,
           plates: Sequence[Tuple[float, float, float]]) -> bool:
    """True when every (colour, ratio) keeps its ratio against every
    composite the bounds allow, all of them on the same side of it."""
    least, most = composite_bounds(opacity, plates)
    for colour, ratio in foregrounds:
        own = luminance(colour)
        lighter = own + 0.05 >= ratio * (most + 0.05)
        darker = least + 0.05 >= ratio * (own + 0.05)
        if not (lighter or darker):
            return False
    return True


def _foregrounds(colours: Dict[str, str]) -> List[Tuple[str, float]]:
    return [(colours["text"], TEXT_RATIO), (colours["focus"], FOCUS_RATIO)]


def minimum_opacity(colours: Dict[str, str], material: str) -> Optional[float]:
    """The opacity a surface's plate is drawn with so that its text and
    focus hold over any world: 1 for solid and paper, the least 8-bit step
    that holds for glass (never under GLASS_MIN_OPACITY) or for MUN's plain
    plate. None if even an opaque plate does not hold."""
    plates = plate_vertices(colours["plate"], material)
    foregrounds = _foregrounds(colours)
    if material in ("solid", "paper"):
        return 1.0 if proven(foregrounds, 1.0, plates) else None
    first = math.ceil(GLASS_MIN_OPACITY * 255) if material == "glass" else 0
    for step in range(first, 256):
        if proven(foregrounds, step / 255, plates):
            return step / 255
    return None


def _bar_holds(colours: Dict[str, str]) -> bool:
    return proven([(colours["bar_text"], TEXT_RATIO)], 1.0, plate_vertices(colours["bar"], "solid"))


def neutral_opacity() -> float:
    """MUN's own plate for dressed surfaces: its colour, plain, at the least
    opacity that keeps MUN's text and focus over any world."""
    return minimum_opacity(NEUTRAL, "plain")


def colour_set(palette: Optional[Dict[str, str]]) -> Dict[str, str]:
    """The game's surface colours: text on its plate, the accent as focus,
    and a chosen entry as the plate's colour on a bar of the text's."""
    if palette is None:
        return dict(NEUTRAL)
    return {"plate": palette["plate"], "text": palette["text"], "focus": palette["accent"],
            "bar": palette["text"], "bar_text": palette["plate"]}


def check_set(colours: Dict[str, str], materials: Dict[str, str]) -> Tuple[Optional[Dict[str, float]], str]:
    """Opacity per surface if the whole set holds on every surface, else
    (None, the first relation that fails)."""
    if not _bar_holds(colours):
        return None, f"barra: {contrast_ratio(colours['bar_text'], colours['bar']):.2f} < {TEXT_RATIO}"
    opacities = {}
    for surface in SURFACES:
        material = materials[surface]
        opacity = minimum_opacity(colours, material)
        if opacity is None:
            text = contrast_ratio(colours["text"], colours["plate"])
            focus = contrast_ratio(colours["focus"], colours["plate"])
            return None, (f"{surface} ({material}): texto {text:.2f}:1 (mínimo {TEXT_RATIO}), "
                          f"foco {focus:.2f}:1 (mínimo {FOCUS_RATIO}) sobre la placa opaca")
        opacities[surface] = opacity
    return opacities, ""


def transition_plan(neutral: Dict[str, str], neutral_alpha: float, colours: Dict[str, str],
                    material: str, alpha: float) -> Dict[str, Any]:
    """How a surface goes from MUN's plate to the game's (and back): only the
    plate blends, and its text and focus colours change at one point.

    The plate's opacity first rises to the higher of both ends' (text that
    holds at an opacity holds at any higher one), its colour then blends at
    that opacity, and the opacity settles to the destination's. Proven for
    every frame, not sampled: every plate colour of the blend, and every
    edge between the two plates, lies within the channel ranges of both
    ends, over which the bounds hold.

    neutral-text: MUN's text holds over the whole blend and changes at its
    end; shape-text: the game's text holds over it and changes at its start;
    bridge: the plate blends through an opaque colour on which both texts
    hold, the text changing there; cut: the surface changes in one frame."""
    neutral_plate = plate_vertices(neutral["plate"], "plain")
    game_plate = plate_vertices(colours["plate"], material)
    held = max(neutral_alpha, alpha)
    if proven(_foregrounds(neutral), held, neutral_plate + game_plate):
        return {"plan": "neutral-text"}
    if proven(_foregrounds(colours), held, neutral_plate + game_plate):
        return {"plan": "shape-text"}
    for bridge in BRIDGES + (neutral["plate"], colours["plate"]):
        middle = plate_vertices(bridge, "plain")
        if proven(_foregrounds(neutral), 1.0, neutral_plate + middle) \
                and proven(_foregrounds(colours), 1.0, middle + game_plate):
            return {"plan": "bridge", "bridge": bridge}
    return {"plan": "cut"}


def bar_plan(neutral: Dict[str, str], colours: Dict[str, str]) -> Dict[str, Any]:
    """The chosen entry's opaque bar: the same plans, with the label as text."""
    ends = plate_vertices(neutral["bar"], "solid") + plate_vertices(colours["bar"], "solid")
    if proven([(neutral["bar_text"], TEXT_RATIO)], 1.0, ends):
        return {"plan": "neutral-text"}
    if proven([(colours["bar_text"], TEXT_RATIO)], 1.0, ends):
        return {"plan": "shape-text"}
    return {"plan": "cut"}


def lent_focus(accent: Optional[str]) -> Tuple[str, float]:
    """MUN's focus and plate opacity when a card lends an accent
    ([presentation] accent, or the one read from its cover): the accent
    becomes the focus on MUN's plate, whose opacity rises as far as that set
    needs, if it holds at all; otherwise MUN's own set stays."""
    if accent is not None and _COLOUR.fullmatch(accent):
        candidate = dict(NEUTRAL, focus=accent.upper())
        opacity = minimum_opacity(candidate, "plain")
        if opacity is not None:
            return candidate["focus"], max(opacity, neutral_opacity())
    return NEUTRAL["focus"], neutral_opacity()


def surfaces_for(palette: Optional[Dict[str, str]], materials: Dict[str, str],
                 notes: List[Note]) -> Dict[str, Any]:
    """The surfaces' colours, opacities and transition plans a shell draws."""
    materials = dict(materials, bands=BAND_MATERIAL)
    colours = colour_set(palette)
    opacities, reason = check_set(colours, materials)
    source = "shape" if palette is not None else "neutral"
    if opacities is None:
        notes.append(Note("shape_contrast", "fallback", reason, block="palette"))
        colours, source = dict(NEUTRAL), "neutral"
        opacities, reason = check_set(colours, materials)
        assert opacities is not None, reason   # MUN's own set is verified by the tests
    base = neutral_opacity()
    result: Dict[str, Any] = {"colours": dict(colours, source=source), "neutral_opacity": round(base, 4)}
    for surface in SURFACES:
        result[surface] = {"material": materials[surface], "opacity": round(opacities[surface], 4),
                           **transition_plan(NEUTRAL, base, colours, materials[surface], opacities[surface])}
    result["bar"] = bar_plan(NEUTRAL, colours)
    return result


# ------------------------------------------------------------------ package

class _Scoped:
    """A card source seen from the package directory."""

    def __init__(self, source, base: str):
        self.source, self.base = source, base.strip("/")

    def full(self, relative: str) -> str:
        return f"{self.base}/{relative}" if self.base else relative

    def stat(self, relative: str):
        return self.source.stat(self.full(relative))

    def read(self, relative: str, limit: int) -> bytes:
        return self.source.read(self.full(relative), limit)


def _limit(role: str) -> int:
    return SOUND_MAX_BYTES if _ROLE_KIND[role] == "wav" else IMAGE_MAX_BYTES


def _stat_file(package: _Scoped, reference: _Reference, stats: Dict[str, Any]) -> int:
    """The size of one named file, from its entry alone: it must be a
    regular file, not behind a link, within its kind's limit. Nothing is
    read. Raises _Invalid."""
    if reference.path not in stats:
        try:
            info = package.stat(reference.path)
            if info is None:
                raise _Invalid("shape_path_missing", reference.path, reference.where)
            if info.kind == "symlink":
                raise _Invalid("shape_path_symlink", reference.path, reference.where)
            if info.kind != "file":
                raise _Invalid("shape_path_type", reference.path, reference.where)
            stats[reference.path] = info.size
        except _Invalid as exc:
            stats[reference.path] = exc
        except CardError as exc:
            stats[reference.path] = _Invalid("shape_path_type", f"{reference.path}: {exc.message}", reference.where)
    found = stats[reference.path]
    if isinstance(found, _Invalid):
        raise _Invalid(found.code, found.detail, reference.where)
    limit = _limit(reference.role)
    if found > limit:
        raise _Invalid("shape_file_too_large", f"{reference.path}: {found} bytes > {limit}", reference.where)
    return found


def _read_file(package: _Scoped, reference: _Reference, size: int, cache: Dict[str, Any]) -> Dict[str, Any]:
    """Read one named file (bounded by its limit) and check its header and
    its role's dimensions, duration and peak. Raises _Invalid."""
    kind = _ROLE_KIND[reference.role]
    key = (reference.path, kind)
    if key not in cache:
        try:
            data = package.read(reference.path, _limit(reference.role))
            if len(data) != size:
                raise _Invalid("shape_path_type", f"{reference.path}: cambió al leerlo", reference.where)
            details = png_header(data) if kind == "png" else wav_header(data)
            cache[key] = {"type": kind, "bytes": size, **details}
        except _Invalid as exc:
            cache[key] = exc
        except CardError as exc:
            cache[key] = _Invalid("shape_path_type", f"{reference.path}: {exc.message}", reference.where)
    found = cache[key]
    if isinstance(found, _Invalid):
        raise _Invalid(found.code, found.detail, reference.where)
    if kind == "png":
        side = SIDE_LIMITS[reference.role]
        if found["width"] > side or found["height"] > side:
            raise _Invalid("shape_png_dimensions", f"{reference.path}: {found['width']}×{found['height']} > {side}×{side}",
                           reference.where)
    else:
        seconds = SOUND_SECONDS[reference.role]
        if found["seconds"] > seconds:
            raise _Invalid("shape_wav_duration", f"{reference.path}: {found['seconds']:.2f} s > {seconds:g} s",
                           reference.where)
        if found["peak_dbfs"] is not None and found["peak_dbfs"] > PEAK_MAX_DBFS:
            raise _Invalid("shape_wav_peak", f"{reference.path}: {found['peak_dbfs']:.2f} dBFS > {PEAK_MAX_DBFS:g}",
                           reference.where)
    return found


def check_package(source, base: str = "") -> ShapeResult:
    """Check the package at `base` in `source` (a card source: stat/read of
    relative paths, never following links): the whole reading a console
    does. `base` is "<content.root>/mun-shape" on a card, "" for a folder
    that is the package itself."""
    package = _Scoped(source, base)
    notes: List[Note] = []
    try:
        if base:
            where = source.stat(base)
            if where is None:
                return ShapeResult("none")
            if where.kind != "dir":
                raise _Unusable("shape_unreadable", f"{base} no es una carpeta")
        info = package.stat(MANIFEST)
        if info is None or info.kind != "file":
            raise _Unusable("shape_unreadable", f"{MANIFEST}: {'no existe' if info is None else info.kind}")
        if info.size > MANIFEST_MAX_BYTES:
            raise _Unusable("shape_too_large", f"{info.size} bytes > {MANIFEST_MAX_BYTES}")
        document = parse_manifest(package.read(MANIFEST, MANIFEST_MAX_BYTES))
        major, minor = format_version(document)
    except _Unusable as exc:
        return ShapeResult("unused", notes=[Note(exc.code, "unused", exc.detail, where=exc.where)])
    except CardError as exc:
        return ShapeResult("unused", notes=[Note("shape_unreadable", "unused", exc.message)])
    if minor > FORMAT_MINOR:
        notes.append(Note("shape_minor_newer", "info", document["format"], where="format"))
    for key in document:
        if key != "format" and key not in SCHEMA:
            notes.append(Note("shape_unknown_field", "ignored", block=None, where=key))

    blocks: Dict[str, Any] = {}
    references: Dict[str, List[_Reference]] = {}
    for name in BLOCKS:
        if name not in document:
            continue
        context = _Context(name, notes)
        try:
            blocks[name] = SCHEMA[name](document[name], name, context)
            references[name] = context.references
        except _Invalid as exc:
            notes.append(Note(exc.code, "dropped", exc.detail, block=name, where=exc.where))

    # Each block's files are first checked from their entries (type, size
    # and the package budget), and only then read: a block that is dropped
    # for its sizes costs no read, which matters when the source is a card.
    files: Dict[str, Dict[str, Any]] = {}
    stats: Dict[str, Any] = {}
    cache: Dict[str, Any] = {}
    total = 0
    for name in FILE_BLOCKS:
        if name not in blocks:
            continue
        try:
            sizes = {ref.path: _stat_file(package, ref, stats) for ref in references[name]}
        except _Invalid as exc:
            notes.append(Note(exc.code, "dropped", exc.detail, block=name, where=exc.where))
            del blocks[name]
            continue
        added = sum(size for path, size in sizes.items() if path not in files)
        if total + added > PACKAGE_MAX_BYTES:
            notes.append(Note("shape_budget", "dropped", f"{total + added} bytes > {PACKAGE_MAX_BYTES}", block=name))
            del blocks[name]
            continue
        try:
            found = {ref.path: _read_file(package, ref, sizes[ref.path], cache) for ref in references[name]}
        except _Invalid as exc:
            notes.append(Note(exc.code, "dropped", exc.detail, block=name, where=exc.where))
            del blocks[name]
            continue
        total += added
        files.update(found)

    declared = [name for name in BLOCKS if name in document]
    if declared and not blocks:
        return ShapeResult("unused", notes=notes)
    materials = {surface: blocks.get("surfaces", {}).get(surface, {"material": "solid"})["material"]
                 for surface in ("entries", "panel")}
    shape: Dict[str, Any] = {"format": FORMAT, "declared_format": document["format"]}
    for name in BLOCKS:
        if name in blocks and name != "surfaces":
            shape[name] = blocks[name]
    shape["surfaces"] = surfaces_for(blocks.get("palette"), materials, notes)
    shape["files"] = sorted(files)
    changed = any(note.level in ("dropped", "fallback") for note in notes)
    return ShapeResult("partial" if changed else "ready", shape, notes, files)


def check_card(source, content_root: str) -> ShapeResult:
    """The package of a valid card, from its content root."""
    return check_package(source, f"{content_root.strip('/')}/{PACKAGE_DIR}")


def exit_status(result: ShapeResult) -> int:
    """The checker's exit status: 0 when every declared aspect is used,
    2 when anything is dropped, replaced or the package is unused."""
    return 0 if result.state == "ready" else 2


def world_class(shape: Optional[Dict[str, Any]]) -> str:
    """still, or the world's frame rate when anything in it moves."""
    world = (shape or {}).get("world")
    if not world:
        return "none"
    moving = world["emitters"] or any(layer["motion"] != "still" for layer in world["layers"]) \
        or any(light["motion"] != "still" for light in world["light"])
    return f"{world['rate']} fps" if moving else "still"


# Memory objectives (docs/shape.md), not measured limits: the shell's peak
# over neutral Home plus the export, per display.
MEMORY_OBJECTIVES = {"1080p": (1920, 1080, 136), "1440p": (2560, 1440, 200)}


def memory_estimate(result: ShapeResult) -> Dict[str, Dict[str, float]]:
    """Arithmetic estimate, in MiB, of what the shell would hold for this
    package at each display, by the categories of docs/shape.md. Not a
    measurement: the shell's own figures are measured with the renderer."""
    shape, files = result.shape or {}, result.files
    world = shape.get("world")
    mib = 1024 * 1024
    estimates = {}
    for name, (width, height, objective) in MEMORY_OBJECTIVES.items():
        scale = (width / 1920) ** 2
        screen = width * height * 4
        full_screen = 0
        sprites = 0.0
        if world:
            full_screen = 1 + len(world["layers"]) + len(world["light"])
            for emitter in world["emitters"]:
                details = files[emitter["sprite"]]
                sprites += details["width"] * details["height"] * 4 * scale * emitter["scale"] ** 2
        window = shape.get("card", {}).get("window")
        if window:
            sprites += files[window]["width"] * files[window]["height"] * 4 * scale
        pngs = [details for details in files.values() if details["type"] == "png"]
        categories = {
            "export": sum(details["bytes"] for details in files.values()) / mib,
            "layers": full_screen * screen / mib,
            "sprites_window": sprites / mib,
            "frames": (2 if world_class(shape).endswith("fps") else (1 if world else 0)) * screen / mib,
            "sounds": sum(details["seconds"] * SOUND_RATE * 4 for details in files.values()
                          if details["type"] == "wav") / mib,
            "decode": max((d["width"] * d["height"] * 4 for d in pngs), default=0) / mib,
            "previous": (screen / mib) if world else 0,
        }
        categories = {key: round(value, 1) for key, value in categories.items()}
        categories["total"] = round(sum(categories.values()), 1)
        categories["objective"] = objective
        estimates[name] = categories
    return estimates
