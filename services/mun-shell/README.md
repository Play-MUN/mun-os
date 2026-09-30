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

## Layout

| Path | Role |
| --- | --- |
| `src/main.cpp` | Process entry: has the display mode chosen before Qt starts, loads the compiled-in fonts, tells QML whether this is the first start since boot, loads the QML module |
| `src/displaymode.*` | The display mode: the player's resolution if the display takes it, else the display's own; Qt's KMS configuration and the canvas's scale for it; the one-shot trial of a new mode |
| `src/systeminfo.*` | Real system facts read from `/proc`, `/sys`, `/etc/os-release`, `/usr/lib/mun/release`, `statvfs` and `getifaddrs` (identity, hardware, display, time zone, network interfaces), raw so the interface words them in its language |
| `src/shellsettings.*` | The player's settings, validated and kept in the service's state directory |
| `src/systemsounds.*` | The menus' sounds (move, enter, back), mixed on a worker thread and played through ALSA |
| `src/powercontrol.*` | The only privileged request: runs `mun-power` through `sudo -n`; reports failure to the UI |
| `src/cardclient.*` | Mirror of the card service over its UNIX socket: snapshot, events, reconnection; exposes state, manifest, error and cover (data URL) to QML |
| `src/launchclient.*` | Client of `mun-launchd`: `launch(slot, serial, version)`, `release(serial)`, `acknowledge()`, launcher state and the last session result (read from `/run/mun/launch/last-result.json` at start, then over the socket) |
| `src/backdrop.*`, `src/heroicon.*` | Home's painted layers: the network of light behind everything, and the large object of the entry in focus |
| `src/cssbox.*`, `src/blur.*` | Surfaces with CSS semantics (radii, gradients at any angle, outer and inset box shadows) and the blur they and the glows use |
| `src/projectedview.*` | Shows an item through a perspective transform, projected once per change |
| `src/sceneclock.*` | One frame clock for the painted layers |
| `qml/Main.qml` | The scene, the navigation, the actions and the key handling |
| `qml/Panels.qml` | What each entry's panel and each dialog says, from the real state |
| `qml/Theme.qml`, `qml/I18n.qml` | Design tokens and motion; the two languages |
| `qml/ArcMenu.qml`, `ArcNode`, `DetailPanel`, `OptionRow`, `StatusBar`, `MoonPhase`, `ModalLayer`, `BootLayer`, `UiText`, `MarkText`, `Shadow`, `Logo.js` | Components: the arcs and their entries, the panel and its options, the status line, dialogs, the start-up and power-off screen, text and shadows |
| `fonts/` | Archivo and Michroma, compiled in, with their licences |
| `sounds/` | The menus' sounds, compiled in: `move.wav`, `enter.wav`, `back.wav` |
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
the path (HOME › SETTINGS › SYSTEM) and the keys (A Select, B Back). The
card's crescent on the Game Card object (the logo's moon) fills while a
card is in the slot. The ambient light at the orb follows the local hour:
cold at night, pale at dawn, grey-white by day, copper at dusk.

Motion: the start-up plays once per boot: on black the letters appear, the
arc draws itself from its tip and the power light comes on; two seconds
later the screen fades into Home. Moving to another entry: the chosen bar
turns light at once while its copper edge, glow and text colour settle in
0.3 s and it slides 4 px out; the new object loads in 0.9 s, a radar-like
sweep drawing its glowing wireframe with a copper line and its face
filling in over the second half while the previous object fades; a wave
leaves the object through the rings and spokes; the panel's content
changes and it slides to stay centred. Entering Settings, the main arc
fades and moves away and the Settings arc comes in its place; while a
panel's options have the focus, its arc dims. Three minutes after the last
input the network, the float and the gear slow to a stop in 1.5 s and the
scene holds still until the next key, which brings the motion back the same
way, so a console left alone draws nothing. The menus' sounds are described
under *Sounds*.

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
about 73 display updates a second. These are virtual-machine figures; a
console with a GPU renderer was not measured.

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

### Colours lent by the card

