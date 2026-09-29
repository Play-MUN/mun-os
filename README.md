# MUN

**BOP — Buy. Own. Play.** MUN is a physical console. MUN OS is its software:
insert a Game Card, play offline, and take your progress with the card.

## Availability

There is no MUN hardware and no MUN OS release yet.

- MUN -1, the first console, will have **one officially supported hardware
  configuration**. It is not selected. The assembled console and DIY
  installations on that configuration will use the same official image.
  Ports to other hardware are welcome as independently maintained forks.
- What exists is a **development image for QEMU**, built from this
  repository, which a computer runs as a virtual console
  ([where it runs](#where-it-runs)). QEMU is development and test
  infrastructure, not a supported platform, and the image is marked as such
  (`environment = "qemu-arm64"`, `release = false`). An image can be built
  here, or downloaded ready to boot with `./mun get`; none is published yet.

In that image, today: MUN Shell boots on its own; Game Cards (virtual, in
the laboratory) are validated and shown; a game on a card starts, runs
sandboxed and hands the console back however it ends; saves are written to
the card and restored on another guest or another build; a card can be
removed safely. The examples are MUN Collect, a small test game, and a
GL/audio probe; a game you own can be compiled from a recipe you keep
outside this repository and put on a card you make. Controllers, card
content updates, OS updates and any physical hardware are not implemented.

## Principles

- A legitimate Game Card can start its offline-capable game without an
  account or an activation server. Optional online features must be
  explicit.
- Saves live on the card and travel between consoles.
- The console's internal storage is for the system, recovery, bounded caches
  and scratch space, not for installing the player's library.
- Updates, once they exist, keep the original version of a game available.
- Common software depends on MUN and Linux contracts; boot, GPU, NPU, reader
  and thermal details belong to the hardware integration.
- Game data a player supplies is separate from MUN's software.

## Where it runs

The console is ARM64, and so is the image. On an ARM64 computer QEMU runs it
with the processor's own virtualization; elsewhere it emulates the
processor (QEMU's TCG): the same console, slower.

| Computer | The console's processor | State |
| --- | --- | --- |
| macOS, Apple Silicon | Virtualized (HVF) | Tested on macOS 27 |
| Linux, ARM64 with KVM | Virtualized (KVM) | Not yet tested |
| Linux, ARM64 without KVM, or x86_64 | Emulated | Tested only in Ubuntu 24.04 virtual machines, not on a Linux computer |
| Windows 11, x86_64 or ARM64 | Emulated | Not yet tested |

## Requirements

### To play a downloaded image

Python 3.9 or later and QEMU 8.2 or later (its `qemu-system-aarch64`,
`qemu-img` and ARM64 UEFI firmware), and about 1.5 GB of free disk. No Git,
compiler or card tool: a downloaded image comes with its Game Cards.

- macOS: `brew install qemu` (Homebrew); the system `python3` works.
- Debian or Ubuntu: `sudo apt install qemu-system-arm qemu-utils
  qemu-efi-aarch64 python3`, and `qemu-system-gui` for the window. Other
  distributions package the same programs; the firmware is usually called
  AAVMF or edk2-aarch64.
- Windows: QEMU for Windows (its installer puts the firmware next to
  `qemu-system-aarch64.exe`; the tools also look in `C:\Program Files\qemu`)
  and Python from python.org; run the tools as `py mun …`.

### To build an image

A Mac with Apple Silicon (tested on macOS 27.0), with the Xcode command line
tools (Git, Make, a C compiler), Python 3.9 or later (the system `python3`
works) and, from Homebrew, QEMU (tested 11.1.1) and e2fsprogs (tested
1.47.4; the card tool uses its `mke2fs` and `debugfs`):

```sh
brew install qemu e2fsprogs
```

The build runs inside a disposable builder VM, so no Linux machine or
container runtime is needed. It downloads its pinned inputs (the Debian cloud
image for the builder, packages from a dated Debian snapshot and, with
`--recipe`, the sources a game recipe pins) and needs about 10 GB of free disk
per build. The built image never uses the network.

The tools also build on an ARM64 Linux computer with KVM (Git, Make, OpenSSH,
e2fsprogs and `xorriso` for the builder's seed; not yet tested); without
hardware virtualization the builder is emulated and a build takes hours.
Windows does not build (use WSL 2).

## First steps

**Play a downloaded image.** With a bundle's address (a web address, or a
directory that `./mun dev bundle` made):

```sh
./mun get <address>          # downloads, verifies every file, installs it as a build; Game Cards into .local/gamecards/
./mun play --card collect    # the console in a window; MUN Collect goes in once it is up
```

`./mun get` resumes an interrupted download when run again and never
replaces a Game Card that already exists (it may hold saves). On Windows:
`py mun get …`, `py mun play …`.

**Build, boot, make a Game Card.** From the repository root:

```sh
make check                                         # host regressions
./mun dev build --name one                         # new builder VM; → .local/mun/builds/one/
./mun dev run --guest first --build one            # new guest disk, no network interface, booted in the background
./mun card create collect --variant game \
    --game .local/mun/builds/one/games/mun-collect/mun-collect   # a MUN Collect card: .local/gamecards/collect.img
./mun dev vm first card-attach collect             # the shell shows "MUN Collect"
./mun dev vm first screenshot --name home.png      # → .local/mun/guests/first/screens/home.png
```

`./mun dev run --guest first --window` boots the same guest in a window with
sound instead (foreground; the window has the keyboard; closing it cuts the
power). Every other step works the same with keys typed in the window.

In MUN Shell, Up and Down move along the menu, Enter (or A) chooses and Esc
(or B) goes back; Home opens on Game Card. The shell speaks English until
Settings › Account and language says otherwise. Mind that Up from Game Card
reaches Turn off, which asks before turning the console off.

**Play, save, power off, restore.** MUN Collect: arrows move the square, `S`
saves to the card, `Esc` exits (the game's own texts are Spanish).

```sh
./mun dev vm first send-key ret ret                # Game Card → Play
./mun dev vm first send-key right right down s     # move, save: "GUARDADO EN LA GAME CARD"
./mun dev vm first send-key esc ret                # exit, then OK on "Session ended"
./mun dev vm first stop                            # power button: a clean shutdown (Turn off in the shell does the same)
./mun dev run --guest first                        # boot the same guest again
./mun dev vm first card-attach collect
./mun dev vm first send-key ret ret                # "PARTIDA RECUPERADA": the square where it was saved
./mun dev vm first send-key esc ret
./mun dev vm first card-detach collect             # safe removal: the console releases the card first
./mun dev vm first stop
```

**A second, independent build with the same card.**

```sh
./mun dev build --name two                         # another new builder, same pinned inputs
./mun dev run --guest second --build two           # a new guest from build two
./mun dev vm second card-attach collect            # a card is only ever in one running guest
./mun dev vm second send-key ret ret               # the progress saved under build one
```

Compare `.local/mun/builds/one/BUILD-INFO.json` with build two's.
`./mun dev bundle --build one` makes a downloadable bundle of build one, with
its Game Cards, in `.local/mun/bundles/one/`: serve that directory, and
`./mun get` its address. An invalid
card: `./mun card create broken --variant bad-arch`, then `card-attach
broken`. Pulling a card during play, with a second MUN Collect card so the
first keeps its progress: `./mun card create pull --variant game --game …`,
`card-attach pull`, Play, then `./mun dev vm first card-detach --abrupt pull`.
Other guest commands: [vm/README.md](vm/README.md).

**Optional: a game you own.** MUN OS carries no game but its examples. To
port one, write a recipe (pinned sources, patches, a build script) and keep
it outside this repository: `./mun dev build --recipe DIR` compiles the game
against the image's libraries into `.local/mun/builds/<build>/games/<name>/`
([game recipes](os/README.md#game-recipes)). Its data is yours and stays in
`.local/`, and the card you make with `./mun card create --variant game-gl
--game … --content …` never leaves it.

## Documentation

- [Documentation index](docs/README.md)
- [Game Cards](docs/game-cards.md), [saves](docs/saves.md) and
  [running a game](docs/runtime.md): the contracts for card and game authors
- [Architecture](docs/architecture.md)
- [Image build](os/README.md) and [laboratory](vm/README.md)
- [Contributing](CONTRIBUTING.md)

MUN is the console and the project, MUN OS its operating system, MUN Shell
its interface and `./mun` the entry point of the tools. The project had
another name before; Game Cards made then carry `neptune.toml`, save as
`neptune-save/1` and hand their games `NEPTUNE_*` variables. The console
still reads and plays them, and `mun-card convert` makes a MUN copy on
request ([naming generations](docs/game-cards.md#naming-generations)).

## Repository map

| Location | Responsibility |
| --- | --- |
| `services/` | MUN Shell, the card service and the launcher |
| `tools/` | `mun-card` and the shared card package |
| `os/` | MUN OS image composition: pinned inputs, mkosi configuration, builder scripts |
| `vm/` | The laboratory: builder VMs, image guests, virtual cards, input and evidence |
| `mun` | Developer entry point: `./mun dev` (builds and guests), `./mun card` (card images), `./mun vm` (a guest) |
| `examples/` | Example games and game integration recipes |
| `tests/`, `scripts/` | Host regressions and the documentation check |
| `docs/` | Contracts, architecture and reference |
| `.local/` | Ignored: builds, guests, card images, game data and generated files |

## Development checks

```sh
make check
```

Requires Python 3.9+ and Make. Checks local documentation links, Python
syntax and host regressions, and builds and runs the example game's C save
tests when a C compiler is available. It does not boot QEMU or build the
shell or an image; `./mun dev build` does the latter. `make test` runs only
the Python regressions.

## Licence

MUN OS's own work in this repository is licensed under the
[Apache License 2.0](LICENSE): the services, the card tools, the image build
configuration, the laboratory, the example games, the tests and the
documentation, with the exceptions below.

Third-party material keeps its own terms:

- **Typefaces.** MUN Shell compiles in Archivo (Copyright 2020 The Archivo
  Project Authors) and Michroma (Copyright 2011 The Michroma Project
  Authors), both under the SIL Open Font License 1.1; the font files and
  their licence texts are in `services/mun-shell/fonts/`, and the image
  carries the licence texts beside the shell.
- **The image's packages.** The image is composed of Debian packages,
  listed with their versions in each build's `BUILD-INFO.json`; each keeps
  its own licence, stated in `/usr/share/doc/<package>/copyright` in the
  image. Among them are the Qt libraries MUN Shell runs on and the fallback
  typeface Inter (`fonts-inter`, SIL Open Font License 1.1).
- **Games.** No third-party game and no game data is part of this
  repository, an image or a download. A game compiled from a recipe kept
  outside the repository keeps its own licence, recorded in that build's
  `BUILD-INFO.json`.

The licence grants no rights to the MUN name or logo (section 6 of the
licence), and the logo's drawing, the paths in
`services/mun-shell/qml/Logo.js`, is not covered by it.
