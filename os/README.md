# MUN OS image composition

This directory builds MUN™ OS images. Only the development image exists:
`qemu-dev`, a Debian 13 (trixie) ARM64 disk for the QEMU laboratory,
identified as `environment = "qemu-arm64"`, `release = false`. QEMU is a
development environment, not a MUN hardware target. There is no release
image until the official hardware configuration is selected (`./mun build`
says so and stops); see the [architecture](../docs/architecture.md).

`./mun dev build` runs everything described here in a new builder
([getting started](../docs/getting-started.md#build-the-image-yourself) for the
route; [compatibility](../docs/compatibility.md#building-an-image) for where
it builds). A published preview comes with the source of its Debian packages
([releasing](../docs/releasing.md)).

## Layout

| Path | Role |
| --- | --- |
| `inputs.json` | Every pinned input: the MUN OS version, the dated Debian snapshot and its archives, the builder's base image (URL, SHA-512) and tools, and each input's distribution conditions |
| `mkosi/mkosi.conf` | The composition every MUN OS image shares: Debian trixie arm64, the console's runtime packages, the build overlay's compilers, output format and manifest |
| `mkosi/mkosi.profiles/qemu-dev/` | What makes the image a QEMU development image: Debian's arm64 kernel, systemd-boot with a unified kernel image, the quiet kernel command line, hostname `mun-qemu`, the development identity and the development access path (below) |
| `mkosi/mkosi.images/initrd/` | The initrd: mkosi's own `mkosi-initrd` configuration, declared as a subimage so its packages are recorded too |
| `mkosi/mkosi.build.chroot` | Compiles and stages the components into the image with their `services/*/deploy/build.sh` and `stage.sh`, and builds the example games against the image's libraries (beside the image, never in it) |
| `mkosi/mkosi.postinst.chroot` | Writes `/usr/lib/mun/release` and checks that the staged components can run on the image's packages |
| `mkosi/mkosi.extra/` | The systemd preset that enables the console services and leaves tty1 to the shell; the journal's bound (32 MiB) and no forwarding to a syslog the image does not have |
| `mkosi/mkosi.repart/` | Partitions: a 512 MiB EFI system partition and a 6 GiB ext4 root |
| `builder/provision.sh` | Prepares a fresh builder: APT pointed only at the snapshot, the builder brought to it, the image tools installed from it |
| `builder/build.sh` | Fetches and verifies external sources and the profile's pinned packages, runs mkosi on a copy of `mkosi/`, writes the disk in the profile's format (qcow2, or raw compressed with xz) and writes `BUILD-INFO.json` and `SHA256SUMS` |
| `builder/build_info.py`, `builder/inputs.py`, `builder/profiles.py` | BUILD-INFO generation, `inputs.json` access, and a profile's image format and pinned packages ([profiles](#profiles)), Python standard library only |

## How a build runs

1. The host (`vm/mundev.py`) checks that the checkout is clean (or that
   `--allow-dirty` was given), makes a tar of exactly what is built (the commit
   itself with `git archive`) and records its commit, tree, commit time and
   archive hash.
2. It verifies the pinned Debian cloud image against its SHA-512 (downloading
   it once, after checking that the published `SHA512SUMS` lists the pinned
   digest), creates a new builder VM over it with its own disk, EFI variables,
   SSH key and cloud-init seed, and boots it with outbound network.
3. `provision.sh` removes the image's own APT sources, points APT at the
   snapshot in `inputs.json` only, runs a full upgrade to it and installs
   mkosi and the other builder tools from it. The builder's complete package
   list becomes part of BUILD-INFO.
4. `build.sh` fetches the sources of each game recipe the host copied in
   (`--recipe`, [below](#game-recipes)) at their pinned commits and applies
   their checked patches, and downloads and verifies the profile's pinned
   packages, if it has any ([profiles](#pinned-packages)), then runs mkosi
   25.3. APT inside mkosi resolves every other package from the same
   snapshot, with signatures checked against Debian's archive keyring.
   Build scripts run without network.
5. The host copies the results back, checks them against `SHA256SUMS`, powers
   the builder off and deletes it (its disk and key included). Nothing of it
   is reused by the next build; the verified cloud image download is.

The output in `.local/mun/builds/<name>/`:

| File | Content |
| --- | --- |
| `mun-os-<version>-qemu-arm64.qcow2` | The disk image (zstd-compressed qcow2). A guest never writes it: each guest gets its own copy-on-write disk over it |
| `mun-os-<version>-<environment>.img.xz` | Instead of the qcow2, for a profile whose image format is `raw` ([profiles](#profiles)): the whole disk, compressed with xz, to write to a board's storage. No guest runs it |
| `BUILD-INFO.json` | The build record (below) |
| `SHA256SUMS` | SHA-256 of every file above and below except the logs |
| `manifest.json`, `initrd-manifest.json` | mkosi's package manifests of the image and its initrd |
| `builder-packages.tsv` | Every package of the builder, with version |
| `games/mun-collect/mun-collect` | MUN Collect, static AArch64, for a Game Card |
| `games/mun-gl-probe/mun-gl-probe` | MUN GL Probe, dynamically linked against the image's SDL2 and OpenAL Soft, for a `linux-arm64-gl-v0` card |
| `games/<name>/…` | A game compiled from a recipe, with `--recipe` only; no game data is ever a build input |
| `recipes.json` | With `--recipe`: each recipe's name, licence, sources, patches and digest |
| `logs/` | Host, provisioning, build and builder console logs |

## Profiles

A profile is a directory of `mkosi/mkosi.profiles/`, built with
`./mun dev build --profile NAME` (`qemu-dev` by default). Its `mkosi.conf`
adds what is particular to it to the common composition (boot, kernel,
development access) and states in `[Build]` `Environment=`:

| Setting | Meaning |
| --- | --- |
| `MUN_ENVIRONMENT` | The environment the image identifies itself with, also part of the image's file name |
| `MUN_RELEASE` | `false`: every profile here is a development profile |
| `MUN_IMAGE_FORMAT` | `qcow2`, the default: the laboratory's image, which guests run. `raw`: the whole disk, compressed with xz (on every core of the builder, a CRC64 check in each block), as `mun-os-<version>-<environment>.img.xz` |

A raw image is written to a board's storage, a microSD card for instance,
with a flashing tool that reads `.img.xz` files, or on Linux with
`xz -dc IMAGE | sudo dd of=/dev/DEVICE bs=4M conv=fsync` (DEVICE is the
whole card, never a partition, and everything on it is replaced). The
laboratory does not run it: `./mun dev run`, `./mun play`, `./mun dev smoke`,
`./mun dev shape` and `./mun dev bundle` refuse a raw build and say why,
and without `--build` the first four take the latest qcow2 build.

### Pinned packages

A development profile for a board may need a package Debian does not have,
such as the kernel, its modules and its device tree as the board's vendor
builds them. The profile declares each such `.deb` in `pinned-packages.json`
beside its `mkosi.conf` and installs it through its `Packages=`, as any
other package:

```json
{
  "format": "mun-pinned-packages/1",
  "packages": [
    {
      "name": "linux-image-<version>-<flavour>",
      "version": "<version>",
      "url": "https://<host>/<path>/linux-image-<version>-<flavour>_<version>_arm64.deb",
      "sha256": "<SHA-256 of the file>",
      "license": "GPL-2.0-only",
      "source": {"repository": "https://<host>/<kernel source>", "commit": "<full commit hash>"}
    }
  ]
}
```

| Field | Holds |
| --- | --- |
| `name`, `version` | The Debian package name and version; the file's own control fields must say the same |
| `url` | Where the file is downloaded from: https, without credentials, query or fragment, ending in the file's plain `.deb` name |
| `sha256` | The file's SHA-256 |
| `license` | The package's licence |
| `source` | The public source it was built from: its `repository` (https) and either the full `commit` or a `tag` |

Nothing else is accepted, in the document or in an entry, but a
`_comment` note at the top. Everything in it is untrusted input:

- the host checks the declaration and the profile's image format
  (`builder/profiles.py`) before a builder starts;
- in the builder, `build.sh` downloads each file over https, following a
  redirect only to another https URL, and keeps it only if it matches its
  SHA-256 and if `dpkg-deb` reads the declared package and version, for
  the arm64 or the all architecture, from its control file;
- mkosi gets the directory of verified files (`--package-directory`) and
  makes it a local APT repository, with `reprepro`, for the image and its
  initrd;
- the image must have installed the pinned version: if APT took a package of
  the same name and another version from elsewhere, the build fails.

`BUILD-INFO.json` records each pinned package. A build with pinned packages
is a laboratory build: `./mun dev sources` fetches the source of Debian
packages only and refuses it, as the pinned packages' sources are the
repositories they name. A profile without `pinned-packages.json` fetches
nothing, and mkosi gets no package directory.

## Game recipes

MUN OS carries no game but its own examples; games reach the console on Game
Cards their owners make. To port a game this repository does not carry,
keep a recipe outside it and pass it to the build:

```sh
./mun dev build --name one --recipe ~/recipes/mygame     # repeatable
```

A recipe is a directory (`os/builder/recipes.py`):

| File | Holds |
| --- | --- |
| `recipe.json` | `format: mun-recipe/1`, the game's `name` (the directory's name too), its `license`, the `sources` to fetch (`path`, an https `git` URL and the full 40-character `commit`) and the `patches` (`file`, the `source` it applies to, its `sha256`) |
| `build.sh` | Run by the image's build step with `RECIPE` (the recipe with its fetched sources), `WORKDIR` (scratch) and `OUT` (where the game's files go) |
| `*.patch` | The patches `recipe.json` names |

The host validates every recipe before a builder starts and refuses one
larger than 16 MiB: a recipe holds instructions and patches, never game
data. The builder fetches each source at its pinned commit and checks it,
checks every patch against its digest and applies it; the build step
compiles the game against the image's own libraries, as the examples are,
into `games/<name>/`, beside the image and never in it. `BUILD-INFO.json`
records the recipe. The game's data, and the card you make with it, stay in
`.local/`.

## BUILD-INFO

`BUILD-INFO.json` (`format: mun-build-info/1`) records: `name`, `version`,
`environment`, `release`, `profile`, `image_format` (`qcow2` or `raw`;
builds made before it was recorded are qcow2) and `build_id`; build start
and end in UTC; the source (`commit`, `tree`, `clean`, `describe`, archive
SHA-256 and whether the builder image came from the verified cache); the
Debian suite, snapshot, archives, components and keyring; the kernel, Mesa
and firmware packages (the kernel's packages from the snapshot in
`kernel.packages`, a pinned kernel in `kernel.pinned`; firmware: not
installed when the profile has none, as `qemu-dev`, whose virtio devices
need none); the build overlay's toolchain; the builder's image, tools,
package count and list hash; SHA-256 of `inputs.json` and of every file of
the mkosi configuration; each pinned package (`pinned_packages`: URL,
SHA-256, size, name, version, architecture, licence, source and whether the
image installed it; empty for a profile without any); every image package
and every initrd package with its exact version; the
initrd's hash; each game with size and SHA-256 (a recipe's game also with
its recipe: sources, commits, patches, licence and digest); the MUN Shape
format the image's console reads (`shape.format`, `mun-shape/1`, taken from
the checker the card service uses; builds made before it was recorded have
no `shape`); and the image with its hash, beside the hash of the raw disk it
was made from (for a raw image, what it decompresses to).

Inside the image, `/usr/lib/mun/release` carries the identity (name, version,
environment, release, profile, build id, source commit, snapshot); the
shell's system information screen shows it, and `/etc/os-release` names the
image (`IMAGE_ID=mun-os`, `IMAGE_VERSION`). Each component leaves a
`/opt/mun/<component>/BUILD-INFO` with its source hash.

## What is in the image

- The card service, the launcher and the shell, with their units in
  `/etc/systemd/system`, their users from `sysusers.d` and their files in
  `/opt/mun/*` and `/usr/local/libexec/*`. Moving the system to
  a read-only `/usr` belongs with the OS update design, not to this image.
- The runtime packages the services list in `services/*/deploy/packages*`,
  including the provisional GL game profile's libraries. `tests/test_os.py`
  checks that every one of them is in `mkosi.conf`.
- No network configuration and no network service: the QEMU guest has no
  network interface at all, and nothing is downloaded at boot.
- Root is locked, no user can log in, there is no SSH server, and no key or
  password is in the image. `/etc/machine-id` is generated on first boot, so
  each guest has its own.

### Development access

The `qemu-dev` profile adds qemu-ga, QEMU's guest-side control daemon (the
Debian package of that name listed in the profile). It runs commands as root
for whoever can open the host side of its virtio-serial socket
(`.local/mun/guests/<name>/qga.sock`, which only the developer's user can). That is
how the host reads card records, asks for a safe card release and collects
evidence (`./mun dev vm <guest> run -- ...`). It is development access by
design and is named here as such; a release composition would not include
this profile.

## Reproducibility

Two builds of the same commit use the same declared inputs: the snapshot fixes
every Debian package version, the builder starts from the same pinned image
and is brought to the same snapshot, and file times are clamped to the commit
time (`SOURCE_DATE_EPOCH`). What is expected to differ between them, and why
equal image hashes are not the criterion for this milestone:

- the build id (UTC time and commit), which the shell binary embeds;
- partition and file system UUIDs, which systemd-repart draws at random;
- the qcow2 container and anything else not yet measured.

Compare two builds by their BUILD-INFO (packages, initrd packages, toolchain,
configuration hashes, component source hashes) and by behaviour.

## Building without the Mac tooling

`builder/provision.sh` and `builder/build.sh` are ordinary shell scripts for a
disposable Debian 13 arm64 machine: copy the source tree to
`/srv/mun/src`, write a `source.json` like the one `vm/mundev.py` writes, then
`sudo os/builder/provision.sh /srv/mun/out` and
`sudo os/builder/build.sh --profile qemu-dev --out /srv/mun/out --source-info source.json`.
They take over that machine's APT configuration. Only the builder VM route
has been run.
