"""A MUN Shape package, and the sources that make it, exported at one commit
of the Git repository they are kept in (docs/shape.md, "Exporting a
package"): a folder named after the package and the commit, every file in it
listed with its SHA-256 in export.json. Whoever uses the example elsewhere (a
website, a development kit) takes that version whole, pins it by the
SHA-256 of export.json, and checks it against that (verify()).

export.json holds only what the commit and the paths given decide: the
commit, the paths, the package's declared format and every file's digest.
Where the commit is published, the repository's remote and the checker's
verdict are said when exporting, not written: the same commit and paths
give the same bytes, whatever the checkout knows, and a remote branch seen
locally is information, never an authorisation to publish.

Everything is taken from the commit, never from the working tree: the
package is checked, and the files it uses chosen, from the commit's bytes.
The export refuses:

- a package or a source with changes not committed, or with new files Git
  does not track (ignored files, such as a bake's output, are not sources);
- a declared source the commit does not have, that gives no file, that is
  the package or inside it, or that is inside another source;
- a symbolic link or a submodule in the package or a source: Git keeps a
  link as a link and a submodule as another repository's commit, neither a
  file of this one, and a link's target is never written as a file;
- a name an export cannot hold: verify() accepts exactly the paths export()
  writes (parts of letters, digits, '.', '_' and '-'; never '.' or '..'; no
  two that differ only in case).

Sources are sorted and repeated ones dropped, so the same paths give the same
export in any order. It records a commit, not a branch, so nothing that uses
it follows a branch. It is written beside its final name, verified, and
only then given that name: an export that fails leaves nothing that looks
like one.

Python's standard library and Git.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence

from . import shape
from .errors import CardError
from .source import DirectorySource

FORMAT = "mun-shape-export/1"
MANIFEST = "export.json"
# The repository's own terms, carried beside the files when it has them.
NOTICES = ("LICENSE", "NOTICE", "NAME-AND-LOGO.txt")
SECTIONS = ("package", "sources", "notices")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PART = re.compile(r"[A-Za-z0-9._-]+")
# Git's modes for a file, an executable, a symbolic link and a submodule.
_FILE_MODES = ("100644", "100755")
_LINK, _SUBMODULE = "120000", "160000"


def git_environment() -> dict:
    """This process's environment without what points Git elsewhere: a Git
    hook runs with GIT_DIR and GIT_INDEX_FILE set for the repository it is
    committing in, and the export reads the package's own repository,
    whatever runs it."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(repo: Path, *args: str, binary: bool = False):
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False, env=git_environment())
    except FileNotFoundError:
        raise CardError("export_git", "Git no está instalado")
    if done.returncode != 0:
        raise CardError("export_git", "Git no pudo leer el repositorio",
                        done.stderr.decode("utf-8", "replace").strip()[:200])
    return done.stdout if binary else done.stdout.decode("utf-8")


def _relative(repo: Path, path: Path) -> str:
    """`path` in the repository, its folders resolved and its last part as
    given: a link named as a source stays a link (and is refused), never
    its target under another name."""
    path = Path(path)
    full = path.resolve() if path.name in ("", ".", "..") else path.parent.resolve() / path.name
    try:
        return full.relative_to(repo).as_posix()
    except ValueError:
        raise CardError("export_outside", "No está dentro del repositorio", str(path))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def holds(path: str) -> bool:
    """A path an export holds, in export.json and on disk: in one of its
    sections, each part letters, digits, '.', '_' or '-', never '.' or
    '..'. export() writes only these; verify() accepts only these."""
    parts = path.split("/")
    return len(parts) >= 2 and parts[0] in SECTIONS and all(_PART.fullmatch(p) and p not in (".", "..") for p in parts)


