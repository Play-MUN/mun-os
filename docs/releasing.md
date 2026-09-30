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

With a clean checkout of exactly that commit, build it under a local build
name: `dev2` here. Build names take 2 to 16 lowercase letters, digits and
`-`, no dots, so the version is only the release's tag.

```sh
./mun dev build --name dev2
```

`BUILD-INFO.json` must say `"clean": true` and name the commit; a build of
uncommitted changes is for local tests only (`./mun dev bundle` warns).

## 3. The bundle

```sh
./mun dev bundle --build dev2
```

`.local/mun/bundles/dev2/` then holds the files a release carries: the
image, `BUILD-INFO.json`, the two Game Cards (`card-collect.img.xz`,
`card-demo.img.xz`), `LICENSE`, `NOTICE`, `NAME-AND-LOGO.txt`, the
typefaces' licences and `release.json`, which lists all of them with their
sizes and SHA-256.

## 4. The corresponding source

```sh
./mun dev sources --build dev2 --out .local/mun/sources/mun-os-v0.1.0-dev.2-sources
tar -cf .local/mun/mun-os-v0.1.0-dev.2-sources.tar -C .local/mun/sources mun-os-v0.1.0-dev.2-sources
(cd .local/mun && shasum -a 256 mun-os-v0.1.0-dev.2-sources.tar > mun-os-v0.1.0-dev.2-sources.tar.sha256)
```

The first command fetches, verified, the Debian source of every package in
the image, its initrd and its kernel (vm/sources.py; about 1 GB), and
`SOURCES.json` names the build and lists the sources with the binaries each
built ([licensing](licensing.md#images-and-their-corresponding-source)).
Running it again only fetches what is missing. On Linux, `sha256sum`
replaces `shasum -a 256`. A release asset must stay under 2 GiB.

## 5. Check it as a player would

From a fresh copy of the tools of that commit, not the checkout that built
it:

```sh
bundle="$PWD/.local/mun/bundles/dev2"
fresh="$(mktemp -d)"
git archive <commit> | tar -x -C "$fresh"
(cd "$fresh" && ./mun dev smoke --bundle "$bundle")
```

The console starts, plays MUN Collect, gives the card back and powers off.
The copy installs the image and the cards in its own `.local/`; remove
`"$fresh"` afterwards.

## 6. Publish, then run the hosts

```sh
gh release create v0.1.0-dev.2 --prerelease --target <commit> \
    --title "MUN OS 0.1.0-dev.2 — development image for QEMU" --notes-file notes.md \
    .local/mun/bundles/dev2/* \
    .local/mun/mun-os-v0.1.0-dev.2-sources.tar .local/mun/mun-os-v0.1.0-dev.2-sources.tar.sha256
gh workflow run hosts.yml -f release=v0.1.0-dev.2
```

`./mun get` downloads only what `release.json` lists: players never download
the sources. The Hosts workflow downloads the release on macOS, Linux
(x86_64, ARM64) and Windows (x86_64, ARM64) and runs the same check there.
If it fails, the release is marked as such in its notes and the fix goes
into the next one.

## After publishing

Point the preview's address in [getting started](getting-started.md#3-the-image)
and in the bug report template (`.github/ISSUE_TEMPLATE/bug_report.yml`) at the
new release.

## Release notes

What changed; the commands to get and play it
([getting started](getting-started.md)); the hosts it was checked on and how
(the Hosts run, a real computer, headless or with a window); what is known
not to work; the build id and the commit. The digests are in `release.json`, the
sources' in their `.sha256`.
