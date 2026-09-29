"""Manifest v0 validation. Treats every byte on the card as untrusted."""

import re
import struct
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from . import minitoml
from .errors import CardError
from .source import DirectorySource  # noqa: F401  (re-exported for callers)

# Two naming generations of card format v0 (docs/game-cards.md): the manifest's file
# name tells them apart and decides the save envelope's format. Same layout,
# fields and card.schema; only the names differ.
MANIFEST_NAMES = {"mun": "mun.toml", "earlier": "neptune.toml"}
SAVE_FORMATS = {"mun": "mun-save/1", "earlier": "neptune-save/1"}
SCHEMA_VERSIONS = (1,)
KINDS = ("test", "game")
ARCHITECTURES = ("aarch64",)
# linux-arm64-gl-v0 is provisional (docs/runtime.md): measured in QEMU guests
# only, not yet qualified on the official hardware.
PROFILES = ("linux-arm64-v0", "linux-arm64-gl-v0")
# How a game reaches its content during play (docs/runtime.md): `copy` stages the
# entry alone into RAM (docs/runtime.md); `mount` additionally gives the unit a
# read-only view of content.root. Default copy, as before.
ACCESS_MODES = ("copy", "mount")

MANIFEST_MAX_BYTES = 64 * 1024
COVER_MAX_BYTES = 1024 * 1024
COVER_MAX_SIDE = 1024
TITLE_MAX_CHARS = 120

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
_VERSION = re.compile(r"[0-9]+(\.[0-9]+){0,3}([-+][0-9A-Za-z.-]{1,32})?")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# [presentation] colours: the shell tints its accent and glow with them while
# the card is the active one, and reverts when it goes. sRGB hex only.
_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
# [saves] for games that write their own files (docs/saves.md). The directory is
# relative to the game's HOME, so dot-directories such as ".mygame" are
# allowed; unit patterns name files of that one directory, with "*" as the
# only wildcard.
_HOME_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,128}")
_UNIT_PATTERN = re.compile(r"[A-Za-z0-9._*-]{1,64}")
SAVE_CHECKS = ("zlib-xml", "zlib", "xml", "any")
DIRECTORY_SAVES_MAX_BYTES = 8 * 1024 * 1024
MAX_SAVE_UNITS = 8


