# Releasing

How a preview release of MUN OS is made. Every release ties together one
commit of `main`, the tools of that commit, one image built from it, its
Game Cards, its licences and the source of everything in the image. A
release is never changed after it is published: a mistake is fixed in the
next one.

Preview releases are GitHub pre-releases named `v<version>-dev.<n>`
(`v0.1.0-dev.2`), with images marked `release = false`, until MUN has
hardware and a release that is not a preview.

## 1. The commit

On `main`, with the pull-request checks green and `make check` passing
locally. Note its full hash; the tag goes on it at the end.

## 2. The image, from that commit

With a clean checkout of exactly that commit:

```sh
./mun dev build --name v0.1.0-dev.2
```

`BUILD-INFO.json` must say `"clean": true` and name the commit; a build of
uncommitted changes is for local tests only (`./mun dev bundle` warns).

## 3. The bundle

```sh
./mun dev bundle --build v0.1.0-dev.2
```

`.local/mun/bundles/v0.1.0-dev.2/` then holds the files a release carries:
the image, `BUILD-INFO.json`, the two Game Cards (`card-collect.img.xz`,
`card-demo.img.xz`), `LICENSE`, `NOTICE`, the typefaces' licences and
`release.json`, which lists all of them with their sizes and SHA-256.

## 4. The corresponding source

```sh
./mun dev sources --build v0.1.0-dev.2
tar -cf mun-os-v0.1.0-dev.2-sources.tar -C .local/mun/sources <build_id>
```

This fetches, verified, the Debian source of every package in the image, its
initrd and its kernel (vm/sources.py; about 1 GB), and `SOURCES.json` lists
them with the binaries each built ([licensing](licensing.md#images-and-their-corresponding-source)).
Running it again only fetches what is missing.

## 5. Check it as a player would

```sh
./mun dev smoke --bundle .local/mun/bundles/v0.1.0-dev.2
```

from a fresh copy of the tools (`git archive` of the commit): the console
starts, plays MUN Collect, gives the card back and powers off.

## 6. Publish, then run the hosts

```sh
gh release create v0.1.0-dev.2 --prerelease --target <commit> \
    --title "MUN OS 0.1.0-dev.2 — development image for QEMU" --notes-file notes.md \
    .local/mun/bundles/v0.1.0-dev.2/* mun-os-v0.1.0-dev.2-sources.tar
gh workflow run hosts.yml -f release=v0.1.0-dev.2
```

The Hosts workflow downloads the release on macOS, Linux (x86_64, ARM64)
and Windows (x86_64, ARM64) and runs the same check there. If it fails, the
release is marked as such in its notes and the fix goes into the next one.

## Release notes

What changed; the commands to get and play it
([getting started](getting-started.md)); the hosts it was checked on and how
(the Hosts run, a real computer, headless or with a window); what is known
not to work; the build id and the commit. The digests are in `release.json`.
