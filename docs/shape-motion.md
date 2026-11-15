# MUN Shape motion (draft)

**Status: experimental draft, in development.** This page describes the
motion blocks of format `mun-shape/1.1` as the package checker reads them
today. v0.1.0-dev.5 and consoles built from this source play them; the
previews up to v0.1.0-dev.4 do not. Names, defaults, limits and budgets are provisional
until the console's player is measured; any of them may change in a later
version, which the draft's revision tells apart (below). [MUN Shape](shape.md)
(v1, `mun-shape/1`) is the stable contract and is unchanged.

Motion lets a package's scene move and answer the console: planes at
depths, characters with frame animations following paths, particles,
lights, moments of activity with calm between them, and a few reactions to
what the player does. The package brings images and declares their
behaviour as data. MUN plays every package with the same player and runs
nothing from the card: no scripts, expressions, shaders or QML.

## Compatibility

A package with motion is a `mun-shape/1.1` package that keeps its v1 blocks
(`palette`, `card`, `world`, …): they are its look on consoles that only
read v1. Those consoles ignore the motion blocks (a later minor version)
and never read or copy their files. For that to hold, a 1.1 package:

- keeps `format` at major version 1;
- only adds blocks and fields: never a new value in a v1 enumeration, a
  wider range, a longer list or another type for a v1 field;
- keeps the strict profile of the whole `shape.json` (64 KiB, 6 levels of
  nesting counting arrays, strings of 512 characters, no repeated keys);
- keeps complete v1 blocks for the v1 look.

While it is a draft, a package with motion also says which revision of the
draft it is written for: `"motion_draft": 1`, an integer at the top of
`shape.json`, beside `format` (consoles that only read v1 ignore it as an
unknown field). A console reads the motion only for the revision it knows,
and otherwise drops it with `shape_motion_draft`, the v1 look staying; the
motion of a package without the key is not read either. The revision goes
up whenever a change would read a draft package differently, so that no
package is played by rules it was not written for. Today's revision is 1.

A console that knows motion reads it as one aspect: a defect anywhere in
it drops all of the motion, with one of the `shape_motion_*` notes (below),
and the v1 look stays.

## Blocks

| Block | What it declares |
| --- | --- |
| `scene` | `look`, `rate` (10–30 frames per second, 20 by default), `poster` (the still composition), `planes`, `current` (the water's), `depth` (perspective), `sway` (the viewpoint's drift) |
| `sheets` | Named sprite sheets (they may also be written inline where used), with `views` for things seen from every side |
| `actors` | Characters: a sheet, a clip, a place (`at`), a depth, copies in formation, a rest state, what to keep; how they face, pitch, turn round, bend and keep away from another |
| `paths` | Points `[x, y]` or `[x, y, z]`, measured from the canvas or from an anchor (`origin`), or a course to or round an anchor; duration or speed; an even speed or an even pace; easing; loop; a smooth curve or straight lines |
| `states` | Named states of actors, emitters and lights (`thing.state`) |
| `emitters` | Particles: one source (region, ring round an anchor, point), rate or burst, life, velocity, spread, gravity, sway, growth, fade, a maximum alive |
| `lights` | A light texture or MUN's glow (which needs `at`: a point or an anchor), blended (`alpha` by default, `screen`, `add`), with drift and variation |
| `beats` | Moments of activity, one at a time, with a calm gap between them |
| `react` | Reactions to the console's events |

Positions are pixels of the 1920 × 1080 design canvas; depth `z` goes from
0 (near) to 1 (far). The only anchor today is `disc`, the card object,
wherever the console draws it. A path's `origin` (`canvas` by default, or
`disc`) is what its points are measured from: with `disc`, `[dx, dy]` or
`[dx, dy, z]` from where the card object is when the course starts, so a
course composed round the card object keeps its shape wherever Home draws
it. A plane's `at` is where its top left corner
goes (a plane wider than the screen starts left of it).

**Look.** `pixel` composes the scene at 640 × 360 with colour reduced to
15 bits and an ordered dither, enlarged by whole pixels; `native` composes
at the display's resolution; an object names a 16:9 `resolution` (320–1920
wide), `colour` (15 or 24) and `dither` (`none` or `bayer4`). The console's
menus, text and the card object are never part of the scene.