def _repository_path(path) -> bool:
    """A path in the repository as export.json names it (the package, a
    source): the parts export paths are made of."""
    return isinstance(path, str) and all(_PART.fullmatch(p) and p not in (".", "..") for p in path.split("/"))


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _case_twins(paths) -> List[str]:
    """Paths that differ from another only in case: one file on a system
    that does not tell case apart."""
    seen: Dict[str, str] = {}
    twins = []
    for path in sorted(paths):
        other = seen.setdefault(path.lower(), path)
        if other != path:
            twins.append(path)
    return twins


def _tree(repo: Path, commit: str, roots: Sequence[str]) -> Dict[str, str]:
    """What Git has at `commit` under `roots`: path -> mode."""
    entries = {}
    for record in _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", *roots).split("\0"):
        if record:
            meta, _, path = record.partition("\t")
            entries[path] = meta.split(" ")[0]
    return entries


def _sources(repo: Path, package_root: str, sources: Sequence[Path]) -> List[str]:
    """The declared sources as repository paths: sorted, repeated ones
    dropped; refused if one is the package or inside it, or inside another."""
    roots = sorted({_relative(repo, Path(s)) for s in sources})
    for root in roots:
        if not _repository_path(root):
            raise CardError("export_path", "Una fuente con un nombre que una exportación no admite", root[:120])
        if _within(root, package_root) or _within(package_root, root):
            raise CardError("export_source", "Una fuente es el paquete, lo contiene o está dentro de él", root)
        outer = next((other for other in roots if other != root and _within(root, other)), None)
        if outer:
            raise CardError("export_source", f"Una fuente está dentro de otra ({outer})", root)
    return roots


def _files_of(tree: Dict[str, str], root: str, what: str) -> List[str]:
    """The files Git has under `root`, refusing links and submodules."""
    found = sorted(path for path in tree if _within(path, root))
    for path in found:
        mode = tree[path]
        if mode == _LINK:
            raise CardError("export_symlink", f"Un enlace simbólico en {what}: la exportación no lo convierte en archivo",
                            path)
        if mode == _SUBMODULE:
            raise CardError("export_submodule", f"Un submódulo en {what}: no es un archivo de este repositorio", path)
        if mode not in _FILE_MODES:
            raise CardError("export_git", f"Una entrada de Git que no es un archivo en {what}", f"{path} ({mode})")
    return found


