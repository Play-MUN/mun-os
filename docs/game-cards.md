# Game Cards

A Game Card carries a game, its presentation and its saves. This document is
the contract of card format v0 as MUN™ OS implements it: the image, the
manifest, how the console treats a card, and the two naming generations it
reads. Saves are described in [saves.md](saves.md), what a game may do once
started in [runtime.md](runtime.md).

The development laboratory makes cards as image files and hot-plugs them into
a QEMU guest ([vm/README.md](../vm/README.md)); the physical card reader
belongs to the hardware integration, which is not selected yet.

## Image

- One ext4 file system directly on the medium, no partition table. A
  partitioned image is refused (`image_partitioned`).
- Layout: the manifest at the root, `content/` (or the directory the manifest
  names) for the game or test payload, `cover.png` optional, and the save
  directory, `saves/` by default.
- `mun-card` ([tools/mun-card](../tools/mun-card/README.md)) builds images on
  the host with e2fsprogs (`mke2fs -d`), fixed timestamps, a null UUID and a
  fixed hash seed. The image also records each file's permissions, user and
  group, so the same content yields the same bytes when those, and the
  e2fsprogs version and configuration, are the same too. Label `MUNCARD`,
  64 MiB unless a size is given.
- Before mounting, the console requires a whole-image ext4 with a clean
  superblock: an image that needs journal recovery (`image_needs_recovery`)
  or is marked with errors (`image_has_errors`) is refused and never
  repaired by the console.

## Manifest