**Sheets.** Frames in a grid, `frame` `[width, height]` (square frames of
the image's height by default). `clips` names `[first, count, fps, mode?]`
(`loop` by default, `once`, or `blend`: looping with each frame dissolving
into the next, for light and water); without clips, every frame loops at
8 fps. A sheet with `views` (2–32) has one row of frames per heading, the
thing drawn the same way in each: the first row facing right, each next one
turned further away from the viewer, round through facing left and back
towards the viewer, evenly spaced; its clips then name columns, the same in
every row.

**Variation.** `vary` takes `x`, `y`, `rotate`, `scale` or `opacity`, each
`[min, max, period, shape?, phase?]`: `sine` by default, `saw`, `step` or
`fade` (at `max` but for the cycle's first and last tenth, rising from and
falling to `min`), starting `phase` (0–1) into its cycle. A saw and a fade
of the same period and phase go together: what rises fades out at the top
and in again below. `drift` moves at a steady `x`/`y` speed. A light that
drifts sideways repeats across the screen, as a plane does.

**Depth.** `scene.depth` gives the scene perspective: a thing at depth `z`
is drawn `1 / (1 + z (1/far - 1))` times its size at depth 0, `far` being
that share at depth 1 (0.25: four times as far). `span` is how far, in
canvas pixels at depth 0, the whole depth from 0 to 1 is, so that a course
going away counts as far as one going across, and headings and distances
take depth in. Each image has its own size where its actor rests (its `z`,
plus its copy's place): draw it at the size it has there, and a course
that takes it deeper or nearer scales it, with its formation, sway and
wobble. Without `depth`, `z` only orders what is drawn.

**The viewpoint's drift.** `scene.sway` moves the view `x` and `y` canvas
pixels over its `period`s (seconds): what is at depth 0 moves so far, what
is at `pivot` not at all, what is beyond moves the other way (by
perspective with `depth`, in proportion without it). Planes, characters,
particles and lights alike: the scene's parallax. Planes then move a display
pixel at a time; make them wider than the screen by the drift (`at`).

**Water.** `scene.current` is the water's flow, `x` and `y` in canvas pixels
a second, with `gust` (a share of it, 0–1) coming and going over about
`period` seconds. Particles drift with it, the near ones more than the far;
actors that bend lean with it.

**Moving like a swimmer.** A path goes through its points on a smooth curve
(`smooth`, true by default), and leaves the way the actor was already
going. Its speed eases at the ends. A state's `surge` pulses it with each
cycle of the clip, the stroke: a number (the amount, 0–1) or `{amount,
at}`, `at` being where in the cycle the actor is fastest (0–1, 0.25 by
default), just after the stroke's push. Its `glide` (`clip`, `every` [min,
max] whole strokes, `seconds` [min, max]) has the actor coast between strokes,
its speed fading: the glide begins on the stroke's first frame and ends,
after its seconds, when its own clip comes round to its first frame, which
is the stroke's first pose; the stroke goes on from its first frame. An
actor with `face` `mirror` turns to face where its course goes over the
next second or so, through its `turn` clip when it has one, else by a
quick turn about its middle: the turn begins on the stroke's first pose and
ends ready for it, never within 1.5 s of the last, and while it faces away
from where it must go it all but stops rather than going backwards; a
course that sets off behind it starts with a swim on and round. Facing
left, an actor shows a clip's `NAME-left` version as drawn when the sheet
has one (lit as it should be), else the clip mirrored. `bank` (0–1)
pitches it with its course, up to 28°.

**Bending.** An actor's `bend` (`amount` in canvas pixels at its top,
`period`, `wave`, `follow`) bends it from the foot of its frame: a wave
rising up it (going down it for a negative `wave`), plus the current times
`follow`, more the higher it is.

**Headings.** An actor that faces `views` shows the row of the heading it
moves in, across and in depth, its turn eased round the short way; it
changes row only once well past halfway to the next. `bank` pitches it with
its course, fully seen side on, hardly nose or tail on.

**Copies.** `copies` makes a formation round the actor: `count` figures
scattered within `spread` `[x, y]` canvas pixels and `depth` either side
(seeded: the same each time), or one at each of `places` (`[dx, dy]` or
`[dx, dy, dz]` from the actor). Each may sway up and down (`wobble`
`[pixels, seconds]`), runs its clip `lag` seconds after the one before,
and, with `clips`, shows them in turn: still things of many looks (the
strands of a kelp bed, the rays of a sea) as one actor.

**Keeping away.** `avoid` `{actor, radius, push}`: within `radius` canvas
pixels of the other actor (depth counted by perspective), an actor is
pushed straight away from it, up to `push` pixels the nearer it is, eased in
and out over about 0.4 s; its course is unchanged.

**Paces.** A path's `pace` is `speed` (an even speed along it, by default)
or `even`: every point reached in the same time, whatever the distance
between them, so that a motion sampled at even times (from an animation
tool or a 3D scene) keeps its rhythm, slowing where its points are close. A
loop is joined where it comes nearest the actor, what lies between fading
out over a second or so; other courses start where the actor is.

