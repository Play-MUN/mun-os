# Saves on the Game Card

Saves live on the Game Card and travel with it: a player can continue on
another console with nothing but the card. The console writes them; a game
never writes to the card itself. A game on a `mount`-access card reads its
content through a read-only mount ([runtime.md](runtime.md#content-during-play));
saves never go through it. This document is the save
contract as MUN™ OS implements it. The card format is in
[game-cards.md](game-cards.md), the game's side of a session in
[runtime.md](runtime.md).

There are two ways a game's progress reaches the card:

- **Single-object saves.** The game asks the console to save one JSON object
  (MUN Collect).
- **Directory saves.** The game writes its own files, unmodified, and the
  console copies declared files to the card at moments it can verify.

Both are stored the same way.

## Storage

A card's save is `<saves.location>/<card.id>/save.json`, for example
`saves/mun.collect/save.json`: a JSON envelope written by the console.

```json
{"format": "mun-save/1", "game": "mun.collect", "content_version": "0.1.0",
 "schema": 1, "saved_at": "2026-09-19T16:40:12Z", "session": "s…", "insertion": "…",
 "payload": { … }}
```

- `format` is the card's generation: `mun-save/1`, or `neptune-save/1` on a
  card of the earlier generation ([game-cards.md](game-cards.md#naming-generations)).
- `content_version` is the game version from the manifest; `schema` is the
  save schema the game declares. They are independent: the game decides which
  schemas it can read.
- A directory save's envelope also carries `"payload_kind": "files"` and a
  payload listing each file with its size, SHA-256 and base64 data.

Next to it the console keeps `save.json.prev`, the previous envelope, and
moves a file it cannot parse aside as `save.json.damaged-<time>` instead of
deleting it.

### What "saved" means

A save is confirmed only after the new envelope was written to a temporary
file and synced, the previous `save.json` got its second name
`save.json.prev`, the temporary file was renamed onto `save.json` in one
operation, the directory was synced and the card was remounted read-only,
which flushes the journal and marks the file system clean. At every step the
card still holds a loadable save: the old one until the final rename, the new
one after it.

The writer reaches the save directory one component at a time without
following links; a link or a component of the wrong type anywhere on the way
is `save_path_unsafe` and nothing is written. Nothing existing is opened for
writing, and a pipe, device or socket left under a save name can never make
the console wait.

### Restore

When a game is started, the card service copies the current `save.json` into
the session's working directory. If `save.json` is missing or is not a
regular file while `save.json.prev` is a valid envelope of the card's
generation, the previous save is handed out instead, marked as recovered; for
a directory-save card, an envelope whose files do not match their own hashes
counts as unusable too. A single-object `save.json` that does not parse as an
envelope is still handed to the game, which reports it, and is not
overwritten until the player saves on purpose.

A save envelope of the other naming generation is not restored: a valid
`save.json.prev` of the card's own generation is used instead, otherwise the
game starts without a save and the session result says the card's save was
not used. The file stays on the card.

## Single-object saves

The console gives the game two environment variables ([runtime.md](runtime.md)):

- `MUN_SAVE_FILE`: the current save, copied into the working directory
  (`work/save.json`); absent file means a new game.
- `MUN_SAVE_SOCKET`: a UNIX socket that only this session's game can reach,
  open only while the session is running.

To save, the game connects, sends one line and reads one line:

```json
{"type":"save","schema":1,"data":{ … the game's own object … }}
{"type":"saved","ok":true,"bytes":196}
{"type":"saved","ok":false,"error":{"code":"no_space","message":"…"}}
```

- `schema` is a positive integer; `data` is a JSON object of at most 64 KiB,
  stored verbatim and never interpreted by the console.
- One save at a time per session (`save_busy`). A reply arrives only after the
  card holds the save, or with the reason it does not: `no_space`,
  `card_removed`, `card_mismatch`, `save_path_unsafe`, `save_too_large`,
  `save_invalid`, `sync_failed`, `write_failed`, `no_session`,
  `reader_unavailable`, `card_unavailable`.
- A save is bound to the session and the insertion it was started from; a
  late reply for a session that has ended is not delivered, and a card that
  replaced the original in the same slot never receives it.
- A game should show "saved" only after `ok: true`. Without the two
  variables it can still play, but cannot save.

`examples/mun-collect` implements the game side
([README](../examples/mun-collect/README.md)).

## Directory saves

For a game that writes its own save files, the manifest declares which files
are saves:

```toml
[saves]
location = "saves"
directory = ".mygame/save"           # relative to the game's HOME
units = ["*.sav", "screen-*.zsc"]     # file name patterns in that directory, "*" the only wildcard
checks = ["zlib-xml", "zlib"]         # one completeness check per pattern
max_bytes = 8388608                   # bound on the sum of the saved files, 1 KiB to 8 MiB
```

The four fields go together or not at all, and only a `game` card may declare
them. At most eight patterns. Each file in `directory` whose name matches a
pattern is a **unit**: a file that is a complete save by itself. Nothing else
in the game's home is saved; settings and caches stay in the session's RAM.

| Check | A unit passes when |
| --- | --- |
| `zlib` | its bytes are one complete zlib stream, with nothing after its end |
| `zlib-xml` | as `zlib`, and the inflated bytes are well-formed XML once wrapped in one root element |
| `xml` | well-formed XML once wrapped in one root element |
| `any` | always; only for formats that cannot be torn |

For both XML checks one NUL at the very end is ignored; document type and
entity declarations are refused. A truncated in-place write fails `zlib` and
`zlib-xml` by construction.

### When the console copies them

The console copies units only at an instant it can show to be a save point:

1. A unit was closed after being written, or renamed into place, or the game
   has stopped. Nothing is copied because time passed.
2. The game is frozen (its whole cgroup), so no process of it runs meanwhile.
3. Every unit can be read with a read lease, which the kernel grants only
   when no process has the file open for writing or mapped shared and
   writable. A refused lease means a writer is in the middle: nothing is
   taken, the game is thawed, and the next close tries again.
4. Every unit is a regular file with one link, within the bound, and passes
   its check.

The freeze lasts milliseconds. A unit that fails its check or has vanished
keeps its last saved version (it is *carried*), so a torn file never costs
another unit's new progress; a new unit that fails is left out. Both are
reported. A copy identical to the last one written is not written again.
After the game stops, the console accounts for every write it made, captures
once more and returns to the shell only when the card has answered.

At the start of a session the console checks the envelope (format, card id,
names, sizes, hashes and checks) and writes the units into `directory`
before the game runs; anything wrong refuses the whole envelope, and the
player is told. Directory-save games get neither `MUN_SAVE_FILE` nor
`MUN_SAVE_SOCKET`.

### What the player is told

The session result carries the outcome, shown under the result line:
"Partida guardada en la Game Card (HH:MM)"; "La última partida no se guardó:
<cause>" when the last attempt failed; "Se perdieron los cambios de la
partida posteriores a HH:MM" when the card was removed with unsaved changes;
"Una ranura no estaba completa; se conservó su versión anterior" when a unit
was carried; and whether the save was restored, recovered from the previous
copy or could not be loaded.

### Limits

- Each unit is independent: there is no transaction across several files. A
  game whose save spans files that must match cannot be declared safely yet.
- Deleting a unit is not propagated; it stays on the card.
- Settings do not travel; a save made on one console is loaded from the
  game's slot list on another.

## Removal and failures

- **Safe removal** refuses while a session uses the card; otherwise no new
  writes are accepted, a write in flight completes, the card is unmounted
  strictly, and only then may it be pulled. If the unmount fails, the card
  stays mounted and usable and the request may be retried.
- **Unexpected removal** ends the session (`card_removed`); a write in flight
  fails and is reported; the previous save on the card is intact.
- A card pulled during a write comes back needing journal recovery, and the
  console refuses it until it is repaired elsewhere (`e2fsck`); the console
  has no in-place repair.
- A card full of data fails the write with `no_space`; the previous envelope
  stays.
- In the laboratory a card image is never attached to two running guests at
  once; moving a card between guests is detach, then attach.

These guarantees are established in QEMU guests. What a physical card does
on power loss or an abrupt pull has not been measured.
