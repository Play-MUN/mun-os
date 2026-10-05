# MUN Shape

MUN Shape (Shape for short) lets a Game Card dress the console in its game's
identity while the card is in: a palette, the card object, a world behind the
menus, the materials of the menu entries and the game's panel, a transition
and the menu sounds. The card describes it with data; MUN™ draws every
package with the same code, and nothing on the card runs.

**Status.** This document is the package contract, format `mun-shape/1`, and
its checker, `mun-card shape`. The card service copies a valid card's
package, checked, to RAM for the shell, and the shell draws all of it: the
palette and materials of the eligible surfaces, the card object, the world
behind Home with its motion, the transitions that bring the identity in and
take it away, the menu sounds and the insertion cue. How the console draws
each part is under [How the console draws a world](#how-the-console-draws-a-world)
and [How the console uses a package](#how-the-console-uses-a-package); what
it was measured to cost, in a virtual machine, is under
[Measured](#measured).

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

#### How the console draws a world

The same code draws every package; the numbers below are part of the
contract, so that a publisher sees on the console what it designed.

- **Backdrop**: an image covers the canvas, scaled keeping its proportions
  and centred; a gradient runs from its first colour at the top to its last
  at the bottom, the others evenly between.
- **Layers**, drawn in order over the backdrop: each is scaled to the
  canvas's height, keeping its proportions, and repeated across the width
  (make the right edge meet the left). `still` stays; `drift` moves left at
  `speed`; `sway` comes and goes, `max(16, speed)` canvas pixels either way
  every 14 s; `parallax` moves only with navigation. Every layer also leans
  with the chosen entry by `depth` times 18 canvas pixels per entry.
  `opacity` applies to the whole layer. Transparent rows cost nothing.
- **Emitters**: sprites face right in their images and are mirrored when
  they travel left. Each sprite takes one of three sizes (0.8, 1 and 1.2
  times `scale`) and ±20 % of `speed`, the same for the same package every
  time. `rise` and `fall` cross the `band` upwards or downwards with a
  gentle sway, fading at its edges; `drift` crosses the screen rightwards
  within the band; `school` is one group, the sprites in their places
  within it (360 × up to 120 canvas pixels), crossing the screen, turning
  back and coming again at another height of the band; `orbit` circles the
  card object on a flattened orbit.
- **Light textures** are stretched over the canvas and blended by `screen`
  or `add` at `opacity`: `sway` moves them 28 canvas pixels either way every
  16 s (they are drawn 6 % larger, so their edges never show), `ripple`
  shifts bands of rows by two slow waves (light through water), `pulse`
  breathes between 72 % and 100 % of `opacity` every 5 s.
- **Rate**: the world moves at `rate` frames per second; a transition's
  front at 30. A world at rest (three minutes without input) slows to a stop
  and draws nothing more, as MUN's own does.
- **Fits the display**: before anything decodes, the console estimates what
  the world will take at its peak at the display's size from the images'
  headers, and draws it at the richest level that fits the budget (136 MiB at
  1080p, 200 MiB at 1440p): full; without light textures; the backdrop and
  the nearest layer at 10 frames per second; still (the backdrop and the
  nearest layer, composed once); none (the palette over MUN's world). The
  estimate counts the two frames, the transition's content and one frame
  more for a copy of a frame, the export, everything the level keeps (a
  layer as wide as it scales to at the display's height), and the largest
  image being prepared: its decoded pixels in the format its header gives
  (16-bit and palette images decode larger, or to a copy) and its converted
  copy. Only what can show is made: the backdrop's part the canvas shows,
  the band of a layer's rows that show (all its width, which moves across
  and repeats), a light texture's part that shows; still draws its layer
  straight from the decoded image. Each image is checked against what the
  level has left before its memory is taken, and a level that would go over
  is given up for the next one first. While
  navigating, if the interface's own frames fall behind (95th percentile over
  25 ms) or keys take over 50 ms to show, for two 2-second windows in a row,
  the world steps down one level for the rest of the insertion, which only
  frees memory (still is composed onto the backdrop itself). A transition's
  own frames are timed, not judged. An image that fails to decode drops the
  whole world, as the checker would.

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

- `tide`: a circle of light grows from the card object to the screen's
  farthest corner, slow to leave, fast across, slow to arrive; the world is
  inside it, MUN's outside. The card object changes first (its window, then
  its outline and light, within the front's first 260 canvas pixels); each
  menu entry, the panel and each band change as the front crosses them.
- `sweep`: a vertical front from the left edge to the right one; each surface
  changes as it passes over it.
- `fade`: everything at once.
- When: a card inserted while the console is on Home comes in with `in` over
  `seconds`, once Home is wholly on screen: no dialog, Settings or start-up
  layer over it or still fading away (a card inserted in Settings waits for
  Home). Back from a game, or after a changed choice, the
  identity comes back with a 0.6 s fade, under any dialog. *Eject safely*
  confirmed: `out`, over three quarters of `seconds` (0.8–2.4 s); the card
  stays dressed until the console has confirmed its release. Removed,
  replaced or with Shape turned off: 0.5 s back to MUN, the way it came if it
  had not finished coming in. With *Reduce motion* every transition is a
  fade (0.8 s for an arrival).
- A surface is MUN's or the game's on each frame, never under an opacity
  between: from the first frame the front reaches it, its plate and text
  are drawn whole, blended by the surface's plan, also while the game's
  options have the focus (MUN's own entries fade then; dressed ones do not).
  Likewise the sockets of the status line's lights are whole on the first
  frame a band is the game's.

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
  1 s, `insert` at most 3 s; peaks at or under −1 dBFS. A silent sound
  (every sample zero) is valid; reports give its peak as `null`. To remove
  metadata:
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
computes the console's own estimate (*Fits the display*) from the headers
and the level of detail a console would choose at 1080p and 1440p; an
estimate before decoding, not a measurement. A
package that fits the enforced limits but not a display's budget is to be
drawn at a lower level of detail (without light textures, then with fewer
layers, then still, then colours only), never at the expense of navigation.

### Measured

In a virtual machine, not on a console's hardware: MUN OS's development
image under QEMU with HVF on an ARM64 Mac, 4 vCPUs, 8 GiB, the software
renderer. Each value covers one shell and the same sample world (the `sea`
package; a second, private package with a world of the same size gave
figures within 8 points), navigated with one key every 300 ms for 30 s; at
1440p, two runs.

| | 1080p, no card | 1080p, world | 1440p, no card | 1440p, world |
| --- | --- | --- | --- | --- |
| CPU, one core's share, at rest on Home | 7.5 % | 35 % | 14–15 % | 57–58 % |
| CPU while navigating | 51 % | 72 % | 81–84 % | 120–121 % |
| Key to frame on screen, median / 95th percentile | 20 / 39 ms | 22 / 37 ms | 28 / 47–49 ms | 33 / 50 ms |
| The interface's frames while navigating, 95th percentile | 14 ms | 13 ms | 17 ms | 20 ms |
| The world's frames per second (rate 20) / paint time, median | – | 18.5–19.5 / 9–13 ms | – | 18.5–19.6 / 15–18 ms |
| A transition's frames, 95th percentile | – | 19–24 ms | – | 18–52 ms |
| Memory the world adds, peak (the shell's, over its own without a card) | – | 73 MB | – | 111 MB (154–163 MB after a second card, whose predecessor's freed memory the process keeps) |

At 1080p the full level of detail holds: navigation stays within 10 ms of
the console without a card, and memory within the budget. At 1440p the
virtual machine is at the watchdog's edge: in one of the two runs the world
stepped down once while navigating with the private package, to the level
without light textures, after two windows whose slowest frames took up to
34 ms (the card object's layers repainting on the interface thread; even
without a card, one window reached 30 ms); in the other run it held the
full level. Light textures are most of a world's paint time: without them,
about 1.5 ms instead of 13 ms at 1440p. The estimate before decoding was
81–83 MiB at 1080p and 137–139 MiB at 1440p; the behaviour regressions
measure the shell's own peak against the estimate, for the samples and for
worlds that keep every limit but scale large. A world at 10 frames per
second without light textures, as the `paper` sample's, costs far less: at
rest 8 % of a core at 1080p and 18 % at 1440p, about MUN's own, and
navigation as without a card.

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
| An image that fails to decode in the console, or exceeds the display's budget | That block, at run time, falls back to MUN (the card object whole, its outline and light too) or to a lower level |

A block is taken or dropped whole, so what a player sees is either the
block as designed or MUN's: one defective layer, emitter or light texture
disables the whole `world`, and one defective sound all of `sounds`. The
checker names the field and the file concerned.

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
- An image records the format its console reads in its BUILD-INFO
  (`"shape": {"format": "mun-shape/1"}`). An image made before that record
  existed does not say what it shows (v0.1.0-dev.2 shows no MUN Shape). The
  laboratory's preview takes only an image that records the format, and
  `./mun dev list` shows it per build and guest.

## How the console uses a package

**The card service** (implemented; wire and hooks in its
[README](../services/mun-cardd/README.md#mun-shape-export)):

- It starts only after the card is valid, and only after that state has
  been published: nothing about Shape delays `valid`, *Play* or *Eject
  safely*. One worker per insertion does all the reading; the service's
  event loop never reads the card for it.
- The worker reads the package through the same checker as `mun-card`
  (`mun_card/shape.py`), which checks every file's entry, size and the
  budget before reading it; each file it reads is read once from the card,
  in chunks of 256 KiB, into `/run/mun/shape/.<insertion>.<attempt>.part/`, and the
  checker validates exactly those bytes. Links are never followed and a FIFO
  under a package name is never waited on. Cancellation and the card's
  identity are checked between chunks. The service never decodes an image
  or a sound.
- The export holds only the files the checker accepted and the normalised
  `shape.json`, bound to the insertion and content version; files are 0440
  and folders 0550 (group: the shell's), sealed before it is published,
  as the last step, with one rename as `/run/mun/shape/<insertion>.<attempt>/`.
  Complete and immutable, or nothing: a copy that fails, is cancelled or
  arrives for an insertion that is no longer current is deleted, never
  published or kept. Each attempt has names of its own, so the background
  cleanup of an earlier one never touches a copy started again.
- The card record gains `shape` (`preparing`, then `ready`, `partial`,
  `unused` or `none`, with its notes and, when there is an export, its
  path); a change of it travels as its own `shape` message.
- An export is deleted on safe release, removal and replacement, and every
  export, `.part` copies included, when the service starts and stops.
- One copy reads a card at a time: a card that becomes valid while an
  earlier copy has not closed its files (a read stuck on a card already
  removed) waits in `preparing`, valid and playable, until that copy ends.
- ***Eject safely* stays exact.** A release cancels the copy; the card is
  unmounted only once no save is in flight and the copy has closed its
  files, whichever ends last. The wait for the copy is asynchronous and at
  most 3 s. Past that (a read blocked on a failing card cannot be
  interrupted) the answer is
  `card_busy`, "La tarjeta sigue en uso; no la retires", and the release
  stays pending: no new saves, games or copies, and the card is unmounted
  strictly and published as released only once the copy has closed its
  files. A physical removal does not necessarily end a blocked read at once.

**The shell** (implemented; details in its
[README](../services/mun-shell/README.md#a-game-cards-identity-mun-shape)):

- It reads only the service's copy for the insertion in the card's record,
  decodes the window image, the cover and the world's images off the
  interface thread with their dimensions checked first and an allocation
  limit, and verifies the surfaces' colours again with the same rule,
  falling back to MUN's set.
- Dressed:
  - the world behind Home;
  - the main arc's entries, the game's panel and the bands of the status
    line, the path and the hints: plates in their material at their
    computed opacity, with the text and focus colours, whole from their
    first dressed frame. While the game's options have the focus, the arc's
    entries keep their plate and text as proven and only their ornaments
    fade;
  - the card object: its window image or the cover, its outline, its
    light, all of it or MUN's object when that image does not decode;
  - outside the world, the ambient light and a tint of MUN's world in the
    card's hue at MUN's own luminance;
  - the menus' sounds on those surfaces;
  - the insertion cue, once, for a card that arrived while the shell was
    watching, however long its copy took.
- MUN's: Settings, their panels and every dialog, with MUN's focus and
  sounds even for a card that lends colours, over the game's world under a
  fixed MUN scrim. Also the start-up, the hand-over's words, the layout and
  every word. MUN's own panels on Home sit on MUN's plate over a world, and
  the status line's lights on sockets of MUN's.
- Without a palette: the lent colours, else the palette read from the
  cover; its accent is the focus only if it keeps 3:1 on MUN's plate.
- The world is painted off the interface thread into frames the interface
  shows without waiting for them, at the detail level the display's budget
  allows. It steps down when navigation suffers, and it is still at rest
  or with *Reduce motion*.
- The transitions follow the card's state: in when Home is wholly on
  screen, back after a game with a short fade and without the cue, out only
  once *Eject safely* is confirmed, quickly on removal or replacement.
- *Play* never waits: the game's deep colour grows from the card object
  while the launcher takes the screen.
- Settings: *MUN Shape* Full, Colours only or Off; *Game sounds on the
  menus*; *Reduce motion* (fades, and a still world and objects).
- Not yet: an ambient sound loop, a gallery, a custom typeface; game-file
  preloading is out of scope. The organic outline is reached during the
  transition and then held still: moving it continuously would repaint the
  card object's glows on the interface thread, which the software renderer
  cannot afford without cost to navigation.

## Tools

The way from a folder to a card, step by step, is a guide:
[dress the console in your game](guides/shape-your-game.md).

```sh
./mun card shape init DIR [--cover cover.png]   # a template; the palette read from the cover
./mun card shape init DIR --example sea         # or a copy of a sample package (sea, paper)
./mun card shape check DIR [--report] [--json] [--cover cover.png]
./mun card shape check CARD [--report] [--json] # the package on a card image, from its content root
./mun card shape variants [OUT]                 # the defective packages below
./mun card create NAME … --shape DIR [--shape-partial]   # a card that carries the package
./mun card inspect CARD                         # the card, and its package in brief
./mun dev shape DIR [--watch] [--window] [--base CARD]   # preview it in a laboratory console
```

`check` prints what a console would use, block by block, each surface's
colours, opacity and transition plan, and every note; `--report` adds the
contrast ratios and, per display, the level of detail a console would draw
the world at with its peak by category (the console's arithmetic, the
richer levels' peaks when it would step down), and the card window and
sounds outside the world's budget; `--json` prints the normalised
package, the files, the notes and the estimates, as strict JSON (no `NaN`
or infinities; a silent sound's `peak_dbfs` is `null`). It exits 0 when
everything declared is used, 2 when anything is dropped, replaced or the
package is not used, and 1 when it cannot run, for example with a cover that
cannot be read (`cover_invalid`, `cover_too_large`); a cover is read up to
its 1 MiB and decoded no further than its own size. When no block survives,
each block's cause is still printed.

`init` checks the whole destination before it writes anything, and writes
nothing if the check fails. It refuses a link anywhere it would write
(the folder itself, a folder on the way, a file's name, a dangling link),
even with `--force`, and a folder or special file where a file goes
(`shape_destination_link`, `shape_destination_type`). An existing file at a
name it writes is refused (`shape_exists`) unless `--force`. Each file is
written whole under a temporary name, then published. Without `--force` the
publication never replaces anything, even a file or link that appeared after
the check: it is kept as it is, the temporary is removed and `init` stops
with `shape_exists`, naming the files it had already written. This needs a
folder that allows hard links; one that does not is refused
(`shape_destination_unsupported`). `--force` replaces the names it writes
only, each by renaming its new file over the name, so a file that is also
linked elsewhere keeps its content there; every other file in the folder is
left as it was. The template declares a palette, the
card object, the surfaces and a transition, which need no file, and a README
explains how to add the world and sounds.

On a card, the package is the folder `content/mun-shape/`.

- `create --shape DIR` puts it there for any variant. It copies
  `shape.json` and the files it names, whatever their block's verdict, so the
  card declares what the folder declares and the console drops what the
  folder's check drops; other files in the folder stay out. Only regular
  files are copied, never through a link. It refuses a package the console
  would not use whole (`shape_not_ready`, with the reasons) unless
  `--shape-partial`, and a content tree that already has `mun-shape/`
  (`shape_conflict`). The card is then read back: its package's verdict is
  printed.
- For a `game-gl` card made with `--content DIR`, the package may instead
  be `DIR/mun-shape/`; the tool copies the tree into `content/`.
- `check CARD` reads the card as the console does (the card's own validity
  first, then `<content root>/mun-shape/`) and shows the read level of the
  card's own cover; `--cover` is for folders only.
- `inspect` adds one summary: the format the package declares, its state,
  the blocks used, each block dropped with its code and reason, and the
  notes; `--json` gives them under `shape`.
- `./mun dev shape DIR` makes a disposable card from the folder (MUN
  Collect from the guest's build, or with `--base CARD` a copy of that card,
  which is only read) and inserts it in a laboratory console (`shape` by
  default, made the first time). With `--watch`, every change in the folder
  is checked and, once the folder has been still for a second and no game
  is being played, the card leaves by the safe removal and a new one is
  inserted: a new insertion each time, never a package changed under one.
  A folder the console would not use is reported and the card stays. The
  preview's cards are `pv0-<guest>` and `pv1-<guest>`, known by the files it
  made, not by their names: a file at those names it did not make is never
  written, taken out or deleted. One preview runs per console, and it starts
  only in a console with no other card in. The console's build must record
  the MUN Shape it reads (`shape` in its BUILD-INFO); an older guest is
  refused, with the way to one that shows it
  ([the laboratory](../vm/README.md#mun-shape-preview)).

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