**States.** What a state may set, by kind:

| Kind | Fields | Built-in states |
| --- | --- | --- |
| Actor | `path`, `clip`, `vary`, `then`, `hidden`, `only` (`visible`), `surge`, `glide`, `seconds` (a state without a path lasts so long, then `then`) | `idle` |
| Emitter | `on`, `burst`, `rate` (a factor) | `on`, `off`, `burst` (as many as the emitter's own `burst`, which it must then give) |

An emitter's `pull` (`radius`, `speed`, `swirl`, `swallow`) has the disc draw
in what comes within `radius` canvas pixels, the more the nearer, up to
`speed` pixels a second, turning round it by `swirl`, and swallow it within
`swallow`.
| Light | `on`, `opacity`, `vary` | `on`, `off` |

Beats and reactions only switch states; nothing else is reachable from the
package.

**Events.** `arrive`, `focus.play`, `focus.away`, `launch`, `return`,
`leave`. A reaction is either `{thing: state}` or `{do: {thing: state}}`;
`focus.play`'s may also give `settle`, `cooldown` and `once` (every other
event applies at the next step, and refuses them). When Play leaves focus, the actors still in its
reaction go back to rest first; then `focus.away`'s own reaction applies,
so what it names takes the state it declares.

## Defaults

- An actor stays at `at` with its first clip (`idle`), at depth 0.5, facing
  as drawn; it is kept as `identity` if it answers `focus.play`, as
  `ambient` otherwise.
- A path starts where the actor is, at 120 canvas pixels a second unless it
  gives `seconds`, eased in and out.
- A focus reaction waits for the focus to settle 0.6 s, runs once per
  visit, and not again within 20 s.
- Emitters are kept as `detail`, lights as `ambient`.
- Without `beats`, the scene only rests.

## Limits

Provisional, enforced by the checker:

| Item | Limit |
| --- | --- |
| Planes | 8 |
| Sheets | 16, each image at most 2048 × 2048, at most 32 views |
| Actors | 24, with 160 figures in all (copies included, 64 an actor) |
| Paths | 24, 32 points each |
| States | 48 |
| Emitters | 4, with 320 particles alive in all |
| Lights | 4 |
| Beats | 12 |
| Things a beat or a reaction moves | 4 |
| Images named by the motion | 32 MiB, apart from the v1 package's 32 MiB |
| Images the player decodes (all but the poster), together | 64 MiB at 4 bytes a pixel |
| One image while it is decoded | 16 MiB in the format it is read into (16-bit colour: 8 bytes a pixel, so at most about 1448 × 1448) |
| The document the card service exports, normalised | 256 KiB, what the shell reads, measured with the card's insertion and content version as the service writes them; over it, the motion is dropped, and if what stays is over it too, the package is not used |
| A plane or a light that drifts sideways | at least 240 canvas pixels wide (it repeats across the screen) |

Every image is checked as v1 checks its images (PNG structure, at most
4 MiB, its side read from the header before anything decodes it). What it
decodes to is worked out from the header too: the player decodes every
image the motion draws once, keeps them while the card is in, and draws
nothing until all of them are decoded.

## Notes

| Code | Meaning |
| --- | --- |
| `shape_motion_field` | A missing or mistyped field, or an unknown key, in the motion |
| `shape_motion_reference` | The motion names a sheet, clip, path, state or thing that does not exist |
| `shape_motion_count` | The motion goes over one of its limits |
| `shape_motion_budget` | The motion's images go over their budget |
| `shape_motion_draft` | The package is written for another revision of the draft (`motion_draft`), or says none |
| `shape_motion_empty` | The motion declares nothing to draw (no plane, actor, emitter or light): the v1 world is drawn, not an empty scene |
| `shape_motion_not_drawn` | Information only, the motion is used: it uses something this console reads and does not draw yet (below), once for each kind, saying where |
| `shape_motion_error` | The checker itself failed while reading the motion; the detail says how (a fault to report, not a defect of the package) |

The motion also uses v1's `shape_range`, `shape_enum` and file codes. Each,
but `shape_motion_not_drawn`, drops the motion as a whole.

## The console's player (draft)

