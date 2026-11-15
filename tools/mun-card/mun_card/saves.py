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
from xml.parsers import expat

SAVE_MAX_BYTES = 64 * 1024            # a game's payload, measured as compact JSON
# The envelope on the card, and what staging copies back to the game. The
# console writes the envelope with a space after each `,` and `:` that
# separates values, and compact JSON has at most one of those per two bytes
# (`[0,0,0]`), so the payload takes at most half as much again on the card;
# the rest is room for the envelope's own fields. The service that writes
# measures the bytes it is about to write against this same bound, so a save
# it confirms is one it reads back whole; an envelope over the bound is a
# damaged save for the console and for the card tool alike.
SAVE_FILE_MAX_BYTES = SAVE_MAX_BYTES * 3 // 2 + 16 * 1024
# Directory saves (docs/saves.md): the card declares the bound on the raw files;
# the envelope carries them in base64 (4/3) plus names and hashes.
FILES_PAYLOAD_MAX_FILES = 64
DIRECTORY_SAVE_SCHEMA = 1
# How far a unit may inflate and still be a save, as the console's launcher
# bounds it: by a multiple of what the card declared for all its saves, and
# absolutely. XML is also read in full, so it has a bound of its own.
INFLATE_LIMIT = 64 * 1024 * 1024
INFLATE_FACTOR = 64
XML_INFLATE_LIMIT = 16 * 1024 * 1024
_FILE_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")
_XML_SPACE = re.compile(rb"[ \t\n\r\x0b\x0c]*")


def _field(info, name):
    return info.get(name) if isinstance(info, dict) else getattr(info, name, None)


def save_bounds(info) -> "tuple[int, int]":
    """(payload, envelope file) bounds in bytes for this card's saves."""
    raw = _field(info, "saves_max_bytes")
    if not raw:
        return SAVE_MAX_BYTES, SAVE_FILE_MAX_BYTES
    payload = (int(raw) * 4 + 2) // 3 + 64 * 1024
    return payload, payload + 8 * 1024


def inflate_limit(max_bytes: int) -> int:
    """How far a unit may inflate on a card that declares `max_bytes` for its saves."""
    return min(INFLATE_LIMIT, INFLATE_FACTOR * max_bytes)


def unit_check(check: str, data: bytes, limit: int = INFLATE_LIMIT) -> Optional[str]:
    """Why `data` is not one complete unit under `check` (docs/saves.md), or
    None. `limit` is how far it may inflate (inflate_limit()). The launcher's
    rule, word for word: it never raises for what `data` holds, and its
    memory is bounded by the limits, the inflated bytes of an XML unit once,
    of any other unit a step at a time."""
    if check == "any":
        return None
    if check in ("zlib", "zlib-xml"):
        if check == "zlib-xml":
            limit = min(limit, XML_INFLATE_LIMIT)
        # Inflate in bounded steps and keep the output only when XML follows.
        inflater, pending, total, parts = zlib.decompressobj(), data, 0, []
        try:
            while True:
                chunk = inflater.decompress(pending, 1 << 20)
                total += len(chunk)
                if total > limit:
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
        del parts
    # The document is read where it lies, by position: no second copy of it.
    # Some engines write their XML with the C string terminator, a NUL at the
    # very end; their own loaders stop there, so one final NUL is not part of
    # the document. A NUL anywhere else still fails the parse.
    end = len(data) - 1 if data.endswith(b"\0") else len(data)
    # Some games' files have several top-level elements: they are wrapped in
    # one root, after the XML declaration if there is one (it must stay first).
    start = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    declared = _XML_SPACE.match(data, start, end).end()
    body = start
    if data.startswith(b"<?xml", declared, end):
        body = data.find(b"?>", declared, end) + 2
        if body < 2:
            return "declaración XML incompleta"
    if data.find(b"<!DOCTYPE", body, end) >= 0 or data.find(b"<!ENTITY", body, end) >= 0:
        return "XML con declaraciones no admitidas"
    # Well-formedness only: the parser is given no handlers, so it builds
    # nothing (a document tree of a unit made of millions of empty elements
    # took gigabytes), and with the document inside a root element of ours
    # no document type can be declared, so there are no entities to expand.
    view, parser = memoryview(data), expat.ParserCreate(None, "}")
    try:
        for piece in (view[:start], view[declared:body] if body > start else b"", b"<mun-unit>", view[body:end]):
            parser.Parse(piece, False)
        parser.Parse(b"</mun-unit>", True)
    except expat.ExpatError as exc:
        return f"XML incompleto o dañado ({exc})"
    except (LookupError, ValueError) as exc:
        # The declaration names an encoding the parser cannot read: it knows
        # UTF-8, UTF-16, US-ASCII and ISO-8859-1 itself and takes any other
        # single-byte encoding from Python's codecs; a name they do not have
        # is a LookupError there and a multibyte encoding (Shift-JIS, EUC-JP)
        # a ValueError. Such a unit cannot be shown to be complete.
        return f"codificación XML no admitida ({exc})"
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
            problem = unit_check(check, base64.b64decode(entry["data"]), inflate_limit(max_bytes))
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
