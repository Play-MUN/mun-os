# Architecture

MUN OS as it is built today: the image, the console services, the host
tools and the boundaries between them. Contracts for card authors and games
are in [game-cards.md](game-cards.md), [saves.md](saves.md) and
[runtime.md](runtime.md).

## Product boundary

MUN -1 will have one officially supported physical hardware configuration;
it is not selected yet. The assembled console and a DIY installation on that
same configuration will use the same official MUN OS image. Ports to other
hardware are welcome as independently maintained forks; they do not extend
the official support.

The only image that exists is a development image for QEMU on an Apple
Silicon Mac. QEMU is development and test infrastructure, not a product
platform. `./mun build`, the official image, refuses until the hardware
configuration is selected, and never produces a QEMU image labelled as a
release.

## Components

```
Game Card ──► mun-cardd ── card states (cardd.sock) ──────────► mun-shell, mun-launchd
                  ▲
                  └── stage · save · release (cardd-control.sock, root only) ── mun-launchd

mun-shell ── launch · release · acknowledge (launchd.sock) ──► mun-launchd
mun-launchd ── systemd-run ──► mun-game-<session>   runs the copy in /run/mun/launch/<session>/
```

| Component | Language | Role |
| --- | --- | --- |
| [MUN Shell](../services/mun-shell/README.md) (`mun-shell`) | C++ and Qt 6 QML | The interface on tty1: home, card states and options, Play, session results, safe eject, settings and system information, power off. Unprivileged; it reads the system, never changes it, except through one root helper with a fixed vocabulary (`mun-power poweroff|reboot`) |
| [Card service](../services/mun-cardd/README.md) (`mun-cardd`) | Python | Mounts cards for the console and is the only process that writes to one: detects eligible media, checks the file system and mounts it read-only in its private mount namespace, validates the manifest, publishes states, stages a game's entry, and writes saves |
| [Launcher](../services/mun-launchd/README.md) (`mun-launchd`) | Python | Authorises and supervises game sessions: re-checks the card, has the entry staged, stops the shell, runs the game as a sandboxed transient unit, coordinates saves, records the result and restores the shell whatever happened |
| [`mun_card`](../tools/mun-card/README.md) | Python | The card format: strict TOML subset, validation, save bounds and the envelope rule. Shared by the card service and the host tool, so both apply the same rules |
| [`mun-card`](../tools/mun-card/README.md) | Python | Host tool: creates, inspects offline, hashes and converts card images |
| [Image build](../os/README.md) (`os/`) | shell, mkosi | Composes the image from pinned inputs in a disposable builder and records BUILD-INFO |
| [Laboratory](../vm/README.md) (`vm/`, `./mun dev`, `./mun vm`) | Python | Host side: builds, QEMU guests, virtual cards, input, evidence |

The services are systemd units with local UNIX sockets carrying
newline-delimited JSON. Each component's README documents its protocol,
units and privileges.

## Boundaries

- **The card is untrusted input, checked where the console relies on it.**
  Before mounting, the card service requires a whole-image ext4 with a clean
  superblock. It then validates the manifest; the paths the manifest declares,
  which must be relative, link-free and of the expected type, checked
  component by component; the cover's size and dimensions; and, at launch,
  the entry's size and ELF header. Saves are bounded in size and checked as
  [saves.md](saves.md) describes before they reach a game; a single-object
  game still parses its own save. The rest of the content is not inspected:
  the kernel still parses the card's file system, the shell decodes the
  cover, and the game reads its own data, so each of them handles untrusted
  bytes. Nothing establishes who made a card. Nothing on the card becomes a
  command line.
- **One writer for the medium.** The card service mounts a card for the
  console inside its own mount namespace, read-only except during a save's
  write window, and is the only process that writes to it. For a card with
  `content.access = "mount"`, systemd mounts the same device a second time,
  read-only, inside the game unit's own mount namespace; that view is the
  game's only access to the card ([runtime.md](runtime.md#content-during-play)).
  The shell and the launcher never see a mount.
- **Only a copy crosses.** A game's executable is copied to an executable
  RAM file system for its session and run from there; nothing runs from the
  card.
- **The game is sandboxed.** Unprivileged user, no capabilities, no network,
  a read-only system and only the devices of its runtime profile. It can
  write to its session's `work/` directory, to the private `/tmp` and
  `/var/tmp` systemd gives its unit, and to the system's shared-memory file
  systems ([runtime.md](runtime.md#what-the-game-gets)); nothing it writes
  reaches the card except through the console's saves.
- **The shell holds no privileges.** It requests; the launcher and the card
  service decide. The shell and a game never own the display or the keyboard
  at the same time.
- **Recovery does not depend on the launcher.** Each game unit has its own
  cleanup unit, the launcher restarts and adopts a running game, and a
  restore unit brings the shell back if nothing else does.
- **Offline.** The image has no network configuration and no network
  service; playing, saving and restoring need no account and no server.

## Development and release

The development image and a future release compose the same services and the
same common root file system; the development profile adds explicit
development access (qemu-ga, QEMU's guest-side control daemon, on a
virtio-serial port that only the host developer can open) and nothing else. A release composition
will not include it. The image identifies itself in `/usr/lib/mun/release`
and BUILD-INFO (`environment = "qemu-arm64"`, `release = false`).

What is specific to the QEMU development environment today, and will belong
to the hardware integration once one is selected:

- card media discovery: virtio block devices with an `NPT-` serial, which the
  laboratory assigns; a physical reader will identify cards its own way, and
  must exclude the system disk explicitly;
- display: virtio-gpu, 1920×1080 by default and any mode the shell asks for
  (720p to 1440p), the shell on Qt's `linuxfb` with software rendering and
  a scale factor, games on Mesa's software renderer;
- boot, kernel and firmware: Debian's arm64 kernel with systemd-boot;
- sound: a virtio-sound device;
- input: USB keyboard and tablet.

The hardware integration will own boot, kernel and firmware, GPU and NPU
libraries, device permissions, display and audio configuration, reader
discovery and power and thermal policies. Common code requests a MUN
operation or a runtime profile rather than wrapping hardware generally.

## Not implemented

- Physical hardware support of any kind.
- Versioned content and updates on the card, and the object store they need.
- OS updates, rollback and recovery.
- Controllers: input is keyboard and pointer.
- Emulators and imported game collections.
- A published release image.
