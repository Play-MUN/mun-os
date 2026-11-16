# MUN Dev: the development board profile (experimental)

**English** · [Español](es/mun-dev.md)

MUN Dev is the board MUN™ OS is developed on when a virtual console is not
enough: a Radxa Dragon Q6A (Qualcomm QCS6490, Adreno 643 graphics), with a
display on HDMI, a keyboard, wired network and its microSD slot standing in
for a card reader. The `q6a-dev` profile of the image build makes MUN OS
for it.

**It is not MUN.** The console is being designed around another board, the
Radxa Dragon Q8B, which nothing here has run on or says anything about. The
image says what it is (`environment = "radxa-dragon-q6a"`, `release =
false`), the board's microSD slot is not the console's reader, and nothing
on this page is a hardware qualification. The profile is experimental: it
exists so that the system meets a real GPU, a real display, real sound and
a real card, and it changes as that work does.

## What you get, and what you do not

- **The profile, in source.** Everything that makes the image is in
  [`os/mkosi/mkosi.profiles/q6a-dev/`](../os/mkosi/mkosi.profiles/q6a-dev/):
  you build the image yourself.
- **No downloadable image.** A preview release carries the QEMU image only.
  This profile installs packages that Debian does not have (the board's
  kernel, its GPU and audio DSP firmware, its audio topology:
  [`pinned-packages.json`](../os/mkosi/mkosi.profiles/q6a-dev/pinned-packages.json)
  names each with its source), some of it firmware that states no licence of
  its own, and `./mun dev sources` does not gather their source
  ([pinned packages](../os/README.md#pinned-packages)). Until that is
  settled, no image of this profile is published.
- **No support promise.** It is the board on our desk, with the displays and
  cards we have.

## Build and install

```sh
./mun dev build --profile q6a-dev --name NAME
```

The result is `mun-os-<version>-radxa-dragon-q6a.img.xz` under
`.local/mun/builds/NAME/`, the whole disk, with its `BUILD-INFO.json`
([image build](../os/README.md#profiles)). Write it to the board's eMMC, or
to a microSD card to start from the slot; everything on the target is
replaced:

```sh
xz -dc mun-os-<version>-radxa-dragon-q6a.img.xz | sudo dd of=/dev/DEVICE bs=4M conv=fsync
```

We write the eMMC from the vendor's system started from a microSD card, and
read it back against the image. The board's firmware must start the eMMC
before the slot for a card in the slot to be read as a Game Card and not
booted. The board we use has firmware 6.0.251230 in its SPI flash, as it
came; we have not updated it. The profile boots through a plain boot entry
rather than a unified kernel image because a report about a later firmware
(6.0.260120) says that one restarts on such images; we have not tried
either on this board.

The image carries no SSH key: add yours to `/root/.ssh/authorized_keys` on
the installed system. The wired network takes an address by DHCP, and SSH
accepts keys only.

## What the profile does differently

- **Kernel and boot.** Radxa's kernel and device tree, started through
  Type 1 boot entries; a hidden menu with entries that print the boot on
  HDMI, for diagnosis (a key held at power-on shows it). Its "MUN OS
  diagnostic" entry starts the board without the interface and opens a root
  shell on tty1, HDMI and a USB keyboard, with no password
  (`systemd.debug_shell=tty1`,
  [`mkosi.postoutput`](../os/mkosi/mkosi.profiles/q6a-dev/mkosi.postoutput)).
  Anyone who can hold a key at power-on has root: the entry is there for a
  development board and must not be carried into a console image.
- **Display.** The shell draws through the GPU (Qt's eglfs on KMS, Mesa's
  freedreno) at 1920x1080 and 60 Hz whatever the display prefers, and the
  kernel starts in that mode too. The link is capped at 8 bits per channel
  (the board's DisplayPort-to-HDMI bridge washes out 10-bit input), and the
  cap is set before anything is shown.
- **The start.** The console shows MUN's standby picture from the moment
  the system can draw, then its start-up with sound. The board's firmware
  waits 1 s for a key at power-on instead of its 5: this is a setting of the
  firmware, kept in its flash, that the image imposes at every boot
  (`/usr/lib/mun/board-firmware-wait`); F2 and F12 at power-on still reach
  its menus, and masking `mun-board-firmware-wait.service` keeps another
  wait.
- **Sound.** On HDMI, through the board's audio DSP. The sound card comes
  up about 10 s into the boot (the vendor's kernel waits 5 s for an answer
  from the DSP), so the shell loads meanwhile and its start-up waits for the
  sound, up to 30 s (`MUN_SHELL_SOUND_WAIT`). The sound card appears a moment
  before its route to HDMI is set, and a device opened in between plays to
  nothing and fails: within that wait the shell opens the device only once
  the file the board's route script writes exists
  (`MUN_SHELL_SOUND_READY=/run/mun-board-audio/route-set`, set by the
  profile's drop-in for the shell's unit). A DSP whose services do not
  reach Linux has its link reset once.
  No saved mixer state is restored: on this board that restore turned the
  route to HDMI off.
- **Game Cards.** A card image written whole to a microSD card is a Game
  Card in the board's slot (`MUN_CARDD_SLOTS`,
  [card service](../services/mun-cardd/README.md)).
- **Network.** The wired link keeps out of the PCIe power states L1.1 and
  L1.2. It can still go down: see *Known limits*.
- **Support.** A diagnostic dump on the boot partition at every boot and
  shutdown, and one line per boot about how the console started (times,
  sound, start-up, failed units), read over SSH with
  `/usr/lib/mun/board-report`.

The profile's [`mkosi.conf`](../os/mkosi/mkosi.profiles/q6a-dev/mkosi.conf)
says why each of these is there.

## What has been checked

By us, on our one board, with a person at the display. A build's
`BUILD-INFO.json` names its commit; the candidate here is the one this
page's release is made from.

| What | State |
| --- | --- |
| Arrival of a card, navigation, playing MUN Collect and coming back, a save written to the physical card, safe and abrupt removal | Checked on 13 November 2026, with an earlier build of this preview installed whole on the eMMC |
| Reef behind the menus (MUN Shape's motion), dimming after ten minutes, *Reduce motion* | Checked the same day: left running for 33.6 minutes with no restart of the shell or of the services, the display dimming after ten. Frame times were not recorded in that run and it says nothing about a frame rate: [where the motion stands](shape-motion.md#where-it-stands) has what was measured on this board, where the motion does not hold 60 frames a second |
| Start-up: one change of picture after the firmware's, standby picture, sound from its first note | Checked on 14 November 2026 on three kinds of start (after unplugging, after a power cut, power button), with a television; with the candidate's shell and board files put over the earlier installation |
| The candidate's image installed whole, and a walk on it | Checked on 16 November 2026: build `20261115T203329Z-4fcdda77249c` (commit `4fcdda7`) installed whole on the eMMC and read back; start-up (picture and sound), Reef on Home, a card put in and pulled while running, safe removal, playing MUN Collect and coming back, a save written to the card, power-off from the menu. A change of resolution and a save read back after a new insertion were not part of that walk |
| A start with no sound device | Pending on the board; the shell's own checks cover it |
| Displays | A 1080p monitor, a 3440x1440 monitor (the shell at 1080p) and a television, all on HDMI |
| Controllers | One pad, a GameSir Nova Lite over Bluetooth, drove the menus on this board with an earlier laboratory image, Bluetooth set up by hand. The profile sets up no Bluetooth, and no controller has been tried with the candidate |

## Known limits

- The 5 s the vendor's kernel waits for the audio DSP are part of every
  start.
- The reset of a DSP whose services do not arrive has been done by hand on
  the one boot that needed it; the automatic one has not met such a boot
  yet.
- **The wired network can go down.** On our board, in its case, the wired
  link went down while the board ran, at moments when the board was touched
  (a card put in or taken out, the power button) and once with no card in
  (16 November 2026). The kernel reports PCIe completion timeouts on the
  network's link, and the link comes back at the next start. The cause
  remains undetermined. The console goes on: cards, games and saves work.
- The board has no clock battery: without a time server its clock starts
  from an old time at every boot.
- Sound is HDMI only. No Wi-Fi, no Bluetooth setup.
- Power cuts have been survived, not studied: nothing here establishes
  what a card or the system disk holds after one.
- Figures measured here say nothing about the console's board.
