# The images in these pages

Every image here is a screenshot of MUN OS running, taken from the
console's own display, not a drawing or a mock-up. None of them is of MUN
Dev, the development board, or of the console's hardware.

## Taken for MUN OS v0.1.0-dev.5

| Images | What they show |
| --- | --- |
| `home.en.jpg`, `home.es.jpg` | Home with the slot empty |
| `home-collect.en.jpg`, `home-collect.es.jpg` | Home with the MUN Collect Game Card, which carries no MUN Shape package |
| `home-sea.en.jpg`, `home-sea.es.jpg` | Home with MUN Collect dressed in the `sea` sample (MUN Shape v1) |
| `home-reef.en.jpg`, `home-reef.es.jpg` | Home with MUN Collect dressed in Reef (MUN Shape's motion, an experimental draft): one frame of a scene that moves |
| `settings.en.jpg`, `settings.es.jpg` | Settings, on *Display* |
| `settings-appearance.en.jpg`, `settings-appearance.es.jpg` | Settings, on *Appearance and accessibility* |

How they were taken:

- **System:** MUN OS 0.1.0-dev, the `qemu-dev` profile
  (`environment = "qemu-arm64"`, `release = false`), build
  `20261114T161131Z-30e567bd69f7`, from commit
  `30e567bd69f7936beecd361ffb85c26d2c3124a5`. The image's SHA-256 is
  `ce3348cf4e9c378ce76c1b8dc6a8ab8d4544870399e4b75330960f05c4cc0d0b`.
- **Where:** the virtual console, a QEMU 11.1.1 guest on a Mac with Apple
  Silicon (hardware virtualization), started from that build's bundle with
  the tools of that commit. The build was made before the release's own,
  from an earlier commit of the same work. The Game Cards are the bundle's
  own: `collect`, `collect-sea` and `collect-reef`.
- **Drawn by:** Qt Quick's software renderer, as the virtual console draws
  (`renderer: software` in the shell's journal), at 1920×1080.
- **Language:** English, the console's default, in the `.en` images;
  Spanish, chosen in *Settings* › *Language and time*, in the `.es` ones.
- **Taken with:** the guest's display dumped by QEMU (`./mun dev vm GUEST
  screenshot`) on 2026-11-14, with every screen reached from the keyboard
  as a player would. Scaled to 1600×900 and saved as JPEG; nothing was
  drawn over them or retouched.

One image was taken again later: `home-sea.es.jpg`, on 2026-11-15, from
build `20261115T145925Z-3f136840027e` of commit
`3f136840027eecd97f545a93fac7330abfe73c50` (image SHA-256
`4d394ae90bfda6e23789bd3d7a6cad09f417a718b717636e461066bff7ee8ef5`), in the
same way and with that build's own `collect-sea` card. The earlier one
caught the scene at a moment when its light was faint and a shoal of fish
crossed the panel's text.

The time in the top bar is the guest's clock. In the two languages the
pictures were taken minutes apart, `home-sea.es.jpg` a day later: the
moving scenes are not at the same instant.

## The recording

The README opens with a still linked to a recording of MUN OS
v0.1.0-dev.5, one take in English (`dev5-reef.en.jpg`) and one in Spanish
(`dev5-reef.es.jpg`), on 2026-11-18.

- **System:** the published v0.1.0-dev.5, not a build of our own: its image
  `mun-os-0.1.0-dev-qemu-arm64.qcow2` (build
  `20261116T133852Z-fa5344bee486`, SHA-256
  `dcf93d57719b5c9e90aa1017bc8e914be8b214ce6d46c05f8a4335949c94ae50`) and
  its `card-collect-reef` Game Card (`card-collect-reef.img.xz`, SHA-256
  `357c8f20ba1a66a23538a31dae3149a4bbbeccf51a1bb90b38a4b85b745b3b12`),
  installed by `./mun get` from the release's `release.json` with the tools
  of the `v0.1.0-dev.5` tag, each file checked against it.
- **Where:** the virtual console on a Mac with Apple Silicon (macOS 27,
  QEMU 11.1.1, hardware virtualization), in the window `./mun play` opens.
- **Drawn by:** Qt Quick's software renderer at 1920×1080, 60 Hz
  (`renderer: software` in the shell's journal).
- **What happens:** Home with the slot empty; the disk goes in
  (`./mun dev vm play card-attach collect-reef`) and Reef arrives; Enter
  opens MUN Disk's panel with Play in focus, and a school of fish gathers
  under the disk; Down and Enter choose *Eject safely*; Reef leaves, the
  window takes the disk out and Home is empty again. The keys reach the
  console through QEMU's virtual keyboard; the game is not started. Reef
  dresses the disk with MUN Shape v1, and its moving scene is MUN Shape's
  motion, an experimental draft. Before the Spanish take the language was
  set in *Settings* › *Language and time*.
- **How:** one continuous take per language, in real time: the console's
  window alone, without its title bar, and the console's own sound, as
  macOS captures one window. Nothing was sped up, slowed down, cut inside
  or drawn over; only the start and the end were trimmed, 1.5 s before the
  disk goes in and 1.5 s after the slot empties. The published files are
  H.264 at 1920×1080 and 60 frames per second with AAC sound, 26 s in
  English and 27 s in Spanish. The stills are frames of those files at the
  moment the school gathers under Play, scaled to 1600×900.
- **Where they are:** attached to the project on GitHub:
  [English](https://github.com/user-attachments/assets/cfbd2371-ca5a-46b0-8a82-014e34362d1d), [Spanish](https://github.com/user-attachments/assets/cd53cfca-da51-44ce-bae6-20aa61908423).