@dataclass
class CardInfo:
    """What the console knows about a valid card. Serialisable as-is."""
    schema: int
    id: str
    title: str
    version: str
    kind: str
    arch: str
    profile: str
    root: str
    entry: Optional[str]
    cover: Optional[str]
    access: str
    accent: Optional[str]
    background: Optional[str]
    saves_directory: Optional[str]
    saves_units: Optional[List[str]]
    saves_checks: Optional[List[str]]
    saves_max_bytes: Optional[int]
    saves: str
    # True when the card declares an executable entry (kind = game). Whether the
    # console can actually launch it is the launcher's decision at launch time.
    runnable: bool = False
    # Which names the card uses (docs/game-cards.md): "mun" (mun.toml, mun-save/1) or
    # "earlier" (neptune.toml, neptune-save/1). Not the card service's
    # insertion counter, which it calls generation.
    naming: str = "earlier"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_card(source) -> CardInfo:
    """Validate a card exposed through a source. Raises CardError on the first problem."""
    naming, manifest_name, entry = manifest_of(source)
    if entry.kind == "symlink":
        raise CardError("path_symlink", f"{manifest_name} es un enlace simbólico")
    if entry.kind != "file":
        raise CardError("path_type", f"{manifest_name} no es un archivo")
    if entry.size > MANIFEST_MAX_BYTES:
        raise CardError("manifest_too_large", "El manifiesto supera el tamaño permitido",
                        f"{entry.size} bytes > {MANIFEST_MAX_BYTES}")
    raw = source.read(manifest_name, MANIFEST_MAX_BYTES)
    if len(raw) > MANIFEST_MAX_BYTES:
        raise CardError("manifest_too_large", "El manifiesto supera el tamaño permitido")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CardError("manifest_syntax", "El manifiesto no es UTF-8 válido", str(exc))
    try:
        data = minitoml.loads(text)
    except minitoml.TomlSyntaxError as exc:
        raise CardError("manifest_syntax", "El manifiesto no es TOML válido", str(exc))

    card = _table(data, "card")
    content = _table(data, "content")
    saves = data.get("saves", {})
    if not isinstance(saves, dict):
        raise CardError("manifest_field", "[saves] debe ser una tabla")

    schema = _field(card, "card", "schema", int)
    if schema not in SCHEMA_VERSIONS:
        raise CardError("schema_unsupported", "La versión de esquema del manifiesto no es compatible",
                        f"schema {schema}; admitidas: {', '.join(map(str, SCHEMA_VERSIONS))}")
    card_id = _field(card, "card", "id", str)
    if not _ID.fullmatch(card_id):
        raise CardError("id_invalid", "El identificador de la tarjeta no es válido",
                        "3-64 caracteres: minúsculas, dígitos, punto, guion o guion bajo")
    title = _field(card, "card", "title", str).strip()
    if not title or len(title) > TITLE_MAX_CHARS:
        raise CardError("manifest_field", "El título está vacío o es demasiado largo")

    version = _field(content, "content", "version", str)
    if not _VERSION.fullmatch(version):
        raise CardError("version_invalid", "La versión del contenido no tiene un formato válido", version)
    kind = _field(content, "content", "kind", str)
    if kind not in KINDS:
        raise CardError("kind_unsupported", "El tipo de contenido no es compatible", kind)
    arch = _field(content, "content", "arch", str)
    if arch not in ARCHITECTURES:
        raise CardError("arch_unsupported", "La arquitectura del contenido no es compatible", arch)
    profile = _field(content, "content", "profile", str)
    if profile not in PROFILES:
        raise CardError("profile_unsupported", "El perfil de ejecución no es compatible", profile)

    root = _safe_path(_field(content, "content", "root", str), "content.root")
    _require(source, root, "dir", "content.root")

    entry_path = None
    if "entry" in content:
        entry_path = _safe_path(_field(content, "content", "entry", str), "content.entry")
        _require(source, entry_path, "file", "content.entry")
    elif kind == "game":
        raise CardError("manifest_field", "Un contenido de tipo game necesita content.entry")

    cover = None
    if "cover" in content:
        cover = _safe_path(_field(content, "content", "cover", str), "content.cover")
        _check_cover(source, cover)

    access = "copy"
    if "access" in content:
        access = _field(content, "content", "access", str)
        if access not in ACCESS_MODES:
            raise CardError("manifest_field", "content.access debe ser copy o mount", access)

    accent = background = None
    presentation = data.get("presentation")
    if presentation is not None:
        if not isinstance(presentation, dict):
            raise CardError("manifest_field", "[presentation] debe ser una tabla")
        accent = _colour(presentation, "accent")
        background = _colour(presentation, "background")

    saves_location = "saves"
    if "location" in saves:
        saves_location = _safe_path(_field(saves, "saves", "location", str), "saves.location")
    existing = source.stat(saves_location)
    if existing is not None and existing.kind != "dir":
        raise CardError("path_type", "saves.location existe pero no es una carpeta", saves_location)
    saves_directory, saves_units, saves_checks, saves_max_bytes = _directory_saves(saves, kind)

    return CardInfo(schema=schema, id=card_id, title=title, version=version, kind=kind,
                    arch=arch, profile=profile, root=root, entry=entry_path, cover=cover, access=access,
                    accent=accent, background=background,
                    saves_directory=saves_directory, saves_units=saves_units,
                    saves_checks=saves_checks, saves_max_bytes=saves_max_bytes,
                    saves=saves_location, runnable=(kind == "game" and entry_path is not None),
                    naming=naming)


def manifest_of(source):
    """(naming, manifest file name, its entry) for the card's generation.

    Both names at the root are refused whatever either file holds: which one
    would be authoritative cannot be decided from untrusted media."""
    found = {naming: entry for naming, name in MANIFEST_NAMES.items()
             if (entry := source.stat(name)) is not None}
    if len(found) > 1:
        raise CardError("manifest_ambiguous", "La tarjeta tiene mun.toml y neptune.toml a la vez",
                        "una tarjeta lleva un solo manifiesto")
    if not found:
        raise CardError("manifest_missing", "No se encontró mun.toml ni neptune.toml en la tarjeta")
    naming, entry = next(iter(found.items()))
    return naming, MANIFEST_NAMES[naming], entry


def save_format(info) -> str:
    """The save envelope format of a card's generation; a card described
    without one (an earlier record) is of the earlier one."""
    naming = info.get("naming") if isinstance(info, dict) else getattr(info, "naming", None)
    return SAVE_FORMATS.get(naming or "earlier", SAVE_FORMATS["earlier"])


