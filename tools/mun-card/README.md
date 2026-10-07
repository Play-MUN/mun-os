# mun-card

Host tool for laboratory Game Card images: create, inspect offline, hash,
convert. It shares the `mun_card` package with the console's card service,
so the host and the guest apply exactly the same rules
([card format](../../docs/game-cards.md)).

```sh
./mun card tools                # e2fsprogs binaries and versions in use
./mun card create demo          # valid "MUN Test Card" → .local/gamecards/demo.img
./mun card inspect demo         # offline validation; exit 0 valid, 2 invalid
./mun card variants             # list of deliberate defects
./mun card create broken --variant bad-arch
./mun card hash demo
./mun card hash demo --files --ignore saves   # per-file digests of the content, saves left out
./mun card create old --earlier-names         # a card of the earlier naming generation (neptune.toml)
./mun card create mine --variant game --game mun-collect --title "My Collect" \
    --id org.example.mycollect --version 1.0.0 --cover cover.png --accent "#2E7EC5"   # a card of your own
./mun card convert old new --game-supports-mun-names   # a MUN-names copy; old is never changed
```

`--id` and `--version` set `card.id` (the same for every edition of a game:
saves belong to it) and `content.version`, checked by the console's own rules
when the card is made; without them a variant keeps its own. The whole walk,
from the executable to a save restored after a restart, is in
[create a Game Card](../../docs/guides/create-game-card.md).

New cards carry `mun.toml` and their saves `mun-save/1`; `--earlier-names`
makes a card with `neptune.toml` and `neptune-save/1` for compatibility
tests, and `inspect` says which names a card uses
([naming generations](../../docs/game-cards.md#naming-generations)). A card
with both manifest names is refused.

`convert SOURCE DEST` makes a MUN-names copy of an earlier card in
`.local/gamecards`: it holds both names in the laboratory's attach registry,
so no guest can attach them meanwhile; refuses a source that is attached,
unclean, invalid, already MUN, holds special files or has a save the console
would not restore (decided from the image, links in the save path refused
before anything is read); renames the manifest
and rewrites the `format` of `save.json` and `save.json.prev`, nothing else;
builds under a temporary name and publishes only after checking that every
other file is byte-identical and each save's content is unchanged; and never
replaces an existing card. It refuses a game whose executable shows it knows
only the earlier names, and otherwise needs `--game-supports-mun-names`: the
tool verifies structure, not the game. The conversion is complete when a
guest restores the save from the copy; until then use the original.

A `game-gl` card may declare directory saves ([saves](../../docs/saves.md#directory-saves)): `--saves-directory
PATH` (relative to the game's `HOME`), one `--saves-unit PATTERN:CHECK` per
file name pattern that is a complete save by itself (`zlib-xml`, `zlib`,
`xml` or `any`; the check is never implied), and `--saves-max-bytes BYTES`
(1 KiB to 8 MiB for the sum of the files). The four fields go together or
not at all, and `inspect` prints them.

Requires Python 3.9+ and e2fsprogs (`brew install e2fsprogs`, keg-only; the
tool finds `mke2fs`/`debugfs` through `brew --prefix`, no PATH changes).
Images are 64 MiB ext4 by default, built with `mke2fs -d` from a temporary
staging tree, fixed timestamps, null UUID and fixed hash seed. The image also
records each file's permissions, user and group as the staging tree has them:
the user who runs the tool, permissions from the umask for the files it
writes (game executables are 0755), and a group that can come from the
folder holding the staging tree (`TMPDIR`), as on macOS. The same content
yields the same bytes when those, and the e2fsprogs version and
configuration, are the same too. Only regular image files are ever written.

`inspect` reads the image through `debugfs`, never mounts it, and prints
whether the SHA-256 changed during inspection (it must not).

`shape` checks MUN Shape packages ([docs/shape.md](../../docs/shape.md)),
the folder that goes on a card as `content/mun-shape/`:

```sh
./mun card shape init .local/mypkg --cover cover.png   # template; palette read from the cover
./mun card shape init .local/mypkg --example sea       # or a copy of a sample package
./mun card shape check .local/mypkg --report           # exit 0 all used, 2 something dropped or unused
./mun card shape variants .local/shape-fixtures        # one defective package per rule
./mun card create mine … --shape .local/mypkg          # the package on the card, refused unless used whole
./mun card shape check mine                            # the package on the card, as the console reads it
./mun card inspect mine                                # the card, and its package in brief
```

`init` writes nothing into a destination with links, or with files at the
names it writes unless `--force`, which replaces only those files.
`create --shape` copies `shape.json` and the files it names (regular files,
never through a link) to `content/mun-shape/`; `--shape-partial` makes the
card even when the console would drop part of the package or all of it. The
whole way, previewed in a console: [dress the console in your
game](../../docs/guides/shape-your-game.md).

Package layout: `minitoml` (strict TOML subset used on both sides),
`validate` (manifest v0 rules, naming generations and `CardInfo`), `source`
(mounted directory or `debugfs` image), `ext4` (superblock checks), `image`
(creation, variants, generated cover), `saves` (save bounds, payload
integrity and the envelope rule the console restores by, shared with the
card service and kept equal to the launcher's by a test), `convert` (the
conversion to MUN names), `errors` (stable codes and messages), `shape`
(Shape packages: strict JSON, schema, file headers, budgets, contrast; no
decoding, so the card service can share it) and `shapetools` (host only: the
cover's palette, which needs a PNG decoder, the template and the defective
packages).
