# Virtual console laboratory

Host-side tools that build MUN™ OS development images and run them as QEMU
guests on macOS, Linux and Windows ([where it runs](../README.md#where-it-runs)).
They are not console components: the console runtime lives in the image.

- `mundev.py` (`./mun dev`, `./mun get`, `./mun play`) builds an image in a
  disposable builder VM, bundles a build for download, installs a downloaded
  one and creates guests from builds ([image build](../os/README.md)).
- `munvm.py` (`./mun vm`) operates a guest: start and stop, Game Cards, keys
  and pointer, screenshots, commands and logs.
- `host.py` holds what differs between hosts; `bundle.py` the download format;
  `sources.py` (`./mun dev sources`) fetches the source of a build's Debian
  packages for its release ([releasing](../docs/releasing.md)).

## Requirements

To run a guest: Python 3.9 or later and QEMU 8.2 or later (virtio-sound)
with `qemu-img` and its ARM64 UEFI firmware. No third-party Python packages;
running a guest needs no network. To build, also Git, OpenSSH, an ISO tool
for the builder's cloud-init seed (`hdiutil` on macOS; `xorriso`,
`genisoimage` or `mkisofs` on Linux) and e2fsprogs for the example cards; a
build downloads the pinned Debian cloud image and packages from the dated
Debian snapshot. Windows runs guests but does not build.

What each host changes (`host.py`):

| | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Accelerator | HVF on Apple Silicon when `kern.hv_support` is 1 | KVM on ARM64 with a usable `/dev/kvm` | — |
| Otherwise | TCG (`-cpu max,pauth-impdef=on`) | TCG | TCG |
| Firmware | next to QEMU, or its firmware descriptors | QEMU's firmware descriptors (Debian and Ubuntu `qemu-efi-aarch64`), known paths | next to `qemu-system-aarch64.exe` |
| Window | `cocoa` | `gtk`, else `sdl` | `gtk`, else `sdl` |
| Audible sound | `coreaudio` | `pipewire`, `pa`, `alsa`, `sdl` | `dsound`, `sdl` |
| Background start | `-daemonize` | `-daemonize` | detached process; its output in `qemu.log` |
| QMP and qemu-ga | UNIX sockets | UNIX sockets | loopback TCP ports chosen at start, in `control.json` |

`MUN_VM_ACCEL=tcg` forces emulation on any host; `MUN_VM_FIRMWARE` names the
firmware file; on Windows `MUN_QEMU_DIR` names QEMU's directory when it is
neither on `PATH` nor in `Program Files\qemu`. On Windows the loopback ports have no file permissions: while
a guest runs, any program of the computer could connect to them.

## Build and run

```sh
./mun dev build --name one                       # new builder VM → .local/mun/builds/one/ (image, BUILD-INFO.json, games/)
./mun dev run --guest first --build one          # new copy-on-write disk over that image, boot in the background
./mun dev vm first card-attach collect           # = ./mun vm --instance first card-attach collect
./mun dev vm first run -- journalctl -u mun-launchd -b --no-pager   # a command as root, through qemu-ga
./mun dev vm first stop                          # ACPI power-down; --hard quits QEMU
./mun dev run --guest first --window             # the same guest with a window and sound (foreground)
./mun dev list                                   # builds and guests
```

`./mun dev run` without `--guest` uses the guest `dev`, created from the
latest build the first time; `./mun vm` without `--instance` addresses that
same guest. A guest stays bound to the build it was created from; a new build
needs a new guest name. Guest names are 2–16 lowercase letters, digits or
`-`, starting with a letter or digit.

## Guests

- **No network interface** (`-nic none`): the guest has only `lo`. There is
  no SSH server and nothing is downloaded at boot.
- **qemu-ga, QEMU's guest-side control daemon**, listens on a virtio-serial
  port, `.local/mun/guests/<name>/qga.sock` on the host (or a short private
  directory when that path is too long for a UNIX socket). `run`, the logs,
  `probe`, `card-detach` (the safe release) and the card watcher use it; it
  answers one client at a time, under `qga.lock`. It is development access,
  present only in the development image ([image build](../os/README.md)).
- The system disk has the serial `MUN-SYSTEM`; a card has `NPT-<name>`, the
  serial prefix the card service accepts.
- `start --print-command` shows the exact QEMU command. `destroy --yes`
  removes the guest's disk and identity; its build and the cards stay.

## Console session

```sh
./mun dev run --window           # boots the console with a window on this computer; foreground
./mun vm send-key right ret esc  # inject keys through the virtual USB keyboard (QEMU qcode names)
./mun vm send-mouse 0.5 0.5 --click   # pointer as fractions of the display
./mun vm screenshot --name home.png   # the framebuffer, into the guest's screens/
./mun vm shell-log               # journal of the shell service; also launchd-log, cardd-log
```

The window shows the framebuffer the shell draws on and has the guest's
keyboard focus while it is frontmost. Close the session from inside the shell
(Turn off, confirm) and QEMU exits by itself; closing the window instead kills
the guest without a clean shutdown. `open` (what `--window` runs) refuses while
the same guest is already running in the background; stop it first.

`open` also unplugs every card the console releases: choosing Eject safely in
the shell ends with the card gone, as a player's hand would. For a background
start, `card-watch` does the same in the foreground until Ctrl-C. The watcher
polls the guest's live card records (`launchd.py cards`) every two seconds and
unplugs a card only when the insertion bound to that attachment is released,
so an earlier insertion's record or a card being played is never unplugged.
Every attach and detach is one transaction under the guest's `lifecycle.lock`
(and the shared registry's own lock): a `card-attach` that overlaps the
watcher's unplug waits for it ("waiting for another lab command …"), at most
120 s, and then inserts the card. Re-attaching a card that is still plugged in
works whoever gets there first: if the guest shows it released, `card-attach`
pulls it itself and inserts it again; if the guest shows it in use, it is
refused at once; if nothing can be proven yet, the attach waits without
holding the lock, at most 20 s. A card attached while the guest could not be
asked is bound by the watcher the first time it is seen valid; if it is
released before that, the watcher says so once and leaves it to
`card-detach`.

If the shell fails, the console still answers through qemu-ga:
`./mun vm run -- systemctl status mun-shell`,
`./mun vm run -- journalctl -u mun-shell -b --no-pager`. The serial console
(`.local/mun/guests/<name>/console.log`) receives kernel and systemd
messages. The privilege model is in
[services/mun-shell](../services/mun-shell/README.md).

## Game Cards

A build leaves its example games in `.local/mun/builds/<name>/games/`.
`mun-card` ([tools/mun-card](../tools/mun-card/README.md)) makes card images
from them in `.local/gamecards/`:

```sh
python3 tools/mun-card/mun-card create demo              # valid MUN Test Card → .local/gamecards/demo.img
python3 tools/mun-card/mun-card inspect demo             # offline check on the host, image untouched
python3 tools/mun-card/mun-card create game --variant game --game .local/mun/builds/one/games/mun-collect/mun-collect
./mun vm card-attach game                                # Home shows "MUN Collect · juego" within a few seconds
./mun vm send-key ret ret                                # Game Card → Play: the shell hands the screen to the game
./mun vm send-key esc                                    # game exits → shell back with "Session ended" → Enter
./mun vm card-detach game                                # safe removal, then unplug
python3 tools/mun-card/mun-card hash game --files --ignore saves   # manifest, executable, content unchanged
```

`card-attach` gives the disk the serial `NPT-<name>`, which is what makes it
eligible for the card service; the system disk is never touched. A second
attached card waits until the first is removed. The tool refuses a card path
that is not a regular file. The game runs from a temporary copy in RAM, as
user `mun-game`, and the card must stay inserted: pulling it ends the
session. A card that declares saves keeps them on the card; with
`--variant full` `mun-card` makes a card with no free blocks, for the
out-of-space case.

Rules the tool enforces:

- **One writer.** `.local/gamecards/attached.json` records which guest holds
  each image, and which attachment of it. `card-attach` refuses an image a
  running guest holds elsewhere ("detach it there first") or that a
  `mun-card convert` has reserved; a stale entry of a QEMU that is gone is
  dropped. QEMU's own image locking is the second line of defence on macOS
  and Linux; QEMU for Windows has none, so there the registry is the only
  one.
- **Safe removal by default.** `card-detach` asks the guest to release the
  card (`launchd.py release`): refused with `in_use` while a session runs on
  it; otherwise the card service stops accepting writes, finishes the one in
  flight, unmounts and reports, and only then the device is unplugged.
  `card-detach --abrupt` skips all of that to simulate pulling the card.

## MUN Shape preview

`./mun dev shape DIR` shows a MUN Shape package folder in a console of the
laboratory, through the real card service and shell
([docs/shape.md](../docs/shape.md#tools),
[the guide](../docs/guides/shape-your-game.md)):

```sh
./mun dev shape examples/shape/sea --window            # a console in a window with the package on a card
./mun dev shape .local/mypkg --watch                   # no window; every change re-inserted; Ctrl-C ends
./mun dev shape .local/mypkg --watch --base mygame     # a copy of mygame dressed instead of MUN Collect
```

- The console is the guest `shape` (`--guest`), made the first time from the
  latest build (`--build`). Its build must record the MUN Shape its console
  reads (`shape` in `BUILD-INFO.json`, `mun-shape/1`), as builds of this
  checkout do; `./mun dev list` shows it for every build and guest
  (`shape not recorded` for older ones). A build that records none was made
  before builds recorded it and does not say what it shows (the published
  v0.1.0-dev.2 shows no MUN Shape); a guest of one is refused and left as it
  is, with the way to one that shows it: `--guest NAME` for a new guest from
  the latest build, or `./mun dev vm NAME destroy --yes`.
- The card is MUN Collect from that build with the package
  (`mun-card create --variant game --shape`), or, with `--base`, a copy of a
  card from `.local/gamecards/`, cloned and given the package with debugfs;
  the card started from is only read. A downloaded image has no MUN Collect
  executable: its `collect.img` is the base then.
- With `--watch` the folder is followed: once it has been still for a
  second, it is checked; a package the console would not use is reported and
  changes nothing. Otherwise, while no game is being played (the launcher's
  state in the guest), the inserted card leaves by `card-detach`'s safe
  removal (a refusal is tried again later, never forced) and the next card
  is attached: a new insertion, with its own export.
- Its cards are `pv0-<guest>` and `pv1-<guest>` (a long guest name is
  shortened with a digest, so two guests never share them). A card is the
  preview's by what it made, not by its name: it makes each card aside, puts
  it at its name only if nothing is there, and records the file's identity
  (device, inode, size, birth time where the host keeps one) and the card's
  identifier in `.local/gamecards/.shape-preview/`. It remakes, unplugs or
  deletes only that same file holding that same card. Any other file at
  those names (a card of yours, a copy moved there or copied into it) keeps
  its bytes and its inode: the preview does not start, or a change waits,
  and it names the file. It never deletes a card another guest holds.
- One preview per console: while it runs it holds
  `.local/gamecards/.shape-preview/<guest>.lock`, and a second one for the
  same guest is refused.
- It starts only in a guest with no other card attached and never detaches
  another card. It unplugs a card the console releases (*Eject safely*), as
  the window's watcher does.
- At the end (Ctrl-C, or the console turned off) its card leaves safely if
  the console is still on and no game is being played, and its own cards are
  deleted. The guest stays: `./mun dev vm shape stop` or `destroy --yes`.

## Sound and pointer

A guest has a virtio-sound device by default with the silent `none` backend;
an audible backend of the host plays it (`--audio coreaudio` on a Mac; the
host's first available one is the default of `--window`), `wav`
records it to the guest's `audio.wav` (its header is completed once QEMU has
exited) and `off` removes the device. A GL-profile game may rely on the sound
device, and some engines crash without one.

`send-mouse X Y [--click | --hold MS]` moves the virtual USB tablet's pointer
to a position given as fractions of the display and optionally left-clicks or
holds the left button. Fractions address whatever mode the game uses. Under
software rendering a game that samples the keyboard once per frame can miss a
short tap: `send-key esc --hold 150` holds the key longer.

## Fixed inputs

| Item | Value |
| --- | --- |
| Machine | `virt`, `gic-version=max`; `accel=hvf` or `kvm` with `-cpu host`, or `tcg` with `-cpu max,pauth-impdef=on`; the QEMU installation's ARM64 UEFI firmware (see the host table) |
| Sizing | 4 vCPU, 8 GiB (`MUN_VM_VCPUS`, `MUN_VM_MEMORY`): development settings, not console specifications |
| Storage | copy-on-write qcow2 over the build's image, virtio-blk serial `MUN-SYSTEM`; two reserved `pcie-root-port` slots for cards |
| Display / input | `virtio-gpu-pci`; the development image gives it the EDID of a display that prefers 1920×1080 and takes 2560×1440 and 1280×720 (`drm.edid_firmware`, `os/builder/lab_edid.py`), so the shell's Resolution setting and the games see those modes as a television's; the window and screenshots follow the mode; `qemu-xhci` with `usb-kbd` and `usb-tablet`; `-display none` in the background, captured with QMP `screendump` |
| Sound | `virtio-sound-pci`, playback only; backend `none`, `wav`, `off` or one of the host's audible ones |
| Network | none |
| Control | QMP and qemu-ga UNIX sockets in `.local/mun/guests/<name>/` (loopback ports on Windows); serial console to `console.log` there |

Generated files live under `.local/mun/` and `.local/gamecards/`, both ignored
by Git. Never point the tool at a host physical disk; card images are plain
files.

## Card lifecycle contract

- Each fresh QEMU process starts with empty card slots. Stop/start never deletes
  the card image; reconnect it explicitly after boot. A guest reboot inside the
  same QEMU process does not recreate the virtual machine or its devices.
- Names contain 1–16 lowercase ASCII letters/digits, `_` or `-`, starting with
  a letter/digit. The exact serial is `NPT-<name>` (at most 20 bytes), with no
  truncation. For `card01`, the guest sees `/dev/disk/by-id/virtio-NPT-card01`.
- Attach and detach are serialised across processes by the guest's lifecycle
  lock and the registry lock, as described above; state is written atomically.
- A failed device insertion releases the newly created block backend. Failure
  to create a backend does not delete an existing one.
- Successful insertion confirms the QEMU device, not guest readiness; the card
  service reports the card once the guest has seen it.
- Detach waits for the requested device's deletion event, including events that
  arrive before the command response. If the wait expires, retry `card-detach`:
  it checks the actual QEMU device/node state and completes delayed cleanup.
  A busy PCIe transition can reject an immediate removal after insertion; the
  error retains state, so retry once the guest finishes the transition.
- `card-create --force` rejects cards recorded as attached. Card paths must be
  regular image files, not symlinks or host devices.

## Regression checks

`make check` runs the host tests for these tools (`tests/test_vm.py`,
`tests/test_mundev.py`, `tests/test_host.py`, `tests/test_bundle.py`): each
host's command line, firmware lookup and backends, the download's checks and
resumption, QMP event ordering and filtering, qemu-ga framing,
restart state, unique serials, the attach registry, the lifecycle locks and
the release reconciliation, failed insertion cleanup, delayed removal recovery,
the verified download and the builder. They use temporary files and fake
protocol and process boundaries; they do not boot a VM.

## Known limits

- HVF and KVM pass the host CPU model through: the guest sees the computer's
  CPU, not a console's; under TCG it sees QEMU's `max`. Timing results do not
  transfer to hardware, and emulated timings not even between computers.
- virtio-gpu here is an unaccelerated framebuffer path. It proves a display and
  input stack exists in the guest; it says nothing about a hardware GPU.
- Hot-plug is PCIe device add/remove, which resembles but is not SD card
  insertion. The guest still sees real kernel block-device add/remove events,
  which is what the card lifecycle software needs to handle.
- The Cocoa display runs in the foreground only; QEMU cannot daemonise with it.
