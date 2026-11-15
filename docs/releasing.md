# Releasing

How a preview release of MUN™ OS is made. Every release ties together one
commit of `main`, the tools of that commit, one image built from it, its
Game Cards, its licences and the source of everything in the image. A
release is never changed after it is published: a mistake is fixed in the
next one.

Preview releases are GitHub pre-releases named `v<version>-dev.<n>`
(`v0.1.0-dev.2`), with images marked `release = false`, until MUN has
hardware and a release that is not a preview.

## 1. The commit

Before `dev` goes into `main`, its pages already speak of the new release:
a pull request to `dev` points the preview's address in
[getting started](getting-started.md#3-the-image) and in the bug report
template (`.github/ISSUE_TEMPLATE/bug_report.yml`) at the new release, in
English and in Spanish, and says what changes for its players. Next to that
address, getting started says which release its pages are for and what to
use while that release is not on the releases page yet, so the pages hold
before and after it is published. What earlier releases did or did not do
stays, named as theirs. Where a release was
checked is never carried over: until the Hosts workflow has run on the new
release, its Linux and Windows checks are pending, in the pages and in its
notes.

Then `dev` merged into `main` through a pull request (a merge commit), with
its checks green and `make check` passing locally. Note the full hash of the
resulting commit on `main`; the tag goes on it at the end.

## 2. The image, from that commit

With a clean checkout of exactly that commit, build it under a local build
name: `dev2` here. Build names take 2 to 16 lowercase letters, digits and
`-`, no dots, so the version is only the release's tag.

```sh
./mun dev build --name dev2
```

`BUILD-INFO.json` must say `"clean": true` and name the commit; a build of
uncommitted changes is for local tests only (`./mun dev bundle` warns, and
its `release.json` says so).

The image build runs the shell's behaviour regressions and fails with
them, but it only records how long Home's transitions take (`--timing
record`): a builder's frames are the builder's, not the shell's. Judge
them once on the same commit, on a machine doing nothing else, with Debian
13 and Qt 6.8 as the image build has them, and Xvfb:

```sh
fresh="$(mktemp -d)"
git archive <commit> | tar -x -C "$fresh"
sh "$fresh/services/mun-shell/deploy/build.sh" "$fresh/services/mun-shell" "$fresh/shell" <build_id>
python3 "$fresh/services/mun-shell/tests/behaviour.py" "$fresh/shell/mun-shell" \
    --work "$fresh/home" --only home --timing check
```

`<build_id>` is the one `BUILD-INFO.json` gives. It must exit with status
0, in each of Home's three ways (the previous and the orbital designs on
the software renderer, the orbital design through OpenGL). Keep its output
and `$fresh/home/report.json`, with the machine it ran on (processor, cores,
system, container). A duration out of its bounds holds the release until it
is understood: a slower shell is a regression; a busy machine is no
measurement, and the run is repeated on a quiet one.

## 3. The bundle

```sh
./mun dev bundle --build dev2
```

`.local/mun/bundles/dev2/` then holds the files a release carries: the
image, `BUILD-INFO.json`, the Game Cards (`card-collect.img.xz`,
`card-collect-sea.img.xz`, MUN Collect dressed in the `sea` MUN Shape
sample, `card-collect-reef.img.xz`, dressed in Reef, the example of MUN
Shape's motion, and `card-demo.img.xz`), `LICENSE`, `NOTICE`, `NAME-AND-LOGO.txt`,
the typefaces' licences and `release.json`, which lists all of them with
their sizes and SHA-256.

The image and the game come from the build; the cards' MUN Shape packages,
the card tool that makes them and the licence texts come from the checkout
the command runs in, and `release.json` names the build's commit for all of
it. So run it in a checkout at exactly the build's commit, with a clean
working tree: for a build recorded clean, any other checkout is refused,
saying which commit the build is of, and the game put on the cards must
match the hash `BUILD-INFO.json` recorded for it. `release.json` then
carries `"one_commit": true` in its `source`. A build of uncommitted
changes is still bundled for a local test, with `"one_commit": false`,
which `./mun get` remarks when it installs it; a release image is never
bundled that way.

## 4. The corresponding source

```sh
./mun dev sources --build dev2 --out .local/mun/sources/mun-os-v0.1.0-dev.2-sources
COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs --no-acls --no-fflags \
    --uid 0 --gid 0 --uname root --gname root \
    -cf .local/mun/mun-os-v0.1.0-dev.2-sources.tar -C .local/mun/sources mun-os-v0.1.0-dev.2-sources
(cd .local/mun && shasum -a 256 mun-os-v0.1.0-dev.2-sources.tar > mun-os-v0.1.0-dev.2-sources.tar.sha256)
tar -tf .local/mun/mun-os-v0.1.0-dev.2-sources.tar | grep -E '(^|/)\._'    # prints nothing
```

The first command fetches, verified, the Debian source of every package in
the image, its initrd and its kernel, and of what they were built using,
as their `Built-Using` and `Static-Built-Using` fields record it (the second
is where Debian 13 names code linked statically, as in Go and Rust
programs; vm/sources.py; about 1.1 GB), and `SOURCES.json` names the build and lists
the sources with the binaries each built
([licensing](licensing.md#images-and-their-corresponding-source)). Running
it again only fetches what is missing, and a copy of another build's
directory saves the download when the sources are the same. The archive
carries the files and nothing of the Mac that made it: no `._` companion
files, extended attributes or local user names. On Linux, GNU tar does the
same with `tar --owner=0 --group=0 --numeric-owner -cf …`, and `sha256sum`
replaces `shasum -a 256`. A release asset must stay under 2 GiB.

## 5. Check it as a player would

From a fresh copy of the tools of that commit, not the checkout that built
it:

```sh
bundle="$PWD/.local/mun/bundles/dev2"
fresh="$(mktemp -d)"
git archive <commit> | tar -x -C "$fresh"
(cd "$fresh" && ./mun dev smoke --bundle "$bundle")
(cd "$fresh" && ./mun dev smoke --bundle "$bundle" --guest smoke-reef --card collect-reef --motion)
```

The console starts, plays MUN Collect, gives the card back and powers off.
The second run, in a guest of its own with the card dressed in Reef (the
bundle is installed already), also requires the shell to say it plays the
card's motion, not the package's v1 look.
The copy installs the image and the cards in its own `.local/`; remove
`"$fresh"` afterwards. Installing never replaces a card that exists, so in
a copy that already had one the check would play that card: it says so
before it starts the guest, when the card there is not the bundle's own
(another card, or one with saves on it). A fresh copy has none.

## 6. Check the hosts on a draft, then publish

The hosts check the candidate's own bundle before anything is published. A
draft release carries it: only the repository's members see a draft, and
its tag is not created until it is published.

```sh
gh release create v0.1.0-dev.2 --draft --prerelease --target <commit> \
    --title "MUN OS 0.1.0-dev.2 — development image for QEMU" --notes-file notes.md \
    .local/mun/bundles/dev2/* \
    .local/mun/mun-os-v0.1.0-dev.2-sources.tar .local/mun/mun-os-v0.1.0-dev.2-sources.tar.sha256
gh workflow run hosts.yml --ref <branch> -f draft=v0.1.0-dev.2
```

The Hosts workflow takes the draft's bundle and, on macOS, Linux (x86_64,
ARM64) and Windows (x86_64, ARM64), runs the same two checks as step 5 and,
on Windows, those of the console's control channel. Only one job of it
reads the draft, which needs write access; it hands the bundle to the
others, which keep read-only access. When every host passes, publish the
draft as it is, and its tag is created at `<commit>`:

```sh
gh release edit v0.1.0-dev.2 --draft=false
```

If a host fails, the draft is not published: the fix goes into a new
commit, whose image, bundle and draft are made again from step 2.
`./mun get` downloads only what `release.json` lists: players never
download the sources. A published release can be checked again with
`gh workflow run hosts.yml -f release=<tag>`.

## Release notes

What changed; the commands to get and play it
([getting started](getting-started.md)); the hosts it was checked on and how
(the Hosts run, a real computer, headless or with a window); what is known
not to work; the build id and the commit. The digests are in `release.json`, the
sources' in their `.sha256`.
