# Licensing

## MUN OS's own work

MUN OS's own work is Copyright 2026 Iván Moreno Mendoza, who develops MUN as
Play MUN, and is licensed under the [Apache License 2.0](../LICENSE): the
services, the card tools, the image build configuration, the laboratory, the
example games, the interface sounds, the tests and the documentation; not
the MUN logo (below). The licence lets anyone use, modify and redistribute it
under its conditions; it does not transfer its ownership. Whoever
redistributes it keeps its attribution notices, those in [NOTICE](../NOTICE)
among them, in one of the places section 4 of the licence allows. Every
image carries the licence, NOTICE and the terms of the name and logo in
`/usr/share/doc/mun-os/`, and every download beside the image.

Contributions stay their authors'. Whoever submits a contribution licenses it
under the same terms (section 5) and certifies with a sign-off that they may
([contributing](../CONTRIBUTING.md#rights-and-sign-off)).

## Third-party material

Each keeps its own copyright and terms:

- **Typefaces.** MUN Shell compiles in Archivo (Copyright 2020 The Archivo
  Project Authors) and Michroma (Copyright 2011 The Michroma Project
  Authors), both under the SIL Open Font License 1.1. The font files and
  their licence texts are in `services/mun-shell/fonts/`; the image carries
  the texts in `/usr/share/doc/mun-shell/`, and every download beside it.
- **The image's packages.** An image is composed of Debian packages, each
  under its own licence, stated in `/usr/share/doc/<package>/copyright` in
  the image; each build's `BUILD-INFO.json` lists them with their exact
  versions. Among them are the Linux kernel, the Qt libraries MUN Shell runs
  on, Mesa, SDL and the fallback typeface Inter (`fonts-inter`, SIL Open
  Font License 1.1).
- **Games.** No third-party game and no game data is part of this
  repository, an image or a download. A game someone compiles from a recipe
  kept outside the repository keeps its own licence, which that build's
  `BUILD-INFO.json` records.

## Images and their corresponding source

Several of the image's packages are under licences (the GNU GPL and LGPL
among them) that oblige whoever distributes the binaries to make their
corresponding source available as well. The source of MUN OS's own
components is this repository at the commit each image names in its
`BUILD-INFO.json`. For the Debian packages, every published image comes with
their source: `./mun dev sources --build NAME` resolves each package of the
image, its initrd and its kernel, at its exact version, to its Debian source
package, adds the sources Debian records a package was built using (the
signed kernel's source holds only signatures; the kernel's own is `linux`),
and fetches those sources, checked against their digests, from
[snapshot.debian.org](https://snapshot.debian.org) into one directory with a
`SOURCES.json` index. That directory is published beside the release as one
archive with its SHA-256, never in Git
([releasing](releasing.md#4-the-corresponding-source)). Debian's own
guidance on distributing derived images:
[Debian for vendors](https://www.debian.org/CD/vendors/legal).

## The MUN name and logo

The Apache License grants no rights to the MUN name, the Play MUN name or the
MUN logo (section 6); they are Iván Moreno Mendoza's. The logo's drawing, the
paths in `services/mun-shell/qml/Logo.js`, is not covered by the licence.
Their terms are in [NAME-AND-LOGO.txt](../NAME-AND-LOGO.txt), which goes with
every image and every download: official images may be redistributed
unchanged, the logo goes with MUN OS and its modified versions where their
interface shows it, and a fork distributed to others takes its own name and
logo; saying truthfully that a work is based on MUN OS is fine.
