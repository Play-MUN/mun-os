#!/usr/bin/env python3
"""Read os/inputs.json for the builder scripts (Python 3 standard library only).

    inputs.py sources           APT deb822 sources for the pinned Debian snapshot
    inputs.py get KEY[.KEY...]  one value (strings and numbers as text, lists one per line)
"""

import json
import sys
from pathlib import Path

INPUTS = Path(__file__).resolve().parents[1] / "inputs.json"


def load(path: Path = INPUTS) -> dict:
    return json.loads(path.read_text())


def sources(inputs: dict) -> str:
    """One deb822 stanza per archive. Snapshot archives are immutable, so their
    Release files outlive their Valid-Until dates; apt is told not to check
    them (Check-Valid-Until) and still verifies every signature and hash."""
    debian = inputs["debian"]
    stanzas = []
    for archive in debian["archives"]:
        stanzas.append("\n".join([
            "Types: deb",
            f"URIs: {archive['url']}",
            f"Suites: {' '.join(archive['suites'])}",
            f"Components: {' '.join(debian['components'])}",
            f"Signed-By: {debian['keyring']}",
            "Check-Valid-Until: no",
        ]))
    return "\n\n".join(stanzas) + "\n"


def get(inputs: dict, dotted: str) -> str:
    value = inputs
    for key in dotted.split("."):
        value = value[key]
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, (dict,)):
        return json.dumps(value)
    return str(value)


def main(argv: list) -> int:
    inputs = load()
    if argv[:1] == ["sources"]:
        sys.stdout.write(sources(inputs))
        return 0
    if len(argv) == 2 and argv[0] == "get":
        print(get(inputs, argv[1]))
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