A card of the current generation carries `mun.toml`; see
[Naming generations](#naming-generations) for `neptune.toml`. The manifest is
a strict subset of TOML: comments, `[table]` headers, `key = value` with
basic or literal strings, integers, booleans and arrays of strings; anything
else is a syntax error. It is UTF-8 and at most 64 KiB. The same parser and
validator run on the host (`mun-card inspect`) and in the console, so a card
one accepts is a card the other accepts.

```toml
[card]
schema = 1
id = "mun.collect"
title = "MUN Collect"

[content]
version = "0.1.0"
kind = "game"
arch = "aarch64"
profile = "linux-arm64-v0"
root = "content"
entry = "content/mun-collect"
cover = "cover.png"

[saves]
location = "saves"

[presentation]
accent = "#2E7EC5"
background = "#BFD9F2"
```

| Table.key | Required | Rule |
| --- | --- | --- |
| `card.schema` | yes | integer; `1` is the only accepted value |
| `card.id` | yes | `[a-z0-9][a-z0-9._-]{2,63}`; also names the card's save directory |
| `card.title` | yes | 1–120 characters |
| `content.version` | yes | `N(.N){0,3}` with an optional `-`/`+` suffix; the content's own version, independent of `card.schema` |
| `content.kind` | yes | `test` (described, never executed) or `game` |
| `content.arch` | yes | `aarch64` |
| `content.profile` | yes | `linux-arm64-v0` or `linux-arm64-gl-v0` ([runtime.md](runtime.md)) |
| `content.root` | yes | a directory |
| `content.entry` | for `game` | a regular file: the executable |
| `content.cover` | no | a PNG of at most 1 MiB and 1024×1024 |
| `content.access` | no | `copy` (default: only the entry is staged) or `mount` (the content is also mounted read-only for the game; [runtime.md](runtime.md)) |
| `presentation.accent` | no | `#RRGGBB`: while the card is the active one, the focus of Home's main arc and of the game's panel takes it, if it keeps its contrast there ([shape.md](shape.md#contrast)); Settings and dialogs keep MUN's |
| `presentation.background` | no | `#RRGGBB`: likewise for the ambient light at Home's orb. The card lends colours, never layout or structure; a MUN Shape palette takes precedence |
| `saves.location` | no | default `saves`; if present on the card it must be a directory |
| `saves.directory`, `saves.units`, `saves.checks`, `saves.max_bytes` | no, all four or none | directory saves for a game that writes its own files ([saves.md](saves.md)) |

The directory `mun-shape/` at the root of the content is reserved for MUN
Shape, the package with which a card dresses the console in its game's
identity ([shape.md](shape.md)). A game's own files do not use that name. The
manifest names nothing of it, and a package never makes a card invalid. A
console shows it when its image records the MUN Shape it reads
([shape.md](shape.md#compatibility)): v0.1.0-dev.3 does; v0.1.0-dev.2 and
earlier do not.

Every path in the manifest is relative, stays inside the card, has no empty,
`.` or `..` segment, no backslash or NUL, uses only letters, digits, `.`, `_`
and `-` in each segment (at most 255 characters in all) and must not be or
traverse a symbolic link.

Validation stops at the first defect and reports a stable code with a
sentence in Spanish, the console's language. The codes are listed in
[`mun_card/errors.py`](../tools/mun-card/mun_card/errors.py) (`ERROR_CODES`);
`mun-card variants` makes one card per deliberate defect.

## How the console treats a card

The card service ([services/mun-cardd](../services/mun-cardd/README.md))
mounts cards for the console and is the only process that writes to one. For
a card with `content.access = "mount"`, systemd also mounts it, read-only,
inside the game's unit ([runtime.md](runtime.md#content-during-play)).

- **Eligible media.** In the laboratory, virtio block devices whose serial
  starts with `NPT-`, the serial the laboratory tool gives a card; the system
  disk and anything else are never inspected or mounted.
- **Mount.** `ro,nosuid,nodev,noexec`, with the journal loaded, inside the
  service's private mount namespace. The card is written only for a save, in a
  short read-write window ([saves.md](saves.md)).
- **States.** `reading`, `valid`, `invalid`, `waiting`. One card is active at
  a time, the first inserted; a second card waits unmounted and is evaluated
  when the first is removed. Each insertion gets a random token that consumers
  compare to know they still see the same insertion.
- **Nothing on a card runs by being inserted.** A game runs only when the
  player chooses Play, from a copy ([runtime.md](runtime.md)).
- **Removal.** Safe removal (Eject safely in the shell, `card-detach` in the
  laboratory) releases the card first: no session may be using it, a write in
  flight completes, the card is unmounted strictly, and only then may it be
  pulled. An unexpected removal ends a running session and is reported.

## Naming generations

The project began under another name, and cards made then carry it. Card
format v0 exists in two naming generations with the same layout, fields and
`card.schema`:

| | Earlier generation | MUN generation |
| --- | --- | --- |
| Manifest | `neptune.toml` | `mun.toml` |
| Save envelope `format` | `neptune-save/1` | `mun-save/1` |
| Game environment | `MUN_*` and, identical, `NEPTUNE_*` | `MUN_*` |
| Content mount | `/run/mun/card`, also reachable as `/run/neptune/card` | `/run/mun/card` |

- The manifest's file name tells the generations apart. A card with both
  names at its root is invalid (`manifest_ambiguous`), whatever either file
  holds: which would be authoritative cannot be decided from untrusted media.
- `mun-card create` makes MUN-generation cards; `--earlier-names` makes an
  earlier-generation card, for compatibility tests. `mun-card inspect` says
  which generation a card is.
- The console reads both and always writes a card's own generation: an
  earlier card keeps `neptune-save/1` on every save, and no console upgrades
  a card on its own. A save envelope of the other generation is not restored
  ([saves.md](saves.md)).
- Games on earlier cards were built against `NEPTUNE_*` and, for engines with
  a compiled-in data path, `/run/neptune/card`; the console keeps both for
  those cards. Games on MUN-generation cards must use the `MUN_*` names and
  `/run/mun/card` only.

### Converting an earlier card

`mun-card convert SOURCE DEST` makes a MUN-generation copy of an earlier
card and never modifies the source (its SHA-256 is the same before and
after, on success and on failure).

- It works on card images in the laboratory's card directory and holds both
  names in the laboratory's attach registry meanwhile, so no guest can attach
  them during the conversion.
- The source must be a clean file system, a valid earlier-generation card,
  hold no special files (pipes, devices, sockets) and carry only saves the
  console would restore: a damaged save is recovered explicitly on another
  copy first, never made to look valid by a new format name.
- It changes exactly this: the manifest is renamed to `mun.toml`, and the
  `format` of `save.json` and `save.json.prev` becomes `mun-save/1`. Every
  other file is byte-identical, which the tool verifies before it publishes
  the copy under its final name; a failure leaves nothing under that name,
  and an existing destination is never replaced.
- The tool verifies structure, not the game. It refuses a game whose
  executable shows it knows only the earlier names (`neptune-save/1` for a
  game that reads its own envelope, `NEPTUNE_` without `MUN_`, or
  `/run/neptune/card` without `/run/mun/card`), and otherwise needs
  `--game-supports-mun-names`, the operator's statement that the game uses
  the MUN names. A conversion is complete once the copy's save has been
  restored in play; until then keep using the original.

## Limits of format v0

- One save slot per game on the card; no profiles.
- No versioned content or updates on the card yet, no signatures and no
  content encryption.
- One active card at a time.
- The laboratory's hot-plug is a PCIe device add/remove, which resembles but
  is not SD card insertion; what a physical card does on power loss or an
  abrupt pull has not been measured.
