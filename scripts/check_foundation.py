#!/usr/bin/env python3
"""Check that local Markdown links in the repository documents resolve.

Dependency-free on purpose so it runs on any host with Python 3. It does not
fetch external URLs, validate anchors, execute snippets, or build anything.
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
ROOT_DOCS = ("README.md", "CONTRIBUTING.md")
DOC_ROOTS = ("docs", "services", "tools", "scripts", "vm", "os", "examples", "tests")


def markdown_documents():
    """Every tracked Markdown file, so a new document is checked wherever it
    lives; without Git (an exported tree), the documentation roots."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z", "--", "*.md"], cwd=ROOT,
                                check=True, capture_output=True).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        listed = None
    if listed:
        return [ROOT / path for path in listed.split("\0") if path and (ROOT / path).is_file()]
    documents = [ROOT / name for name in ROOT_DOCS if (ROOT / name).is_file()]
    for directory in DOC_ROOTS:
        documents.extend((ROOT / directory).rglob("*.md"))
    return documents


def main():
    errors = []
    documents = markdown_documents()

    # This intentionally checks inline file links, not external URLs or anchors.
    for document in sorted(set(documents)):
        content = document.read_text(encoding="utf-8")
        content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
        for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", content):
            target = match.group(1).strip()
            if target.startswith("<"):
                target = target[1:target.index(">")]
            else:
                target = target.split()[0]
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            destination = document.parent / unquote(parsed.path)
            if not destination.exists():
                errors.append(f"{document.relative_to(ROOT)}: missing link {target}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Foundation OK: {len(set(documents))} documents; local file links resolve.")
    print("Not checked: external URLs, anchors, builds or runtime tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
