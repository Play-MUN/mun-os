# Compatibility

**English** · [Español](es/compatibility.md)

Three different things run on your computer, with different needs: playing a
downloaded image, making Game Cards, and building an image. "Checked" below
says how and where; what was not tried is said as well.

## Playing a downloaded image

`./mun get` and `./mun play` (or `./mun dev run`): Python 3.9 or later and
QEMU 8.2 or later with its ARM64 UEFI firmware ([getting started](getting-started.md#1-what-you-need)).

| Computer | Console's processor | Headless (download, start, card, game, power-off) | Window, keyboard, sound |
| --- | --- | --- | --- |
| macOS 27, Apple Silicon | Virtualized (HVF) | Checked on a Mac (v0.1.0-dev.5, with MUN Collect and with the card dressed in Reef, whose motion the shell must play; v0.1.0-dev.4) | v0.1.0-dev.5: its sound recorded from a headless console; a window, keyboard and sound (Cocoa, CoreAudio) with an earlier build of the preview. Checked on a Mac with v0.1.0-dev.4 |
| macOS 15, ARM64 (GitHub's virtual machine) | Emulated: no HVF inside a VM | v0.1.0-dev.5 pending; checked in CI (v0.1.0-dev.4) | Not tried |
| Linux x86_64, Ubuntu 24.04 | Emulated | v0.1.0-dev.5 pending; checked in CI (v0.1.0-dev.4) and in a virtual machine (v0.1.0-dev.2) | Not tried (GTK or SDL) |
| Linux ARM64, Ubuntu 24.04 | Emulated (no `/dev/kvm` in either) | v0.1.0-dev.5 pending; checked in CI (v0.1.0-dev.4) and in a virtual machine (v0.1.0-dev.2) | Not tried |
| Linux ARM64 with KVM | Virtualized (KVM) | Not tried | Not tried |
| Windows Server 2025, x86_64 | Emulated, QEMU for Windows | v0.1.0-dev.5 pending; checked in CI (v0.1.0-dev.4) | Not tried |
| Windows 11, ARM64 | Emulated, MSYS2's ARM64 QEMU | v0.1.0-dev.5 pending; checked in CI (v0.1.0-dev.4) | Not tried |

"Checked in CI" is the repository's Hosts workflow
(`.github/workflows/hosts.yml`) on GitHub's hosted virtual machines: it
downloads a preview release as a player would, starts the console, inserts
MUN Collect, plays and exits it, ejects the card and powers off. It runs on
each preview once it is published, and that preview's release notes give
the result: v0.1.0-dev.4 passed on all five
([its run](https://github.com/Play-MUN/mun-os/actions/runs/38839513795));
v0.1.0-dev.5 is pending until the workflow has run on it. It says the tools and the image work on that system; it
does not measure speed, and no physical Linux or Windows computer has been
tried. Emulated consoles are several times slower than
virtualized ones.

Inside the console, games start in the console's resolution when they use
SDL or OpenGL, and framebuffer games at the size the display had at boot
([running a game](runtime.md#display)).

The virtual console draws with Qt Quick's software renderer and has a
keyboard and a pointer. Controllers drive MUN Shell's menus
([MUN Shell, Controls](../services/mun-shell/README.md#controls)), which has
been tried on the development board only (below): none has been passed to
a virtual console.

## Making Game Cards

`./mun card create`, `inspect`, `hash`, `convert` build and read card images
on the host without mounting them. They need Python and e2fsprogs 1.47 or
later (`mke2fs`, `debugfs`).

| Computer | State |
| --- | --- |
| macOS, with `brew install e2fsprogs` | Checked |
| Linux, with the distribution's e2fsprogs | Expected to work; the pull-request checks exercise it on Ubuntu |
| Windows | Not supported natively; use WSL 2 (not tried) |

A preview release already carries its cards: playing needs none of this.

## Building an image

`./mun dev build` composes the image with mkosi inside a disposable builder
VM from pinned inputs, and needs Git, Make, OpenSSH, QEMU, e2fsprogs, about
10 GB of disk and the network during the build.

| Computer | State |
| --- | --- |
| macOS, Apple Silicon (HVF, `hdiutil` for the builder's seed) | Checked: the v0.1.0-dev.4 build took about 10 minutes, its inputs already downloaded |
| Linux ARM64 with KVM (`xorriso` for the seed) | Supported by the tools; not tried |
| Linux x86_64, or ARM64 without KVM | The builder is emulated: hours; not tried |
| Windows | Not supported; WSL 2 not tried |

## Hardware

No hardware is supported. MUN™ -1 will have one officially supported
hardware configuration; it is being designed around the Radxa Dragon Q8B,
on which MUN OS has not run.

MUN OS does run on **MUN Dev**, our development board, a Radxa Dragon Q6A,
from an experimental profile you build yourself (no image is published):
1080p at 60 Hz over HDMI through its GPU, sound, Game Cards on microSD
cards. [MUN Dev](mun-dev.md#what-has-been-checked) says what was checked
there, on which build, and what is pending. It is a development platform:
nothing checked on it is a qualification of the console's hardware.
