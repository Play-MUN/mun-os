#!/usr/bin/env python3
"""Check the repository's Markdown documents: their local links and anchors,
and the translations listed in docs/translations.json.

Dependency-free on purpose so it runs on any host with Python 3, from a
clone or from a downloaded archive: nothing here needs Git's history. It
does not fetch external URLs, execute snippets, or build anything.

Links: every local link resolves, and a link to a heading resolves to one,
with GitHub's anchors (lower case, punctuation dropped, accents kept, a
repeated heading numbered -1, -2, ...).

Translations: docs/translations.json pairs each translated page with its
English original, which stays the technical reference, and records the
original's SHA-256 when the translation was last reviewed against it.
- Each listed pair exists and follows the layout: docs/es/ mirrors docs/,
  and a page elsewhere has NAME.es.md beside it.
- Each page links to its counterpart (the English / Español line).
- Every Spanish page is listed; only listed pages need a translation, so
  coverage grows page by page.
- A translation links to the translation of a page when there is one, and
  says «en inglés» in the same paragraph, item or row when it links an
  English page instead.
- An original that changed after the review fails the check until its
  translation has been reviewed again; only then is source_sha256 set to the
  original's new hash, by hand. A changed original does not mean the
  translation is wrong, and an unchanged one does not prove it right.
"""

import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOC_ROOTS = ("docs", "services", "tools", "scripts", "vm", "os", "examples", "tests")
TRANSLATIONS = "docs/translations.json"
FORMAT = "mun-translations/1"
ENGLISH_MARK = "en inglés"


