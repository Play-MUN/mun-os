"""What makes a console save envelope valid (docs/saves.md, docs/game-cards.md).

Shared by the card service, which checks every save it writes and reads,
and by the card tool's converter, which refuses to carry a save the console
would not restore. `envelope_problem` for a directory save is the launcher's
restore rule (DirectorySync._check_envelope); a host test keeps the two
deciding alike, since the launcher does not ship this package."""

import base64
import fnmatch
import hashlib
import re
import zlib
from typing import Optional
from xml.etree import ElementTree

SAVE_MAX_BYTES = 64 * 1024            # a game's payload
SAVE_FILE_MAX_BYTES = 80 * 1024       # the envelope on the card, and what staging copies back
# Directory saves (docs/saves.md): the card declares the bound on the raw files;
# the envelope carries them in base64 (4/3) plus names and hashes.
FILES_PAYLOAD_MAX_FILES = 64
DIRECTORY_SAVE_SCHEMA = 1
INFLATE_LIMIT = 64 * 1024 * 1024      # a unit that inflates beyond this is not a save
_FILE_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _field(info, name):
    return info.get(name) if isinstance(info, dict) else getattr(info, name, None)


def save_bounds(info) -> "tuple[int, int]":
    """(payload, envelope file) bounds in bytes for this card's saves."""
    raw = _field(info, "saves_max_bytes")
    if not raw:
        return SAVE_MAX_BYTES, SAVE_FILE_MAX_BYTES
    payload = (int(raw) * 4 + 2) // 3 + 64 * 1024
    return payload, payload + 8 * 1024


def unit_check(check: str, data: bytes) -> Optional[str]:
    """Why `data` is not one complete unit under `check` (docs/saves.md), or None.
    The launcher's rule, word for word."""
    if check == "any":
        return None
    if check in ("zlib", "zlib-xml"):
        inflater, pending, total, parts = zlib.decompressobj(), data, 0, []
        try:
            while True:
                chunk = inflater.decompress(pending, 1 << 20)
                total += len(chunk)
                if total > INFLATE_LIMIT:
                    return "se descomprime más allá del límite"
                if check == "zlib-xml":
                    parts.append(chunk)
                pending = inflater.unconsumed_tail
                if inflater.eof or (not chunk and not pending):
                    break
        except zlib.error as exc:
            return f"no es un flujo zlib válido ({exc})"
        if not inflater.eof:
            return "el flujo zlib está incompleto (escritura a medias)"
        if inflater.unused_data:
            return "hay datos después del flujo zlib"
        if check == "zlib":
            return None
        data = b"".join(parts)
    if data.endswith(b"\0"):
        data = data[:-1]
    text, declaration = data, b""
    if text.startswith(b"\xef\xbb\xbf"):
        declaration, text = text[:3], text[3:]
    if text.lstrip().startswith(b"<?xml"):
        text = text.lstrip()
        end = text.find(b"?>")
        if end < 0:
            return "declaración XML incompleta"
        declaration, text = declaration + text[:end + 2], text[end + 2:]
    if b"<!DOCTYPE" in text or b"<!ENTITY" in text:
        return "XML con declaraciones no admitidas"
    try:
        ElementTree.fromstring(declaration + b"<mun-unit>" + text + b"</mun-unit>")
    except ElementTree.ParseError as exc:
        return f"XML incompleto o dañado ({exc})"
    return None


def envelope_problem(document: object, info, wanted_format: str) -> Optional[str]:
    """Why `document` is not a console save of this card in `wanted_format`,
    as the console reads it back, or None. A directory-save card (docs/saves.md)
    needs the files kind and schema, an intact payload within its bound and
    every file a declared unit passing its check; a single-object card
    (docs/saves.md) a JSON object payload with a save schema, never the files kind.
    The game's own payload is not interpreted."""
    if not isinstance(document, dict):
        return "no es un objeto JSON"
    if document.get("format") != wanted_format:
        return f"no es una partida {wanted_format}"
    if document.get("game") != _field(info, "id"):
        return "la partida es de otro juego"
    if _field(info, "saves_directory"):
        if document.get("payload_kind") != "files" or document.get("schema") != DIRECTORY_SAVE_SCHEMA:
            return "no es una partida de carpeta de esta versión"
        max_bytes = int(_field(info, "saves_max_bytes") or 0)
        problem = files_payload_problem(document.get("payload"), max_bytes) if max_bytes > 0 \
            else "la tarjeta no declara el tamaño de sus partidas"
        if problem:
            return problem
        patterns = list(zip(_field(info, "saves_units") or [], _field(info, "saves_checks") or []))
        for entry in document["payload"]["files"]:
            name = entry["path"]
            check = next((c for pattern, c in patterns if fnmatch.fnmatchcase(name, pattern)), None)
            if check is None:
                return f"{name} no es un archivo de partida declarado"
            problem = unit_check(check, base64.b64decode(entry["data"]))
            if problem:
                return f"{name}: {problem}"
        return None
    if "payload_kind" in document:
        return "una partida de carpeta en una tarjeta que no las declara"
    schema = document.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        return "el esquema de la partida no es válido"
    if not isinstance(document.get("payload"), dict):
        return "la partida no es un objeto JSON"
    return None


def files_payload_problem(payload: object, max_bytes: int) -> Optional[str]:
    """Why a directory-save payload (docs/saves.md) is not intact, or None.

    Checked on every write and on every read of the card: names are single
    safe segments, every file matches its size and SHA-256, the sum stays
    within the card's bound. An envelope that fails is damaged, never a valid
    previous copy."""
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        return "payload without a file list"
    files = payload["files"]
    if len(files) > FILES_PAYLOAD_MAX_FILES:
        return f"more than {FILES_PAYLOAD_MAX_FILES} files"
    seen, total = set(), 0
    for entry in files:
        if not isinstance(entry, dict):
            return "file entry is not an object"
        name, size, digest, data = entry.get("path"), entry.get("size"), entry.get("sha256"), entry.get("data")
        if not isinstance(name, str) or not _FILE_NAME.fullmatch(name) or name in (".", "..") or name in seen:
            return f"bad or repeated file name {str(name)[:40]!r}"
        seen.add(name)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or not isinstance(digest, str) \
                or not isinstance(data, str):
            return f"{name}: bad size, hash or data"
        total += size
        if total > max_bytes:
            return f"files exceed {max_bytes} bytes"
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            return f"{name}: data is not base64"
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            return f"{name}: size or hash does not match"
    return None
