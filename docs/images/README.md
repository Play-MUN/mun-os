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
  Silicon (hardware virtualisation), started from that build's bundle with
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

The time in the top bar is the guest's clock. In the two languages the
pictures were taken minutes apart: the moving scenes are not at the same
instant.
