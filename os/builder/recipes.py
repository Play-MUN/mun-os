#!/usr/bin/env python3
"""Game recipes: how a build compiles a game this repository does not carry.

MUN OS is agnostic of any particular game: games reach the console on Game
Cards their owners make. To port a game you own, keep a recipe outside this
repository and pass it to `./mun dev build --recipe DIR`; the game is
compiled against the image's own libraries, as the example games are, and
left beside the image (`games/<name>/`), never in it (os/README.md, "Game
recipes").

A recipe is a directory:

    recipe.json   format "mun-recipe/1"; name; licence; the sources to fetch
                  (git URL and the full commit); the patches to apply, each
                  with its SHA-256
    build.sh      run by the image's build step with RECIPE (this directory
                  with its fetched sources), WORKDIR (scratch) and OUT (where
                  the game's files go)
    *.patch       the patches recipe.json names

Everything in a recipe is untrusted input: its fields are validated, sources
are fetched at the pinned commit only and checked, and a patch that does not
match its digest stops the build.

    recipes.py check DIR...              validate recipes (the host does, before a builder starts)
    recipes.py fetch RECIPES_DIR WORK    fetch and patch every recipe into WORK, write WORK/recipes.json
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

FORMAT = "mun-recipe/1"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,31}")
RELATIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
# Our own example games' names: a recipe cannot take them.
RESERVED = {"mun-collect", "mun-gl-probe"}


class RecipeError(Exception):
    """A recipe that cannot be used; the message says why."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(directory: Path, allow_local_sources: bool = False) -> Dict[str, Any]:
    """recipe.json of `directory`, checked. `allow_local_sources` admits
    file:// git URLs (tests only)."""
    manifest = directory / "recipe.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise RecipeError(f"{manifest}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise RecipeError(f"{manifest}: not a {FORMAT} recipe")
    name = data.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in RESERVED:
        raise RecipeError(f"{manifest}: name must be 2-32 lowercase letters, digits or '-', not an example's")
    if not isinstance(data.get("license"), str) or not data["license"].strip():
        raise RecipeError(f"{manifest}: state the game's licence (what may be distributed)")
    if not (directory / "build.sh").is_file():
        raise RecipeError(f"{directory}: build.sh missing")
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise RecipeError(f"{manifest}: sources must be a list")
    paths = set()
    schemes = ("https://", "file://") if allow_local_sources else ("https://",)
    for source in sources:
        if not isinstance(source, dict):
            raise RecipeError(f"{manifest}: a source is not an object")
        path, url, commit = source.get("path"), source.get("git"), source.get("commit")
        if not isinstance(path, str) or not RELATIVE.fullmatch(path) or path in paths or path.startswith("."):
            raise RecipeError(f"{manifest}: source path not allowed: {path!r}")
        paths.add(path)
        if not isinstance(url, str) or not url.startswith(schemes):
            raise RecipeError(f"{manifest}: source {path}: an https git URL is needed")
        if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
            raise RecipeError(f"{manifest}: source {path}: pin the full 40-character commit")
    patches = data.get("patches", [])
    if not isinstance(patches, list):
        raise RecipeError(f"{manifest}: patches must be a list")
    for patch in patches:
        if not isinstance(patch, dict):
            raise RecipeError(f"{manifest}: a patch is not an object")
        file, digest, target = patch.get("file"), patch.get("sha256"), patch.get("source")
        if not isinstance(file, str) or not RELATIVE.fullmatch(file) or not (directory / file).is_file():
            raise RecipeError(f"{manifest}: patch file not found in the recipe: {file!r}")
        if target not in paths:
            raise RecipeError(f"{manifest}: patch {file} names no source of the recipe")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise RecipeError(f"{manifest}: patch {file}: give its SHA-256")
        if sha256(directory / file) != digest:
            raise RecipeError(f"{manifest}: patch {file} does not match its SHA-256")
    return data


def recipe_digest(directory: Path) -> str:
    """One SHA-256 over the recipe's own files (names and contents), for BUILD-INFO."""
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0" + sha256(path).encode() + b"\n")
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "advice.detachedHead=false", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def fetch(recipes_dir: Path, work: Path, allow_local_sources: bool = False) -> List[Dict[str, Any]]:
    """Each recipe of `recipes_dir` into work/<name>/ with its sources at
    their pinned commits and its patches applied; work/recipes.json records
    them for BUILD-INFO."""
    records = []
    work.mkdir(parents=True, exist_ok=True)
    for directory in sorted(p for p in recipes_dir.iterdir() if p.is_dir()):
        data = load(directory, allow_local_sources)
        if directory.name != data["name"]:
            raise RecipeError(f"{directory}: the directory must be named after the recipe ({data['name']})")
        target = work / data["name"]
        if target.exists():
            raise RecipeError(f"two recipes named {data['name']}")
        shutil.copytree(directory, target)
        for source in data.get("sources", []):
            checkout = target / source["path"]
            print(f"== recipe {data['name']}: {source['git']} at {source['commit']}", flush=True)
            git("clone", "--quiet", source["git"], str(checkout))
            git("-C", str(checkout), "checkout", "--quiet", source["commit"])
            if git("-C", str(checkout), "rev-parse", "HEAD") != source["commit"]:
                raise RecipeError(f"recipe {data['name']}: {source['path']} is not at the pinned commit")
        for patch in data.get("patches", []):
            git("-C", str(target / patch["source"]), "apply", str(target / patch["file"]))
        for source in data.get("sources", []):
            shutil.rmtree(target / source["path"] / ".git")
        records.append({"name": data["name"], "license": data["license"], "recipe_sha256": recipe_digest(directory),
                        "sources": [{key: source[key] for key in ("path", "git", "commit")}
                                    for source in data.get("sources", [])],
                        "patches": [{key: patch[key] for key in ("file", "source", "sha256")}
                                    for patch in data.get("patches", [])]})
    (work / "recipes.json").write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    return records


def main(argv: List[str]) -> int:
    try:
        if argv[:1] == ["check"] and len(argv) > 1:
            for directory in argv[1:]:
                data = load(Path(directory))
                print(f"recipe {data['name']}: {len(data.get('sources', []))} source(s), "
                      f"{len(data.get('patches', []))} patch(es)")
            return 0
        if argv[:1] == ["fetch"] and len(argv) == 3:
            fetch(Path(argv[1]), Path(argv[2]))
            return 0
    except (RecipeError, subprocess.CalledProcessError) as exc:
        print(f"recipes: {exc}", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