MUN Shell plays the motion the card service exported, behind Home and in
place of the v1 world, with one player for every package
([`MotionScene`](../services/mun-shell/README.md#a-game-cards-identity-mun-shape)).
Its images are decoded once, off the interface's thread; nothing is drawn
until they are. One simulation decides each frame and can be drawn two
ways: composed into one image at the look's resolution on a thread of its
own, or as one scene graph node per thing (drawn by the GPU where there is
one). The two show the same frame; which costs less on the console is what
is being measured, so neither is fixed yet.

The console's events reach it as follows: `arrive` when a card's Shape comes
in after an insertion, `return` when it comes back after a game, `leave`
when the card is released, `focus.play` while MUN Disk's Play is in focus
(`focus.away` when it no longer is), and `launch` when Play is pressed. A
launch the launcher refuses, or one it accepted that ends before the game
starts (its preparation failed), gives back what `launch` changed, and
Play's focus reaction again if Play is still in focus; Play can then be
pressed again. A focus reaction still settling when Play is pressed does
not come after it. With *Reduce motion* it holds one still frame, and of
the events that come meanwhile only where the focus is waits for it to
move again.

Its images decoded together stay within their budget (64 MiB); if one
does not decode, if they go over it, if the export is gone, or if the motion
declares nothing to draw, nothing of the motion is drawn, the journal says why, and Home draws the package's v1
world instead. If decoding takes the shell down, its next start skips that
insertion's motion and shows its v1 look (a marker naming the insertion is
written before decoding; if it cannot be written, nothing is decoded and
the v1 world is drawn; a clean stop, as around a game, removes it). A
frame lists 8,192 things at
most (a package the checker accepts lists far fewer). MUN's panel sits on
its plate over the motion, as over a world. The scene's time advances in
fixed steps of 1/`rate` s while it is drawn, and an event is applied at the
next step (the journal says which), with a fixed seed: the same events at
the same steps give the same scene on any console. It is drawn on every
frame of the display (Qt's animation driver), what moves placed between
its last two steps, a step behind at most; in the pixel look what moves is
placed on the display's pixels, so it glides, while its own pixels stay
whole (the composed way keeps them on the look's). On the graphics path the
scene graph way draws every run of things from one image, one after the
other in depth, as one node: some tens of nodes for hundreds of things.

Not drawn yet, though the checker reads them; the checker says so with
`shape_motion_not_drawn`:

| What the package says | What the player does today |
| --- | --- |
| The look's `colour` 15 and `dither` `bayer4` (the `pixel` look's) | composes at 24 bits, without dither |
| A light's `screen` or `add` blend | draws it as `alpha` |
| `rotate` or `scale` variation on a plane or a light | leaves it unturned, at its size |
| `rotate` variation, or a `blend` clip, on an actor that bends | does not turn it; shows its frames without dissolving |
| A glow's `colour` | draws MUN's glow, white |
| A glow's `drift`, or its `x` or `y` variation | keeps it where it is |
| A light state's `vary` | uses its `on` and `opacity` only |
| `keep` | draws everything or nothing: it gives nothing up yet |
| `once` `per-insertion` | runs the reaction once each visit, as `per-visit` |
| A beat's `every` [min, max] | waits at least `min` between two of that beat, never longer |
| A `poster` that is not `world.backdrop`'s image | holds the scene's frame under *Reduce motion*; consoles that only read v1 show the backdrop |
| `velocity`, `gravity` or `sway` on an emitter going `toward` the disc | takes its particles to the disc at a steady speed |
| A `once` or `blend` clip on an emitter | loops it |

In the pixel look the composed way turns and bends on the scene's own
pixels; the scene graph way turns the enlarged image.

## Tools

`mun-card shape check DIR` reads the motion, and with `--report` gives an
estimate before decoding: its images decoded, the pixels drawn per frame
at the look's resolution and per second at 60 display frames a second
(the player draws on every display frame, whatever the scene's `rate` of
simulation steps), and which things a console would give up first by their
`keep` (none yet: the player draws everything). It counts what is listed
to draw, not what a backend or a GPU costs: it is an estimate for
comparing packages, and the budgets it is to be held against are not
measured yet.

## Trying it

A package with motion is checked and copied with the card tools like any
other:

```sh
./mun card shape check examples/shape/reef --report      # what a console reads, and the estimate
./mun card shape init .local/my-shape --example reef     # a copy to change
```

Only a console whose shell has the player draws the motion: v0.1.0-dev.5
([getting started](getting-started.md#3-the-image)), or an image built
from this source (`./mun dev build`,
[build the image yourself](getting-started.md#build-the-image-yourself)).
The previews up to v0.1.0-dev.4 read `mun-shape/1` and show such a
package's v1 look: its still poster, palette, sounds and window. A release
or a bundle of a build with the player (`./mun dev bundle`) carries MUN
Collect dressed in the reef, the card `collect-reef`, beside `collect-sea`
(the `sea` sample, v1): `./mun play --card collect-reef` plays it without
the card tools. In a guest of such an image,
`./mun dev shape examples/shape/reef --window` shows the motion behind Home
([see MUN Shape in action](guides/see-shape-in-action.md)).

While it is a draft:

- v0.1.0-dev.5, and a build from this source, record, beside the MUN Shape its console reads
  (`"format": "mun-shape/1"`, what every tool reads), the revision of the
  motion draft its shell plays: `"minor": 1, "motion": {"draft": 1}` in
  `BUILD-INFO.json` (`./mun dev list`: `with motion (draft 1)`). The
  previews up to v0.1.0-dev.4 record none and play no motion. `./mun dev shape`
  with a package that has motion, on a guest whose build plays none or
  another revision, warns before the console starts and with each card it
  inserts: that console shows the package's v1 look. What it says is
  what it knows: the verdict is this checkout's checker, run on the host
  on the card as made; what the console plays is what its BUILD-INFO
  declares; whether the console draws the motion is not observed (the
  guest is not asked: `./mun dev vm shape shell-log` shows what its shell
  says of it).
- The motion is drawn behind Home as Home is: its orbital design, on the
  software renderer (as the virtual console draws it) or on a graphics API,
  or its previous design (`MUN_SHELL_DESIGN=previous`, the reference the
  shell's checks compare with). Fixed positions in a package assume where Home's
  card object is; the `disc` anchor, and points measured from it
  (`origin`), follow it wherever it is. In the reef, the light, the dive
  into the disc and the school's milling with Play in focus (below the
  card object) all do.

## Where it stands

The player has been tried with the reef on two machines: a virtual console
(QEMU, an arm64 guest, Qt Quick's software renderer, no GPU) and a Radxa
Dragon Q6A development board (its GPU through OpenGL ES 3.2, Mesa 25.0.7,
a 60 Hz display), both at 1920 × 1080, drawn the scene graph way. Those runs used a
laboratory arrangement of Home (its entries in a row along the bottom, the
card object in the middle), not Home's own designs, and a shell built from
the same player code with other shell changes this source does not have.
Neither machine has yet been measured with the motion behind Home's own
designs. What was found:

- **The same scene on both.** Driven by the same keys, both machines applied
  the same nine events at the same simulation steps, and the simulation's
  digests (`MUN_SHELL_MOTION_TRACE`) were equal at all 83 seconds compared.
  Frames captured at the same steps differ by 1.2 to 4.6 levels in 255 on
  average, most of it in a laboratory element of that arrangement animated
  by the clock rather than by the scene's steps; elsewhere 0.7 to 2.4:
  where a moving thing is within a step, and each renderer's sampling and
  text.
- **The console's moments.** *Reduce motion* holds one still frame; a card
  removed while Play's reaction runs leaves, and the shell goes on to MUN's
  Home. On the virtual console, ten minutes without input dim Home with the
  scene still moving under it, and the first press only wakes it; on the
  board the dimming has been seen once, by eye
  ([MUN Dev](mun-dev.md#what-has-been-checked)), and that the first press
  only wakes it has not been checked there.
- **Sustained, on the board.** Two runs with a key every 20 seconds
  (Play's reaction coming and going) and no recording. Instrumentation on in
  both: Qt Quick's timing line for every frame (`QSG_RENDER_TIMING`) to the
  journal, the shell's key-to-frame log (`MUN_SHELL_TIMING`), the digests,
  the shell's CPU and memory and the thermal zones sampled every 2 s, the
  GPU's busy share every 0.25 s.

| | 45 minutes | 30 minutes |
| --- | --- | --- |
| Frame times covered | its last 3.4 minutes | all 30 minutes |
| Frames a second, median of 10 s windows (lowest) | 57.1 (53.3) | 48.8 (30.2) |
| Frame time (Qt Quick's, sync to the end of the swap): median, p95, p99, maximum | 16, 32, 33, 50 ms | 16, 33, 34, 68 ms |
| Frames over 20 ms | 5.5 % | 23 % |
| Simulation steps dropped | 1 | 0 |
| The shell's CPU, of one core | 80 % | 71 % |
| The shell's resident memory, highest | 224 MiB | 225 MiB |
| Hottest thermal zone, highest | 67 °C | 70 °C |
| GPU busy, mean | 72 % | 65 % |

About 820 things were drawn in each frame. A kernel trace of 28 seconds
(with the same logging on) gave the GPU's time for each frame: 12.3 ms on
average, 15.8 ms at the 99th percentile, of the 16.7 ms a 60 Hz display
allows. Why the two runs differ, and how much the per-frame logging
costs, is not established. The motion does not hold 60 frames a second on
that board today, and no frame rate is promised; the limits and budgets
above stay provisional until it is measured further.
