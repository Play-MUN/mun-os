"""Create and hash lab card images on the host. Never touches a real disk.

Images are plain files holding one ext4 filesystem (no partition table), built
with e2fsprogs' `mke2fs -d` from a staging directory, so creation needs no
mount and no root. e2fsprogs on macOS is keg-only in Homebrew; the tools are
located explicitly rather than through PATH changes.
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional

from .errors import CardError
from .validate import MANIFEST_NAMES

CARD_LABEL_PREFIX = "MUNCARD"
DEFAULT_SIZE_MIB = 64
MIN_SIZE_MIB = 8


def find_tool(name: str) -> str:
    """Locate an e2fsprogs binary: Homebrew keg first, then usual sbin dirs, then PATH."""
    candidates: List[Path] = []
    try:
        prefix = subprocess.run(["brew", "--prefix", "e2fsprogs"], capture_output=True, text=True,
                                check=False).stdout.strip()
        if prefix:
            candidates.append(Path(prefix) / "sbin" / name)
    except OSError:
        pass
    candidates += [Path("/opt/homebrew/opt/e2fsprogs/sbin") / name,
                   Path("/usr/local/opt/e2fsprogs/sbin") / name,
                   Path("/usr/sbin") / name, Path("/sbin") / name]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    how = "brew install e2fsprogs" if sys.platform == "darwin" else "el paquete e2fsprogs de tu distribución"
    raise CardError("tool_missing", f"No se encontró {name}; instala e2fsprogs ({how})")


def tool_version(tool: str) -> str:
    result = subprocess.run([tool, "-V"], capture_output=True, text=True, check=False)
    text = (result.stderr or result.stdout).strip().splitlines()
    return text[0] if text else "unknown"


def create_image(destination: Path, staging: Path, size_mib: int = DEFAULT_SIZE_MIB,
                 label: str = CARD_LABEL_PREFIX) -> Dict[str, str]:
    """Build `destination` as an ext4 image populated from `staging`."""
    if size_mib < MIN_SIZE_MIB:
        raise CardError("image_size", f"El tamaño mínimo es {MIN_SIZE_MIB} MiB")
    destination = Path(destination)
    if destination.exists() and not destination.is_file():
        raise CardError("image_path", "El destino existe y no es un archivo normal", str(destination))
    mke2fs = find_tool("mke2fs")
    _normalise_times(staging)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    # Deterministic-enough for a lab: fixed uuid/hash seed and epoch make two
    # builds of the same staging tree byte-identical except for timestamps.
    env = dict(os.environ, E2FSPROGS_FAKE_TIME=str(FIXED_EPOCH), SOURCE_DATE_EPOCH=str(FIXED_EPOCH))
    cmd = [mke2fs, "-q", "-F", "-t", "ext4", "-L", label[:16], "-d", str(staging),
           "-E", "root_owner=0:0,hash_seed=6d756e63-6172-6430-8000-000000000001",
           "-U", "clear", str(partial), f"{size_mib}M"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        if partial.exists():
            partial.unlink()
        raise CardError("mke2fs_failed", "mke2fs no pudo crear la imagen", (result.stderr or result.stdout).strip())
    partial.replace(destination)
    return {"tool": mke2fs, "version": tool_version(mke2fs), "sha256": sha256_file(destination)}


FIXED_EPOCH = 1700000000


def _normalise_times(staging: Path) -> None:
    """Pin every mtime so two builds of the same tree hash identically."""
    for path in [staging, *staging.rglob("*")]:
        try:
            os.utime(path, (FIXED_EPOCH, FIXED_EPOCH), follow_symlinks=False)
        except (NotImplementedError, OSError):
            os.utime(path, (FIXED_EPOCH, FIXED_EPOCH))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------- test card content

VALID_MANIFEST = """# MUN Test Card — manifest v0. See docs/game-cards.md in MUN OS.
[card]
schema = 1
id = "mun.testcard"
title = "MUN Test Card"

[content]
version = "0.1.0"
kind = "test"          # a test card: described by the console, never executed
arch = "aarch64"
profile = "linux-arm64-v0"
root = "content"
cover = "cover.png"