A valid Game Card may carry `[presentation] accent` and `background`
([manifest](../../docs/game-cards.md#manifest)). While it is the active
card, the focus (a chosen entry's ring, dot, wire and edge, a chosen
option's frame) takes the accent, and the ambient light at the orb the
background, at the hour's strength. Everything else, from the logo to the
type, wording, layout and navigation, stays MUN. The moment the card is
released or removed the shell is MUN again. Cards without the table change
nothing.

## Resolution

The interface is laid out on a 1920×1080 logical canvas, the design's own
pixels, and scaled to the display mode: 2/3 at 1280×720, 1 at 1920×1080,
4/3 at 2560×1440. The objects are drawn, so a larger mode is a sharper
picture of the same layout, and in the laboratory a larger window. Other
aspect ratios centre the canvas; adaptive layout is a later concern.

Settings › Picture and sound › Resolution offers *Automatic*, the
display's own mode (the default; the laboratory's is 1920×1080,
`MUN_VM_DISPLAY_WIDTH`/`_HEIGHT` in `vm/munvm.py`), and 720p, 1080p and
1440p where the display takes them: listed by the connector (from the
display's EDID on real hardware), or on a virtual connector, which takes
any mode and gets it as a CVT reduced-blanking modeline. Qt opens the
display once, so the mode is chosen before `QGuiApplication` exists
(`src/displaymode.cpp`): the shell writes Qt's KMS configuration for it to
`/run/mun-shell/kms.json` and points `QT_QPA_KMS_CONFIG` there. A change
restarts the shell: it exits with status 75, which the unit restarts at once
(`RestartForceExitStatus=75`), and returns to Picture and sound.

A new mode is on trial. It is stored under a one-shot key that the next
start removes before opening the display, and the shell asks whether to
keep it: without an answer it goes back to the previous one after 15
seconds. A start that fails, shows nothing or loses power therefore leaves
the previous choice in place. A stored mode the display no longer offers
reads as Automatic until it does again. Reset settings returns to
Automatic.

The choice is the shell's alone: a game sets its own mode once the shell has
released the display. One display is handled, the first connected connector
of `/dev/dri/card0`. An explicit `QT_SCALE_FACTOR` in the environment wins
over the scale, and a KMS configuration given in the environment
(`QT_QPA_KMS_CONFIG`, `QT_QPA_EGLFS_KMS_CONFIG`) over the choice, which is
then not offered. *Safe area* scales the interface to 97, 94 or 91 % for
displays that crop their edges.

## Settings

| Setting | State |
| --- | --- |
| Language | English (default) or Español; kept |
| Clock format | 24-hour (default) or 12-hour; kept |
| Resolution | Automatic (default), or 720p, 1080p or 1440p where the display takes them; kept once confirmed (*Resolution*) |
| Safe area | 100 (default), 97, 94 or 91 %; kept |
| System sounds | On (default) or Off: the menus' sounds (*Sounds*); kept |
| Auto power off | Never, or after 1 (default), 3 or 6 hours without input on the menus; kept. A game in progress does not count: the shell is stopped while it runs |
| Turn off console, About, Reset settings | Work; reset keeps the language |
| Time zone, developer mode | Shown as they are (the system's zone, set by the image); changing them is not available yet |
| Ethernet, Wi-Fi | Shown as the kernel sees them (not detected, not connected, connected; no adapter); joining networks is not available yet |
| Account, HDR, refresh rate, audio output and format, start-up sound, status light, setting the time, updates | Not available yet |

The settings live in `/var/lib/mun-shell/settings.ini`
(`StateDirectory=mun-shell`). The file is read as untrusted input: a value
outside the allowed set reads as its default. Nothing else is stored in the
console: games and saves live on their Game Cards.

## Sounds

Moving the focus or changing a choice plays `move`, going in or running an
action `enter`, going back or closing a dialog `back`: only when a key or
the pointer changed something, never for the console's own changes (a card
arriving, a result shown). They are MUN's own, compiled in from `sounds/`,
all in one chosen format: PCM, 16-bit, 48 kHz, stereo, only the format and
the samples (no metadata chunks), under a second each (`tests/test_os.py`
checks all of that). The shell asks ALSA for that format and lets it convert
when a device plays another; it has been heard only through the virtual
console's sound device, and physical outputs are tested on the chosen board.

`src/systemsounds.*` plays them through ALSA's default device, the one the
games use: the unit adds the `audio` group. A worker thread mixes up to
four at once at 3/4 of their level, so quick moves overlap rather than cut
each other, with about 40 ms buffered ahead. The device is opened on the
first sound, fed silence between sounds so that it never runs dry (the
laboratory's virtual device, played through the Mac's CoreAudio, did not
resume a stream that had: the first sound played, later ones failed with an
I/O error), and let go after three seconds of silence; the shell holds none
of it while a game runs (the launcher stops the shell first). A device that
fails is opened afresh at once; no device, or one that fails again, means
silence and one line in the journal, and the next sound tries again after
five seconds. Settings › Picture and sound
› System sounds turns them off. In the laboratory the guest's sound device
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
| Right | Enter an entry, or change a choice forwards |
| Left | Back, or change a choice backwards |
| A, Enter, Return, Space | Enter, run the option, or answer a dialog |
| B, Esc, Backspace | Back one level, or close the dialog; inside a game, the game decides (MUN Collect exits) |
| Pointer | A click on an entry focuses it, or enters it when it has the focus; on an option runs it; on a dialog's answer chooses it; the wheel moves (a convenience of the laboratory's window) |

In a dialog, Up, Down, Left and Right move between its answers. Back from
the Settings arc returns to Settings on the main arc.

Controller: not wired in this increment. Qt 6 has no gamepad module; the plan
is a small evdev-to-key bridge (D-pad → arrows, A → Return, B → Escape) or a
libinput/SDL input source feeding the same key handlers, decided when a real
controller is attached to the VM. Nothing controller-related has been tested
yet.

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
