"""Just enough ext4 superblock reading to refuse images that must not be mounted.

Layout facts come from the kernel's ext4 documentation. The superblock sits at
byte 1024; we read magic, state, feature flags and label. A card that needs
journal recovery or is flagged with errors is rejected before `mount` because a
read-only mount with `noload` will not repair it and we never write to cards.
"""

import struct
from pathlib import Path
from typing import Dict

from .errors import CardError

SUPERBLOCK_OFFSET = 1024
EXT4_MAGIC = 0xEF53
STATE_CLEAN = 0x0001
STATE_ERRORS = 0x0002
INCOMPAT_RECOVER = 0x0004


def read_superblock(image: Path) -> Dict[str, object]:
    with open(image, "rb") as handle:
        head = handle.read(512)
        handle.seek(SUPERBLOCK_OFFSET)
        sb = handle.read(1024)
    if len(sb) < 1024:
        raise CardError("image_not_ext4", "La imagen es demasiado pequeña para contener ext4")
    magic = struct.unpack_from("<H", sb, 0x38)[0]
    state = struct.unpack_from("<H", sb, 0x3A)[0]
    log_block_size = struct.unpack_from("<I", sb, 0x18)[0]
    feature_incompat = struct.unpack_from("<I", sb, 0x60)[0]
    feature_ro_compat = struct.unpack_from("<I", sb, 0x64)[0]
    label = sb[0x78:0x88].split(b"\0", 1)[0].decode("utf-8", "replace")
    uuid_bytes = sb[0x68:0x78]
    return {
        "magic_ok": magic == EXT4_MAGIC,
        "clean": bool(state & STATE_CLEAN),
        "errors": bool(state & STATE_ERRORS),
        "needs_recovery": bool(feature_incompat & INCOMPAT_RECOVER),
        "block_size": 1024 << log_block_size,
        "feature_incompat": feature_incompat,
        "feature_ro_compat": feature_ro_compat,
        "label": label,
        "uuid": uuid_bytes.hex(),
        "mbr_signature": head[510:512] == b"\x55\xaa",
        "partition_entries": any(head[446 + 16 * i: 446 + 16 * (i + 1)] != b"\0" * 16 for i in range(4)),
    }


def check_mountable(image: Path) -> Dict[str, object]:
    """Raise CardError unless the image is a clean, whole-image ext4 filesystem."""
    info = read_superblock(image)
    if info["mbr_signature"] and info["partition_entries"] and not info["magic_ok"]:
        raise CardError("image_partitioned", "La imagen tiene tabla de particiones; v0 usa ext4 directo")
    if not info["magic_ok"]:
        raise CardError("image_not_ext4", "La imagen no contiene un sistema de archivos ext4")
    if info["needs_recovery"]:
        raise CardError("image_needs_recovery", "El sistema de archivos necesita recuperación y no se monta")
    if info["errors"] or not info["clean"]:
        raise CardError("image_has_errors", "El sistema de archivos está marcado con errores")
    return info
