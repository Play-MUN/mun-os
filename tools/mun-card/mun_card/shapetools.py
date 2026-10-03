"""Authoring helpers for MUN Shape packages, on the host only: the palette a
console reads from a cover, a template, and one defective package per rule.

The card service never imports this module: it does not decode images. The
PNG decoder here serves the publisher's preview of the cover palette, which
the shell derives with the same algorithm (docs/shape.md, "Read level").
"""

import errno
import json
import math
import os
import secrets
import stat
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import shape
from .errors import CardError
from .validate import COVER_MAX_BYTES, COVER_MAX_SIDE

# ------------------------------------------------------------------ PNG I/O

_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _chunks(data: bytes):
    offset = 8
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        yield kind, data[offset + 8:offset + 8 + length]
        offset += 12 + length


def _unfilter(raw: bytes, width: int, height: int, bits_per_pixel: int) -> List[bytearray]:
    stride = (width * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)
    rows: List[bytearray] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        kind = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif kind != 0:
            raise CardError("png_invalid", "Filtro PNG desconocido", str(kind))
        rows.append(line)
        previous = line
    return rows


def _samples(line: bytearray, width: int, depth: int, channels: int) -> List[int]:
    """One row's samples scaled to 8 bits (16-bit samples keep their high byte)."""
    count = width * channels
    if depth == 8:
        return list(line[:count])
    if depth == 16:
        return list(line[0:count * 2:2])
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    values = []
    for i in range(count):
        byte = line[i // per_byte]
        values.append((byte >> (8 - depth * (i % per_byte + 1))) & mask)
    return values


def decode_png(data: bytes, max_side: int = 4096) -> Tuple[int, int, List[bytes]]:
    """Decode a PNG to rows of 8-bit RGB, composited over black. Every colour
    type, bit depth and Adam7 interlacing; bounded by `max_side` before
    anything is inflated, and inflated no further than the image's size.
    Host preview only. Any defect, including a stream that does not inflate,
    raises CardError("png_invalid")."""
    try:
        return _decode_png(data, max_side)
    except CardError:
        raise
    except (zlib.error, struct.error, IndexError, ValueError) as exc:
        # Every size is checked before it is unpacked; this keeps a defect
        # the checks did not foresee an error of the file, not of the tool.
        raise CardError("png_invalid", "No es un PNG válido", f"{type(exc).__name__}: {exc}")


def _decode_png(data: bytes, max_side: int) -> Tuple[int, int, List[bytes]]:
    try:
        header = shape.png_header(data)
    except shape._Invalid as exc:
        raise CardError("png_invalid", "No es un PNG válido", exc.detail)
    width, height, depth, colour = header["width"], header["height"], header["depth"], header["colour"]
    if width > max_side or height > max_side:
        raise CardError("png_too_large", "El PNG es demasiado grande para leerlo", f"{width}×{height}")
    palette, transparency, compressed = b"", None, bytearray()
    for kind, body in _chunks(data):
        if kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            if transparency is not None:
                raise CardError("png_invalid", "tRNS repetido")
            transparency = body
        elif kind == b"IDAT":
            compressed += body
    # tRNS has one size per colour type (PNG 11.3.2.1), checked before use.
    if transparency is not None:
        if colour == 0 and len(transparency) != 2 or colour == 2 and len(transparency) != 6 \
                or colour == 3 and not 1 <= len(transparency) <= len(palette) // 3 or colour in (4, 6):
            raise CardError("png_invalid", "tRNS no válido para este tipo de color",
                            f"{len(transparency)} bytes, tipo {colour}")
    transparency = transparency or b""
    channels = _CHANNELS[colour]
    bits = depth * channels
    passes = _ADAM7 if header["interlace"] else ((0, 0, 1, 1),)
    sizes = []
    expected = 0
    for x0, y0, dx, dy in passes:
        pw, ph = (width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy
        sizes.append((pw, ph))
        if pw and ph:
            expected += ph * (1 + (pw * bits + 7) // 8)
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(bytes(compressed), expected)
    except zlib.error as exc:
        raise CardError("png_invalid", "Los datos del PNG no se pueden descomprimir", str(exc))
    # Exactly the image's bytes, and then the end of a complete stream: no
    # more output, no truncated stream, nothing after it.
    if len(raw) != expected or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data:
        raise CardError("png_invalid", "Los datos del PNG no tienen el tamaño de la imagen o el flujo está incompleto")
    pixels = [bytearray(width * 3) for _ in range(height)]
    key = None
    if transparency and colour == 0:
        key = struct.unpack(">H", transparency[:2])[0]
    elif transparency and colour == 2:
        key = struct.unpack(">HHH", transparency[:6])
    offset = 0
    for (x0, y0, dx, dy), (pw, ph) in zip(passes, sizes):
        if not (pw and ph):
            continue
        size = ph * (1 + (pw * bits + 7) // 8)
        rows = _unfilter(raw[offset:offset + size], pw, ph, bits)
        offset += size
        for row_index, line in enumerate(rows):
            values = _samples(line, pw, depth, channels)
            target = pixels[y0 + row_index * dy]
            for column in range(pw):
                v = values[column * channels:(column + 1) * channels]
                if colour == 3:
                    index = v[0]
                    if index * 3 + 2 >= len(palette):
                        raise CardError("png_invalid", "Índice de paleta fuera de PLTE")
                    rgb = palette[index * 3:index * 3 + 3]
                    alpha = transparency[index] if index < len(transparency) else 255
                elif colour in (0, 4):
                    grey = v[0] * 255 // ((1 << depth) - 1) if depth < 8 else v[0]
                    rgb = (grey, grey, grey)
                    alpha = v[1] if colour == 4 else 255
                    if key is not None and _raw_sample(line, column, depth, 0, channels) == key:
                        alpha = 0
                else:
                    rgb = v[:3]
                    alpha = v[3] if colour == 6 else 255
                    if key is not None and tuple(_raw_sample(line, column, depth, i, channels)
                                                 for i in range(3)) == key:
                        alpha = 0
                x = (x0 + column * dx) * 3
                for i in range(3):
                    target[x + i] = (rgb[i] * alpha + 127) // 255
    return width, height, [bytes(row) for row in pixels]


def _raw_sample(line: bytearray, column: int, depth: int, channel: int, channels: int) -> int:
    """A sample at its own depth, for tRNS colour keys."""
    index = column * channels + channel
    if depth == 16:
        return line[index * 2] << 8 | line[index * 2 + 1]
    if depth == 8:
        return line[index]
    per_byte = 8 // depth
    return (line[index // per_byte] >> (8 - depth * (index % per_byte + 1))) & ((1 << depth) - 1)


def encode_png(width: int, height: int, rows: List[bytes], alpha: bool) -> bytes:
    """8-bit RGB or RGBA, each row filtered with Up (smooth art compresses
    well), zlib level 9."""
    stride = width * (4 if alpha else 3)
    out = bytearray()
    previous = bytes(stride)
    for row in rows:
        if len(row) != stride:
            raise ValueError("row length")
        out.append(2)
        out += bytes((a - b) & 0xFF for a, b in zip(row, previous))
        previous = row

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (shape._PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(out), 9)) + chunk(b"IEND", b""))


def encode_wav(frames: List[Tuple[int, int]]) -> bytes:
    """48 kHz, 16-bit, stereo PCM: exactly the fmt and data chunks."""
    data = b"".join(struct.pack("<hh", left, right) for left, right in frames)
    fmt = struct.pack("<HHIIHH", 1, 2, shape.SOUND_RATE, shape.SOUND_RATE * 4, 4, 16)
    return (b"RIFF" + struct.pack("<I", 4 + 24 + 8 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


# -------------------------------------------------------------- read level

READ_SIZE = 64
# Luminance bands of the sorted 64×64 pixels, as fractions of 100.
READ_BANDS = {"light": (97, 100), "hi": (82, 95), "mid": (50, 70), "low": (20, 35), "deep": (0, 8)}
ACCENT_BRIGHTNESS = 235


def _shrink(width: int, height: int, rows: List[bytes]) -> List[Tuple[int, int, int]]:
    """Box-average to 64×64 in integers: cell i covers source columns
    floor(i·W/64) up to (not including) max(that + 1, floor((i+1)·W/64)),
    rounded half up; rows alike."""
    def spans(size):
        result = []
        for i in range(READ_SIZE):
            start = i * size // READ_SIZE
            result.append((start, max(start + 1, (i + 1) * size // READ_SIZE)))
        return result
    columns, lines = spans(width), spans(height)
    cells = []
    for top, bottom in lines:
        for left, right in columns:
            total = [0, 0, 0]
            count = (bottom - top) * (right - left)
            for y in range(top, bottom):
                row = rows[y]
                for x in range(left, right):
                    total[0] += row[x * 3]
                    total[1] += row[x * 3 + 1]
                    total[2] += row[x * 3 + 2]
            cells.append(tuple((2 * t + count) // (2 * count) for t in total))
    return cells


def _warm(r: int, g: int, b: int) -> Optional[int]:
    """The accent score of a pixel if it is a vivid warm tone, else None:
    hue under 55° or over 330° and saturation over 0.35, in integers."""
    high, low = max(r, g, b), min(r, g, b)
    spread = high - low
    if spread == 0 or 20 * spread <= 7 * high or high != r:
        return None
    if g >= b and 12 * (g - b) < 11 * spread or g < b and 2 * (b - g) < spread:
        return spread
    return None


def read_palette(data: bytes) -> Dict[str, Optional[str]]:
    """The palette a console reads from a cover: the 64×64 reduction sorted
    by luminance (Rec. 709 weights on the encoded values, ties by colour),
    each band averaged, and as accent the most saturated warm tone (first
    in reading order on ties) scaled to a brightness of 235, or None."""
    if len(data) > COVER_MAX_BYTES:
        raise CardError("cover_too_large", "La portada supera el tamaño permitido", f"más de {COVER_MAX_BYTES} bytes")
    try:
        width, height, rows = decode_png(data, max_side=COVER_MAX_SIDE)
    except CardError as exc:
        code = "cover_too_large" if exc.code == "png_too_large" else "cover_invalid"
        raise CardError(code, "La portada no se puede leer", f"{exc.message}: {exc.detail}" if exc.detail else exc.message)
    cells = _shrink(width, height, rows)
    accent, best = None, 0
    for r, g, b in cells:
        score = _warm(r, g, b)
        if score is not None and score > best:
            best, accent = score, (r, g, b)
    ordered = sorted(cells, key=lambda c: (2126 * c[0] + 7152 * c[1] + 722 * c[2], c))
    count = len(ordered)
    result: Dict[str, Optional[str]] = {}
    for name, (start, end) in READ_BANDS.items():
        band = ordered[count * start // 100:count * end // 100]
        n = len(band)
        result[name] = "#" + "".join(f"{(2 * sum(c[i] for c in band) + n) // (2 * n):02X}" for i in range(3))
    if accent is not None:
        top = max(accent)
        result["accent"] = "#" + "".join(f"{min(255, (2 * v * ACCENT_BRIGHTNESS + top) // (2 * top)):02X}"
                                         for v in accent)
    else:
        result["accent"] = None
    return result


# ------------------------------------------------------------------ template

TEMPLATE_README = """# MUN Shape package

This folder becomes `content/mun-shape/` on the Game Card. `shape.json`
describes how the console dresses itself while the card is in; every file it
names lives here. The contract is `docs/shape.md` in MUN OS.

Check it at any time:

    ./mun card shape check {folder} --report

The template declares a palette, the card object, the surfaces' materials and
a transition, none of which needs a file. Add a world and sounds when their
files exist:

    "world": {{
      "backdrop": {{ "image": "world/backdrop.png" }},
      "layers": [ {{ "image": "world/far.png", "motion": "drift", "speed": 12, "depth": 0.3 }} ],
      "emitters": [ {{ "sprite": "world/speck.png", "count": 24, "path": "drift" }} ],
      "rate": 10
    }},
    "sounds": {{ "move": "sfx/move.wav", "enter": "sfx/enter.wav", "back": "sfx/back.wav" }}

Images are PNG; sounds are WAV, PCM 48 kHz, 16-bit, stereo, with only the
`fmt` and `data` chunks (for example `ffmpeg -i in.wav -ar 48000 -ac 2
-c:a pcm_s16le -fflags +bitexact -map_metadata -1 out.wav`), peaks at or
under −1 dBFS.
"""


def template(cover: Optional[bytes] = None) -> Dict[str, object]:
    """A package that uses no files. With a cover, its palette is the one the
    console would read from it, with text and plate chosen to hold."""
    palette = {"light": "#F4F1E9", "mid": "#8F8C87", "deep": "#121317",
               "plate": "#17181C", "text": "#DAD7D1", "accent": "#E39A63"}
    if cover is not None:
        read = read_palette(cover)
        palette.update(light=read["light"], mid=read["mid"], deep=read["deep"])
        palette["accent"] = read["accent"] or palette["accent"]
        # The plate is the cover's deepest tone and the text its lightest,
        # unless they do not hold together: then MUN's pair.
        candidate = dict(palette, plate=read["deep"], text=read["light"])
        opacities, _ = shape.check_set(shape.colour_set(candidate),
                                       {"entries": "glass", "panel": "solid", "bands": "glass"})
        if opacities is not None:
            palette = candidate
    return {
        "format": shape.FORMAT,
        "palette": palette,
        "card": {"shape": "card", "morph": 0, "glow": palette["light"]},
        "surfaces": {"entries": {"material": "glass"}, "panel": {"material": "solid"}},
        "transition": {"in": "fade", "out": "fade", "seconds": 1.6},
    }


def dump(document: Dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def read_cover(cover: Path) -> bytes:
    """A cover file, read bounded by the manifest's cover limit."""
    try:
        with open(cover, "rb") as handle:
            data = handle.read(COVER_MAX_BYTES + 1)
    except OSError as exc:
        raise CardError("cover_unreadable", "No se pudo leer la portada", f"{cover}: {exc.strerror}")
    if len(data) > COVER_MAX_BYTES:
        raise CardError("cover_too_large", "La portada supera el tamaño permitido", f"más de {COVER_MAX_BYTES} bytes")
    return data


def _example_files(example: Path) -> Dict[str, bytes]:
    """The regular files of a sample package, by relative path; links and
    hidden entries are not part of a sample."""
    files = {}
    for path in sorted(Path(example).rglob("*")):
        relative = path.relative_to(example)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        files[relative.as_posix()] = path.read_bytes()
    return files


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _plan(folder: Path, relatives: List[str], force: bool) -> None:
    """Check the whole destination before anything is written: no link as
    the folder, a folder on the way or a final name (dangling ones
    included), no folder or special file where a file goes, and no existing
    file unless forced. Raises CardError naming every problem."""
    links, wrong, existing = [], [], []
    info = _lstat(folder)
    if info is not None and stat.S_ISLNK(info.st_mode):
        raise CardError("shape_destination_link", "La carpeta de destino es un enlace simbólico", str(folder))
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise CardError("shape_destination_type", "El destino existe y no es una carpeta", str(folder))
    seen, blocked = set(), set()
    for relative in relatives:
        parts = relative.split("/")
        for depth in range(1, len(parts) + 1):
            name = "/".join(parts[:depth])
            if name in blocked:
                break      # already reported; nothing below it is looked at
            if name in seen:
                continue
            seen.add(name)
            entry = _lstat(folder / name) if info is not None else None
            if entry is None:
                break      # absent: everything below it is new
            final = depth == len(parts)
            if stat.S_ISLNK(entry.st_mode):
                links.append(name)
                blocked.add(name)
                break
            if final and stat.S_ISREG(entry.st_mode):
                existing.append(name)
            elif final or not stat.S_ISDIR(entry.st_mode):
                wrong.append(name)
                blocked.add(name)
                break
    if links:
        raise CardError("shape_destination_link", "El destino tiene enlaces simbólicos donde se escribiría; "
                        "no se sigue ninguno, ni con --force", ", ".join(links))
    if wrong:
        raise CardError("shape_destination_type", "El destino tiene carpetas u otros objetos donde irían archivos",
                        ", ".join(wrong))
    if existing and not force:
        raise CardError("shape_exists", "Estos archivos ya existen; usa --force para sustituirlos", ", ".join(existing))


def _lstat(path: Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


# Where a hard link cannot be made, exclusive publication is impossible.
_NO_LINKS = {getattr(errno, name) for name in ("EPERM", "ENOTSUP", "EOPNOTSUPP", "EMLINK", "EXDEV")
             if hasattr(errno, name)}


def _write_tree(folder: Path, files: Dict[str, bytes], replace: bool) -> None:
    """Write files under folder through directory descriptors, never
    following a link: each folder is opened with O_NOFOLLOW, and each file
    is written whole to a new temporary name first, then published.

    Without `replace`, publication is a hard link from the temporary to the
    name, which the kernel refuses atomically if the name exists, whatever
    it is: a file that appeared after the plan keeps its bytes and inode,
    and init stops with shape_exists. With `replace` (--force), a rename
    replaces the name only, so a file also linked elsewhere keeps its bytes
    there, and a link at the name is replaced, not followed. The temporary
    is removed in every case."""
    folder.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    root = os.open(folder, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        for relative, data in files.items():
            *directories, name = relative.split("/")
            fd = os.dup(root)
            try:
                for part in directories:
                    try:
                        os.mkdir(part, 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass
                    inner = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = inner
                temporary = f".{name}.{secrets.token_hex(4)}.tmp"
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=fd)
                try:
                    with os.fdopen(out, "wb") as handle:
                        handle.write(data)
                    if replace:
                        os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                    else:
                        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                except FileExistsError:
                    raise CardError("shape_exists", "Un archivo apareció en el destino después de la comprobación; "
                                    "se conserva y no se escribe nada más",
                                    f"{relative}" + (f"; ya escritos: {', '.join(written)}" if written else ""))
                except OSError as exc:
                    if not replace and exc.errno in _NO_LINKS:
                        raise CardError("shape_destination_unsupported",
                                        "El sistema de archivos del destino no permite publicar sin sustituir "
                                        "(enlaces duros); usa otra carpeta o --force",
                                        f"{relative}: {exc.strerror}")
                    raise
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                written.append(relative)
            finally:
                os.close(fd)
    except OSError as exc:
        raise CardError("shape_destination_changed", "El destino cambió mientras se escribía; revísalo",
                        f"{folder}: {exc.strerror}" + (f"; ya escritos: {', '.join(written)}" if written else ""))
    finally:
        os.close(root)


def init_package(folder: Path, cover: Optional[Path] = None, example: Optional[Path] = None,
                 force: bool = False) -> List[str]:
    """Write a template (or a copy of a sample package) into `folder`.

    The destination is checked as a whole first and nothing is written if
    it is refused: links (the folder itself, a folder on the way, a final
    name, a dangling one) are refused even with `force`, as is a folder or
    special file where a file goes. Without `force` no name is ever
    replaced, not even one that appears after that check (_write_tree).
    `force` replaces existing regular files at the names written, by rename
    (never truncating a file linked elsewhere); every other file is left as
    it was. Returns the files written."""
    folder = Path(folder)
    if example is not None:
        files = _example_files(Path(example))
    else:
        document = template(read_cover(Path(cover)) if cover else None)
        files = {shape.MANIFEST: dump(document).encode("utf-8"),
                 "README.md": TEMPLATE_README.format(folder=folder.name or ".").encode("utf-8")}
    _plan(folder, list(files), force)
    _write_tree(folder, files, replace=force)
    return list(files)


# ---------------------------------------------------------------- fixtures

def _tone(seconds: float, frequency: float, level: float) -> bytes:
    frames = []
    total = max(1, int(seconds * shape.SOUND_RATE))
    for n in range(total):
        envelope = min(1.0, n / 240, (total - n) / 240)
        value = int(level * 32767 * envelope * math.sin(2 * math.pi * frequency * n / shape.SOUND_RATE))
        frames.append((value, value))
    return encode_wav(frames)


def _flat_png(width: int, height: int, rgba: Tuple[int, int, int, int]) -> bytes:
    return encode_png(width, height, [bytes(rgba) * width] * height, alpha=True)


BASE_FIXTURE = {
    "format": "mun-shape/1",
    "palette": {"light": "#E8F6F8", "mid": "#3C8FA3", "deep": "#07222E",
                "plate": "#0A1C26", "text": "#F2FBFC", "accent": "#F0B45C"},
    "card": {"shape": "organic", "morph": 0.5, "glow": "#A6ECF6"},
    "world": {
        "backdrop": {"image": "world/backdrop.png"},
        "layers": [{"image": "world/layer.png", "motion": "drift", "speed": 10}],
        "emitters": [{"sprite": "world/sprite.png", "count": 12, "path": "rise"}],
        "light": [{"texture": "world/light.png", "motion": "sway", "opacity": 0.4}],
        "rate": 10,
    },
    "surfaces": {"entries": {"material": "glass"}, "panel": {"material": "solid"}},
    "transition": {"in": "tide", "out": "fade", "seconds": 2.0},
    "sounds": {"move": "sfx/move.wav", "enter": "sfx/enter.wav", "back": "sfx/back.wav"},
}


def _base_files() -> Dict[str, bytes]:
    return {
        "world/backdrop.png": _flat_png(64, 36, (8, 40, 56, 255)),
        "world/layer.png": _flat_png(64, 36, (4, 30, 40, 160)),
        "world/sprite.png": _flat_png(8, 8, (200, 240, 250, 200)),
        "world/light.png": _flat_png(32, 18, (255, 255, 255, 60)),
        "sfx/move.wav": _tone(0.05, 880, 0.5),
        "sfx/enter.wav": _tone(0.08, 660, 0.5),
        "sfx/back.wav": _tone(0.08, 440, 0.5),
    }


def _big_png(width: int, height: int, padding: int) -> bytes:
    """A small valid image made large by an ancillary chunk of padding."""
    data = _flat_png(width, height, (0, 0, 0, 255))
    body = b"pad " + b"\0" * padding
    kind = b"zTXt"
    extra = struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return data[:33] + extra + data[33:]


def _bomb_png() -> bytes:
    """IHDR says 30000×30000; the data is a few bytes (a decompression-bomb shape)."""
    data = bytearray(_flat_png(1, 1, (0, 0, 0, 255)))
    data[16:24] = struct.pack(">II", 30000, 30000)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])) & 0xFFFFFFFF)
    return bytes(data)


def _set(document, path: str, value) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        document = document[int(key)] if isinstance(document, list) else document[key]
    last = keys[-1]
    if isinstance(document, list):
        document[int(last)] = value
    else:
        document[last] = value


# name: (description, code, block or None for the package, mutation)
# A mutation receives (document, files, folder) and may return raw
# manifest bytes to write instead of the document.
FIXTURES = {
    "not-json": ("shape.json no es JSON", "shape_syntax", None,
                 lambda d, f, p: b'{"format": "mun-shape/1", palette: }'),
    "duplicate-key": ("una clave repetida en palette", "shape_duplicate_key", None,
                      lambda d, f, p: shape_bytes(d).replace(b'"text": "#F2FBFC"', b'"text": "#F2FBFC", "text": "#000000"')),
    "nan": ("card.morph = NaN", "shape_number", None,
            lambda d, f, p: shape_bytes(d).replace(b'"morph": 0.5', b'"morph": NaN')),
    "infinite": ("card.morph = 1e999", "shape_number", None,
                 lambda d, f, p: shape_bytes(d).replace(b'"morph": 0.5', b'"morph": 1e999')),
    "deep": ("siete niveles de anidamiento", "shape_depth", None,
             lambda d, f, p: _set(d, "extra", [[[[[[1]]]]]])),
    "long-string": ("un texto de 600 caracteres", "shape_string_too_long", None,
                    lambda d, f, p: _set(d, "note", "x" * 600)),
    "not-utf8": ("un byte que no es UTF-8", "shape_encoding", None,
                 lambda d, f, p: shape_bytes(d).replace(b"organic", b"organ\xffc")),
    "bom": ("empieza por una marca BOM", "shape_encoding", None,
            lambda d, f, p: b"\xef\xbb\xbf" + shape_bytes(d)),
    "too-large": ("shape.json de más de 64 KiB", "shape_too_large", None,
                  lambda d, f, p: shape_bytes(d) + b" " * shape.MANIFEST_MAX_BYTES),
    "not-object": ("el documento es una lista", "shape_structure", None,
                   lambda d, f, p: b"[1, 2, 3]"),
    "unknown-major": ("format = mun-shape/2", "shape_format_unsupported", None,
                      lambda d, f, p: _set(d, "format", "mun-shape/2")),
    "no-format": ("sin format", "shape_format", None,
                  lambda d, f, p: d.pop("format")),
    "unknown-enum": ("world.layers[0].motion = spiral", "shape_enum", "world",
                     lambda d, f, p: _set(d, "world.layers.0.motion", "spiral")),
    "out-of-range": ("card.morph = 1.5", "shape_range", "card",
                     lambda d, f, p: _set(d, "card.morph", 1.5)),
    "bad-colour": ("palette.accent = #GG0000", "shape_colour", "palette",
                   lambda d, f, p: _set(d, "palette.accent", "#GG0000")),
    "missing-field": ("palette sin text", "shape_field", "palette",
                      lambda d, f, p: d["palette"].pop("text")),
    "missing-file": ("sounds.move nombra un archivo que no existe", "shape_path_missing", "sounds",
                     lambda d, f, p: _set(d, "sounds.move", "sfx/none.wav")),
    "symlink": ("world/backdrop.png es un enlace simbólico", "shape_path_symlink", "world",
                lambda d, f, p: f.update({"world/backdrop.png": ("symlink", "/etc/passwd")})),
    "dotdot": ("card.window = ../mun.toml", "shape_path_unsafe", "card",
               lambda d, f, p: _set(d, "card.window", "../mun.toml")),
    "absolute": ("card.window = /etc/passwd", "shape_path_unsafe", "card",
                 lambda d, f, p: _set(d, "card.window", "/etc/passwd")),
    "directory": ("world.layers[0].image es una carpeta", "shape_path_type", "world",
                  lambda d, f, p: f.update({"world/layer.png": ("dir", None)})),
    "oversized-file": ("un sprite de más de 4 MiB", "shape_file_too_large", "world",
                       lambda d, f, p: f.update({"world/sprite.png": _big_png(8, 8, shape.IMAGE_MAX_BYTES)})),
    "png-false-header": ("backdrop.png con firma PNG y un IHDR falso", "shape_png", "world",
                         lambda d, f, p: f.update({"world/backdrop.png": shape._PNG_SIGNATURE + b"\0\0\0\x0dIHDRnot really"})),
    "png-bad-crc": ("backdrop.png con un CRC incorrecto", "shape_png", "world",
                    lambda d, f, p: f.update({"world/backdrop.png": f["world/backdrop.png"][:-5] + b"X" + f["world/backdrop.png"][-4:]})),
    "png-truncated": ("backdrop.png cortado a la mitad", "shape_png", "world",
                      lambda d, f, p: f.update({"world/backdrop.png": f["world/backdrop.png"][:60]})),
    "png-bomb": ("backdrop.png declara 30000×30000", "shape_png_dimensions", "world",
                 lambda d, f, p: f.update({"world/backdrop.png": _bomb_png()})),
    "sprite-too-large": ("un sprite de 512×512", "shape_png_dimensions", "world",
                         lambda d, f, p: f.update({"world/sprite.png": _flat_png(512, 512, (0, 0, 0, 0))})),
    "wav-other-format": ("move.wav a 44,1 kHz mono", "shape_wav", "sounds",
                         lambda d, f, p: f.update({"sfx/move.wav": _mono_wav()})),
    "wav-metadata": ("move.wav con un bloque LIST", "shape_wav", "sounds",
                     lambda d, f, p: f.update({"sfx/move.wav": _wav_with_list(f["sfx/move.wav"])})),
    "wav-too-long": ("move.wav de 1,5 s", "shape_wav_duration", "sounds",
                     lambda d, f, p: f.update({"sfx/move.wav": _tone(1.5, 440, 0.5)})),
    "wav-too-loud": ("move.wav con pico de 0 dBFS", "shape_wav_peak", "sounds",
                     lambda d, f, p: f.update({"sfx/move.wav": _tone(0.05, 440, 1.0)})),
    "too-many-layers": ("cuatro capas", "shape_count", "world",
                        lambda d, f, p: d["world"]["layers"].extend([dict(d["world"]["layers"][0])] * 3)),
    "too-many-sprites": ("dos emisores de 100 figuras", "shape_count", "world",
                         lambda d, f, p: d["world"].update(emitters=[
                             {"sprite": "world/sprite.png", "count": 100, "path": "rise"},
                             {"sprite": "world/sprite.png", "count": 100, "path": "fall"}])),
    "low-contrast": ("texto casi del color de la placa", "shape_contrast", "palette",
                     lambda d, f, p: _set(d, "palette.text", "#24343C")),
    "accent-is-plate": ("el acento es el color de la placa", "shape_contrast", "palette",
                        lambda d, f, p: _set(d, "palette.accent", "#0A1C26")),
    "light-on-light": ("texto claro sobre una placa clara", "shape_contrast", "palette",
                       lambda d, f, p: d["palette"].update(plate="#F4F4F4", text="#FFFFFF")),
    "unknown-field": ("un campo que esta versión no conoce: se ignora", "shape_unknown_field", None,
                      lambda d, f, p: _set(d, "card.sparkle", True)),
    "newer-minor": ("mun-shape/1.4 con un bloque nuevo: se usa lo conocido", "shape_minor_newer", None,
                    lambda d, f, p: (_set(d, "format", "mun-shape/1.4"), _set(d, "gallery", {"images": []}))),
}
# Fixtures that leave the package fully used, with a note.
FIXTURE_READY = ("unknown-field", "newer-minor")


def _mono_wav() -> bytes:
    data = struct.pack("<h", 1000) * 2205
    fmt = struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt \
        + b"data" + struct.pack("<I", len(data)) + data


def _wav_with_list(wav: bytes) -> bytes:
    info = b"LIST" + struct.pack("<I", 12) + b"INFOISFT" + struct.pack("<I", 0)
    body = wav[12:36] + info + wav[36:]
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def shape_bytes(document) -> bytes:
    return dump(document).encode("utf-8")


def write_fixture(folder: Path, name: str) -> Path:
    """Write the base package with one defect into folder/name."""
    if name not in FIXTURES:
        raise CardError("variant_unknown", f"Variante desconocida: {name}", ", ".join(FIXTURES))
    target = Path(folder) / name
    if target.exists():
        raise CardError("shape_exists", f"{target} ya existe")
    document = json.loads(json.dumps(BASE_FIXTURE))
    files: Dict[str, object] = _base_files()
    manifest = FIXTURES[name][3](document, files, target)
    raw = manifest if isinstance(manifest, bytes) else shape_bytes(document)
    target.mkdir(parents=True)
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple) and content[0] == "symlink":
            os.symlink(content[1], path)
        elif isinstance(content, tuple) and content[0] == "dir":
            path.mkdir()
        else:
            path.write_bytes(content)
    (target / shape.MANIFEST).write_bytes(raw)
    return target


# ------------------------------------------------------- a package on a card

def named_files(document: Dict[str, object]) -> List[str]:
    """Every file a package's document names, whether the checker keeps its
    block or not: the card's window, the sounds and the world's images."""
    names: List[str] = []

    def take(value) -> None:
        if isinstance(value, str):
            names.append(value)

    def items(value) -> List[dict]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    card = document.get("card")
    if isinstance(card, dict):
        take(card.get("window"))
    sounds = document.get("sounds")
    if isinstance(sounds, dict):
        for name in ("move", "enter", "back", "insert"):
            take(sounds.get(name))
    world = document.get("world")
    if isinstance(world, dict):
        backdrop = world.get("backdrop")
        if isinstance(backdrop, dict):
            take(backdrop.get("image"))
        for layer in items(world.get("layers")):
            take(layer.get("image"))
        for emitter in items(world.get("emitters")):
            take(emitter.get("sprite"))
        for light in items(world.get("light")):
            take(light.get("texture"))
    return sorted(set(names))


def stage_package(folder: Path, into: Path):
    """Copy the package in `folder` to `into` (a staged card's
    content/mun-shape/) as a console would find it, and return the checker's
    result for the folder.

    Only shape.json and the files it names are copied, whatever their block's
    verdict, so the card says what the publisher declared and the console
    keeps or drops exactly what the folder's check says; anything else in the
    folder (a README, source art) stays out. Only regular files are copied,
    never through a link, under the contract's path rules, and none larger
    than the whole package may be; a name that breaks those rules is left out
    and the checker's notes say why."""
    from .source import DirectorySource    # a card source over a folder; never follows links
    from .validate import safe_path

    folder = Path(folder)
    info = os.lstat(folder) if os.path.lexists(folder) else None
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise CardError("shape_folder_missing", f"No existe la carpeta {folder}")
    source = DirectorySource(folder)
    result = shape.check_package(source)
    into = Path(into)
    if os.path.lexists(into):
        raise CardError("shape_conflict", f"El contenido de la tarjeta ya tiene {shape.PACKAGE_DIR}/",
                        "quítalo del directorio de contenido o no uses --shape")
    into.mkdir(parents=True)
    manifest = source.stat(shape.MANIFEST)
    if manifest is None or manifest.kind != "file":
        return result
    raw = source.read(shape.MANIFEST, shape.MANIFEST_MAX_BYTES + 1)
    (into / shape.MANIFEST).write_bytes(raw)
    try:
        document = shape.parse_manifest(raw)
    except Exception:          # the checker has reported it; nothing else is named
        return result
    for name in named_files(document):
        try:
            safe_path(name, name)
        except CardError:
            continue
        entry = source.stat(name)
        if entry is None or entry.kind != "file" or entry.size > shape.PACKAGE_MAX_BYTES:
            continue
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read(name, entry.size))
    return result


def clone_image(source: Path, destination: Path) -> None:
    """A copy of a card image: a copy-on-write clone where the filesystem has
    them (APFS, Btrfs, XFS), else a plain copy. The source is only read."""
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise CardError("image_exists", f"{destination} ya existe")
    for command in (["cp", "-c", str(source), str(destination)],                 # macOS: clonefile
                    ["cp", "--reflink=auto", str(source), str(destination)]):    # Linux
        try:
            if __import__("subprocess").run(command, capture_output=True).returncode == 0:
                return
        except OSError:
            pass
        if os.path.lexists(destination):
            os.unlink(destination)
    import shutil
    shutil.copyfile(source, destination)


def replace_package(image_path: Path, content_root: str, staged: Path) -> None:
    """Replace `<content_root>/mun-shape/` on a card image with the folder
    `staged` (what stage_package wrote), with debugfs, without mounting.

    For disposable copies only (the laboratory's preview makes one from a
    base card): it writes the image in place, so it is never given a card a
    player keeps. Only the package's own folder is listed, removed and
    written; the rest of the card is not touched. Read back afterwards: every
    file there, with its size."""
    import subprocess
    import tempfile
    from .image import find_tool
    from .source import DebugfsSource

    debugfs = find_tool("debugfs")
    source = DebugfsSource(image_path, debugfs)
    base = f"{content_root.strip('/')}/{shape.PACKAGE_DIR}"
    commands: List[str] = []
    existing = source.stat(base)
    if existing is not None and existing.kind == "dir":
        found: List[Tuple[str, str]] = []
        pending = [base]
        while pending:
            directory = pending.pop()
            for name, kind, _ in source.list(directory):
                path = f"{directory}/{name}"
                found.append((path, kind))
                if kind == "dir":
                    pending.append(path)
        for path, kind in sorted(found, key=lambda item: item[0].count("/"), reverse=True):
            commands.append(f"{'rmdir' if kind == 'dir' else 'rm'} /{path}")
        commands.append(f"rmdir /{base}")
    elif existing is not None:
        commands.append(f"rm /{base}")
    staged = Path(staged)
    wanted: Dict[str, int] = {}
    commands.append(f"mkdir /{base}")
    for path in sorted(staged.rglob("*"), key=lambda item: (item.relative_to(staged).as_posix().count("/"), str(item))):
        relative = f"{base}/{path.relative_to(staged).as_posix()}"
        if path.is_dir():
            commands.append(f"mkdir /{relative}")
        else:
            commands.append(f"write {path} /{relative}")
            wanted[relative] = path.stat().st_size
    with tempfile.NamedTemporaryFile("w", suffix=".debugfs", delete=False) as script:
        script.write("\n".join(commands) + "\n")
    try:
        subprocess.run([debugfs, "-w", "-f", script.name, str(image_path)], capture_output=True, check=False)
    finally:
        os.unlink(script.name)
    for relative, size in wanted.items():
        entry = source.stat(relative)
        if entry is None or entry.kind != "file" or entry.size != size:
            raise CardError("shape_write_failed", "No se pudo escribir el paquete en la copia de la tarjeta", relative)
