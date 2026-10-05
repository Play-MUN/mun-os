# Dress the console in your game (MUN Shape)

While your Game Card is in the console, MUN can take your game's identity:
its colours on the menu, glass or paper under the words, the card object
showing your art, a world moving behind Home, a transition that brings it
in and takes it away, and your game's sounds on the menus. This is MUN
Shape. It is optional and offline, it is a folder on your card, and the
console draws it with its own code: you write no software for it and
nothing in your game changes.

This guide goes from an empty folder to a card you play and eject, with the
sample packages in [`examples/shape`](../../examples/shape/README.md):
create a package, add your resources, check it, preview it in the console,
make the card, insert it, play, come back, eject. The rules, with every
number, are the contract: [MUN Shape](../shape.md).

## Before you start

- The tools, as in [getting started](../getting-started.md) (sections 1
  and 2), and an image that shows MUN Shape, built from this checkout as in
  [build the image yourself](../getting-started.md#build-the-image-yourself)
  (`./mun dev build`): the published v0.1.0-dev.2, which
  `./mun get` downloads, does not show MUN Shape (a card with a package is
  valid and playable there, in MUN's look). `./mun dev list` says which
  images do: `shape mun-shape/1`.
- e2fsprogs 1.47 or later for the card tool ([create a Game
  Card](create-game-card.md#before-you-start)).
- A game on a card, or MUN Collect as in [create a Game
  Card](create-game-card.md) (sections 1 to 3). The preview below needs none:
  it dresses MUN Collect for you.

## 1. Create a package

```sh
./mun card shape init .local/mypkg --example sea      # a copy of a sample to change
./mun card shape init .local/mypkg --cover cover.png  # or a template, its palette read from your cover
```

`--example` copies one of the two samples (`sea`: deep blue glass, a world
under water, a tide; `paper`: cream paper, ink mountains, a sweep). The
template from a cover declares a palette (the one the console would read
from that cover), the card object, the surfaces and a transition, which need
no file; its README says how to add a world and sounds. `init` never writes
over files you have, unless you ask with `--force`. The package lives in
`.local/`, which Git ignores, like the rest of your work in these guides: a
build refuses a checkout with files Git does not know.

## 2. What is in it

```text
.local/mypkg/
  shape.json            the package: JSON, format "mun-shape/1"
  card/window.png       the card object's screen
  world/backdrop.png    the world behind Home: backdrop, layers, sprites, light
  world/…
  sfx/move.wav          the menus' sounds, and the insertion cue
  sfx/enter.wav
  sfx/back.wav
  sfx/insert.wav
```

`shape.json` has six blocks, each optional:

| Block | What it changes | Files |
| --- | --- | --- |
| `palette` | six colours: `light`, `mid`, `deep`, `plate`, `text`, `accent` (the focus) | none |
| `card` | the card object: its window image (or your cover), `card` or `organic` outline, its light | `window` |
| `world` | behind Home: a backdrop (image or gradient), up to 3 layers, 2 sprite emitters (128 sprites) and 2 light textures, at 10 or 20 frames per second | images |
| `surfaces` | the material of the menu's entries and your game's panel: `solid`, `glass` or `paper` | none |
| `transition` | how the identity comes in and leaves: `tide`, `sweep` or `fade`, 0.8–4 s | none |
| `sounds` | `move`, `enter`, `back` on the menus, and the optional `insert` cue | WAV |

Only the files `shape.json` names are read; other files in the folder (a
README, your source art) stay on your computer. The fields and their
defaults are in [the contract](../shape.md#structure); how each element of
a world is drawn (drift, sway, parallax, the emitters' paths, the light
textures' motions) is in [how the console draws a
world](../shape.md#how-the-console-draws-a-world), so that what you design
is what the console shows.

## 3. Add your resources

Replace the sample's files with yours, at the same names or new ones named in
`shape.json`.

| Kind | Format | Limits |
| --- | --- | --- |
| Images | PNG (any bit depth and colour type PNG allows) | 4 MiB each; the largest side 2048 for a backdrop, layer or light texture, 1024 for the card window, 256 for a sprite |
| Sounds | WAV, PCM, 48 kHz, 16-bit, stereo, nothing but `fmt ` and `data` | 1 MiB each; `move`, `enter`, `back` at most 1 s, `insert` at most 3 s; peaks at or under −1 dBFS |
| The package | the files `shape.json` names | 32 MiB in all |

- A backdrop covers the canvas, centred (1920×1080 is the design canvas; a
  16:9 image is shown whole).
- A layer is scaled to the screen's height and repeats across it: make its
  right edge meet its left. Its transparent rows cost nothing.
- Sprites face right; they are mirrored when they travel left.
- A light texture is stretched over the screen and added or screened onto
  it: keep it soft.
- To make a WAV the console takes:
  `ffmpeg -i in.wav -ar 48000 -ac 2 -c:a pcm_s16le -fflags +bitexact -map_metadata -1 out.wav`.

Use only art and sounds you have the right to put on the card.

## 4. Check it

```sh
./mun card shape check .local/mypkg --report
```

The checker reads the folder exactly as a console reads a card. Its first
line is the verdict:

- `LISTO` (ready): the console would use everything declared; exit status 0.
- `PARCIAL` (partial): it would use part, and the rest goes back to MUN's;
  exit status 2.
- `SIN USO` (unused): it would not use the package; the card stays valid.

Then one line per block, `declarado` (used) or `DESCARTADO` (dropped) with
the reason and the field (the card tools speak Spanish, as the console's
services do). Then the surfaces' colours, the opacity each plate is drawn at,
and each surface's transition plan. `--report` adds the contrast ratios
and, at 1080p and 1440p, the level of detail a console would draw your world
at, with what it takes, computed as the console computes it. `--json` gives
all of it for your own scripts. Fix what it names and check again; nothing needs a console
yet.

## 5. Preview it in the console

```sh
./mun dev shape .local/mypkg --watch --window
```

A laboratory console starts in a window (the guest `shape`, made the first
time from your latest image) with a disposable card: MUN Collect dressed in
your package. You see it as a player will: the transition, the world, the
menus' sounds, the panel. Edit a file and save: once the folder is still for
a second, the preview checks it, waits until no game is being played and
the console's menu is back, takes the card out by the console's safe removal
and inserts a new one with your change. Each change is a new insertion, so you see your arrival
transition and hear your cue every time.

- The console must show MUN Shape (`./mun dev list` shows
  `shape mun-shape/1` for its build and guest). A guest made earlier from
  an image that does not is refused, and the preview says how to get one
  that does: a new guest from your latest image (`--guest NAME`), or
  `./mun dev vm shape destroy --yes` and start again.
- A change the console would not use at all is reported in the terminal
  and the card in the console stays as it was.
- `--base mygame` dresses a copy of one of your cards instead of MUN Collect
  (the card itself is only read).
- The preview uses its own cards (`pv0-shape`, `pv1-shape`) and nothing
  else: it does not start in a console with another card in, and it never
  takes out or deletes a card of yours, even one you named `pv0-shape`
  (it stops and names the file). One preview runs per console.
- Without `--window` the console runs without one (look at it with
  `./mun dev vm shape screenshot`), and Ctrl-C ends the preview: the card
  leaves safely, the console stays on (`./mun dev vm shape stop` turns it
  off). With a window, *Turn off* in the console ends it.
  `./mun dev vm shape destroy --yes` removes the console.

## 6. Make the card

Add `--shape` to the command that makes your card:

```sh
./mun card create mygame --variant game --game .local/mycollect/mun-collect \
    --title "My Game" --id org.example.mygame --version 1.0.0 --shape .local/mypkg
```

`--shape` copies `shape.json` and the files it names to the card's
`content/mun-shape/`, and refuses a package the console would not use whole;
`--shape-partial` makes the card anyway (a test of how the console falls
back, for instance). A `game-gl` card made with `--content DIR` may instead
carry the package as `DIR/mun-shape/`; then do not give `--shape` as well.

Then check the card itself, as the console will find it:

```sh
./mun card inspect mygame          # the card, and a summary: MUN Shape: mun-shape/1 · LISTO …
./mun card shape check mygame      # the package on the card, block by block
```

## 7. Insert, play, come back, eject

```sh
./mun play                                  # a console with no card, in a window
./mun dev vm play card-attach mygame        # once Home is on screen, from another terminal
```

`./mun play --card mygame` puts the card in as the console starts: the
console finds it there and is dressed at once, without the arrival and the
cue. To see them, put the card in once Home is up, as above ([see MUN Shape
in action](see-shape-in-action.md) walks through it).

- **Insert.** The card is read, the card object takes your window, then
  your transition brings the identity in from the object outwards and your
  cue plays once. Navigate: your sounds on the menu, your panel, MUN's words.
- **Play.** *Play* starts the game at once: the game's deep colour grows from
  the card object while the console hands the screen over. No animation
  makes it wait.
- **Come back.** When the game ends the console returns with your identity
  already in place: a short fade, no transition from the object, no cue, even
  under the *Session ended* dialog.
- **Eject.** *Eject safely* keeps your identity until the console has
  finished with the card and released it; only then does the identity
  leave, with your out transition, and "You can remove the Game Card"
  appears. If the console cannot release the card, the identity stays.
- **Pull it out** without ejecting, even halfway through a transition: MUN
  comes back within half a second, the way your identity came in. In the
  laboratory `./mun dev vm play card-detach mygame --abrupt` pulls the card;
  the virtual slot reports the removal a few seconds later.

## What you dress, and what stays MUN's

Dressed while your card is the active one:

- Home's world behind the menu, and the card object;
- the main menu's entries and your game's panel: plates in your material,
  your text colour and focus;
- the bands under the status line, the path and the hints;
- the menus' sounds, and your insertion cue.

MUN's always: Settings (its menu, panels, focus and sounds), every dialog and
its sounds, the start-up and power-off, the hand-over's words, the layout,
sizes and order, every word and its language, the indicator lights' colours
(they sit on sockets of MUN's own over your bands). Settings and dialogs
appear over your world under a fixed MUN scrim, so they read the same with
every game.

## When a resource is missing or fails

A package never makes a card invalid, and never delays *Play* or *Eject
safely*. A block is used whole or dropped whole:

| What is wrong | What the console does |
| --- | --- |
| `shape.json` missing, not JSON, over 64 KiB, another major version | The package is not used: MUN, with the colours your card lends or reads from its cover |
| A field out of range, an unknown value, a bad colour, too many elements | That block is dropped; the rest is used |
| A named file missing, a link, too large, not a well-formed PNG or WAV | The block that names it is dropped |
| The palette's colours do not keep contrast | Your surfaces use MUN's colours together; your world is still drawn |
| An image the console cannot decode | The world goes (the palette over MUN's world), or the card object is MUN's whole |
| A world too large for the display | It is drawn at a lower level of detail (below) |
| An unknown field, or a newer minor version | Ignored, with a note; the rest is used |

The checker names each case with a code; `./mun card shape variants` writes
one small defective package per case, to see them in the console.

## Contrast, reduced motion and sounds

- **Contrast is proven, not hoped for.** Text keeps 4.5:1 and focus 3:1
  over any world, from black to white, on every frame of a transition. The
  console computes the opacity of each plate for that; a translucent glass
  becomes as opaque as your colours need. If your colours cannot hold on
  every surface, MUN's set is used on all of them. `check --report` shows the
  ratios and plates before you make a card.
- **Reduce motion** (Settings › Picture and sound): every transition is a
  fade and your world holds still. Design it to read well still.
- **MUN Shape: Full, Colours only, Off.** Colours only keeps your palette
  and plates, without world, images or sounds; Off is MUN alone.
- **Sounds.** *System sounds* off silences all of them, yours included;
  *Game sounds on the menus* off keeps MUN's sounds on your menus and leaves
  the cue out. The cue plays once per insertion, for a card inserted while
  the console is on (in Settings or under a dialog it waits for Home), never
  for a card found at start or after a game.

## Compatibility and degradation

- **Versions.** `"format": "mun-shape/1"`. A console reads any `1.x`,
  ignoring what it does not know with a note; a package of another major
  version is not used. Cards of either naming generation, and cards
  converted with `./mun card convert`, carry the package alike.
- **Without a package.** A card's `[presentation]` colours, or the palette
  read from its cover, still tint MUN while it is in.
- **The display's budget.** Before decoding anything, the console estimates
  what your world takes at its peak and draws it at the richest level that
  fits: full; without light textures; the backdrop and the nearest layer at
  10 frames per second; still; none. `check --report` tells you which
  level each display would draw. While the player navigates, if the menu
  falls behind, the world steps down a level for the rest of the insertion.
  Navigation always comes first.
- **What was measured.** In the laboratory's virtual console (no GPU), a
  world like the `sea` sample's holds the full level at 1080p; at 1440p the
  console is at the edge and may step down once, to the level without light
  textures, which are most of a world's cost. A world like `paper`'s, at 10
  frames per second without light textures, costs about what MUN costs
  alone ([measured](../shape.md#measured)). No console hardware is chosen
  yet: give light textures only what they are worth.

## Going further

- [See MUN Shape in action](see-shape-in-action.md): what a dressed card
  holds, whatever the game, and a walk through it in a console.
- [MUN Shape](../shape.md), the contract: every field, rule and code.
- [The sample packages](../../examples/shape/README.md) and how their art is
  made.
- [Create a Game Card](create-game-card.md) and [bring a game to
  MUN](port-a-game.md).
