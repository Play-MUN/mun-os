# Reef — MUN Shape example

**English** · [Español](README.es.md)

**Draft.** The reef uses MUN Shape's motion blocks (`mun-shape/1.1`,
[docs/shape-motion.md](../../../docs/shape-motion.md)), which are in
development and in no release; names, limits and budgets may still change.
Consoles that only read `mun-shape/1` show its v1 look: the still poster,
the palette, the sounds and the window; only a console built from this
source plays the motion
([trying it](../../../docs/shape-motion.md#trying-it)).

Reef — MUN Shape example, the reef for short, is a sea scene built in code
made into a Game Card's Shape: a 3D scene rendered into images, and those images set moving by the console's
player. It is MUN Shape's official example of motion, and meant as a first
template: copy it, change what you need, and see how each thing is done.

## Three parts

| Part | Where | What it is | Who writes it |
| --- | --- | --- | --- |
| The player | [`services/mun-shell`](../../../services/mun-shell/README.md) (`MotionScene`) | MUN Shell's player of Shape motion: the same for every package, nothing in it about the reef | MUN OS |
| The sources and the tools | this folder | the scene (`sea.js`), the bake (`bake.*`) and the script that makes the package (`make_package.py`) | you, when you change the reef |
| The package | [`../reef`](../reef) | `shape.json` and its images and sounds, as a console reads them; the checker reads it ready (`make check` tests it) | `make_package.py` only: never edited by hand |

A change to the reef is made here and baked again; `../reef` is what that
gives, committed with the change that made it. Used anywhere else (a
website, a development kit), the reef is taken from one commit, whole, with
its sources: [Taking it elsewhere](#taking-it-elsewhere).

| What you see | How the console moves it |
| --- | --- |
| The sea floor, rocks, corals and the water | Four planes, one per band of depth, wider than the screen; the view drifts slowly and each moves by its depth (`scene.sway`, `scene.depth`) |
| The light moving on the sand | Per band of depth, a loop of 16 frames dissolving into each other, breathing (`blend` clips, `vary opacity`) |
| Kelp swaying | 22 strands, each at its own place and depth, bending from its foot (`copies.places`, `copies.clips`, `bend`) |
| Rays from the surface | Seven rays swinging a little and flickering (`vary rotate`, `vary opacity`) |
| Three schools of fish | Formations going round in depth, each fish seen from its heading (16 headings) with its tail beating, three tints, drawing away from the shark (`views`, `face: views`, `copies`, `avoid`, paths with `pace: even`) |
| A shark far behind | Going round slowly, from its heading, its body swaying |
| Two crabs | Sideways a little way and back, rocking (`vary` with `phase`) |
| Six jellyfish | Pulsing, rising, fading out at the top and in again below (`saw` and `fade` of one period) |
| Bubbles and the sea's snow | Particles drawn into the disc and swallowed (`pull`) |
| Play in focus | The disc's light grows and the amber school mills round below the card object; on launch the school dives into the disc; back from the game it comes out and goes round again (`react`). All three follow the card object wherever Home puts it: the light and the dive go to the `disc` anchor, and the milling's points are measured from it (`origin`); refused, the launch gives the school back |

## Start from it

```sh
./mun card shape init .local/my-shape --example reef     # a copy of the package
./mun card shape check .local/my-shape --report           # what the console reads, and its cost
```

The package (`../reef`) is images and `shape.json`; this folder is what
makes them. To change the scene itself, copy this folder too.

## How it is made

| File | What it does |
| --- | --- |
| `sea.js` | The 3D scene in three.js: water, floor, rocks, corals, kelp, the schools, the shark, the crabs, the jellyfish, the rays, the game's cover. `buildSea()` makes it, `seaAt(t)` places everything at time `t` |
| `bake.js`, `bake.html` | Renders the scene in a browser page through the console's pixel look (640 × 360, 15-bit colour, ordered dither) and measures where everything goes on the 1920 × 1080 canvas |
| `bake.mjs` | Runs the bake in a headless Chrome or Chromium and writes `render/` (PNG images and `layout.json`) |
| `make_package.py` | Turns `render/` into the package: `shape.json` with the motion, the images, the `sea` sample's sounds. The behaviour choices (rate, reactions, particles) are constants at its top |

You need Node 22 or later, a Chrome or Chromium (or set `CHROME` to one),
and Python 3. `npm ci` installs three.js (0.187.0) here and nothing else;
it is not part of the package or the console.

```sh
cd examples/shape/reef-studio
npm ci
node bake.mjs                  # render/: about 10 seconds
python3 make_package.py        # ../reef
../../../mun card shape check ../reef --report
```

The package here was baked with Node 24.14.0 and Chrome for Testing
145.0.7632.6 (a browser-testing tool's download), headless, on an Apple
Silicon Mac; baked again there, it comes out the same byte for byte. Another
browser, GPU or zlib may round a few pixels or compress them otherwise:
compare what the images show rather than their bytes.

## Changing it

- **A school's colour or size:** `tones` and `SCHOOLS` in `sea.js`, and the
  tints in `bake.js`; then bake and package.
- **Where a school swims:** `where()` in `sea.js` and the course `bake.js`
  samples from it (32 points over one round, reached at even times, so the
  school slows where it goes in depth as in 3D).
- **What answers Play, and how:** `ANSWERS`, `GATHER` and `react` in
  `make_package.py`; no bake needed.
- **A creature of your own:** add it to `sea.js` after the others (the
  seed's numbers are drawn in order: what comes first keeps its look),
  render it in `bake.js` as the shark is (a row of frames for each heading),
  and give it an actor, a path and states in `make_package.py`.
- **The cover:** `coverTexture()` in `sea.js` paints the game's cover, the
  card's window (`card/window.png`), which MUN shows as the disc's label:
  [the game's cover](#the-games-cover).

## The game's cover

The card window, `card/window.png`, is the cover of the game on the card.
Reef paints MUN Collect's, with its name: MUN OS's sample game, the one a
laboratory console dresses with the reef unless told otherwise
(`./mun dev shape` puts the package on a copy of MUN Collect's card; `--base`
names another game). It is right only while the card runs MUN Collect. For
a card with another game, replace it:

- **In a copy of the package** (`./mun card shape init DIR --example
  reef`), no bake needed: put your game's cover in `card/window.png` (a PNG
  up to 1024 × 1024, see `card` in [MUN Shape](../../../docs/shape.md)), or
  take `window` out of the `card` block, and the console shows the cover the
  card's manifest names. Then `./mun card shape check DIR`.
- **In the studio**, to keep the reef's look for it: change the title and
  colours `coverTexture()` paints in `sea.js`, bake and make the package
  again.

The MUN and MUN Collect names are not under the licence
([`NAME-AND-LOGO.txt`](../../../NAME-AND-LOGO.txt)): a card for another game
does not carry MUN Collect's.

## What runs where

What a console shows is the package played by MUN Shell's player: images
rendered in advance, moved in fixed steps of the scene's rate (20 a second),
drawn at the pixel look's 640 × 360 and enlarged, behind Home, answering the
console's events (Play in focus, Play pressed, a card coming in, coming back
after a game, released). Captures and recordings of that, from a console
or from MUN Shell built from this source, are what show it working; say
which, when one is shown.

The scene in a browser, as `bake.html` renders it to bake it, or any web
page built from `sea.js`, is another thing: three.js drawing the 3D scene
live, every frame from the geometry, at the browser's frame rate and size,
without the console's player, its steps, its look, Home or its events. Its
fish swim the scene's own courses, not the package's paths, and its light,
sand and depth are drawn, not baked. A web page that shows it shows the
scene the reef is made from, not MUN OS running: say so where it is shown.

## Taking it elsewhere

Elsewhere, the reef is used from an export of one commit
([exporting a package](../../../docs/shape.md#exporting-a-package)):

```sh
./mun card shape export examples/shape/reef OUT \
  --sources examples/shape/reef-studio \
  --sources examples/shape/sea/world/caustics.png --sources examples/shape/sea/world/bubble.png
```

`OUT/reef-<commit>/` holds the package, these sources (by their paths in
the repository, so `sea.js` still finds the two textures it borrows from
`sea`), the repository's notices and `export.json`, which records the
commit and lists every file with its SHA-256. Whoever uses it pins the
version by the SHA-256 of `export.json`, checks the export against it in
its own tests (`./mun card shape verify-export --expect SHA256`), and
changes version only by exporting again: nothing is copied by hand and
nothing follows a branch. Whether the commit is in a published branch is
for whoever publishes to decide, not something the export says.

## What the bake keeps to

- Every image is rendered whole at 640 × 360 and cropped, so pixels and the
  vertices' snapping match from one image to the next.
- Each image has the size it has where its thing rests; the console scales
  it only as a course takes it deeper or nearer.
- Planes reach past the screen by the view's drift. Each band's floor ends
  sharp where the next begins and starts under the nearer one, so the drift
  never opens a gap; the light on the sand is cut the same way.
- Things that move are drawn apart from the planes, so they can pass in
  front of and behind each other by depth.

All of it is this repository's own work, under its licence
([licensing](../../../docs/licensing.md)), but the seeded randomness in
`sea.js`, mulberry32, by Tommy Ettinger (2017), who dedicated it to the
public domain; three.js (MIT) is installed by npm when you bake and is not
carried here. The card window carries MUN Collect's name, which is not
under the licence ([the game's cover](#the-games-cover)).
