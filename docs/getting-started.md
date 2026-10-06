# Getting started

**English** · [Español](es/getting-started.md)

Run MUN™ OS on your computer as a virtual console: look around its
interface, insert a Game Card, play, save, eject the card safely and continue
later, then see a game dress the console with MUN Shape. No compiler is
needed: you download the tools and a ready-made image. Where each step has
been checked is in [compatibility](compatibility.md).

## 1. What you need

To play: Python 3.9 or later, QEMU 8.2 or later (its `qemu-system-aarch64`,
`qemu-img` and the ARM64 UEFI firmware) and about 1.5 GB of free disk.

- **macOS**: `brew install qemu` ([Homebrew](https://brew.sh)). The system
  `python3` works.
- **Debian or Ubuntu**: `sudo apt install qemu-system-arm qemu-utils
  qemu-efi-aarch64 python3`, and `qemu-system-gui` for the window. Other
  distributions package the same programs; the firmware is usually called
  AAVMF or edk2-aarch64.
- **Windows on an x86_64 computer**: [QEMU for Windows](https://www.qemu.org/download/#windows)
  (its installer puts the firmware next to `qemu-system-aarch64.exe`; the
  tools also look in `C:\Program Files\qemu`) and Python from
  [python.org](https://www.python.org/downloads/windows/). Run the tools as
  `py mun …`.
- **Windows on an ARM64 computer**: QEMU for Windows is built for x86_64 and
  cannot run the console there. Use [MSYS2](https://www.msys2.org)'s ARM64
  build: in its CLANGARM64 shell, `pacman -S mingw-w64-clang-aarch64-qemu
  mingw-w64-clang-aarch64-qemu-image-util` (the tools also look in
  `C:\msys64\clangarm64\bin`), and Python from python.org.

Sections 2 to 6 need nothing else. Section 7, MUN Shape, makes a card, and
the card tools also need e2fsprogs 1.47 or later: `brew install e2fsprogs`
on macOS, the `e2fsprogs` package on Linux; on Windows, WSL 2 (not tried).
Building the image needs more again ([below](#build-the-image-yourself)).

## 2. The tools

The tools are this repository. Either clone it:

```sh
git clone https://github.com/Play-MUN/mun-os.git
cd mun-os
```

or download the *Source code* archive of a
[preview release](https://github.com/Play-MUN/mun-os/releases) and unpack it.
Use the tools of the same release as the image when you can.

## 3. The image

A preview release carries the image, its record (`BUILD-INFO.json`), two
Game Cards (MUN Collect and the MUN Test Card), the licence texts and
`release.json`, which lists every file with its size and SHA-256. These
pages are for v0.1.0-dev.3; if it is not on the
[releases page](https://github.com/Play-MUN/mun-os/releases) yet, use the
latest preview there, with the pages of its own tag, or build this branch's
image ([build the image yourself](#build-the-image-yourself)). Give
`./mun get` the address of its `release.json`:

```sh
./mun get https://github.com/Play-MUN/mun-os/releases/download/v0.1.0-dev.3/release.json
```

It downloads every file, checks each one against `release.json`, installs
the image under `.local/mun/builds/` and the cards under `.local/gamecards/`.
An interrupted download resumes when you run the same command again. A Game
Card that already exists is never replaced (it may hold your saves). On
Windows: `py mun get …`.

If you played an earlier preview, its console (`play`) stays on that image:
a console keeps the image it was made from, and a new one is made from the
most recently built image you have, which need not be this download. Start
this one by name, in a console of its own:
`./mun dev list` shows the name it was installed under (`d<month><day>-<time>`
of its build, unless you chose one with `./mun get … --name NAME`).

```sh
./mun play --build NAME --guest NAME
```

Use `--guest NAME` in the commands below as well, and `NAME` where they
say `play` (`./mun dev vm NAME card-attach collect`,
`./mun dev vm NAME shell-log`, `.local/mun/guests/NAME/`). The earlier
console stays as it was, with its own settings; your cards and their saves
work in both.

## 4. Start the console and look around

```sh
./mun play
```

The console opens in a window, with sound on the computer's own output
(window, keyboard and sound have been checked on macOS so far). The window
has the keyboard while it is in front:

| Key | In MUN Shell |
| --- | --- |
| Up, Down | Move along the menu |
| Enter (or A) | Choose, go in |
| Esc (or B) | Go back |
| Left, Right | Change a setting |

Home has four entries, each with its panel on the right:

- **Game Card**: the slot. It is empty for now: "Slot empty".
- **My games**: "There are no games on the console. Your games live on their
  Game Cards."
- **Settings**: the language (English or Spanish), the resolution (the
  window follows it), the menus' sounds, MUN Shape and more.
- **Turn off**: shuts the console down cleanly and closes the window; closing
  the window yourself is pulling the plug.

The line at the top says which card is in, the network (*Offline*: the
console needs none) and the time.

## 5. Insert a Game Card and play

With Home on screen, from a second terminal in the same folder:

```sh
./mun dev vm play card-attach collect
```

That puts `.local/gamecards/collect.img` in the console's slot, as a hand
would. The console reads the card and checks it, and Game Card shows MUN
Collect, its panel saying the card is ready. **Enter, Enter** is Play.

In MUN Collect the arrows move the square, **S** saves on the card
("GUARDADO EN LA GAME CARD"; the game's own texts are Spanish) and **Esc**
ends the game. The console comes back with "Session ended": **Enter** for
OK.

## 6. Eject it safely, and continue later

On the Game Card panel choose **Eject safely**: the console finishes with the
card and releases it, "You can remove the Game Card" appears, and the window
takes the card out of the slot. Then **Turn off**: Up from Game Card, Enter,
Enter.

Later, the card goes in as the console starts:

```sh
./mun play --card collect
```

Enter, Enter: MUN Collect says "PARTIDA RECUPERADA" and the square is where
you saved it. The save is on the card (`.local/gamecards/collect.img`), not
in the console: another console, or another build, continues from it.

## 7. See MUN Shape

While its card is in, a game can dress the console in its own identity:
colours, materials, the card object, a world behind the menus, transitions
and sounds, from a package of resources on the card that the console draws
with its own code. With e2fsprogs (section 1), one command shows a sample:

```sh
./mun dev shape examples/shape/sea --window
```

A console of its own (the guest `shape`, made the first time from your
latest image) opens in a window with a disposable card: your MUN Collect
dressed in the `sea` sample, put in once Home is up so you see it arrive.
Play it, come back, eject it; *Turn off* ends the preview. Your own cards
are only read. The other sample is `examples/shape/paper`.

*Settings* › *Picture and sound* › *MUN Shape* (Full, Colours only, Off)
and *Reduce motion* change how much of it you see.
[See MUN Shape in action](guides/see-shape-in-action.md) shows how such a
card is made and what to look at; [dress the console in your
game](guides/shape-your-game.md) makes one of your own.

## Where things are

| Path | What |
| --- | --- |
| `.local/mun/builds/<name>/` | An installed image, its `BUILD-INFO.json`, its licences |
| `.local/mun/guests/<name>/` | A virtual console's own disk, logs and screenshots (`./mun play` uses `play`) |
| `.local/gamecards/*.img` | Game Cards, with their saves |

`./mun dev list` lists builds and consoles. `./mun play --build NAME
--guest NAME` starts another console over another image; a console stays
bound to the image it was created from.

## If something goes wrong

- *No ARM64 UEFI firmware found*: install the firmware package above, or
  name the file with `MUN_VM_FIRMWARE`.
- *QEMU … has no virtio-sound device*: QEMU is older than 8.2; upgrade it,
  or start with `--audio off`.
- On Windows ARM64, *QEMU's x86_64 build, which cannot run the console*: use
  MSYS2's ARM64 build, above.
- The console's own logs: `./mun dev vm play shell-log`, `launchd-log`,
  `cardd-log`; its serial console: `.local/mun/guests/play/console.log`.
- Something unclear or wrong in these steps: say so in an
  [issue](https://github.com/Play-MUN/mun-os/issues/new/choose); that helps
  too.

## Build the image yourself

Building is separate from playing and needs more: a Mac with Apple Silicon
(Xcode command line tools, Python, and `brew install qemu e2fsprogs`), or an
ARM64 Linux computer with KVM (Git, Make, OpenSSH, QEMU, e2fsprogs,
`xorriso`; not tried yet). Windows does not build (use WSL 2). A build runs in
a disposable builder VM, downloads its pinned inputs from a dated Debian
snapshot and takes about 10 GB of disk:

```sh
make check                        # the host checks
./mun dev build --name one        # → .local/mun/builds/one/, with the example games beside the image
./mun card create mycollect --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
./mun play --build one --guest mine --card mycollect
```

A build leaves its games beside the image, not on cards: the third line
makes a MUN Collect card from this build's copy
([create a Game Card](guides/create-game-card.md) explains it).

How an image is composed and what a build records: [image build](../os/README.md).
Consoles in the background, keys and screenshots from a script, virtual card
insertion: [laboratory](../vm/README.md).
