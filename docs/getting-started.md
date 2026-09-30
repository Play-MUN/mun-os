# Getting started

Run MUN OS on your computer as a virtual console, play the Game Card that
comes with it, save, and continue later. No compiler is needed: you download
the tools and a ready-made image. Where each step has been checked is in
[compatibility](compatibility.md).

## 1. What you need

Python 3.9 or later, QEMU 8.2 or later (its `qemu-system-aarch64`, `qemu-img`
and the ARM64 UEFI firmware) and about 1.5 GB of free disk.

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
`release.json`, which lists every file with its size and SHA-256. Give
`./mun get` the address of that `release.json`:

```sh
./mun get https://github.com/Play-MUN/mun-os/releases/download/v0.1.0-dev.2/release.json
```

It downloads every file, checks each one against `release.json`, installs
the image under `.local/mun/builds/` and the cards under `.local/gamecards/`.
An interrupted download resumes when you run the same command again. A Game
Card that already exists is never replaced (it may hold your saves). On
Windows: `py mun get …`.

## 4. Play

```sh
./mun play --card collect
```

The console opens in a window (with sound, on the computer's own output) and
MUN Collect goes in once it is up. The window has the keyboard while it is in
front:

| Key | In MUN Shell |
| --- | --- |
| Up, Down | Move along the menu |
| Enter (or A) | Choose, go in |
| Esc (or B) | Go back |
| Left, Right | Change a setting |

Home opens on Game Card: **Enter, Enter** is Play. In MUN Collect the arrows
move the square, **S** saves on the card ("GUARDADO EN LA GAME CARD"; the
game's own texts are Spanish) and **Esc** ends the game; the console comes
back with "Session ended": **Enter** for OK.

Settings has the language (English or Spanish), the resolution (the window
follows it), the menus' sounds and more. **Turn off** (Up from Game Card)
shuts the console down cleanly and closes the window; closing the window
yourself is pulling the plug.

## 5. Continue later

```sh
./mun play --card collect
```

Enter, Enter: MUN Collect says "PARTIDA RECUPERADA" and the square is where
you saved it. The save is on the card (`.local/gamecards/collect.img`), not
in the console: another console, or another build, continues from it.

To eject a card while the console runs, choose **Eject safely** on its
panel: the console finishes with it and the card leaves the slot.

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