def export(package: Path, out: Path, sources: Sequence[Path] = (), name: Optional[str] = None) -> Path:
    """Writes OUT/<name>-<commit12>/ (package/, sources/ by their paths in the
    repository, the repository's notices, export.json) and returns it. The
    package, as the commit has it, must be one a console would use (ready
    or partial)."""
    package = package.resolve()
    if not package.is_dir():
        raise CardError("export_package", "No es una carpeta", str(package))
    name = name or package.name
    if not _NAME.fullmatch(name):
        raise CardError("export_name", "Nombre de exportación no válido", name[:64])
    repo = Path(_git(package, "rev-parse", "--show-toplevel").strip()).resolve()
    commit = _git(repo, "rev-parse", "HEAD").strip()
    root = _relative(repo, package)
    if not _repository_path(root):
        raise CardError("export_path", "El paquete tiene un nombre que una exportación no admite", root[:120])
    declared = _sources(repo, root, sources)
    # Changes not committed, and new files not ignored: either would make
    # the export say less, or other, than the folders hold.
    for line in _git(repo, "status", "--porcelain", "--untracked-files=all", "--", root, *declared).splitlines():
        if line.startswith("??"):
            raise CardError("export_untracked", "Un archivo nuevo sin seguimiento en el paquete o en sus fuentes",
                            line[3:][:120])
        raise CardError("export_uncommitted", "Hay cambios sin confirmar en el paquete o en sus fuentes", line[:120])
    # Files Git is told not to compare (assume-unchanged, skip-worktree):
    # their changes would not show above.
    for line in _git(repo, "ls-files", "-v", "--", root, *declared).splitlines():
        if line[:1].islower() or line[:1] == "S":
            raise CardError("export_uncommitted", "Un archivo que Git no compara con el commit (assume-unchanged o "
                            "skip-worktree)", line[2:][:120])

    # What the commit has: the package, each source (at least one file
    # each) and the notices.
    tree = _tree(repo, commit, [root, *declared])
    package_files = _files_of(tree, root, "el paquete")
    source_files: List[str] = []
    for source in declared:
        found = _files_of(tree, source, "las fuentes")
        if not found:
            raise CardError("export_source_missing", "Una fuente declarada no está en el commit o no da ningún archivo",
                            source)
        source_files += found
    notices = _tree(repo, commit, NOTICES)
    for notice in notices:
        _files_of(notices, notice, "los avisos")

    def read(path: str) -> bytes:
        return _git(repo, "show", f"{commit}:{path}", binary=True)

    # The package checked from the commit's bytes, as a console reads it.
    package_bytes = {path[len(root) + 1:]: read(path) for path in package_files}
    if shape.MANIFEST not in package_bytes:
        raise CardError("export_untracked", "El paquete no tiene shape.json en el commit", f"{root}/{shape.MANIFEST}")
    with tempfile.TemporaryDirectory(prefix="mun-shape-export-") as checking:
        for relative, data in package_bytes.items():
            destination = Path(checking) / PurePosixPath(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        result = shape.check_package(DirectorySource(Path(checking)))
    if result.state not in ("ready", "partial"):
        raise CardError("export_unused", "Una consola no usaría este paquete", result.state)
    # Whoever exports sees the package in the working tree: one that uses a
    # file the commit lacks (ignored by Git) would be exported as less.
    local = shape.check_package(DirectorySource(package))
    lacking = sorted(set(local.files) - set(result.files))
    if lacking:
        raise CardError("export_untracked", "El paquete usa un archivo que Git no tiene", f"{root}/{lacking[0]}")

    files: Dict[str, bytes] = {}
    for relative in [shape.MANIFEST] + sorted(result.files):
        if relative not in package_bytes:
            raise CardError("export_untracked", "El paquete usa un archivo que Git no tiene", f"{root}/{relative}")
        files[f"package/{relative}"] = package_bytes[relative]
    for path in source_files:
        files[f"sources/{path}"] = read(path)
    for notice in NOTICES:
        if notice in notices:
            files[f"notices/{notice}"] = read(notice)
    for path in files:
        if not holds(path):
            raise CardError("export_path", "Un archivo con un nombre que una exportación no admite", path[:120])
    twins = _case_twins(files)
    if twins:
        raise CardError("export_path", "Dos archivos que solo se distinguen por mayúsculas", twins[0][:120])

    document = json.loads(files["package/" + shape.MANIFEST])
    manifest = {
        "format": FORMAT,
        "name": name,
        "source": {"commit": commit, "package": root, "sources": declared},
        "package": {"format": document.get("format"), "motion_draft": document.get("motion_draft")},
        "files": {path: {"sha256": _digest(data), "bytes": len(data)} for path, data in sorted(files.items())},
    }
    return _publish(Path(out), f"{name}-{commit[:12]}", files, manifest_bytes(manifest))


def manifest_bytes(manifest: Dict[str, object]) -> bytes:
    """export.json, byte for byte: the same on every system (ASCII, "\\n")."""
    return (json.dumps(manifest, indent=1, ensure_ascii=True) + "\n").encode("ascii")


def _publish(out: Path, final: str, files: Dict[str, bytes], manifest: bytes) -> Path:
    """Writes the export beside its final name, verifies it, then gives it
    that name; on any failure nothing is left."""
    target = out / final
    if target.exists():
        raise CardError("export_exists", "La exportación ya existe", str(target))
    part = out / f".{final}.part-{secrets.token_hex(4)}"
    try:
        try:
            out.mkdir(parents=True, exist_ok=True)
            part.mkdir()
            for path, data in files.items():
                destination = part / PurePosixPath(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            (part / MANIFEST).write_bytes(manifest)
        except OSError as exc:
            raise CardError("export_write", "No se pudo escribir la exportación", f"{exc.filename or out}: {exc.strerror}")
        problems = verify(part)
        if problems:
            raise CardError("export_verify", "La exportación escrita no se verifica", "; ".join(problems)[:200])
        if target.exists():
            raise CardError("export_exists", "La exportación ya existe", str(target))
        try:
            os.rename(part, target)
        except OSError as exc:
            raise CardError("export_write", "No se pudo dar su nombre a la exportación", f"{target}: {exc.strerror}")
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    return target


def context(package: Path) -> Dict[str, object]:
    """What the checkout knows about where the package's commit is: its
    remote `origin` and the remote branches that hold the commit, as last
    fetched. Information for whoever exports, not part of the export."""
    repo = Path(_git(package.resolve(), "rev-parse", "--show-toplevel").strip())
    commit = _git(repo, "rev-parse", "HEAD").strip()
    origin = _git(repo, "remote", "get-url", "origin").strip() if "origin" in _git(repo, "remote").split() else None
    branches = sorted(b.strip() for b in _git(repo, "branch", "-r", "--contains", commit).splitlines()
                      if b.strip() and "->" not in b)
    return {"origin": origin, "remote_branches": branches}


def identity(folder: Path) -> str:
    """What names an export: the SHA-256 of its export.json."""
    return _digest((Path(folder) / MANIFEST).read_bytes())


def _keys(value, allowed) -> bool:
    return isinstance(value, dict) and set(value) <= set(allowed)


def _shape_problems(manifest) -> List[str]:
    """What is missing, unknown or malformed in a manifest, before any file
    is read."""
    if not isinstance(manifest, dict):
        return [f"{MANIFEST}: not an object"]
    problems = []
    unknown = sorted(set(manifest) - {"format", "name", "source", "package", "files"})
    if unknown:
        problems.append(f"{MANIFEST}: unknown keys {', '.join(map(str, unknown))}")
    if manifest.get("format") != FORMAT:
        problems.append(f"{MANIFEST}: format {manifest.get('format')!r}, not {FORMAT}")
    if not isinstance(manifest.get("name"), str) or not _NAME.fullmatch(manifest["name"]):
        problems.append(f"{MANIFEST}: no valid name")
    source = manifest.get("source")
    declared: List[str] = []
    if not _keys(source, ("commit", "package", "sources")) or not isinstance(source.get("commit"), str) \
            or not _COMMIT.fullmatch(source["commit"]) or not _repository_path(source.get("package")) \
            or not isinstance(source.get("sources"), list) or not all(map(_repository_path, source["sources"])) \
            or len(set(source["sources"])) != len(source["sources"]):
        problems.append(f"{MANIFEST}: no valid source (commit, package, sources)")
    else:
        declared = source["sources"]
    package = manifest.get("package")
    draft = package.get("motion_draft") if isinstance(package, dict) else None
    if not _keys(package, ("format", "motion_draft")) or not isinstance(package.get("format"), str) \
            or not (draft is None or (isinstance(draft, int) and not isinstance(draft, bool))):
        problems.append(f"{MANIFEST}: no valid package")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return problems + [f"{MANIFEST}: no files"]
    for path, entry in files.items():
        if not holds(path):
            problems.append(f"{path!r}: not a path an export holds")
        elif path.startswith("notices/") and path[len("notices/"):] not in NOTICES:
            problems.append(f"{path}: not a notice an export holds")
        elif path.startswith("sources/") and not any(_within(path[len("sources/"):], s) for s in declared):
            problems.append(f"{path}: under no declared source")
        if not _keys(entry, ("sha256", "bytes")) or not isinstance(entry.get("sha256"), str) \
                or not _DIGEST.fullmatch(entry["sha256"]) or not isinstance(entry.get("bytes"), int) \
                or isinstance(entry.get("bytes"), bool) or entry["bytes"] < 0:
            problems.append(f"{path}: no valid sha256 and bytes")
    for twin in _case_twins(files):
        problems.append(f"{twin}: differs from another path only in case")
    for source_path in declared:
        if not any(p.startswith("sources/") and _within(p[len("sources/"):], source_path) for p in files):
            problems.append(f"{MANIFEST}: the source {source_path} gives no file")
    if f"package/{shape.MANIFEST}" not in files:
        problems.append(f"package/{shape.MANIFEST}: not listed")
    return problems


def verify(folder: Path, expect: Optional[str] = None) -> List[str]:
    """What differs between an export and what it should be: a manifest
    malformed, with unknown keys or missing what an export always holds;
    files changed, missing, unreadable or added (folders too); a package no
    console would use, that names a file the export lacks or that comes with
    files it does not use; and, given `expect`, an identity other than the
    one pinned. Empty: the export is whole, as made. Never raises for what
    it reads."""
    folder = Path(folder)
    try:
        raw = (folder / MANIFEST).read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        return [f"{MANIFEST}: {exc}"]
    problems = _shape_problems(manifest)
    if problems:
        return problems
    if expect is not None and _digest(raw) != expect:
        problems.append(f"{MANIFEST}: identity {_digest(raw)}, not the {expect} expected")
    listed = manifest["files"]
    for path, entry in sorted(listed.items()):
        file = folder / PurePosixPath(path)
        if file.is_symlink() or not file.is_file():
            problems.append(f"{path}: missing")
            continue
        try:
            data = file.read_bytes()
        except OSError as exc:
            problems.append(f"{path}: unreadable ({exc.strerror})")
            continue
        if _digest(data) != entry["sha256"] or len(data) != entry["bytes"]:
            problems.append(f"{path}: changed")
    # Everything present, files, links and folders, without following links.
    folders = {str(PurePosixPath(path).parent) for path in listed}
    folders |= {str(parent) for path in folders for parent in PurePosixPath(path).parents if str(parent) != "."}
    present, extra_folders = set(), set()
    for directory, subfolders, names in os.walk(folder, followlinks=False):
        here = Path(directory).relative_to(folder).as_posix()
        for sub in subfolders:
            relative = sub if here == "." else f"{here}/{sub}"
            if os.path.islink(os.path.join(directory, sub)):
                present.add(relative)
            elif relative not in folders:
                extra_folders.add(relative)
        for file_name in names:
            present.add(file_name if here == "." else f"{here}/{file_name}")
    for path in sorted(present - set(listed) - {MANIFEST}):
        problems.append(f"{path}: not in the export")
    for path in sorted(extra_folders):
        if not any(p.startswith(path + "/") for p in present):
            problems.append(f"{path}/: not in the export")
    if problems:
        return problems
    # The package as exported: one a console would use, every file it
    # names in the export and no other, its declared format the one listed.
    result = shape.check_package(DirectorySource(folder / "package"))
    if result.state not in ("ready", "partial"):
        return problems + [f"package: a console would not use it ({result.state})"]
    for relative in sorted(result.files):
        if f"package/{relative}" not in listed:
            problems.append(f"package/{relative}: named by the package, not in the export")
    used = {f"package/{relative}" for relative in result.files} | {f"package/{shape.MANIFEST}"}
    for path in sorted(p for p in listed if p.startswith("package/") and p not in used):
        problems.append(f"{path}: in the export, not used by the package")
    try:
        document = json.loads((folder / "package" / shape.MANIFEST).read_bytes())
    except (OSError, ValueError) as exc:
        return problems + [f"package/{shape.MANIFEST}: {exc}"]
    if not isinstance(document, dict) or document.get("format") != manifest["package"]["format"] \
            or document.get("motion_draft") != manifest["package"].get("motion_draft"):
        problems.append(f"{MANIFEST}: the package's format or motion_draft is not the one listed")
    return problems
