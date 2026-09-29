"""A strict TOML subset parser used identically on the host and in the console.

Why not `tomllib`: the host tooling supports Python 3.9 (macOS's own), the console
runs Debian's 3.13, and one parser on both sides guarantees that a card the
inspector accepts is a card the service accepts. The subset is deliberately
small: comments, `[table]` headers (no nesting beyond one dotted level),
`key = value` with basic or literal strings, integers, booleans and arrays of
strings. Anything else is a syntax error; the manifest schema does not need
more, and untrusted input gets no extra surface.
"""

import re
from typing import Any, Dict, List

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_INT = re.compile(r"[+-]?(0|[1-9](_?[0-9])*)$")


class TomlSyntaxError(ValueError):
    pass


def loads(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    current = root
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]") or line.startswith("[["):
                raise TomlSyntaxError(f"line {number}: unsupported table header")
            name = line[1:-1].strip()
            current = root
            for part in name.split("."):
                part = part.strip()
                if not _BARE_KEY.fullmatch(part):
                    raise TomlSyntaxError(f"line {number}: invalid table name {name!r}")
                node = current.setdefault(part, {})
                if not isinstance(node, dict):
                    raise TomlSyntaxError(f"line {number}: {part!r} is not a table")
                current = node
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _BARE_KEY.fullmatch(key):
            raise TomlSyntaxError(f"line {number}: expected `key = value`")
        if key in current:
            raise TomlSyntaxError(f"line {number}: duplicate key {key!r}")
        current[key] = _parse_value(value.strip(), number)
    return root


def _strip_comment(line: str) -> str:
    out = []
    quote = None
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#":
            break
        else:
            out.append(char)
    return "".join(out)


def _parse_value(token: str, number: int) -> Any:
    if not token:
        raise TomlSyntaxError(f"line {number}: missing value")
    if token[0] == '"':
        return _parse_basic_string(token, number)
    if token[0] == "'":
        if len(token) < 2 or token[-1] != "'" or "'" in token[1:-1]:
            raise TomlSyntaxError(f"line {number}: unterminated literal string")
        return token[1:-1]
    if token in ("true", "false"):
        return token == "true"
    if token[0] == "[":
        return _parse_string_array(token, number)
    if _INT.fullmatch(token):
        return int(token.replace("_", ""))
    raise TomlSyntaxError(f"line {number}: unsupported value {token!r}")


def _parse_basic_string(token: str, number: int) -> str:
    if len(token) < 2 or token[-1] != '"':
        raise TomlSyntaxError(f"line {number}: unterminated string")
    body = token[1:-1]
    out: List[str] = []
    i = 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
    while i < len(body):
        char = body[i]
        if char == '"':
            raise TomlSyntaxError(f"line {number}: unexpected quote inside string")
        if char == "\\":
            i += 1
            if i >= len(body) or body[i] not in escapes:
                raise TomlSyntaxError(f"line {number}: unsupported escape sequence")
            out.append(escapes[body[i]])
        else:
            out.append(char)
        i += 1
    return "".join(out)


def _parse_string_array(token: str, number: int) -> List[str]:
    if not token.endswith("]"):
        raise TomlSyntaxError(f"line {number}: unterminated array")
    inner = token[1:-1].strip()
    if not inner:
        return []
    items: List[str] = []
    for part in _split_top_level(inner, number):
        part = part.strip()
        if not part or part[0] not in "\"'":
            raise TomlSyntaxError(f"line {number}: arrays may only contain strings")
        items.append(_parse_value(part, number))
    return items


def _split_top_level(text: str, number: int) -> List[str]:
    parts, buf, quote = [], [], None
    for char in text:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            buf.append(char)
        elif char == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if quote:
        raise TomlSyntaxError(f"line {number}: unterminated string in array")
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts
