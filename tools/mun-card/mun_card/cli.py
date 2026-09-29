"""mun-card: create, inspect and hash lab Game Card images on the host (macOS or Linux)."""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import convert, ext4, image
from .errors import CardError
from .source import DebugfsSource
from .validate import validate_card

REPO_ROOT = Path(__file__).resolve().parents[3]
CARD_ROOT = REPO_ROOT / ".local" / "gamecards"


def card_path(name_or_path: str) -> Path:
    candidate = Path(name_or_path)
    if candidate.suffix == ".img" or candidate.exists():
        return candidate
    if "/" in name_or_path or name_or_path.startswith("."):
        raise CardError("name_invalid", "Nombre de tarjeta no válido", name_or_path)
    return CARD_ROOT / f"{name_or_path}.img"


def cmd_create(args: argparse.Namespace) -> int:
    destination = card_path(args.name)
    if destination.exists() and not args.force:
        raise CardError("image_exists", f"{destination} ya existe; usa --force para sobrescribir")
    with tempfile.TemporaryDirectory(prefix="mun-card-") as tmp:
        staging = Path(tmp) / "card"
        staging.mkdir()
        saves = None
        if args.saves_directory:
            if not args.saves_unit or not args.saves_max_bytes:
                raise CardError("manifest_field", "--saves-directory necesita --saves-unit y --saves-max-bytes")
            if any(":" not in unit for unit in args.saves_unit):
                # No default check: "any" accepts a torn file, so it must be chosen, not implied.
                raise CardError("manifest_field", "--saves-unit necesita PATRÓN:COMPROBACIÓN", ", ".join(args.saves_unit))
            pairs = [unit.rsplit(":", 1) for unit in args.saves_unit]
            saves = {"directory": args.saves_directory, "units": [p[0] for p in pairs],
                     "checks": [p[1] for p in pairs], "max_bytes": args.saves_max_bytes}
        image.populate(staging, args.variant, args.title, Path(args.game) if args.game else None,
                       Path(args.content) if args.content else None, args.access,
                       Path(args.cover) if args.cover else None, args.accent, args.background, saves,
                       naming="earlier" if args.earlier_names else "mun")
        info = image.create_image(destination, staging, args.size, label=f"{image.CARD_LABEL_PREFIX}")
        if args.variant == "full":
            added = image.fill_to_capacity(destination)
            info["sha256"] = image.sha256_file(destination)
            print(f"  filled the last {added} blocks with debugfs; the filesystem is now full")
    print(f"created {destination} ({args.size} MiB, variant {args.variant}, "
          f"{'earlier names: neptune.toml' if args.earlier_names else 'MUN names: mun.toml'})")
    print(f"  mke2fs: {info['version']} at {info['tool']}")
    print(f"  sha256: {info['sha256']}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    path = card_path(args.card)
    if not path.is_file():
        raise CardError("image_missing", f"No existe {path}")
    report = {"image": str(path), "sha256": image.sha256_file(path)}
    try:
        fs = ext4.check_mountable(path)
        report["filesystem"] = {"label": fs["label"], "uuid": fs["uuid"], "block_size": fs["block_size"]}
        source = DebugfsSource(path, image.find_tool("debugfs"))
        info = validate_card(source)
        report["valid"] = True
        report["card"] = info.to_dict()
    except CardError as exc:
        report["valid"] = False
        report["error"] = exc.to_dict()
    # Inspection must not modify the image: prove it by hashing again.
    report["sha256_after"] = image.sha256_file(path)
    report["unchanged"] = report["sha256"] == report["sha256_after"]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"{path}")
        if report["valid"]:
            card = report["card"]
            print(f"  VÁLIDA  {card['title']} ({card['id']}) v{card['version']} · {card['kind']} · "
                  f"{card['arch']}/{card['profile']} · portada: {card['cover'] or 'no'} · "
                  f"{'declara ejecutable ' + card['entry'] if card['runnable'] else 'sin ejecutable'}")
            print(f"  nombres: {'MUN (mun.toml, partidas mun-save/1)' if card['naming'] == 'mun' else 'anteriores (neptune.toml, partidas neptune-save/1)'}")
            if card.get("saves_directory"):
                units = ", ".join(f"{u} ({c})" for u, c in zip(card["saves_units"], card["saves_checks"]))
                print(f"  partidas de carpeta: ~/{card['saves_directory']} · {units} · "
                      f"máximo {card['saves_max_bytes']} bytes")
        else:
            err = report["error"]
            print(f"  INVÁLIDA  [{err['code']}] {err['message']}" + (f" — {err['detail']}" if err["detail"] else ""))
        print(f"  sha256 {report['sha256'][:16]}… · sin cambios tras inspección: {'sí' if report['unchanged'] else 'NO'}")
    return 0 if report["valid"] else 2