def markdown_documents(root=ROOT):
    """Every tracked Markdown file, so a new document is checked wherever it
    lives; without Git (an exported tree), the root's and the documentation
    roots' Markdown files."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z", "--", "*.md"], cwd=root,
                                check=True, capture_output=True).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        listed = None
    if listed:
        return sorted({root / path for path in listed.split("\0") if path and (root / path).is_file()})
    documents = [path for path in root.glob("*.md") if path.is_file() and not path.is_symlink()]
    for directory in DOC_ROOTS:
        documents.extend((root / directory).rglob("*.md"))
    return sorted(set(documents))


def without_code(content):
    """The text with fenced code blocks blanked out, keeping every offset and
    line in place (links and headings inside code are not links or headings)."""
    return re.sub(r"```.*?```", lambda m: re.sub(r"[^\n]", " ", m.group(0)), content, flags=re.DOTALL)


def heading_text(raw):
    """A heading's text as GitHub renders it: inline code, links and emphasis
    reduced to their words."""
    text = re.sub(r"\s+#+\s*$", "", raw.strip())
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("`", "").replace("**", "").replace("*", "")


def slug(text):
    """GitHub's anchor for a heading (github-slugger): lower case; letters,
    marks and digits kept in any script, accents included; '-' and '_' kept;
    every other character dropped; spaces become '-'."""
    kept = "".join(ch for ch in text.lower() if ch in "-_ " or unicodedata.category(ch)[0] in "LMN")
    return kept.replace(" ", "-")


def anchors(content):
    """Every anchor a document offers: its headings, a repeated one numbered
    as GitHub does, and explicit <a id/name> targets."""
    found, seen = set(), {}
    for match in re.finditer(r"^ {0,3}#{1,6}[ \t]+(.+)$", without_code(content), flags=re.MULTILINE):
        base = slug(heading_text(match.group(1)))
        result = base
        while result in seen:
            seen[base] += 1
            result = f"{base}-{seen[base]}"
        seen[result] = 0
        found.add(result)
    found.update(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", content))
    return found


def links(content):
    """(target, start, end) of every inline link and image outside code."""
    for match in re.finditer(r"\[[^\]\n]*\]\(([^)\n]+)\)", without_code(content)):
        target = match.group(1).strip()
        target = target[1:target.index(">")] if target.startswith("<") else target.split()[0]
        yield target, match.start(), match.end()


def block_end(content, offset):
    """Where the paragraph, list item or table row holding `offset` ends."""
    end = len(content)
    for pattern in (r"\n[ \t]*\n", r"\n[ \t]*(?:[-*+]|\d+\.)[ \t]", r"\n[ \t]*\|"):
        found = re.compile(pattern).search(content, offset)
        if found:
            end = min(end, found.start())
    line_end = content.find("\n", offset)
    if content[content.rfind("\n", 0, offset) + 1:].lstrip().startswith("|"):
        end = min(end, line_end if line_end != -1 else len(content))   # a table row is its own block
    return end


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_translation(source):
    """docs/X.md has docs/es/X.md; any other page NAME.md has NAME.es.md beside it."""
    if source.startswith("docs/"):
        return "docs/es/" + source[len("docs/"):]
    return source[:-len(".md")] + ".es.md"


def is_spanish(relative):
    return relative.startswith("docs/es/") or relative.endswith(".es.md")


def load_translations(root, errors):
    path = root / TRANSLATIONS
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        errors.append(f"{TRANSLATIONS}: not JSON ({exc})")
        return {}
    if data.get("format") != FORMAT:
        errors.append(f"{TRANSLATIONS}: format must be {FORMAT}")
        return {}
    pairs = {}
    for entry in data.get("translations", []):
        translation, source = entry.get("translation"), entry.get("source")
        if not isinstance(translation, str) or not isinstance(source, str):
            errors.append(f"{TRANSLATIONS}: an entry needs translation and source paths")
            continue
        if translation in pairs:
            errors.append(f"{TRANSLATIONS}: {translation} is listed twice")
        if expected_translation(source) != translation:
            errors.append(f"{TRANSLATIONS}: the translation of {source} lives at {expected_translation(source)}, "
                          f"not {translation}")
        pairs[translation] = entry
    return pairs


def check(root=ROOT):
    """Every problem found, as messages; empty when everything holds."""
    errors = []
    documents = markdown_documents(root)
    relative = {document: document.relative_to(root).as_posix() for document in documents}
    texts = {document: document.read_text(encoding="utf-8") for document in documents}
    anchor_cache = {}

    def anchors_of(path):
        if path not in anchor_cache:
            anchor_cache[path] = anchors(texts[path] if path in texts else path.read_text(encoding="utf-8"))
        return anchor_cache[path]

    pairs = load_translations(root, errors)
    translated = {entry["source"]: name for name, entry in pairs.items()}
    targets = {}                          # document -> local paths it links to (relative to the root)

    for document in documents:
        content = texts[document]
        name = relative[document]
        targets[name] = set()
        for target, start, end in links(content):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            destination = (document.parent / unquote(parsed.path)) if parsed.path else document
            if not destination.exists():
                errors.append(f"{name}: missing link {target}")
                continue
            resolved = destination.resolve()
            try:
                linked = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                linked = None
            if linked:
                targets[name].add(linked)
            if parsed.fragment and destination.suffix == ".md":
                if unquote(parsed.fragment).lower() not in {a.lower() for a in anchors_of(destination)}:
                    errors.append(f"{name}: no heading for {target}")
            # A translation leads to the translations, and says when it cannot.
            if name in pairs and linked and linked.endswith(".md") and linked != pairs[name]["source"]:
                if linked in translated:
                    errors.append(f"{name}: links {target}, which has a translation: {translated[linked]}")
                elif not is_spanish(linked) and ENGLISH_MARK not in content[end:block_end(content, end)]:
                    errors.append(f"{name}: links the English page {target} without saying «{ENGLISH_MARK}»")

    listed = set(pairs)
    for document in documents:
        if is_spanish(relative[document]) and relative[document] not in listed:
            errors.append(f"{relative[document]}: a Spanish page not listed in {TRANSLATIONS}")
    for name, entry in sorted(pairs.items()):
        source = entry["source"]
        if not (root / name).is_file() or not (root / source).is_file():
            errors.append(f"{TRANSLATIONS}: {name} or its original {source} is missing")
            continue
        if name not in targets.get(source, set()):
            errors.append(f"{source}: no link to its translation {name} (the English / Español line)")
        if source not in targets.get(name, set()):
            errors.append(f"{name}: no link to its original {source} (the English / Español line)")
        current = sha256(root / source)
        if entry.get("source_sha256") != current:
            errors.append(f"{name}: its original {source} changed after the translation was reviewed; "
                          f"review the translation against it, then set source_sha256 to {current}")
    return errors, len(documents), len(pairs)


def main():
    errors, documents, translations = check()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Foundation OK: {documents} documents; local links and anchors resolve; "
          f"{translations} translations listed, linked both ways, reviewed against their originals' current text.")
    print("Not checked: external URLs, builds or runtime tests, or whether a translation says what its original says.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
