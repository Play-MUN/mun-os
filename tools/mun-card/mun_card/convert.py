"""Convert a lab card of the earlier naming generation to MUN names (docs/game-cards.md).

The source image is never modified. The converted copy is built under a
temporary name next to the destination and appears under its final name only
after every structural check has passed. For the whole operation the source
and destination names are held in the lab's attach registry, the record every
lab console claims before it attaches a card, so no guest can attach either
image meanwhile.

What the copy proves is structure: the only changes are the manifest's name
and the `format` of the card's two save envelopes. Whether the game on the
card works with MUN names is established by restoring its save in the lab
afterwards; the tool refuses executables that show they name only the earlier
contract and otherwise requires the operator's statement.
"""

import contextlib
import hashlib
import json
import os
import shutil
import stat as statmod
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import ext4, image
from .errors import CardError
from .saves import envelope_problem, save_bounds
from .source import DebugfsSource
from .validate import MANIFEST_NAMES, SAVE_FORMATS, validate_card

REGISTRY_NAME = "attached.json"          # vm/munvm.py: ATTACH_REGISTRY, under the same lock file
RESERVATION = "mun-card convert"         # the holder a lab console sees in the registry
LOCK_TIMEOUT = 30.0
SAVE_FILES = ("save.json", "save.json.prev")
# Signs, in the entry executable, that a game knows only the earlier contract
# (earlier-only marker, MUN marker, why it matters). Used only to refuse:
# finding the MUN marker proves nothing about the game.
EARLIER_ONLY = (
    (b"neptune-save/1", b"mun-save/1", "el juego solo lee partidas neptune-save/1"),
    (b"NEPTUNE_", b"MUN_", "el juego solo lee las variables NEPTUNE_*"),
    (b"/run/neptune/card", b"/run/mun/card", "el juego busca su contenido en /run/neptune/card"),
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _registry_lock(card_root: Path) -> Iterator[None]:
    """The flock(2) vm/munvm.py takes around every change to the registry.
    POSIX only, like the whole tool (cli.main refuses Windows)."""
    import fcntl
    card_root.mkdir(parents=True, exist_ok=True)
    fd = os.open(card_root / (REGISTRY_NAME + ".lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CardError("registry_busy", "El registro de tarjetas del laboratorio está ocupado")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _load_registry(card_root: Path) -> Dict[str, dict]:
    try:
        data = json.loads((card_root / REGISTRY_NAME).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(card_root: Path, registry: Dict[str, dict]) -> None:
    fd, temporary = tempfile.mkstemp(dir=str(card_root), prefix=REGISTRY_NAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, card_root / REGISTRY_NAME)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def reserved(card_root: Path, names: List[str]) -> Iterator[None]:
    """Hold `names` in the attach registry, or refuse if a live guest (or
    another conversion) holds one. Entries of dead processes are stale and
    dropped, as the lab tool drops them."""
    me = os.getpid()
    with _registry_lock(card_root):
        registry = _load_registry(card_root)
        for name in names:
            holder = registry.get(name)
            if holder and _pid_alive(int(holder.get("pid") or 0)):
                raise CardError("card_in_use", "La tarjeta está conectada a una consola o reservada",
                                f"{name}: {holder.get('instance')} (pid {holder.get('pid')})")
        for name in names:
            registry[name] = {"instance": RESERVATION, "pid": me, "purpose": "convert",
                              "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "attachment": None}
        _save_registry(card_root, registry)
    try:
        yield
    finally:
        with _registry_lock(card_root):
            registry = _load_registry(card_root)
            for name in names:
                if (registry.get(name) or {}).get("pid") == me:
                    del registry[name]
            _save_registry(card_root, registry)


def _lab_card(card_root: Path, path: Path, what: str) -> str:
    """The card name of a lab image `<card_root>/<name>.img`, or refuse."""
    path = Path(path)
    if path.suffix != ".img" or path.name.startswith(".") or path.parent.resolve() != card_root.resolve():
        raise CardError("image_path", f"La tarjeta {what} debe ser una imagen del laboratorio en {card_root}", str(path))
    return path.stem


def _dump(image_path: Path, into: Path, debugfs: str, entries: List[Tuple[str, str, int]]) -> Path:
    """Extract the whole image into `into` with debugfs rdump, for comparing
    bytes only, and prove the extraction is the image's inventory exactly:
    rdump reports unowned files as warnings and skips special files, so its
    exit status proves nothing. Nothing about the card is decided from it."""
    into.mkdir()
    subprocess.run([debugfs, "-R", f'rdump / "{into}"', str(image_path)], capture_output=True, check=False)
    found: Dict[str, Tuple[str, int]] = {}
    for current, dirs, files in os.walk(into):             # never follows a link
        if Path(current) == into and "lost+found" in dirs:
            dirs.remove("lost+found")
        for name in dirs + files:
            path = Path(current) / name
            info = os.lstat(path)
            kind = ("symlink" if statmod.S_ISLNK(info.st_mode) else "dir" if statmod.S_ISDIR(info.st_mode)
                    else "file" if statmod.S_ISREG(info.st_mode) else "other")
            found[path.relative_to(into).as_posix()] = (kind, info.st_size if kind == "file" else 0)
    expected = {path: (kind, size if kind == "file" else 0) for path, kind, size in entries}
    if found != expected:
        differing = sorted(set(found.items()) ^ set(expected.items()))[:3]
        raise CardError("source_unreadable", "No se pudo extraer la tarjeta entera",
                        ", ".join(path for path, _ in differing))
    return into


def _tree(root: Path) -> Dict[str, Tuple[str, Optional[bytes]]]:
    """Every entry under `root` by relative path: ("dir", None),
    ("link", target) or ("file", sha256)."""
    entries: Dict[str, Tuple[str, Optional[bytes]]] = {}
    for current, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if relative == "lost+found":
                continue
            if path.is_symlink():
                entries[relative] = ("link", os.readlink(path).encode())
            elif path.is_dir():
                entries[relative] = ("dir", None)
            else:
                entries[relative] = ("file", hashlib.sha256(path.read_bytes()).digest())
    return entries


def _card_saves(card: DebugfsSource, info, save_dir: str) -> Dict[str, dict]:
    """The card's current and previous saves, read from the image itself.

    Presence and type come from the image, every component of the path
    checked for a link, before a byte is read; a name that is present must be
    a regular file holding a save the console would restore, in the earlier
    format. Anything else refuses the conversion: a damaged save is recovered
    explicitly on another copy first, never carried or relabelled."""
    directory = card.stat(save_dir)
    if directory is None:
        return {}
    if directory.kind != "dir":
        raise CardError("save_damaged", "La carpeta de partidas de la tarjeta no es una carpeta normal", save_dir)
    _, bound = save_bounds(info)
    documents: Dict[str, dict] = {}
    for name in SAVE_FILES:
        path = f"{save_dir}/{name}"
        entry = card.stat(path)
        if entry is None:
            continue
        if entry.kind != "file":
            raise CardError("save_damaged", "Una partida de la tarjeta no es un archivo normal", path)
        if entry.size > bound:
            raise CardError("save_damaged", "Una partida de la tarjeta supera el tamaño permitido", path)
        try:
            document = json.loads(card.read(path, bound)[:entry.size])
        except ValueError as exc:
            raise CardError("save_damaged", "La tarjeta tiene una partida dañada; recupérala en otra copia antes",
                            f"{path}: {exc}")
        problem = envelope_problem(document, info, SAVE_FORMATS["earlier"])
        if problem:
            raise CardError("save_damaged", "La tarjeta tiene una partida que la consola no recuperaría", f"{path}: {problem}")
        documents[name] = document
    return documents


def _eligibility(card: DebugfsSource, info, game_supports_mun_names: bool) -> None:
    if not info.entry:
        return                       # a test card: no game to be compatible
    entry = card.stat(info.entry)    # a regular file without links: validate_card checked it
    executable = card.read(info.entry, entry.size)[:entry.size]
    reads_own_envelope = not info.saves_directory
    for earlier, mun, why in EARLIER_ONLY:
        if earlier == b"neptune-save/1" and not reads_own_envelope:
            continue                 # directory saves never reach the game
        if earlier in executable and mun not in executable:
            raise CardError("game_earlier_only", "El juego de la tarjeta solo conoce los nombres anteriores", why)
    if not game_supports_mun_names:
        raise CardError("compatibility_unstated", "Falta declarar que el juego admite los nombres MUN",
                        "--game-supports-mun-names, por ejemplo si es una compilación actual de una receta del repositorio")


def _stat_line(debugfs: str, image_path: Path, directory: str) -> Dict[str, Tuple[int, int, int]]:
    """mode, uid and gid of the regular files in one directory of the image."""
    result = subprocess.run([debugfs, "-R", f'ls -l "/{directory}"', str(image_path)], capture_output=True, check=False)
    found = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        match = DebugfsSource._LS.match(line)
        if match:
            found[match.group(7).strip()] = (int(match.group(2), 8), int(match.group(4)), int(match.group(5)))
    return found


def convert(source: Path, destination: Path, card_root: Path, game_supports_mun_names: bool = False,
            report: Callable[[str], None] = lambda line: None) -> dict:
    """Write `destination`, a MUN-generation copy of the earlier-generation
    card `source`; return what was checked. Raises CardError, leaving no file
    under `destination`, when anything is not as the conversion contract
    (docs/game-cards.md) requires."""
    card_root = Path(card_root)
    source, destination = Path(source), Path(destination)
    source_name = _lab_card(card_root, source, "de origen")
    destination_name = _lab_card(card_root, destination, "de destino")
    if source_name == destination_name:
        raise CardError("image_path", "El destino debe ser otra tarjeta")
    if not source.is_file() or source.is_symlink():
        raise CardError("image_missing", f"No existe {source}")
    debugfs, e2fsck = image.find_tool("debugfs"), image.find_tool("e2fsck")
    with reserved(card_root, [source_name, destination_name]):
        if os.path.lexists(destination):
            raise CardError("image_exists", f"{destination} ya existe; la conversión nunca reemplaza una tarjeta")
        before = image.sha256_file(source)
        ext4.check_mountable(source)          # a card needing recovery is recovered on another copy first
        temporary = destination.with_name(f".{destination.name}.converting-{os.getpid()}")
        with tempfile.TemporaryDirectory(prefix="mun-card-convert-") as work:
            work = Path(work)
            try:
                result = _convert_reserved(source, temporary, destination, work, debugfs, e2fsck,
                                           game_supports_mun_names, report)
                after = image.sha256_file(source)
                if after != before:
                    raise CardError("source_changed", "La tarjeta de origen cambió durante la conversión")
                try:
                    os.link(temporary, destination)      # never replaces: fails if it appeared meanwhile
                except FileExistsError:
                    raise CardError("image_exists", f"{destination} apareció durante la conversión; no se reemplaza")
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary)
        result.update({"source": str(source), "destination": str(destination),
                       "source_sha256": before, "source_unchanged": True,
                       "destination_sha256": image.sha256_file(destination)})
    return result


def _convert_reserved(source: Path, temporary: Path, destination: Path, work: Path, debugfs: str, e2fsck: str,
                      game_supports_mun_names: bool, report: Callable[[str], None]) -> dict:
    # Everything about the card is decided from the image; the host
    # extraction below only serves to compare bytes.
    card = DebugfsSource(source, debugfs)
    entries = card.entries()
    special = [path for path, kind, _ in entries if kind == "other"]
    if special:
        raise CardError("special_file", "La tarjeta contiene archivos especiales (tuberías, dispositivos) "
                        "que la conversión no puede comprobar", ", ".join(special[:5]))
    info = validate_card(card)
    if info.naming != "earlier":
        raise CardError("already_mun", "La tarjeta ya usa los nombres MUN")
    _eligibility(card, info, game_supports_mun_names)

    save_dir = f"{info.saves}/{info.id}"
    rewritten = {name: dict(document, format=SAVE_FORMATS["mun"])
                 for name, document in _card_saves(card, info, save_dir).items()}
    report(f"origen válido ({info.id} {info.version}); partidas a convertir: {', '.join(rewritten) or 'ninguna'}")
    original = _dump(source, work / "source", debugfs, entries)

    shutil.copyfile(source, temporary)
    commands = [f'ln "/{MANIFEST_NAMES["earlier"]}" "/{MANIFEST_NAMES["mun"]}"',
                f'unlink "/{MANIFEST_NAMES["earlier"]}"']
    if rewritten:
        modes = _stat_line(debugfs, source, save_dir)
        if any(name not in modes for name in rewritten):
            raise CardError("source_unreadable", "No se pudieron leer los permisos de las partidas", save_dir)
        commands.append(f'cd "/{save_dir}"')
        for name, document in rewritten.items():
            local = work / f"new-{name}"
            # Serialised as the card service writes envelopes, so the copy
            # reads exactly like a save written on a console.
            local.write_bytes((json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8"))
            mode, uid, gid = modes[name]
            commands += [f'rm "{name}"', f'write "{local}" "{name}"',
                         f'sif "{name}" mode 0{mode:o}', f'sif "{name}" uid {uid}', f'sif "{name}" gid {gid}']
    script = work / "commands"
    script.write_text("\n".join(commands) + "\n")
    subprocess.run([debugfs, "-w", "-f", str(script), str(temporary)], capture_output=True, check=False)

    # Everything below checks the copy as it is, not what the commands meant to do.
    check = subprocess.run([e2fsck, "-fn", str(temporary)], capture_output=True, text=True, check=False)
    if check.returncode != 0:
        raise CardError("convert_failed", "La copia convertida no pasa e2fsck", check.stdout.strip()[-400:])
    ext4.check_mountable(temporary)
    copy = DebugfsSource(temporary, debugfs)
    converted = _dump(temporary, work / "converted", debugfs, copy.entries())
    new_info = validate_card(copy)
    if new_info.naming != "mun" or (new_info.id, new_info.version) != (info.id, info.version):
        raise CardError("convert_failed", "La copia no es la misma tarjeta con los nombres MUN")
    for name in rewritten:
        document = json.loads((converted / save_dir / name).read_bytes())
        problem = envelope_problem(document, new_info, SAVE_FORMATS["mun"])
        if problem:
            raise CardError("convert_failed", "Una partida convertida no la recuperaría la consola", f"{name}: {problem}")
    before, after = _tree(original), _tree(converted)
    expected = {(MANIFEST_NAMES["mun"] if path == MANIFEST_NAMES["earlier"] else path): entry
                for path, entry in before.items()}
    for name in rewritten:
        expected.pop(f"{save_dir}/{name}")
        after_entry = after.pop(f"{save_dir}/{name}", None)
        if after_entry is None or json.loads((converted / save_dir / name).read_bytes()) != rewritten[name]:
            raise CardError("convert_failed", "Una partida convertida no conserva su contenido", name)
    if after != expected:
        differing = sorted(set(after.items()) ^ set(expected.items()))[:5]
        raise CardError("convert_failed", "La copia tiene diferencias no previstas",
                        ", ".join(path for path, _ in differing))
    report(f"copia verificada: {len(after)} entradas iguales, manifiesto renombrado, "
           f"{len(rewritten)} partida(s) con formato {SAVE_FORMATS['mun']}")
    return {"card": info.id, "version": info.version, "saves_converted": sorted(rewritten),
            "entries_identical": len(after), "game_compatibility": "declared, not verified"}
