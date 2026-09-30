# MUN Shape

MUN Shape (Shape for short) lets a Game Card dress the console in its game's
identity while the card is in: a palette, the card object, a world behind the
menus, the materials of the menu entries and the game's panel, a transition
and the menu sounds. The card describes it with data; MUN™ draws every
package with the same code, and nothing on the card runs.

**Status.** This document is the package contract, format `mun-shape/1`, and
its checker, `mun-card shape`. **The console does not render Shape yet**: no
image reads a package, and a card that carries one behaves exactly as the
same card without it. What the console is to do with a package is described
under [How the console is to use a package](#how-the-console-is-to-use-a-package);
the resource figures there are objectives to be measured, not guarantees.

## Where the package lives

The package is the directory `mun-shape/` at the root of the card's content
(`content.root`, [game-cards.md](game-cards.md)): `shape.json` and the files
it names.

```
content/
  mun-collect                 the game
  mun-shape/
    shape.json
    card/window.png
    world/backdrop.png  world/reliefs-far.png  world/fish.png  world/rays.png
    sfx/move.wav  sfx/enter.wav  sfx/back.wav  sfx/insert.wav
```

The console finds a package by that fixed path; the manifest names nothing
and `card.schema` does not change. The directory name is reserved for MUN: a
game's own files do not use it. A card without the directory has no package.
Because the package sits inside the content, whichever view selects a
version of the content will select its package with it.

## Syntax

`shape.json` is JSON under a strict profile, applied before any field is read:

- UTF-8 without a byte-order mark, at most 64 KiB, an object at the top.
- A key may appear only once per object (`shape_duplicate_key`).
- No `NaN` or `Infinity`, no number that overflows to infinity (`1e999`), no
  number literal longer than 32 characters (`shape_number`).
- At most 6 levels of nesting, counting the top object (`shape_depth`),
  measured before parsing.
- No string or key longer than 512 characters (`shape_string_too_long`).

A document that breaks the profile, is not JSON, or has no valid `format` is
not used at all; the card is unaffected. Syntax errors give a line and column.

## Structure

```json
{
  "format": "mun-shape/1",
  "palette": { "light": "#E6FBFF", "mid": "#2A9DBA", "deep": "#02202E",
               "plate": "#04283A", "text": "#EFFFFF", "accent": "#F2B85C" },
  "card": { "window": "card/window.png", "shape": "organic", "morph": 0.6, "glow": "#9FE7F2" },
  "world": {
    "backdrop": { "image": "world/backdrop.png" },
    "layers": [
      { "image": "world/reliefs-far.png", "motion": "drift", "speed": 8, "depth": 0.2, "opacity": 0.9 }
    ],
    "emitters": [
      { "sprite": "world/fish.png", "count": 18, "path": "school", "speed": 45, "band": [0.25, 0.6] }
    ],
    "light": [
      { "texture": "world/rays.png", "blend": "screen", "motion": "sway", "opacity": 0.55 }
    ],
    "rate": 20
  },
  "surfaces": { "entries": { "material": "glass" }, "panel": { "material": "glass" } },
  "transition": { "in": "tide", "out": "tide", "seconds": 3.2 },
  "sounds": { "move": "sfx/move.wav", "enter": "sfx/enter.wav",
              "back": "sfx/back.wav", "insert": "sfx/insert.wav" }
}
```

`format` is required: `mun-shape/1`, or `mun-shape/1.N` for a later minor
version. Each of the six blocks is optional and taken or dropped on its own.
Every value selects or tunes MUN's own drawing: names are enumerations MUN
implements, numbers have ranges, files are PNG images and WAV sounds of the
package. Colours are `#RRGGBB`; paths follow the manifest's path rules
([game-cards.md](game-cards.md#manifest)) relative to `mun-shape/`.

### `palette`

All six colours, or the block is dropped (`shape_field`).

| Field | Meaning |
| --- | --- |
| `light`, `mid`, `deep` | The world's tones: highlights, middle, depth |
| `plate` | The plate under the dressed surfaces' text |
| `text` | Text on that plate; a chosen entry is drawn as `plate` on a bar of `text` |
| `accent` | Focus: a chosen entry's ring and marks |

### `card`

| Field | Default | Rule |
| --- | --- | --- |
| `window` | the card's cover | PNG shown in the card object |
| `shape` | `card` | `card` or `organic` |
| `morph` | `0` | 0–1: how far the organic outline moves |
| `glow` | the palette's `light` | colour of the object's light |

### `world`

| Field | Default | Rule |
| --- | --- | --- |
| `backdrop` | required | `{ "image": PNG }` or `{ "gradient": [2 to 4 colours] }`, one of them |
| `layers` | none | up to 3, drawn in order, the first farthest |
| `layers[].image` | required | PNG; a moving layer tiles horizontally |
| `layers[].motion` | `still` | `still`, `drift`, `parallax` or `sway` |
| `layers[].speed` | `0` | 0–120 canvas pixels per second |
| `layers[].depth` | `0.5` | 0–1: how much the layer follows navigation |
| `layers[].opacity` | `1` | 0–1 |
| `emitters` | none | up to 2, with at most 128 sprites in all |
| `emitters[].sprite` | required | PNG |
| `emitters[].count` | required | 1–128 |
| `emitters[].path` | required | `rise`, `fall`, `drift`, `school` or `orbit` |
| `emitters[].speed` | `40` | 1–240 canvas pixels per second |
| `emitters[].band` | `[0, 1]` | `[from, to]`: the part of the screen's height they move in |
| `emitters[].scale` | `1` | 0.25–2 |
| `light` | none | up to 2 light textures |
| `light[].texture` | required | PNG, stretched over the screen |
| `light[].blend` | `screen` | `screen` or `add` |
| `light[].motion` | `still` | `still`, `sway`, `ripple` or `pulse` |
| `light[].opacity` | `0.5` | 0–1 |
| `rate` | `10` | `10` or `20` frames per second for the world |

Canvas pixels are those of MUN's 1920×1080 design canvas. A world in which
nothing moves (every motion `still`, no emitters) is still.

### `surfaces`

`entries` (the menu's entries) and `panel` (the game's panel) each take
`{ "material": "solid" | "glass" | "paper" }`, `solid` by default. The status
line, the path and the hints sit on a band of MUN's glass.

### `transition`

| Field | Default | Rule |
| --- | --- | --- |
| `in` | `fade` | `tide` (a front from the card object reaches each surface in turn), `fade` or `sweep` |
| `out` | `fade` | likewise, when the package leaves |
| `seconds` | `1.6` | 0.8–4 |

### `sounds`

`move`, `enter` and `back` together, or none; `insert`, played once when the
package is applied, is optional. The game's sounds replace MUN's for those
actions on the menus only; alerts and system sounds stay MUN's.

## Files

- Only files the package names are read; others in the directory are ignored
  and never copied.
- Each is a regular file, neither a symbolic link nor behind one, inside
  `mun-shape/`.
- **Images**: PNG, at most 4 MiB each. The checker reads the chunk structure
  without inflating it: signature, `IHDR` first and valid, every chunk's
  length and CRC, `PLTE` where required, consecutive `IDAT` starting as a
  zlib stream, `IEND` last with nothing after it, no unknown critical chunk.
  The largest side is 2048 for a backdrop, a layer or a light texture, 1024
  for the card window and 256 for a sprite, checked from `IHDR` before
  anything decodes the image.
- **Sounds**: WAV with exactly two chunks, `fmt ` (PCM, 48 kHz, 16-bit,
  stereo) and `data`, at most 1 MiB each; `move`, `enter` and `back` at most
  1 s, `insert` at most 3 s; peaks at or under −1 dBFS. To remove metadata:
  `ffmpeg -i in.wav -ar 48000 -ac 2 -c:a pcm_s16le -fflags +bitexact -map_metadata -1 out.wav`.
- The package's named files add up to at most 32 MiB. Blocks enter that
  budget in the order `card`, `sounds`, `world`; a block that would exceed
  it is dropped (`shape_budget`). A file named twice counts once.

## Limits

**Enforced** by the checker, and by the console once it reads packages:

| Item | Limit |
| --- | --- |
| `shape.json` | 64 KiB, 6 levels, strings of 512 characters |
| Package | 32 MiB of named files |
| Image | 4 MiB; 2048 × 2048 (backdrop, layer, light), 1024 × 1024 (window), 256 × 256 (sprite) |
| World | 3 layers, 2 emitters with 128 sprites in all, 2 light textures, 10 or 20 fps |
| Sound | 1 MiB; 1 s (`move`, `enter`, `back`), 3 s (`insert`); −1 dBFS |
| Transition | 0.8–4 s |

**Objectives**, not yet measured, which the console is to meet when it renders
packages: with a world in motion, navigation latency and menu frame pacing
within 10 ms (95th percentile) of the console without Shape; nothing drawn
continuously while the world is still or resting; incremental memory at most
136 MiB at 1080p and 200 MiB at 1440p, the package's copy included; ready
within 1.5 s of the card being valid. `mun-card shape check --report`
estimates a package's memory by arithmetic, which is not a measurement. A
package that fits the enforced limits but not a display's budget is to be
drawn at a lower level of detail (without light textures, then with fewer
layers, then still, then colours only), never at the expense of navigation.

## When something is wrong

Shape never makes a card invalid, never delays *Play* or *Eject safely*, and
never shows the player an error; its diagnostics go to logs and to the
checker.

| Case | Result |
| --- | --- |
| No `mun-shape/` | No package: the palette read from the cover, or MUN |
| `mun-shape` not a folder, `shape.json` missing, unreadable, over the profile, not JSON, no valid `format`, an unknown major version | The package is not used |
| A block with a missing or mistyped field, a number out of range, an unknown name, a bad colour, too many elements | That block is dropped; the rest is used |
| A named file missing, a link, not a regular file, over its size, not a well-formed PNG or WAV, over its dimensions, duration or peak; the package over its budget | The block that names it is dropped |
| The palette's colours do not keep contrast (below) | The dressed surfaces use MUN's colours, text, plate and focus together; the palette still tones the world |
| An unknown field, or a later minor version | Ignored, with a note; what is known is used |
| An image that fails to decode in the console, or exceeds the display's budget | That block, at run time, falls back to MUN or to a lower level |

## Contrast

A ratio between two colours says nothing about text on a translucent plate
over moving art, so the rules hold for any world beneath, at every frame.

- **Text and focus sit on plates.** Every dressed surface with text or focus
  has a plate drawn by MUN; the focus is drawn on the plate. Text needs 4.5:1
  against everything behind it, focus 3:1 (WCAG 2's ratios and sRGB
  luminance).
- **The opacity is computed.** For a plate of colour P drawn at opacity α
  over a world W, each composed channel α·P + (1 − α)·W lies between α·P and
  α·P + 1 − α, and luminance grows with every channel, so the composite's
  luminance lies between two bounds that hold for every world, from black to
  white. MUN draws the plate at the least 8-bit opacity whose bounds stay on
  one side of the text's luminance and the focus's, with their ratios; one
  rounding step of 8-bit composition is included. `solid` and `paper` plates
  are opaque; `glass` stays translucent at no less than 0.5, or becomes as
  opaque as its text needs.
- **Materials stay within the proof.** Glass may lighten its plate by up to
  6 % (a sheen), paper vary it by up to 4 % towards black or white (grain);
  the bounds include those ranges.
- **Sets, never single colours.** A surface's text, plate and focus, and a
  chosen entry's bar and label, are checked together; if the game's set does
  not hold on every surface, MUN's own set is used on all of them. MUN's set
  (text `#DAD7D1` on plate `#17181C`, focus `#E39A63`, bar `#DAD7D1` with
  label `#131417`) is verified by the tests, on every material.
- **Transitions are proven, not sampled.** Only a plate blends; its text and
  focus change colour at one point. The plate's opacity first rises to the
  higher of both ends (to opaque for a bridge), its colour then blends at
  that opacity, and the opacity settles. Every plate colour of the blend, and every edge where
  one plate meets the other, lies within the channel ranges of the two
  ends, and the bounds are proven over those ranges, so they hold for every
  frame. The checker names each surface's plan, and the same plan runs in
  reverse when the package leaves:

  | Plan | When | What happens |
  | --- | --- | --- |
  | `neutral-text` | MUN's text holds over the whole blend | MUN's text until the blend ends |
  | `shape-text` | the game's text holds over it | the game's text from its start |
  | `bridge` | neither, but both hold on an opaque midpoint | the plate blends through that colour, where the text changes |
  | `cut` | none of these (for example dark text on a light plate against MUN's light text on a dark one) | the surface changes in one frame |

- **Dialogs.** Settings and every system dialog keep MUN's panels and focus
  over the game's world, dimmed by a fixed MUN scrim.
- **Lent colours.** A card's `[presentation] accent`, or the accent read from
  its cover, becomes the focus on MUN's plate only if it keeps 3:1 there; the
  plate's opacity rises as far as that needs, and otherwise MUN's focus stays.

## Read level

A card without a palette gets a modest one read from its cover
(`content.cover`) when it becomes valid: the accent and tints of MUN's own
world. The player can turn it off, and a declared palette takes precedence.
The algorithm, exact so that the console and the checker agree:

1. Decode the cover to 8-bit RGB (a 16-bit sample keeps its high byte),
   compositing transparency over black, rounded half up.
2. Reduce it to 64 × 64 by averaging boxes: cell *i* covers source columns
   ⌊i·W/64⌋ up to, not including, max(⌊i·W/64⌋ + 1, ⌊(i+1)·W/64⌋), rows
   likewise; averages are rounded half up.
3. Sort the 4096 cells by 2126·R + 7152·G + 722·B, then by R, G, B.
4. Average, rounding half up, the cells from ⌊4096·a/100⌋ up to ⌊4096·b/100⌋
   for each band: `deep` 0–8, `low` 20–35, `mid` 50–70, `hi` 82–95,
   `light` 97–100.
5. The accent is the cell, first in reading order on a tie, whose red
   channel is its highest, with saturation (max − min)/max over 0.35 and hue
   under 55° or over 330°, that has the greatest max − min; each channel is
   scaled by 235/max, rounded half up, capped at 255. Without such a cell,
   there is no accent.

`mun-card shape check DIR --cover cover.png` prints what a console would read.

## Compatibility

- A reader uses a package whose major version it knows and ignores what a
  later minor version adds, with a note. A package of another major version
  is not used, and the card stays valid.
- A later minor version adds blocks and fields; it never changes the meaning
  of existing ones.
- Shape adds nothing to `mun.toml`. MUN OS v0.1.0-dev.1 and v0.1.0-dev.2 read
  only what the manifest names inside the content, so to them a card with
  `mun-shape/` is the same card, played with its lent colours.

## How the console is to use a package

This is the intended behaviour, for the implementation to follow and be
measured against; none of it exists yet.

- **The card service** checks the package with the same module as
  `mun-card` (`mun_card/shape.py`), only after the card is valid and never
  before its state is published. One worker per insertion copies the named
  files, bounded and in chunks, to a directory in RAM under `/run/mun/shape/`,
  checks the copy, adds the normalised `shape.json`, makes it read-only and
  publishes it with one rename; a copy that is stale or incomplete is
  deleted, never published. The service never decodes an image or a sound.
- ***Eject safely* stays exact.** A release first cancels the copy, which
  checks for cancellation between chunks. The service waits for the copy's
  files to close for a bounded time, without blocking its own event loop,
  and keeps the release pending meanwhile: it does not report the card as
  ejected until every reader is closed and the card is strictly unmounted. If
  a failing card blocks a read, the answer is the existing "still in use",
  and the player is told not to remove the card; a new insertion does not
  start another copy while one is stuck. A physical removal does not
  necessarily end a blocked read at once.
- **The shell** reads only the service's copy, decodes it off the interface
  thread with the dimensions checked first, paints the world into its own
  frames off the interface thread and hands finished frames to the interface
  without waiting for them, and binds only the eligible surfaces to the
  package; Settings and dialogs keep MUN's. Everything is tagged with the
  card's insertion and dropped if stale.
- **Experience.** Returning from a game with the same card keeps its Shape,
  with a short fade; removal restores MUN. Settings: MUN Shape on, colours
  only or off; reduced motion; the game's menu sounds on or off. The first
  implementation carries no ambient loop, gallery or custom typeface.

## Tools

```sh
./mun card shape init DIR [--cover cover.png]   # a template; the palette read from the cover
./mun card shape init DIR --example sea         # or a copy of a sample package (sea, paper)
./mun card shape check DIR [--report] [--json] [--cover cover.png]
./mun card shape variants [OUT]                 # the defective packages below
```

`check` prints what a console would use, block by block, each surface's
colours, opacity and transition plan, and every note; `--report` adds the
contrast ratios and the memory estimate; `--json` prints the normalised
package, the files, the notes and the estimates. It exits 0 when everything
declared is used, 2 when anything is dropped, replaced or the package is not
used, and 1 when it cannot run. The template declares a palette, the card
object, the surfaces and a transition, which need no file, and a README
explains how to add the world and sounds.

On a card, the package is the folder `content/mun-shape/`. For a
`game-gl` card made with `mun-card create --variant game-gl --content DIR`,
place it in `DIR` as `mun-shape/`; the tool copies the tree into `content/`.

### Defective packages

`variants OUT` writes a small valid package with one defect each; the card
around any of them stays valid.

| Fixture | Defect | Code | Dropped |
| --- | --- | --- | --- |
| `not-json` | not JSON | `shape_syntax` | the package |
| `duplicate-key` | a key twice in `palette` | `shape_duplicate_key` | the package |
| `nan` | `NaN` | `shape_number` | the package |
| `infinite` | `1e999` | `shape_number` | the package |
| `deep` | seven levels of nesting | `shape_depth` | the package |
| `long-string` | a 600-character string | `shape_string_too_long` | the package |
| `not-utf8` | a byte that is not UTF-8 | `shape_encoding` | the package |
| `bom` | a byte-order mark | `shape_encoding` | the package |
| `too-large` | `shape.json` over 64 KiB | `shape_too_large` | the package |
| `not-object` | a list at the top | `shape_structure` | the package |
| `unknown-major` | `mun-shape/2` | `shape_format_unsupported` | the package |
| `no-format` | no `format` | `shape_format` | the package |
| `unknown-enum` | a layer's motion `spiral` | `shape_enum` | `world` |
| `out-of-range` | `morph` 1.5 | `shape_range` | `card` |
| `bad-colour` | `#GG0000` | `shape_colour` | `palette` |
| `missing-field` | a palette without `text` | `shape_field` | `palette` |
| `missing-file` | a sound that does not exist | `shape_path_missing` | `sounds` |
| `symlink` | the backdrop is a symbolic link | `shape_path_symlink` | `world` |
| `dotdot` | `../mun.toml` | `shape_path_unsafe` | `card` |
| `absolute` | `/etc/passwd` | `shape_path_unsafe` | `card` |
| `directory` | a layer that is a folder | `shape_path_type` | `world` |
| `oversized-file` | a sprite over 4 MiB | `shape_file_too_large` | `world` |
| `png-false-header` | a PNG signature and a false `IHDR` | `shape_png` | `world` |
| `png-bad-crc` | a wrong CRC | `shape_png` | `world` |
| `png-truncated` | cut halfway | `shape_png` | `world` |
| `png-bomb` | `IHDR` of 30000 × 30000 over a few bytes | `shape_png_dimensions` | `world` |
| `sprite-too-large` | a 512 × 512 sprite | `shape_png_dimensions` | `world` |
| `wav-other-format` | 44.1 kHz mono | `shape_wav` | `sounds` |
| `wav-metadata` | a `LIST` chunk | `shape_wav` | `sounds` |
| `wav-too-long` | a 1.5 s `move` | `shape_wav_duration` | `sounds` |
| `wav-too-loud` | a 0 dBFS peak | `shape_wav_peak` | `sounds` |
| `too-many-layers` | four layers | `shape_count` | `world` |
| `too-many-sprites` | two emitters of 100 | `shape_count` | `world` |
| `low-contrast` | text almost the plate's colour | `shape_contrast` | the surfaces' colours |
| `accent-is-plate` | the accent is the plate's colour | `shape_contrast` | the surfaces' colours |
| `light-on-light` | light text on a light plate | `shape_contrast` | the surfaces' colours |
| `unknown-field` | a field this version does not know | `shape_unknown_field` | nothing, a note |
| `newer-minor` | `mun-shape/1.4` with a new block | `shape_minor_newer` | nothing, a note |

### Codes

| Code | Level | Meaning |
| --- | --- | --- |
| `shape_unreadable` | package | `mun-shape` or `shape.json` is not a readable folder and file |
| `shape_too_large` | package | `shape.json` over 64 KiB |
| `shape_encoding` | package | not UTF-8, or a byte-order mark |
| `shape_syntax` | package | not JSON |
| `shape_duplicate_key` | package | a key twice in one object |
| `shape_number` | package | `NaN`, infinite or too long a number |
| `shape_depth` | package | more than 6 levels |
| `shape_string_too_long` | package | a string or key over 512 characters |
| `shape_structure` | package | the top is not an object |
| `shape_format` | package | no `format`, or not `mun-shape/N` |
| `shape_format_unsupported` | package | another major version |
| `shape_field` | block | a missing or mistyped field |
| `shape_range` | block | a number out of range |
| `shape_enum` | block | a name MUN does not know |
| `shape_colour` | block | not `#RRGGBB` |
| `shape_count` | block | too many layers, emitters, sprites or light textures |
| `shape_path_unsafe` | block | a path outside the package or not relative |
| `shape_path_missing` | block | a named file does not exist |
| `shape_path_symlink` | block | a named file is or crosses a link |
| `shape_path_type` | block | a named path is not a regular file |
| `shape_file_too_large` | block | a file over its size |
| `shape_budget` | block | the package over 32 MiB |
| `shape_png` | block | not a well-formed PNG |
| `shape_png_dimensions` | block | an image over its dimensions |
| `shape_wav` | block | not PCM WAV at 48 kHz, 16-bit, stereo, with only `fmt ` and `data` |
| `shape_wav_duration` | block | a sound too long |
| `shape_wav_peak` | block | a peak over −1 dBFS |
| `shape_contrast` | colours | the game's set does not keep contrast; MUN's set is used |
| `shape_unknown_field` | note | an unknown field, ignored |
| `shape_minor_newer` | note | a later minor version; what is known is used |

Messages are in Spanish, the console's language; the list is `NOTE_CODES` in
[`mun_card/shape.py`](../tools/mun-card/mun_card/shape.py).

## Sample packages

[`examples/shape`](../examples/shape/README.md) holds two packages made
entirely by a script in this repository, with the same contract and very
different identities: `sea`, a world of layers, fish, bubbles, light rays
and caustics behind glass surfaces with a tide, and `paper`, ink mountains,
drifting specks and falling petals behind paper surfaces with a sweep and
no light. Both use every declared aspect, and the tests check that no file
or name of theirs appears in the console's services.