def _directory_saves(saves: Dict[str, Any], kind: str):
    """[saves] directory/units/checks/max_bytes, all or nothing (docs/saves.md)."""
    keys = ("directory", "units", "checks", "max_bytes")
    present = [key for key in keys if key in saves]
    if not present:
        return None, None, None, None
    if len(present) != len(keys):
        missing = ", ".join(f"saves.{key}" for key in keys if key not in saves)
        raise CardError("manifest_field", "Una partida de directorio necesita saves.directory, units, checks y max_bytes",
                        f"falta {missing}")
    if kind != "game":
        raise CardError("manifest_field", "Solo un juego puede declarar partidas de directorio")
    directory = _field(saves, "saves", "directory", str)
    parts = directory.split("/")
    if not directory or len(directory) > 255 or directory.startswith("/") or any(
            part in ("", ".", "..") or not _HOME_SEGMENT.fullmatch(part) for part in parts):
        raise CardError("path_unsafe", "saves.directory debe ser una ruta relativa al directorio del juego", directory)
    units = saves["units"]
    checks = saves["checks"]
    if not isinstance(units, list) or not units or len(units) > MAX_SAVE_UNITS \
            or not all(isinstance(u, str) and _UNIT_PATTERN.fullmatch(u) and u.strip("*") for u in units):
        raise CardError("manifest_field", f"saves.units debe ser una lista de 1 a {MAX_SAVE_UNITS} patrones de nombre de archivo",
                        str(units)[:80])
    if not isinstance(checks, list) or len(checks) != len(units) or not all(c in SAVE_CHECKS for c in checks):
        raise CardError("manifest_field", "saves.checks debe tener una comprobación por patrón: " + ", ".join(SAVE_CHECKS),
                        str(checks)[:80])
    max_bytes = _field(saves, "saves", "max_bytes", int)
    if not 1024 <= max_bytes <= DIRECTORY_SAVES_MAX_BYTES:
        raise CardError("manifest_field", f"saves.max_bytes debe estar entre 1024 y {DIRECTORY_SAVES_MAX_BYTES}", str(max_bytes))
    return directory, list(units), list(checks), max_bytes


def _colour(table: Dict[str, Any], key: str) -> Optional[str]:
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str) or not _COLOUR.fullmatch(value):
        raise CardError("manifest_field", f"presentation.{key} debe ser un color #RRGGBB", str(value)[:32])
    return value.upper()


def _table(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    table = data.get(name)
    if not isinstance(table, dict):
        raise CardError("manifest_field", f"Falta la tabla [{name}]")
    return table


def _field(table: Dict[str, Any], table_name: str, key: str, kind):
    if key not in table:
        raise CardError("manifest_field", f"Falta {table_name}.{key}")
    value = table[key]
    if kind is int and isinstance(value, bool) or not isinstance(value, kind):
        raise CardError("manifest_field", f"{table_name}.{key} debe ser {kind.__name__}")
    return value


def _safe_path(value: str, field: str) -> str:
    """Accept only plain relative POSIX paths that stay inside the card."""
    if not value or len(value) > 255:
        raise CardError("path_unsafe", f"{field} está vacío o es demasiado largo")
    if value.startswith("/") or "\\" in value or "\x00" in value:
        raise CardError("path_unsafe", f"{field} debe ser una ruta relativa", value)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise CardError("path_unsafe", f"{field} contiene segmentos no permitidos", value)
    # A conservative alphabet: card paths are identifiers, not free text. This
    # also keeps them safe to hand to external tools such as debugfs.
    if not all(_SEGMENT.fullmatch(part) for part in parts):
        raise CardError("path_unsafe", f"{field} contiene caracteres no permitidos", value)
    return value


def _require(source, relative: str, kind: str, field: str) -> None:
    info = source.stat(relative)
    if info is None:
        raise CardError("path_missing", f"{field} no existe en la tarjeta", relative)
    if info.kind == "symlink":
        raise CardError("path_symlink", f"{field} es o atraviesa un enlace simbólico", relative)
    if info.kind != kind:
        expected = "una carpeta" if kind == "dir" else "un archivo"
        raise CardError("path_type", f"{field} debe ser {expected}", relative)


def _check_cover(source, relative: str) -> None:
    info = source.stat(relative)
    if info is None:
        raise CardError("path_missing", "content.cover no existe en la tarjeta", relative)
    if info.kind == "symlink":
        raise CardError("path_symlink", "content.cover es un enlace simbólico", relative)
    if info.kind != "file":
        raise CardError("path_type", "content.cover debe ser un archivo", relative)
    if info.size > COVER_MAX_BYTES:
        raise CardError("cover_too_large", "La portada supera el tamaño permitido",
                        f"{info.size} bytes > {COVER_MAX_BYTES}")
    # Only the header is needed to reject oversized images before anything decodes them.
    head = source.read(relative, 32)
    if len(head) < 24 or not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        raise CardError("cover_invalid", "La portada no es un PNG válido", relative)
    width, height = struct.unpack(">II", head[16:24])
    if width == 0 or height == 0 or width > COVER_MAX_SIDE or height > COVER_MAX_SIDE:
        raise CardError("cover_too_large", "La portada supera las dimensiones permitidas",
                        f"{width}×{height} > {COVER_MAX_SIDE}")
