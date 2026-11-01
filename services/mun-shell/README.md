# MUN Shell

The console's interface: the start-up, Home with its menu arcs and the
panel of the entry in focus, the Game Card states, playing and the session
result, safe eject, settings (language, clock, resolution, safe area,
interface sounds, automatic power off), the menus' sounds, information about the console and a confirmed clean power-off, in
English or Spanish. What the console cannot do yet is shown in its place as
"Not available yet", never imitated. Launch contract:
[docs/runtime.md](../../docs/runtime.md).

Qt 6 Quick renders with the software scene graph through Qt's `linuxfb`
plugin, drawing into DRM dumb buffers with page flips: no display server
and no GPU dependency. When the shell stops for a game it releases the
display, and a game drawing on `/dev/fb0` has it (see *Display path*).
The same binary also runs on a graphics API (Qt's `eglfs` over a GPU's
OpenGL ES, on hardware that has one); it finds out which at run time and
draws the parts that depend on it accordingly (*How it is drawn*).

## Layout

| Path | Role |
| --- | --- |
| `src/main.cpp` | Process entry: waits for the display and has its mode chosen before Qt starts, loads the compiled-in fonts, tells QML whether this is the first start since boot, loads the QML module |
| `src/displaymode.*` | The display mode: the player's resolution if the display takes it, else the display's own (or, under a board's cap, the mode the cap picks); Qt's KMS configuration and the canvas's scale for it; the one-shot trial of a new mode; the wait for the display and the mode actually set, in the journal |
| `src/displaydevice.*` | The rules behind it, in standard C++ (no Qt, no libdrm): which DRM device is usable, what to say when none is, and which mode to start in under a cap |
| `src/systeminfo.*` | Real system facts read from `/proc`, `/sys`, `/etc/os-release`, `/usr/lib/mun/release`, `statvfs` and `getifaddrs` (identity, hardware, display, time zone, network interfaces), raw so the interface words them in its language |
| `src/shellsettings.*` | The player's settings, validated and kept in the service's state directory |
| `src/systemsounds.*` | The menus' sounds (move, enter, back), mixed on a worker thread and played through ALSA |
| `src/powercontrol.*` | The only privileged request: runs `mun-power` through `sudo -n`; reports failure to the UI |
| `src/cardclient.*` | Mirror of the card service over its UNIX socket: snapshot, events, reconnection; exposes state, manifest, error, cover (data URL) and whether the active card arrived while the shell was watching to QML |
| `src/shape.*`, `src/contrast.h`, `src/readpalette.*` | A Game Card's MUN Shape on the eligible surfaces: the export read and decoded off the GUI thread, its colours verified again with the contrast rule, the palette read from a cover, the phases of its coming in and leaving (*A Game Card's identity*) |
| `src/shapeworld.*`, `src/shapefront.h` | The package's world, prepared and painted on its own thread, and the fronts of its transitions |
| `src/framewatch.*` | The interface's frame and key timings while a world is drawn; the step down when navigation suffers |
| `src/renderinfo.*` | How the window is drawn: the software renderer or a graphics API, whether that API is accelerated, the renderer and render loop, in the journal |
| `src/memory.*` | Once the start-up is over, most of the memory the shell frees goes back to the system (glibc's thresholds fixed, then a trim) |
| `src/launchclient.*` | Client of `mun-launchd`: `launch(slot, serial, version)`, `release(serial)`, `acknowledge()`, launcher state and the last session result (read from `/run/mun/launch/last-result.json` at start, then over the socket) |
| `src/backdrop.*`, `src/heroicon.*` | Home's painted layers: the network of light behind everything, and the large object of the entry in focus |
| `src/networknode.*`, `shaders/network.*` | The network on a graphics API: one geometry, built once, that the GPU places on every frame |
| `src/cssbox.*`, `src/blur.*` | Surfaces with CSS semantics (radii, gradients at any angle, outer and inset box shadows) and the blur they and the glows use |
| `src/surfacenode.*`, `shaders/box.*`, `shaders/shadow.*` | The same surfaces on a graphics API, drawn by the GPU from their parameters |
| `src/projectedview.*`, `qml/ProjectedLayer.qml` | Shows an item through a perspective transform: projected once per change into a copy (software renderer), or rendered into a layer that the GPU projects (graphics API) |
| `src/sceneclock.*` | One clock for the painted layers: a timer, or the frames the window renders |
| `src/bootintro.*`, `src/bootintroshapes.h`, `images/boot-*.png` | The start-up's picture, part by part, as a function of the sound's time; its shapes (the MUN letters, the moon's arc, the LED) and its two images of the console's lid |
| `qml/Main.qml` | The scene, the navigation, the actions and the key handling |
| `qml/Panels.qml` | What each entry's panel and each dialog says, from the real state |
| `qml/Theme.qml`, `qml/I18n.qml` | Design tokens and motion; the two languages |
| `qml/ArcMenu.qml`, `ArcNode`, `DetailPanel`, `OptionRow`, `StatusBar`, `MoonPhase`, `ModalLayer`, `BootLayer`, `UiText`, `MarkText`, `Shadow`, `Material`, `Logo.js` | Components: the arcs and their entries, the panel and its options, the status line, dialogs, the start-up and power-off screen, text and shadows; the MUN logo |
| `tests/behaviour.py`, `tests/scenes/` | Behaviour regressions run on the compiled binary, offscreen (*A Game Card's identity*), and the software and OpenGL drawing paths side by side on Xvfb (*On a graphics API*) |
| `tests/displaydevice_test.cpp`, `tests/Makefile` | The display rules on fake sysfs trees and mode lists, compiled and run on the host by `make check` |
| `fonts/` | Archivo and Michroma, compiled in, with their licences |
| `sounds/` | The interface's sounds, compiled in: the menus' `move.wav`, `enter.wav`, `back.wav`, the console's events' (`launch.wav`, `game_return.wav`, `shape_exit.wav`, `eject.wav`, `shutdown.wav`, `success.wav`, `warning.wav`, `error.wav`, `controller_connect.wav`, `controller_disconnect.wav`) and the start-up's `startup.wav` |
| `deploy/mun-shell.service` | systemd unit on tty1 as user `mun-shell` with the display (DRM) and evdev environment and the state directory |
| `deploy/mun-shell.sudoers` | Allows exactly `mun-power poweroff|reboot` for that user |
| `deploy/mun-power` | Root-side helper with a fixed vocabulary in front of `systemctl` |
| `deploy/packages`, `deploy/build-packages` | Debian packages at run time and for compiling; the image build mirrors them in `os/mkosi/mkosi.conf` |
| `deploy/build.sh` | Compiles out of tree with a given build id; run by the image build |
| `deploy/stage.sh` | Installs a compiled shell, the unit, the power helper, the sudoers rule and `deploy/sysusers.conf` under a root directory, the image's |

Separation: the UI (QML) never touches the system; `SystemInfo` reads it;
`PowerControl` asks a root helper through a sudoers rule that names one binary
and two arguments. The service runs unprivileged with `ProtectSystem=strict`.

## Identity

MUN Shell names the system from `/usr/lib/mun/release`, which a MUN OS image
build writes from its own metadata (`os/inputs.json` and the build profile):
*About this console* shows "MUN OS 0.1.0-dev · development · qemu-arm64"
with the machine, processor, memory, storage, display, network, kernel and
the build. A published release would say "release"; a system without that
file, its services installed outside an image build, says "MUN OS · lab
installation".

The look is MUN's night: a deep blue-black stage, lunar grey `#DAD7D1` for
type and surfaces, ash `#8F8C87` for what is secondary, copper
`#C27B48`/`#E39A63` for the focus, patina `#7FB3A3` for labels and the
network, the console's LED white `#DDE9FF`. Type: Archivo, a variable font
(labels are bold at 118 % width), for everything read; Michroma, the mark's
face, for short upper-case labels, the path at the bottom and the clock.

Home: on the left a large object for the entry in focus (the Game Card, a
fan of cards, a gear, the power sign; in Settings a person with a globe, a
screen with sound, the network's arcs, a chip) floats at the centre of a
network of light: rings drifting out of it, slowly turning spokes, two
dashed orbits and a floor of lines sliding towards the viewer. Around it an
arc of entries (Game Card, My games,
Settings, Turn off), each a knob, a wire and a bar, leans back into the
scene; the chosen one turns moon-white with a copper edge. A on Turn off
asks to confirm and turns the console off. On the right a panel leans
back from its right edge: what the entry is, and its options. At the top
right, the slot, the network, tonight's moon and the time; at the bottom,
the path, starting at the console's mark (MUN™ › SETTINGS › SYSTEM), and
the keys (A Select, B Back). The card's crescent on the Game Card object
(the logo's moon) fills while a card is in the slot. The ambient light at the orb follows the local hour:
cold at night, pale at dawn, grey-white by day, copper at dusk.

Motion: the start-up plays once per boot, over the start-up sound (13.05
s): the console waking in the dark, under the vignette of a tube, still
from start to end: the console, its LED and the U of MUN on
the screen's centre line, the moon reaching out to the right, the finished
logo with the LED under it centred. At 0.3 s a pim lights the amber LED, a point that breathes once and
grows into the console's long oval, two sweeps of light running along it;
at 3.45 s a hit shakes the screen and flashes, a faint light behind the
console shows only the top of its lid, and a gleam runs along the MUN
engraved on its dome; at 6.65 s MUN arrives higher up with a ding and a
discharge from the LED that makes its strokes tremble, a warm glow rising;
from 7.85 s the moon comes out of an eclipse, copper, then silver as the
shadow withdraws; from 10.05 s the LED melts away in silence and the logo
fades while the sound rings on. The system loads meanwhile: once the card
service and the launcher have answered, "A Skip" shows at the bottom right
and A (or Enter, or Space) ends the start-up; until then no key does
anything. When the picture has faded (11.75 s), Home comes in if they have
answered, while the sound rings on; if not, the LED waits, breathing, until
they do, or 20 s after the start whatever they do. The picture keeps time
with the sound: on every frame its clock is what has been heard of it, the
samples written to the device less what the device still holds. The black
screen waits up to 3 s for the sound's first samples, and the picture holds
while the sound stalls, up to 2 s, then follows it again; with System sounds
off, no device, or a longer wait, it plays on its own clock from where it
was. The journal says each time the picture changes clock. *Reduce motion*
takes out the shake, the tremor and the flicker; every time stays. Moving to another entry: the chosen bar
turns light at once while its copper edge, glow and text colour settle in
0.3 s and it slides 4 px out; the new object loads in 0.9 s, a radar-like
sweep drawing its glowing wireframe with a copper line and its face
filling in over the second half while the previous object fades; a wave
leaves the object through the rings and spokes; the panel's content
changes and it slides to stay centred. Entering Settings, the main arc
fades and moves away and the Settings arc comes in its place; while a
panel's options have the focus, its arc dims. Left alone, the scene keeps
moving (*Reduce motion* holds it still); ten minutes after the last input the
interface dims to half its brightness, in 2 s, and the next key, wheel turn or click only
brings it back, in 0.25 s: it does nothing else, and that key does nothing
while it is held. The times count on the monotonic clock, so setting the
date or time does not move them. The menus' sounds are described under
*Sounds*.

### How it is drawn

The software scene graph repaints only what changes, but whatever moves in
Home's background changes the whole screen, and everything over it is
composed again on each of those frames. So the parts are made to be cheap to
compose:

- The background and the large object are painted in C++ (`Backdrop`,
  `HeroIcon`), not Canvas, and paced by one clock (`SceneClock`): the
  network drifts at 10 frames/s (its rings move 1.4 px a frame), a wave and
  the gear run at 20 and an object loads at 60; the float of the object only
  moves the painted image. Each drift frame repaints the whole screen, so
  the background paints its own frames: the gradients once per change of
  the light, then per frame a copy of them, the rings and spokes through
  `hairline` (a direct antialiased rasterizer: QPainter's general stroking
  spent 5.4 of the frame's 7.5 ms on them) and the orbits and floor with
  QPainter, handed to the scene graph without a further copy. Each object is
  painted once into still layers, as the item shows them, that a frame only
  clips, fades and copies (the gear's are turned).
- Surfaces, gradients at any angle and box shadows (`Box`, `BoxShadow`)
  follow CSS and paint once per change of size or style; each frame they
  cost a copy.
- A transformed item is slow in the software renderer: every glyph and
  image under it takes a per-pixel path, on every frame that repaints its
  region. The panel and each entry of the arcs are laid out flat, out of
  sight, and shown through `ProjectedLayer`, which projects them once per
  change with the design's exact perspective (`ProjectedView`, into an image
  cropped to what the projection covers), frame by frame only while an
  entry's transition runs, and stands them on whole pixels so that each
  frame copies them as they are. At a device pixel
  ratio under 1 (720p) Qt 6.8's software grab keeps only that fraction of
  an item, so the flat copies are given room to spare.
- Nothing clips inside a transformed item and layers draw wrongly in this
  renderer, so neither is used where it would be transformed; the options'
  list clips in the flat panel.

Measured in the laboratory's guest (QEMU on an ARM64 Mac, 4 vCPU, 1920×1080,
percent of one core used by the shell): Home with the network drifting
about 10 % (a frame takes 5.5 ms to paint and compose, and linuxfb then
composes the screen and copies it to the display), and 0 % at rest; one move about
13 % over the 3 s around it; Settings in focus (the gear turning) about
20 %; moving through the menu every 0.7 s about 31 %, while QEMU counts
about 73 display updates a second. These are virtual-machine figures; the
graphics path's are below.

#### On a graphics API

The software renderer's economies above cost a GPU more than they save it.
`RenderInfo` (`src/renderinfo.*`) says which renderer draws the window: what
was asked for before the window exists, then what the scene graph actually
started with (a graphics API that cannot start makes Qt fall back), and,
from the scene graph's own context, whether that API is accelerated (an
OpenGL renderer can itself be software, Mesa's llvmpipe). Its decision goes
to the journal once, as "mun-shell: renderer: …", with the renderer's name
and the render loop. On a graphics API:

- `ProjectedLayer` shows its flat content itself, rendered into a layer (a
  texture, so that an opacity on it applies to the whole, as on the copy)
  and drawn through the projection by the GPU, which renders the layer again
  only when the content changes; nothing is grabbed or projected on the CPU.
  Sampling a layer through the projection softens thin lines (a 2-pixel
  focus frame lost a tenth of its contrast), so the panel, which never fades
  as a whole, has no layer: the GPU draws its lines and glyphs through the
  projection at the display's own pixels, and its text and focus frame keep
  the contrast they have on the software path.
  The transform is the projection the copy uses, the plane's projective map
  with no depth: the design's perspectives would otherwise send parts of a
  panel outside the depth range a graphics API clips to.
- Under the threaded render loop that such an API usually runs, frames are
  presented on the render thread: `FrameWatch` reads a frame's time there
  and hands only that time to the GUI thread, where everything it keeps
  lives.
- The network is one geometry, built once (`NetworkNode`): every line of
  the rings, the spokes, the stretches a wave lights, the orbits and the
  floor, as vertices with the light `hairline` gives a line. Its vertex
  shader (`shaders/network.vert`) places them on every frame from a few
  numbers (how far the rings have drifted and the spokes turned, the waves
  in flight, the dashes' offsets, the floor's slide); the CPU computes those
  numbers, nothing else. The gradients under it are a texture, painted and
  uploaded once per change of the light, the image then let go. The
  shaders are compiled when the shell is built (Qt Shader Tools' `qsb`,
  `qt6-shadertools-dev`), not at run time. Up to eight waves at once: a
  ninth takes the place of the oldest.
- Surfaces and their shadows (`Box`, `BoxShadow`) are drawn by the GPU
  from their parameters (`src/surfacenode.*`): the rounded box from its
  distance, its gradient and its inset lines, and each outer shadow as
  its shape blurred by a Gaussian computed in the shader, exactly across
  and by eight samples along, the corners following each row. Nothing is
  painted or kept when a panel grows or a bar's glow changes colour. What
  the shaders do not draw (an inset with blur, more than four stops,
  insets or shadows; none is used today) is painted as before.
- The large object's layers (`HeroIcon`) are painted on a thread of their
  own, never where frames are made, and each becomes a texture, the image
  let go once uploaded. A frame sets their opacities, the load's sweep (a
  clip), the gear's turn (a transform) and the copper line (a little
  geometry), which the GPU applies; the whole loaded object is composed
  from the three layers already painted instead of being painted again.
  Until an object's layers are ready the previous one stays and the load
  waits, so navigation never waits for a painting. Only the layers of the
  object in focus and of the one fading out are kept.
- On an accelerated renderer, and only there, `SceneClock` ticks on the
  frames the window renders (Qt Quick's animation driver, which the threaded
  render loop paces by the display) instead of its timer, and the network
  drifts, its waves run, the gear turns and the object floats (by fractions
  of a pixel) on every frame: what moves costs the GPU, not a repaint. The journal says which ("mun-shell: motion: …"). With the
  software renderer, or OpenGL in software, the rates above stay. Rest and
  *Reduce motion* stop the motion as before, and a still screen draws
  nothing.

Measured on an ARM64 development board (OpenGL ES 3.2 on Mesa's freedreno,
1920×1080 at 60 Hz, the threaded render loop; percent of one core used by
the shell, sampled while a virtual keyboard played the same keys): Home at
rest with the network drifting, 60 frames/s for about 18 %; moving through
Home's menu, about 60 frames/s, 99 % of frames within 25 ms and none over
40 ms in four runs, for about 60 %; moving through Settings' sections
about 58 frames/s, entering Settings still taking up to 90 ms once; the
gear turning, 60 frames/s for about 18 %. Before this path, the shell managed 10 to 13
frames/s on the same board and display.

`tests/behaviour.py` ("paths") draws the same still screen both ways in the
same binary, the software renderer offscreen and OpenGL on Mesa's llvmpipe
under Xvfb (in the image build's overlay), checks that each says in the
journal which renderer and clock it took, and compares the frames: Home's
network, objects loaded after a change, the panel's surface and shadow, a
chosen entry's bar, glow and knob. Where Xvfb is missing it says the case
was skipped. It cannot show an accelerated renderer's timing; that is
measured on hardware.

The same screen drawn by both renderers (the software renderer and OpenGL on
llvmpipe, in the same binary) differs by about one level in 255 on average,
the moving network and the clock aside; Home's network held still differs
by 0.03 levels on average, the orbits' dashes falling on the same pixels.

The objects are drawn, not images, so they stay sharp at any resolution.
The logo's letters and arc are vector paths (`qml/Logo.js`).

Archivo is Copyright 2020 The Archivo Project Authors
(<https://github.com/Omnibus-Type/Archivo>) and Michroma Copyright 2011 The
Michroma Project Authors (<https://github.com/googlefonts/Michroma-font>),
both under the SIL Open Font License 1.1, whose texts are
`fonts/Archivo-OFL.txt` and `fonts/Michroma-OFL.txt`. The files are
`ofl/archivo/Archivo[wdth,wght].ttf` (renamed `Archivo-Variable.ttf`) and
`ofl/michroma/Michroma-Regular.ttf` of the Google Fonts repository.
Characters they lack fall back to the image's fonts (`fonts-inter`).

### Display path

The unit sets `QT_QPA_FB_DRM=1`: the linuxfb plugin opens the DRM device
and shows each frame with a page flip. It used to draw on `/dev/fb0`, which
on virtio-gpu is the kernel's fbdev emulation: it groups changes before
passing them to the display, and motion looked uneven. Counted in QEMU (its
`virtio_gpu_cmd_res_flush` trace) while a menu animated, with each frame
rendered in 1–3 ms either way, about 15 frames per second reached the
display through `/dev/fb0` and about 44 through DRM. Games keep drawing on
`/dev/fb0` (or DRM, for the GL profile): the launcher stops the shell
first, and the shell takes the display again when it restarts.

The device is `/dev/dri/card0`: Qt 6.8's linuxfb plugin opens that one
whatever its KMS configuration names. Before Qt starts, the shell waits for
card0 to be a usable display (`src/displaydevice.*`): driven by the real
display driver, not a framebuffer the firmware left (simpledrm and the
like), with a connector `connected`. It looks every 250 ms, and has the
kernel read the connectors again every 2 s while none is, for
`MUN_DISPLAY_WAIT_SECONDS` (30 by default; 0 to 600). The journal says what
it found:

```text
mun-shell: display: card0 HDMI-A-1 (virtio_gpu)
```

or, when the wait ran out, every DRM device with its driver and connectors,
the framebuffer devices, and why none is usable; a display connected on
another card is named, since only the image (its kernel and command line)
can make it card0. The shell then exits with 69, and the unit, which has no
condition on the display, starts it again 5 seconds later, with no limit.
So a display driver that comes up late, or a display plugged in later,
still gets the interface. An interface that does not load exits with 70;
both are failures, never the clean stop (1) or the restart for a new mode
(75).

### A Game Card's identity (MUN Shape)

A valid Game Card dresses Home while it is the active card
([docs/shape.md](../../docs/shape.md)), from what the card service exported
for this insertion and nothing else, and every package is drawn by the same
code (`src/shape.*`, `src/shapeworld.*`, `src/shapefront.h`):

- **What it dresses**:
  - The world behind Home (`ShapeWorld`): the package's backdrop, layers,
    sprite emitters and light textures, in motion (docs/shape.md, "How the
    console draws a world").
  - The main arc's entries: the game's plate under each label, in its
    material and at the opacity its text needs over any world; its text
    colour; a chosen bar of that colour with the plate's colour as label;
    its focus on the knob, wire and edge, all whole from the first frame the
    entry is dressed. While the game's options have the focus, the entries
    keep that plate and text as they are drawn (MUN's own entries fade; a
    dressed one never eases out of that fade), only the knobs and wires
    fade, and the chosen bar turns back into a plate, so the panel's option
    is the only focus.
  - The game's panel (the Game Card entry's): plate, text, focus on its
    options.
  - Bands of MUN's glass in the plate's colour under the status line, the
    path and the hints, whose words take the text colour. The status
    line's lights keep MUN's colours, each on a socket of MUN's own (a dark
    disc with a fine rim, whole from the band's first dressed frame), so
    they show as MUN draws them on any band.
  - The card object: its screen shows the package's window image or the
    card's cover in place of the crescent, with an organic outline if the
    package asks (reached during the transition, then still) and its light
    in the game's colour. It is all of that or none: without that image
    (missing, or not decoded) the object is MUN's crescent, with neither the
    game's outline nor its light.
  - Outside the world (while it comes in, or when a package has none): the
    ambient light at the orb (the object's light while the object is the
    game's, else the palette's light) and a tint of MUN's world, both in the
    card's hue at MUN's own luminance.
  - The menus' sounds on those surfaces, with the package's insertion cue.
  - Where the game's colours do not hold but its world is drawn, the
    surfaces sit on MUN's own set on plates of their proven opacity. MUN's
    other panels on Home (*My games*, *Settings*, *Turn off*) sit on MUN's
    plate over a world.
- **Coming in and leaving** (`Shape`'s phases: ready, entering, present,
  leaving; Main.qml runs them): one progress drives the front, the card
  object, which changes first, and each surface, which changes as the front
  reaches it.
  - A card inserted on Home comes in with the package's transition (`tide`,
    `sweep` or `fade`) over its seconds. With Settings or a dialog open, it
    waits for Home wholly on screen (the arc's fade in, the dialog's fade out
    finished).
  - Back from a game, or after a changed choice, it returns with a 0.6 s
    fade, under any dialog, without the cue.
  - *Eject safely*: the identity stays until the card service has released
    the card, then leaves with the package's `out` over three quarters of
    its seconds (0.8–2.4 s). A release that fails keeps it.
  - Removed, replaced, or with Shape turned off: 0.5 s back to MUN, the way
    it came if it had not finished. A second card's identity waits until
    the first has left.
  - *Play*: the game's deep colour, at the luminance of MUN's hand-over,
    grows from the card object under MUN's words while the launcher takes
    the screen. Nothing waits for it.
- **Settings and dialogs** keep MUN's panels, focus and sounds over the
  game's world, which dims under a fixed MUN scrim.
- **Contrast**: only a plate blends, by the plan the checker proved for its
  surface (`neutral-text`, `shape-text`, `bridge` or `cut`), and its text and
  focus change at one point of it. No label eases through colours the rule
  has not proven, during a transition or when the choice moves.
- **The insertion cue** plays once, when the identity of a card that arrived
  while the shell was watching begins to come in (CardClient: the card was
  not the active one in the card service's snapshot), however long its copy
  took. A card found at start, as after a game or a reconnection, is not
  greeted. That first identity spends the insertion's cue, and a marker in
  the runtime directory (`shape-cue`) keeps it spent across the shell's
  restarts.
- **What stays MUN's**: Settings (their arc, panels, focus and sounds),
  every dialog and its sounds, the start-up and power-off, the hand-over's
  words, the layout, sizes, order and focus behaviour, every word and its
  language, the path's MUN™, the lights' colours. The focus there is always
  MUN's copper, lent colours included.
- **Where the colours come from**: the package's surfaces, as the card
  service's checker proved them, verified again here with the same rule
  (`src/contrast.h`) and replaced by MUN's, as a set, if they do not hold.
  Where the package has no palette, the card's `[presentation]` colours;
  with neither, the palette read from the cover (`src/readpalette.*`, the
  checker's algorithm). A lent or read accent becomes the focus only if it
  keeps 3:1 on MUN's plate. These only tint MUN, at once.
- **The world, navigation first**:
  - It is decoded and scaled once to the display's device pixels, only
    what can show (the backdrop's part the canvas shows, a layer's band of
    rows that show, a light texture's part that shows), and painted on a
    thread of its own into two frames that take turns. The GUI thread only
    shows the finished one; a frame never waits for another.
  - It moves at the package's rate (10 or 20 frames per second), its
    transitions' front at 30. A still world (*Reduce motion*) or one at rest
    draws nothing more.
  - Its detail level is chosen from the images' headers against the
    display's budget (136 MiB at 1080p, 200 MiB at 1440p), counting its
    peak: the frames and one frame more for a copy Qt may make of one, the
    export, what the level keeps, and the largest image being prepared,
    decoded in the format its header gives and converted. Levels: full;
    without light textures; the backdrop and the nearest layer at 10 frames
    per second; still (those two, composed once onto the backdrop); none.
    Each image is checked against what the level has left before its memory
    is taken; a level that would go over is given up for the next one first.
    `tests/behaviour.py` measures the shell's own peak against the estimate.
  - `FrameWatch` (`src/framewatch.*`) steps it down one level when
    navigation suffers for two 2-second windows in a row (95th percentile of
    the interface's frames over 25 ms, or keys taking over 50 ms to show);
    a transition's frames are timed, not judged.
  - An image that does not decode drops the whole world: the palette over
    MUN's world.
  - What it costs, measured in the laboratory's virtual machine (software
    renderer) at 1080p and 1440p: CPU at rest and navigating, key-to-frame
    time, frame pacing, memory and the estimate, in
    [docs/shape.md](../../docs/shape.md#measured). Light textures are most
    of a world's paint time; at 1440p the VM is at the watchdog's edge.
- **Decoding**: the export's `shape.json` (bound to the insertion), the
  window image, the cover and the world's images are read and decoded off
  the GUI thread, with the dimensions checked first (1024 × 1024 for the
  window and the cover, 2048 for the world's large images, 256 for a
  sprite) and an allocation limit of 16 MiB. The shell reads only
  `/run/mun/shape/<insertion>.<attempt>/` for the insertion in the record.
  A marker in the runtime directory (`shape-decoding`) names the insertion
  being decoded: if the shell ends meanwhile, its next start skips that
  insertion's identity, so a decoder that crashes cannot do it in a loop.
  Sounds are read in the mixer's format and within the contract's
  durations, the set whole or not at all.
- **Settings** (Appearance and accessibility; Sound): *MUN Shape* Full (default), Colours only
  (colours and plates; no world, images or sounds, MUN's object, the
  palette's light as the ambient) or Off (MUN alone); *Game sounds on the
  menus*; *Reduce motion*, which makes every transition a fade and holds the
  world and Home's objects still.
- **The journal** says what was applied, once per identity (`shape for
  insertion …: shape; dressed: entries glass 0.722, …; focus #F2B85C; window
  512x512; sounds back/enter/insert/move; world; arrival tide/tide 3.2 s;
  cue due`), when it comes in and leaves, and the world's detail level.
  With `MUN_SHELL_TIMING=FILE` (laboratory only), the interface's frame and
  key timings go to that file, with the long frames while the interface
  moves (100 ms or more between two frames of the same motion: stutters,
  counted but not judged), and the world's and the card object's paint
  times to the journal.
- **Behaviour regressions**: `tests/behaviour.py BUILD_DIR/mun-shell` runs
  the compiled binary offscreen with a scene of `tests/scenes/` in place of
  `Main.qml` (`MUN_SHELL_QML_DIR`), against exports made with the card
  tool's checker and stand-ins for the card service's and the launcher's
  sockets. What it covers:
  - the card object when an image does not decode, and a result that comes
    after a newer card;
  - the insertion cue's rule;
  - the presence's phases and reasons: release, removal while coming in,
    another card, a changed choice, a result that waits;
  - the world alone: its detail, its frames in motion, still and at rest,
    its steps down, a world that does not decode;
  - the world's memory at 1080p and 1440p, the shell's own peak measured
    against the estimate: layers and a backdrop that scale large, 16-bit
    and palette images, a sample stepped down to still (composed in place);
    and how the extreme ones look (covered to the edges, the backdrop's
    middle);
  - the shell's own `Main.qml` against the stand-ins: an arrival's tide, a
    dialog that makes it wait, a return under a dialog, a removal mid-tide,
    *Eject safely* confirmed and refused, another card, Reduce motion,
    Colours only, Off, a defective world, Settings over the world;
  - with the options focused, frame by frame: a package ready after the
    options took the focus (a fade and a tide), *Eject safely*, a changed
    choice (Colours only, Off, Full), another card; the dressed entries are
    whole on every frame and MUN's ease; an arrival waits for Home's arc to
    be whole after Settings, and for a dialog's layer to fade;
  - the dressed arc's and panel's contrast measured on grabbed frames: the
    two samples and a palette at the rule's limit, over a white and a black
    world, on Home, with the options focused, and mid-transition; and on
    the real world during a tide.

  The image build runs it after compiling the shell and fails with it; its
  measurements are the build's `logs/shell-behaviour.json`.

## Resolution

The interface is laid out on a 1920×1080 logical canvas, the design's own
pixels, and scaled to the display mode: 2/3 at 1280×720, 1 at 1920×1080,
4/3 at 2560×1440. The objects are drawn, so a larger mode is a sharper
picture of the same layout, and in the laboratory a larger window. Other
aspect ratios centre the canvas; adaptive layout is a later concern.

Settings › Display › Resolution offers *Automatic*, the
display's own mode (the default; the laboratory's is 1920×1080), and 720p,
1080p and 1440p where the display takes them: listed by the connector (from the
display's EDID on real hardware, and in the laboratory from the EDID the
development image gives its virtual display, `os/builder/lab_edid.py`), or
on a virtual connector without them, which takes any mode and gets it as a
CVT reduced-blanking modeline (but a game looking for the current mode in
the connector's list would not find it there). Qt opens the
display once, so the mode is chosen before `QGuiApplication` exists
(`src/displaymode.cpp`): the shell writes Qt's KMS configuration for it to
`/run/mun-shell/kms.json` and points `QT_QPA_KMS_CONFIG` there. A change
restarts the shell: it exits with status 75, which the unit restarts at once
(`RestartForceExitStatus=75`), and returns to Display.

A new mode is on trial. It is stored under a one-shot key that the next
start removes before opening the display, and the shell asks whether to
keep it: without an answer it goes back to the previous one after 15
seconds. A start that fails, shows nothing or loses power therefore leaves
the previous choice in place. A stored mode the display no longer offers
reads as Automatic until it does again. Reset settings returns to
Automatic.

The choice is the console's: a game on the GL profile starts in it, the
launcher keeping the display in that mode between the shell and the game
([docs/runtime.md](../../docs/runtime.md#display)); a framebuffer game gets
the size the kernel set at boot. One display is handled, the first connected
connector of `/dev/dri/card0` (*Display path*); its modes are read from DRM
(libdrm). An explicit `QT_SCALE_FACTOR` in the
environment wins over the scale, and a KMS configuration given in the
environment (`QT_QPA_KMS_CONFIG`, `QT_QPA_EGLFS_KMS_CONFIG`) over the choice,
which is then not offered. *Safe area* scales the interface to 97, 94 or 91 % for
displays that crop their edges.

A board's image may cap Automatic: `MUN_DISPLAY_MAX=1920x1080@60` in the
unit's environment (a drop-in of the board's profile; the QEMU image sets
none, and Automatic is then the display's own mode). The shell then starts
in, from the modes the display lists:

1. the cap's size at its rate, 60.000 Hz before 59.940 Hz (the television
   rate, 60/1.001), then the preferred mode, then the list's order;
2. the largest mode within the cap at that rate (1280×720 at 60 Hz);
3. the cap's size at a lower rate (1920×1080 at 50 Hz);
4. the largest mode within the cap at any rate up to it;
5. the display's preferred mode, above the cap, so that there is a picture.

Interlaced modes are never chosen. The mode is given to Qt as its own
modeline (the connector's timings, its clock written so that Qt's parser
reads it back exact), since a size and a whole rate would get the first
mode that matches them. Options 2 to 5 are logged as warnings. Once Qt has
opened the display, the shell reads the mode actually set and logs it
against the request:

```text
mun-shell: display: requested 1920x1080@60.000 (the cap 1920x1080@60), applied 1920x1080@60.000 on card0 HDMI-A-1
```

A difference is logged as an error. The player's own choice in Settings is
not capped.

## Settings

Six sections, none of which needs an account. An option either works (it
changes the console at once and is kept), informs (it shows the value as the
system has it), or says *Not available yet*: the console never offers what
it cannot do.

| Section | Setting | State |
| --- | --- | --- |
| Display | Resolution | Works: Automatic (default), or 720p, 1080p or 1440p where the display takes them; kept once confirmed, back to the previous one on its own otherwise (*Resolution*) |
| | Safe area | Works: 100 (default), 97, 94 or 91 %; kept |
| | HDR, refresh rate | Not available yet |
| Sound | System sounds | Works: On (default) or Off, the menus' sounds and the start-up's (*Sounds*); kept |
| | Game sounds on the menus | Works: On (default) or Off, a Game Card's own menu sounds and cue (*A Game Card's identity*); kept |
| | Output, format | Not available yet |
| Appearance and accessibility | MUN Shape | Works: Full (default), Colours only or Off (*A Game Card's identity*); kept |
| | Reduce motion | Works: Off (default) or On; Home's world and objects hold still, every transition is a fade, the start-up loses its shake, tremor and flicker; kept |
| Language and time | Language | Works: English (default) or Español; kept |
| | Time format | Works: 24-hour (default) or 12-hour; kept |
| | Time zone | Informs: the system's zone, set by the image |
| | Set time | Not available yet |
| Network | Ethernet, Wi-Fi | Inform: as the kernel sees them (not detected, not connected, connected; no adapter); joining networks is not available yet |
| System | Auto power off | Works: Never, or after 1 (default), 3 or 6 hours without input on the menus; kept. A game in progress does not count: the shell is stopped while it runs |
| | Turn off console, About | Work |
| | Updates | Not available yet |
| | Developer mode | Informs: set by the image (on for a development build) |
| | Reset settings | Works: every setting back to its default but the language; a resolution other than Automatic restarts the shell |
| | Reset console | Works: after two confirmations, the console restarts and starts as on its first day, in English: the settings, the system's history and the shell's caches go, nothing of a Game Card ([mun-reset](../mun-reset/README.md)). Refused while a card is in the slot or a game runs |

The settings live in `/var/lib/mun-shell/settings.ini`
(`StateDirectory=mun-shell`). The file is read as untrusted input: a value
outside the allowed set reads as its default, and a damaged file reads as
the defaults until the next choice writes a valid one. Every change replaces
the file whole (QSettings writes a new file and renames it over the old
one), so a write cut short leaves the previous file as it was. The
directory is not synced after the rename: power lost within seconds of a
change may lose that change, never the file. Nothing else is stored in the
console: games and saves live on their Game Cards.

## Sounds

Moving the focus or changing a choice plays `move`, going in or running an
action `enter` (Play plays `launch` instead, and Reset settings `success`),
going back or closing a dialog `back`: only when a key or the pointer
changed something. The console's own events have theirs: `game_return`
when a game's result is first shown, `shape_exit` as Shape leaves, `eject`
when a safe release is done and `warning` when it is refused, `shutdown`
as the console turns off (it goes down once the sound has played, 3.2 s,
the screen dark), `error` when launching, turning off or saving a choice
fails, and `controller_connect` and `controller_disconnect` when a
controller comes or goes while the shell runs. A card arriving is greeted
by the game's own cue (*MUN Shape*), not by these. All of them follow
Settings › Sound › System sounds. The start-up sound, `startup` (13.05 s),
plays once per boot with the start-up (*Motion*), at the level it was
mastered at rather than the others' 3/4; skipping the start-up fades it
out in 0.1 s. They are MUN's own, compiled in from `sounds/`, all in one
chosen format: PCM, 16-bit, 48 kHz, stereo, only the format and the
samples (no metadata chunks), the menus' under a second each, the
events' under 3.5 and the start-up's under 13.1 (`tests/test_os.py`
checks all of that, and that every one is used); the start-up's was made from
its 96 kHz, 24-bit master, resampled with a linear-phase filter (no sample
moves in time) and dithered to 16 bits. The shell asks ALSA for that format and lets it convert
when a device plays another; it has been heard only through the virtual
console's sound device, and physical outputs are tested on the chosen board.

While a Game Card with its own sounds is active and *Game sounds on the
menus* is on, the menus play the game's `move`, `enter` and `back` instead,
and its `insert` once when the card arrives (*A Game Card's identity*).

`src/systemsounds.*` plays them through ALSA's default device, the one the
games use: the unit adds the `audio` group. A worker thread mixes up to
four at once at 3/4 of their level, so quick moves overlap rather than cut
each other, with about 40 ms buffered ahead. The device is opened on the
first sound, fed silence between sounds so that it never runs dry (the
laboratory's virtual device, played through the Mac's CoreAudio, did not
resume a stream that had: the first sound played, later ones failed with an
I/O error). That silence is a noise far below hearing (at most 2 of 32768)
rather than exact zeros. While the menus are shown with their sounds on,
dimmed too, the device stays open, at about one percent of a core on a
board: an HDMI display may need a moment to hear a stream that starts
again, and the first move after a pause would be lost in it. Otherwise it
is let go after three seconds of silence; the shell holds none of it while
a game runs (the launcher stops the shell first). A device that
fails is opened afresh at once; no device, or one that fails again, means
silence and one line in the journal, and the next sound tries again after
five seconds. Settings › Sound › System
sounds turns them off. In the laboratory the guest's sound device
is silent unless it plays to the Mac (`--window`, or `--audio coreaudio`)
or records to `audio.wav` (`--audio wav`).

## Languages

English is the default and Spanish the other choice; every string is
written in both where it is used (`I18n.t("…", "…")`). The console's
services word their messages in Spanish: in English the shell says what it
knows by each message's code (card errors, launch refusals, session
results) and shows the service's own words only when it has nothing better.

## Card states on Home

| State | Status line | The Game Card entry and its panel |
| --- | --- | --- |
| Reader unavailable | "Reader unavailable" | "No reader"; the panel says the console cannot reach its card reader and reconnects on its own. Shown while the socket is down, never confused with "no card" |
| Absent | "Slot empty" | "Empty"; "Insert a Game Card…" |
| Reading | "Reading the card…" | "Reading…"; the rest of the UI stays usable |
| Valid | The title, the slot's light in the LED's white | The title; the crescent fills; options *Play* (a game with an entry and a reachable, idle launcher) and *Eject safely* |
| Invalid | "Card not valid" | "Not valid"; the panel gives the reason; *Details* opens the code, the service's detail and what to do |
| Released | "Remove the card" | "Remove it"; "You can remove the Game Card" |
| Another card waiting | — | The panel adds that another Game Card is waiting: it will be read when the current one is removed |

My games lists the games the console has in front of it: today the Game
Card in the slot. The console itself holds no games, and says so.

## Playing

*Play* sends one request; the hand-over screen ("Starting MUN Collect…")
covers Home and input waits. The launcher stops the shell, runs the game as
`mun-game` on the same display, and starts the shell again when the session
ends for any reason. The shell then comes back straight to Home, never
through the start-up (it plays once per boot), with the session's result in
a dialog (A or B acknowledges it): "Session ended", "The game closed
unexpectedly", "The Game Card was removed", "The card reader was lost", "The
Game Card has no valid program", "The game could not start", "The session
was interrupted", with what happened to the progress. The result may arrive
a moment after the shell starts (the launcher records it on its next poll);
the dialog then opens over whatever is shown. A refused launch is said
under the panel's options. Wording is the console's; nothing in these
dialogs comes from the game.

### Safe eject

*Eject safely* asks the launcher for a safe release (`{"type": "release"}`
on its socket): refused with a visible reason while a session uses the card,
otherwise the card service flushes, unmounts and publishes `released`, and
Home says "You can remove the Game Card". The shell never touches the device.

## Controls

| Input | Action |
| --- | --- |
| Up / Down | Move within the arc or the options, wrapping |
| Left / Right | Change a choice backwards or forwards; nothing elsewhere |
| A, Enter, Return, Space | Enter, run the option, or answer a dialog |
| B, Esc, Backspace | Back one level, or close the dialog; inside a game, the game decides (MUN Collect exits) |
| Pointer | A click on an entry focuses it, or enters it when it has the focus; on an option runs it; on a dialog's answer chooses it; the wheel moves (a convenience of the laboratory's window) |

In a dialog, Up, Down, Left and Right move between its answers. Back from
the Settings arc returns to Settings on the main arc.

Controllers (`src/gamepads.*`, rules in `src/gamepadmap.*`): every pad
Linux's input layer reports with a gamepad's south button drives the menus
as the keyboard does. Its directional pad and left stick are Up, Down, Left
and Right; the bottom face button (the kernel's south) is Return and the
right one (east) Escape, delivered to the window as those keys, so waking,
a key held and the sounds behave the same. A pad that presents itself as
Nintendo's (vendor 057e) keeps Nintendo's layout: A, on the right, is
Return and B, at the bottom, Escape. A direction held repeats after
400 ms, then every 80 ms; the stick counts once pushed past 60 % of its
travel and until it is back within 40 %, and diagonally the axis pushed
further counts. Pads are found as they connect and let go of when they
disconnect, nothing held after. The shell reads `/dev/input` through its
`input` group; nothing goes back through the kernel.

Tested: GameSir Nova Lite over Bluetooth, on a laboratory board, in its
Android mode (kernel `hid-generic`) and its Switch mode (a Pro Controller to
the kernel, `hid-nintendo`). Planned, not tested: DualShock 4 and DualSense
(`hid-playstation`), Xbox controllers (`xpad`, `hid-microsoft`) and
Nintendo's own (`hid-nintendo`), which the kernel reports with the same
codes.
Pairing is the system's (BlueZ), not the shell's.

## Build and run

The image build compiles the shell with `deploy/build.sh` (Debian 13, Qt 6.8)
and installs it with `deploy/stage.sh`; `./mun dev build` runs both (see
[vm/README.md](../../vm/README.md)). In a running guest:

```sh
./mun vm shell-log                          # journal of the shell service
./mun vm run -- systemctl restart mun-shell
```

For UI iteration, `MUN_SHELL_QML_DIR=/path/to/qml` makes the binary load
`Main.qml` from disk instead of its compiled-in module. Types compiled into
the binary take precedence over files of the same name in that directory,
so other changed components only take effect under different names (or in
a new build).

Under a desktop session (X11/Wayland) the same binary runs in a window: unset
`QT_QPA_PLATFORM` and keep `QT_QUICK_BACKEND=software` if no GPU is available.

## Recovery

- The unit treats exit status 1 as success: Qt's framebuffer VT handler exits
  with 1 after SIGTERM, which is how the launcher stops the shell for a game.
  Status 75 is the shell asking to be started again (a new resolution).
  Status 69 (no usable display) and 70 (the interface did not load) are
  failures, restarted after 5 seconds without a limit; `./mun vm shell-log`
  shows the shell's own account of each.
- A resolution that leaves the screen dark goes back by itself after 15
  seconds, and a restart or power cut before it is kept does the same. The
  choice is `display/resolution` in the settings file; removing the line
  returns to Automatic.
- qemu-ga keeps answering regardless of the shell: `./mun vm run -- …`.
- If the launcher dies mid-session, systemd starts the shell when the game unit
  stops (`ExecStopPost`), and the launcher's restart adopts a running game; see
  `services/mun-launchd/README.md`.
- `sudo systemctl stop mun-shell && sudo systemctl start getty@tty1` returns
  a text login on the console display.
- `sudo systemctl disable mun-shell && sudo systemctl enable getty@tty1`
  makes that permanent.
- The serial console (`.local/mun/guests/<name>/console.log`) still receives
  kernel and systemd messages; the shell owns only tty1.
