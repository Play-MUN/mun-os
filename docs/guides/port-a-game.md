# Bring a game to MUN

What it takes for a game to run on MUN™, beyond putting it on a card
([create a Game Card](create-game-card.md) covers that part). This is for
someone who has, or may compile, the game's source: MUN runs programs built
for it, and nothing else.

## What a MUN game is

An **AArch64 Linux program** for one of the console's two runtime profiles,
started by the console in a sandbox, offline, with the card's content and
saves as the contracts describe ([running a game](../runtime.md)):

| | `linux-arm64-v0` | `linux-arm64-gl-v0` (provisional) |
| --- | --- | --- |
| For | a game that draws on the framebuffer and reads evdev input | a game on SDL2, OpenGL and OpenAL |
| Links against | nothing, or only the system's own C libraries (static is simplest) | the libraries the image provides: SDL2, OpenAL Soft, tinyxml2, Mesa (GL, EGL, GBM), ALSA, libstdc++ |
| Display | the framebuffer, at the size the kernel set at boot | the console's resolution, current when the game starts (`MUN_DISPLAY_MODE`) |
| Card | `./mun card create … --variant game` | `./mun card create … --variant game-gl` |

A Windows or macOS program, or an x86_64 Linux one, is not a MUN game, on a
card or anywhere else: it has to be built again. Engines and libraries the
image does not provide must be linked into the game statically: the card's
`content/` is mounted `noexec`, so nothing there runs or loads as code; it
holds data only. In the development
image OpenGL is rendered in software: it costs CPU, and a game must pace
itself.

## Who needs what

- **Players** need a MUN console, or the virtual one ([getting started](../getting-started.md)),
  and the card. Nothing to build.
- **Whoever brings the game** needs to build it for ARM64 Linux against the
  console's libraries, make the card, and try it in the virtual console:
  [building an image](../compatibility.md#building-an-image) and
  [making cards](../compatibility.md#making-game-cards) say where that works.

## Build it against the image's libraries

A game on the GL profile must use the same library versions the console has.
The reliable way is to build it where the image is built, from a **recipe**:
a directory you keep outside this repository with `recipe.json` (the game's
name and licence, the sources at pinned commits, the patches with their
digests) and a `build.sh` that compiles it:

```sh
./mun dev build --name mygame --recipe ~/recipes/mygame
```

The game comes out in `.local/mun/builds/mygame/games/mygame/`, beside the
image, and the build's `BUILD-INFO.json` records the recipe
([game recipes](../../os/README.md#game-recipes)). A recipe holds
instructions and patches, never the game's data, and MUN OS never carries a
third-party game: your port stays yours.

## Its data

Data the game reads, the part of a game its owner supplies, goes on the card
under `content/`, read-only during play; the game finds it at
`MUN_CONTENT_DIR` ([content during play](../runtime.md#content-during-play)):

```sh
./mun card create mygame --variant game-gl --game .local/mun/builds/mygame/games/mygame/mygame \
    --content ~/mygame-data --title "My Game" --id org.example.mygame --version 1.0.0
```

The card, with the data on it, stays in `.local/`. Never commit game data you
have no right to distribute.

## Its saves

Saves live on the card, never in the console ([saves](../saves.md)). Two
ways, chosen in the manifest:

- **Single-object saves**: the game reads its save from `MUN_SAVE_FILE` and
  asks the console to write a new one over `MUN_SAVE_SOCKET`; the console
  writes it to the card atomically and says when it is safe
  ([single-object saves](../saves.md#single-object-saves)). MUN Collect saves
  this way.
- **Directory saves**, for a game that already writes its own save files:
  the card declares which files in the game's home are saves and how to tell
  a complete one (`--saves-directory`, `--saves-unit PATTERN:CHECK`,
  `--saves-max-bytes`), and the console copies them to the card, checked,
  while the game runs and when it ends
  ([directory saves](../saves.md#directory-saves)).

Nothing else the game writes is kept for it: its home, `/tmp` and `/var/tmp`
go with the session ([what the game gets](../runtime.md#what-the-game-gets)),
and a game must not rely on anything it leaves elsewhere.

## Try it, then check the whole path

In the virtual console: insert the card, play, save, end the game, eject the
card, restart the console and continue ([create a Game Card](create-game-card.md#5-play-and-save)
walks through it). The session's result says how the game ended; the
launcher's journal (`./mun dev vm GUEST launchd-log`) says why a start
failed. Remove the card while the game runs, too: the game ends and the
saves already written are safe ([removal and failures](../saves.md#removal-and-failures)).

## What is not there yet

Controllers (keyboard only for now), updates to a card's content, other
runtime profiles and physical hardware: see
[architecture](../architecture.md). The GL profile is provisional: its
library list is a record of versions, not an ABI promise.