[saves]
location = "saves"     # reserved for future save data; empty in v0
"""

# Each variant is a deliberate defect for tests and demos. Keys are stable CLI names.
VARIANTS = {
    "valid": "Tarjeta válida: MUN Test Card con portada y contenido de ejemplo",
    "missing-manifest": "Sin manifiesto (ni mun.toml ni neptune.toml)",
    "ambiguous-manifest": "mun.toml y neptune.toml a la vez: la tarjeta no dice cuál manda",
    "bad-toml": "Manifiesto con sintaxis TOML rota",
    "missing-fields": "Manifiesto sin content.version ni content.root",
    "bad-schema": "card.schema = 99",
    "bad-arch": "content.arch = x86_64",
    "bad-profile": "content.profile desconocido",
    "unsafe-path": "content.root = ../../etc",
    "absolute-path": "content.cover = /etc/passwd",
    "missing-file": "content.cover apunta a un archivo inexistente",
    "symlink": "content/ es un enlace simbólico fuera de la tarjeta",
    "cover-too-big": "Portada PNG de 2048×2048",
    "cover-near-limit": "Portada PNG válida de ruido, unos 3 KiB por debajo del límite de 1 MiB",
    "full": "Tarjeta válida casi llena: un relleno en content/ deja menos de 16 KiB libres, para provocar falta de espacio al guardar",
    "cover-not-png": "cover.png no es un PNG",
    "game-without-entry": "kind = game sin content.entry",
    "game": "Juego: kind = game con content.entry apuntando a un ejecutable proporcionado (--game)",
    "game-bad-entry": "kind = game cuyo content.entry es un archivo de texto, no un ejecutable",
    "game-gl": "Juego con perfil linux-arm64-gl-v0 (DRM/OpenGL y audio): content.entry es el ejecutable proporcionado (--game), nombrado como el archivo; --content DIR añade sus datos con access = mount",
}


def copy_content_tree(source: Path, content: Path) -> int:
    """Copy a game's data tree into content/ for the game-gl variant: regular
    files and directories only, no symlinks, nothing above the tree. Returns
    the number of files copied. Lab packaging, not a card feature."""
    if not source.is_dir():
        raise CardError("content_dir_missing", "El directorio de contenido no existe", str(source))
    count = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise CardError("content_symlink", "El contenido no puede contener enlaces simbólicos", str(relative))
        target = content / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
        else:
            raise CardError("content_special", "El contenido solo admite archivos y carpetas", str(relative))
    return count


def populate(staging: Path, variant: str = "valid", title: str = "MUN Test Card",
             game_binary: Optional[Path] = None, content_dir: Optional[Path] = None,
             access: Optional[str] = None, cover: Optional[Path] = None,
             accent: Optional[str] = None, background: Optional[str] = None,
             saves: Optional[Dict[str, object]] = None, naming: str = "mun") -> None:
    """Write the staging tree for a variant. `staging` must be empty.

    `content_dir` (game-gl only) copies a data tree into content/ and
    `access` sets content.access; a content directory implies `mount`.
    `cover` replaces the generated cover with a PNG of the author's choosing
    (the validator still bounds it); `accent`/`background` write a
    [presentation] table the shell tints itself with. `saves` (game-gl
    only) adds directory saves to [saves]: directory, units, checks, max_bytes
    (docs/saves.md). `naming` picks the card's names (docs/game-cards.md): "mun" writes
    mun.toml, "earlier" writes neptune.toml, as cards made before it."""
    if naming not in MANIFEST_NAMES:
        raise CardError("naming_unknown", f"Generación de nombres desconocida: {naming}", ", ".join(MANIFEST_NAMES))
    if variant not in VARIANTS:
        raise CardError("variant_unknown", f"Variante desconocida: {variant}", ", ".join(VARIANTS))
    staging = Path(staging)
    manifest = VALID_MANIFEST.replace('title = "MUN Test Card"', f'title = "{title}"')
    content = staging / "content"
    content.mkdir(parents=True)
    (staging / "saves").mkdir()
    (content / "README.txt").write_text(
        "MUN Test Card\n\nContenido de ejemplo para validar el reconocimiento de tarjetas.\n"
        "No es un juego ejecutable. Formato v0; ver docs/game-cards.md en MUN OS.\n", encoding="utf-8")
    (content / "hello.txt").write_text("hola desde la tarjeta\n", encoding="utf-8")
    (content / "assets").mkdir()
    (content / "assets" / "palette.txt").write_text("#F4F1E9 #161412 #B8623A\n", encoding="utf-8")
    write_cover(staging / "cover.png", 512, 512)

    if variant == "missing-manifest":
        return
    if variant == "bad-toml":
        manifest = manifest.replace('title = "', 'title = "MUN Test Card\n[broken')
    elif variant == "missing-fields":
        manifest = manifest.replace('version = "0.1.0"\n', "").replace('root = "content"\n', "")
    elif variant == "bad-schema":
        manifest = manifest.replace("schema = 1", "schema = 99")
    elif variant == "bad-arch":
        manifest = manifest.replace('arch = "aarch64"', 'arch = "x86_64"')
    elif variant == "bad-profile":
        manifest = manifest.replace('profile = "linux-arm64-v0"', 'profile = "windows-v9"')
    elif variant == "unsafe-path":
        manifest = manifest.replace('root = "content"', 'root = "../../etc"')
    elif variant == "absolute-path":
        manifest = manifest.replace('cover = "cover.png"', 'cover = "/etc/passwd"')
    elif variant == "missing-file":
        manifest = manifest.replace('cover = "cover.png"', 'cover = "art/cover.png"')
    elif variant == "symlink":
        shutil.rmtree(content)
        os.symlink("/etc", content)
    elif variant == "cover-too-big":
        write_cover(staging / "cover.png", 2048, 2048)
    elif variant == "cover-near-limit":
        write_noise_cover(staging / "cover.png", 590)   # ~1,045,000 bytes: incompressible, under COVER_MAX_BYTES
    elif variant == "full" and game_binary is not None:
        # A full *game* card: the save path meets ENOSPC on the first write.
        shutil.copyfile(game_binary, content / "mun-collect")
        os.chmod(content / "mun-collect", 0o755)
        manifest = (manifest.replace('kind = "test"          # a test card: described by the console, never executed',
                                     'kind = "game"\nentry = "content/mun-collect"')
                            .replace('id = "mun.testcard"', 'id = "mun.collect"')
                            .replace('title = "MUN Test Card"', 'title = "MUN Collect"'))
        with open(content / "filler.bin", "wb") as filler:
            chunk = b"MUN-FILLER-" * 93 + b"\n"
            remaining = FULL_FILLER_BYTES - (content / "mun-collect").stat().st_size
            while remaining > 0:
                filler.write(chunk[:remaining])
                remaining -= len(chunk)
    elif variant == "full":
        # Sized for the 64 MiB image with 1 KiB blocks: what the valid variant
        # leaves free minus a few blocks. Root (the card service) may use the
        # reserved blocks too, so the filler must leave almost nothing.
        # Non-zero bytes on purpose: mke2fs -d turns all-zero blocks into holes,
        # which would allocate nothing.
        with open(content / "filler.bin", "wb") as filler:
            chunk = b"MUN-FILLER-" * 93 + b"\n"          # 1024 bytes
            remaining = FULL_FILLER_BYTES
            while remaining > 0:
                filler.write(chunk[:remaining])
                remaining -= len(chunk)
    elif variant == "cover-not-png":
        (staging / "cover.png").write_bytes(b"GIF89a not a png")
    elif variant == "game-without-entry":
        manifest = manifest.replace('kind = "test"', 'kind = "game"')
    elif variant == "game":
        if game_binary is None or not Path(game_binary).is_file():
            raise CardError("game_binary_missing", "La variante game necesita --game <ejecutable>")
        shutil.copyfile(game_binary, content / "mun-collect")
        os.chmod(content / "mun-collect", 0o755)
        manifest = (manifest.replace('kind = "test"          # a test card: described by the console, never executed',
                                     'kind = "game"\nentry = "content/mun-collect"')
                            .replace('id = "mun.testcard"', 'id = "mun.collect"')
                            .replace(f'title = "{title}"', f'title = "{title if title != "MUN Test Card" else "MUN Collect"}"'))
    elif variant == "game-gl":
        if game_binary is None or not Path(game_binary).is_file():
            raise CardError("game_binary_missing", "La variante game-gl necesita --game <ejecutable>")
        name = Path(game_binary).name
        if content_dir is not None:
            # The entry is content/<name>. An installed game's directory often
            # holds its own engine under that very name: copying the tree over
            # the chosen executable would silently package the wrong program,
            # so a data tree that claims the entry's path is refused.
            clash = Path(content_dir) / name
            if clash.exists() or clash.is_symlink():
                kind = "una carpeta" if clash.is_dir() and not clash.is_symlink() else "un archivo"
                raise CardError("content_entry_conflict",
                                f"El contenido ya tiene {kind} llamado {name}, el nombre del ejecutable elegido",
                                f"{clash}: renómbralo o exclúyelo del directorio de contenido")
            copy_content_tree(Path(content_dir), content)
            access = access or "mount"
        shutil.copyfile(game_binary, content / name)
        os.chmod(content / name, 0o755)
        if sha256_file(content / name) != sha256_file(Path(game_binary)):
            raise CardError("entry_mismatch", "La entrada empaquetada no coincide con el ejecutable elegido", name)
        access_line = f'\naccess = "{access}"' if access else ""
        manifest = (manifest.replace('kind = "test"          # a test card: described by the console, never executed',
                                     f'kind = "game"\nentry = "content/{name}"{access_line}')
                            .replace('profile = "linux-arm64-v0"', 'profile = "linux-arm64-gl-v0"')
                            .replace('id = "mun.testcard"', f'id = "mun.{name.replace("-", "").lower()}"')
                            .replace(f'title = "{title}"', f'title = "{title if title != "MUN Test Card" else name}"'))
    elif variant == "game-bad-entry":
        (content / "not-a-binary").write_text("this is text, not an ELF executable\n", encoding="utf-8")
        manifest = (manifest.replace('kind = "test"          # a test card: described by the console, never executed',
                                     'kind = "game"\nentry = "content/not-a-binary"')
                            .replace('id = "mun.testcard"', 'id = "mun.badentry"'))
    if cover is not None:
        data = Path(cover).read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CardError("cover_invalid", "La portada debe ser un PNG", str(cover))
        (staging / "cover.png").write_bytes(data)
    if saves:
        def toml_list(values):
            return "[" + ", ".join(f'"{value}"' for value in values) + "]"
        extra = (f'directory = "{saves["directory"]}"\n'
                 f'units = {toml_list(saves["units"])}\n'
                 f'checks = {toml_list(saves["checks"])}\n'
                 f'max_bytes = {int(saves["max_bytes"])}\n')
        old_saves = 'location = "saves"     # reserved for future save data; empty in v0\n'
        assert old_saves in manifest
        manifest = manifest.replace(old_saves, 'location = "saves"\n' + extra, 1)
    if accent or background:
        lines = ["", "[presentation]"]
        if accent:
            lines.append(f'accent = "{accent}"')
        if background:
            lines.append(f'background = "{background}"')
        manifest = manifest.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    (staging / MANIFEST_NAMES[naming]).write_text(manifest, encoding="utf-8")
    if variant == "ambiguous-manifest":
        # The other generation's name too, with the same text: still refused.
        other = next(name for key, name in MANIFEST_NAMES.items() if key != naming)
        (staging / other).write_text(manifest, encoding="utf-8")


FULL_FILLER_BYTES = 57290000   # what mke2fs -d still accepts on the 64 MiB image; fill_to_capacity() takes the rest


def fill_to_capacity(destination: Path) -> int:
    """Consume the blocks mke2fs leaves free, so the first save hits ENOSPC.

    `mke2fs -d` refuses a tree that does not fit but keeps a margin of a few
    dozen blocks; debugfs can allocate those one file at a time until the
    filesystem is genuinely full. Returns the number of blocks it managed to add.
    Used only by the `full` variant; a lab tool, not a card feature.
    """
    debugfs = find_tool("debugfs")
    dumpe2fs = find_tool("dumpe2fs")
    with tempfile.NamedTemporaryFile("wb", suffix=".blk", delete=False) as block:
        block.write(b"MUN-FILL" * 128)   # exactly one 1 KiB block
        block_path = block.name
    added = 0
    try:
        for index in range(4096):
            result = subprocess.run([debugfs, "-w", "-R", f'write {block_path} content/fill-{index:04d}.bin', str(destination)],
                                    capture_output=True, text=True, check=False)
            if result.returncode != 0 or "Could not allocate" in (result.stderr + result.stdout):
                break
            added += 1
            free = subprocess.run([dumpe2fs, "-h", str(destination)], capture_output=True, text=True, check=False).stdout
            for line in free.splitlines():
                if line.startswith("Free blocks:") and int(line.split(":")[1]) == 0:
                    return added
    finally:
        os.unlink(block_path)
    return added


def write_noise_cover(path: Path, side: int) -> None:
    """A valid but incompressible PNG (deterministic noise), to exercise size limits and transport."""
    import random
    noise = random.Random(20260919).randbytes(side * side * 3)
    rows = bytearray()
    for y in range(side):
        rows.append(0)
        rows += noise[y * side * 3:(y + 1) * side * 3]
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 1)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def write_cover(path: Path, width: int, height: int) -> None:
    """A dependency-free PNG: MUN-like two-tone disc on the console's off-white."""
    background = (0xF4, 0xF1, 0xE9)
    left, right, ring = (0xF0, 0xE0, 0xCC), (0xB8, 0x62, 0x3A), (0x9A, 0x4E, 0x2C)
    cx, cy, radius = width / 2, height / 2, min(width, height) * 0.36
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d > radius:
                rows += bytes(background)
            elif d > radius - max(2, radius * 0.03):
                rows += bytes(ring)
            else:
                rows += bytes(left if dx < 0 else right)
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)