def cmd_hash(args: argparse.Namespace) -> int:
    path = card_path(args.card)
    if not args.files:
        print(f"{image.sha256_file(path)}  {path}")
        return 0
    # Per-file digests: once saves are written the image hash changes legitimately,
    # so the manifest, executable and content are compared file by file instead.
    source = DebugfsSource(path, image.find_tool("debugfs"))
    for relative, size in source.walk():
        if any(relative == prefix or relative.startswith(prefix.rstrip("/") + "/") for prefix in args.ignore):
            continue
        print(f"{hashlib.sha256(source.read(relative, size)[:size]).hexdigest()}  {relative}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    source, destination = card_path(args.source), card_path(args.destination)
    result = convert.convert(source, destination, CARD_ROOT, args.game_supports_mun_names,
                             report=lambda line: print(f"  {line}"))
    print(f"convertida {result['destination']}")
    print(f"  origen sin cambios (sha256 {result['source_sha256'][:16]}…); "
          f"copia sha256 {result['destination_sha256'][:16]}…")
    print("  estructura verificada; compatibilidad del juego declarada, no comprobada.")
    print("  La conversión termina cuando el laboratorio recupera la partida de la copia en juego;")
    print("  hasta entonces la tarjeta en uso sigue siendo el original.")
    return 0


def cmd_variants(args: argparse.Namespace) -> int:
    for name, description in image.VARIANTS.items():
        print(f"{name:20s} {description}")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    for name in ("mke2fs", "debugfs"):
        tool = image.find_tool(name)
        print(f"{name:8s} {image.tool_version(tool)}  ({tool})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mun-card", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create", help="build a lab card image under .local/gamecards/")
    p.add_argument("name", help="card name (becomes <name>.img) or an explicit .img path")
    p.add_argument("--variant", default="valid", choices=sorted(image.VARIANTS), help="valid or a deliberate defect")
    p.add_argument("--title", default="MUN Test Card")
    p.add_argument("--game", metavar="EXECUTABLE", help="executable to embed for --variant game / game-gl")
    p.add_argument("--content", metavar="DIR", help="game-gl: data tree copied into content/ and read by the game during play (content.access = mount)")
    p.add_argument("--access", choices=("copy", "mount"), default=None, help="game-gl: content.access; --content implies mount")
    p.add_argument("--cover", metavar="PNG", help="cover image (PNG, at most 1 MiB and 1024x1024) instead of the generated one")
    p.add_argument("--accent", metavar="#RRGGBB", help="[presentation] accent colour the shell adopts while the card is active")
    p.add_argument("--background", metavar="#RRGGBB", help="[presentation] glow colour the shell adopts while the card is active")
    p.add_argument("--saves-directory", metavar="PATH", help="game-gl: directory saves, relative to the game's HOME (docs/saves.md)")
    p.add_argument("--saves-unit", action="append", default=[], metavar="PATTERN:CHECK",
                   help="a file name pattern that is one complete save, with its check (zlib-xml, zlib, xml, any); repeatable")
    p.add_argument("--saves-max-bytes", type=int, metavar="BYTES", help="bound on the sum of the saved files")
    p.add_argument("--size", type=int, default=image.DEFAULT_SIZE_MIB, metavar="MIB")
    p.add_argument("--earlier-names", action="store_true",
                   help="make a card of the earlier naming generation (neptune.toml, neptune-save/1), for compatibility tests")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("inspect", help="validate an image offline without mounting or modifying it")
    p.add_argument("card")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("hash", help="print the SHA-256 of an image, or of every file on it")
    p.add_argument("card")
    p.add_argument("--files", action="store_true", help="one line per regular file: sha256 and path")
    p.add_argument("--ignore", action="append", default=[], metavar="PREFIX",
                   help="with --files: skip this path or directory (e.g. saves)")
    p.set_defaults(func=cmd_hash)
    p = sub.add_parser("convert", help="copy a card of the earlier names to a new card with MUN names (docs/game-cards.md); "
                       "never modifies the source")
    p.add_argument("source", help="lab card name (or .local/gamecards/<name>.img) with neptune.toml")
    p.add_argument("destination", help="new lab card name; must not exist")
    p.add_argument("--game-supports-mun-names", action="store_true",
                   help="state that the card's game works with MUN names (MUN_*, mun-save/1, /run/mun/card), "
                        "e.g. a current build of a recipe in this repository; the tool cannot prove it")
    p.set_defaults(func=cmd_convert)
    sub.add_parser("variants", help="list the test card variants").set_defaults(func=cmd_variants)
    sub.add_parser("tools", help="show the e2fsprogs binaries and versions in use").set_defaults(func=cmd_tools)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if sys.platform == "win32":
        # e2fsprogs does not run on Windows, and the lab's process and lock
        # checks here are POSIX ones.
        print("error [host_unsupported]: mun-card necesita e2fsprogs (mke2fs, debugfs, e2fsck) y no funciona en "
              "Windows; úsalo dentro de WSL 2. La imagen descargada trae sus tarjetas ya creadas.", file=sys.stderr)
        return 1
    try:
        return args.func(args)
    except CardError as exc:
        print(f"error [{exc.code}]: {exc.message}" + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
