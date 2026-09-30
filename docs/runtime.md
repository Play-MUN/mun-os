# Running a game

What MUN™ OS does when the player chooses Play, and what a game on a Game
Card may expect and must do. The card format is in
[game-cards.md](game-cards.md), saves in [saves.md](saves.md). The launcher's
internals (states, recovery, adoption after a restart) are in
[services/mun-launchd](../services/mun-launchd/README.md).

## Launch

1. The shell asks the launcher to start the active card, naming its slot,
   serial and content version. The launcher checks, at that moment, that the
   card is exactly that one, valid, of kind `game` with an entry; otherwise
   nothing changes.
2. The card service copies `content.entry` into a session directory on an
   executable RAM file system (`/run/mun/launch/<session>/game`, 128 MiB in
   all). The entry must be a regular file of at most 64 MiB whose header is a
   little-endian 64-bit ELF for AArch64; anything else is
   `entry_not_executable` and nothing is copied. The copy is made read-only
   and root-owned, and only a complete copy is ever run. Nothing is executed
   from the card.
3. The shell is stopped, and the game runs as a transient systemd unit,
   `mun-game-<session>`. The shell and the game never draw or read keys at
   the same time.
4. When the game ends, however it ends, the copy is removed, the result is
   recorded and the shell comes back and shows it until the player
   acknowledges.

The game must fit in RAM twice (the copy and the process), and the card must
stay inserted during play: removing it ends the session.

## What the game gets

- **User.** An unprivileged user, `mun-game`, with no capabilities,
  `NoNewPrivileges`, its own cgroup and no network (`PrivateNetwork`).
- **Files.** A read-only system (`ProtectSystem=strict`, `ProtectHome`).
  The game can write to:
  - the session's `work/`, which is also the working directory and `HOME`,
    in RAM and gone with the session;
  - a private `/tmp` (in RAM) and `/var/tmp` (on the system disk), which
    systemd creates for the unit (`PrivateTmp`) and removes when it stops;
  - the system's shared-memory file systems, `/dev/shm` and `/dev/mqueue`,
    which are not private to the session and are emptied only when the
    console restarts.

  None of them is on the card: only saves reach it ([saves.md](saves.md)).
- **Devices.** Only those of its runtime profile (below).
- **Command line.** Always the fixed path of the copy; nothing from the
  manifest becomes an argument or a command.

A game must exit 0 on its own quit action, end cleanly on SIGTERM (it gets
3 s before SIGKILL), need no network, and keep its files in its working
directory, using the temporary directories only for scratch data.

## Runtime profiles

The card's `content.profile` decides what the unit may touch; everything
else is common.

| | `linux-arm64-v0` | `linux-arm64-gl-v0` |
| --- | --- | --- |
| For | a game that draws to the framebuffer and reads evdev | a game built on SDL2, OpenGL and OpenAL |
| Devices | `/dev/fb0`, input devices | DRM (card and render nodes), ALSA, input devices |
| Groups | `video`, `input` | `video`, `render`, `audio`, `input` |
| Memory / tasks | 512 MiB / 32 | 2 GiB / 64 |
| Environment | — | `SDL_VIDEODRIVER=kmsdrm`, `SDL_AUDIODRIVER=alsa`, `ALSOFT_DRIVERS=alsa` |

**`linux-arm64-v0`.** An AArch64 Linux ELF, static or linked only against
the system's own libraries, that draws to `/dev/fb0` (32 bpp, queried, never
assumed) and finds keyboards by capability.

**`linux-arm64-gl-v0`.** The game becomes DRM master by opening the card
node; the console runs no display or sound server, which is why the
environment hints are set. For now a GL-profile game links dynamically
against system libraries the image provides: SDL2, OpenAL Soft, tinyxml2,
Mesa (`libgl1`, `libegl1`, `libgbm1`, `libgl1-mesa-dri`), ALSA and
libstdc++ ([the list](../services/mun-launchd/deploy/runtime-linux-arm64-gl-v0.packages)).
Each image records their exact versions in its `BUILD-INFO.json`. The list is
a version record, not an ABI promise. In the QEMU development image, OpenGL
is software rendered; it costs CPU, and a game must pace itself.

## Environment

| Variable | Value |
| --- | --- |
| `MUN_CARD_ID` | the card's `card.id` |
| `MUN_CONTENT_VERSION` | the card's `content.version` |
| `MUN_SESSION` | the session identifier |
| `MUN_RUNTIME_PROFILE` | the card's profile |
| `MUN_CONTENT_DIR` | with `content.access = "mount"`: the content directory, read-only |
| `MUN_SAVE_FILE`, `MUN_SAVE_SOCKET` | single-object saves ([saves.md](saves.md)); absent for a directory-save game |
| `MUN_DISPLAY_MODE` | with `linux-arm64-gl-v0`: the display's mode as the game starts, `WIDTHxHEIGHT` (*Display*) |
| `HOME` | the session's `work/` |

For a card of the earlier naming generation (`neptune.toml`), every `MUN_`
variable is also published, with the same value, under its `NEPTUNE_` name,
which games on those cards were built against. A card with `mun.toml` gets
`MUN_*` only ([game-cards.md](game-cards.md#naming-generations)).

## Display

The resolution the player chooses (Settings › Picture and sound ›
Resolution) is the console's, not only the shell's: it applies at once, no
restart, and the display stays in it when a game starts.

- **`linux-arm64-gl-v0`.** The game starts with the display already in the
  console's mode, one of the display's own modes: a game that asks for the
  current mode (SDL's desktop mode, a fullscreen-desktop window) gets it,
  and `MUN_DISPLAY_MODE` says it too. The launcher reads the mode while
  the shell shows it and, once the shell has stopped, sets it again with a
  black picture, so the display is not left to the kernel's console mode in
  between; nobody is the display's master when the game opens the card, so
  the game is. A game may set another of the display's modes itself.
- **`linux-arm64-v0`.** The game draws on the kernel console's framebuffer,
  whose size the kernel sets at boot from the display's preferred mode: it
  gets that size (query it), not a resolution chosen afterwards.

## Content during play

With `content.access = "copy"` (the default) only the entry reaches the
session. With `"mount"`, the game also sees the card's file system read-only
at `/run/mun/card`, and `MUN_CONTENT_DIR` points at `content.root` there.

- The mount exists only inside the game unit's own mount namespace, with
  `ro,nosuid,nodev,noexec`: writing fails and nothing there can be executed.
  It disappears with the unit.
- The raw card device stays unreadable to the game.
- The entry still runs from the copy, never from the card.
- `/run/neptune/card` leads to the same place, for engines of earlier-generation
  cards that have that path compiled in. A game on a MUN-generation card must
  not rely on it.

## Session results

The shell shows one result per session: the game `exited` (exit 0), `failed`
(non-zero exit), `crashed` (a signal or a core dump) or did not start
(`start_failed`, `prepare_failed`, or a staging code such as
`entry_not_executable`); or the session ended because the card was removed
(`card_removed`), the card reader was lost for more than 5 s (`reader_lost`),
or the launcher found the session ended while it was not running
(`interrupted`). A failure of the platform around a clean exit is shown
apart, never as the game's error. The result also carries the outcome of
saving ([saves.md](saves.md)).

## Limits

- The game runs from a RAM copy; assets are not streamed from the card
  except through a `mount` card's read-only view, and direct execution from
  the medium is not supported.
- No controller path yet: input is keyboard and pointer (USB HID in the
  development environment).
- Both profiles are measured in QEMU guests only; the libraries, limits and
  performance of the official hardware are not qualified yet.
