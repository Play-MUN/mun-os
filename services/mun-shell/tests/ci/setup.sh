#!/bin/sh
# Install, on a fresh Debian 13 (a container or a disposable machine, as
# root), what tests/ci/check.sh needs: the packages that build MUN Shell in
# the image (deploy/build-packages) and the ones its tests run with, all from
# the dated Debian snapshot the image is built from (os/inputs.json), so the
# shell is built and tested with the image's own Qt, compiler and ALSA.
#
#   sh services/mun-shell/tests/ci/setup.sh    # from the repository's root
set -eu
[ -f os/inputs.json ] || { echo "run from the repository's root" >&2; exit 1; }
snapshot=$(sed -n 's/.*"snapshot": *"\([0-9TZ]*\)".*/\1/p' os/inputs.json | head -n 1)
[ -n "$snapshot" ] || { echo "no Debian snapshot in os/inputs.json" >&2; exit 1; }
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*
cat > /etc/apt/sources.list.d/snapshot.sources <<SOURCES
Types: deb
URIs: http://snapshot.debian.org/archive/debian/$snapshot
Suites: trixie trixie-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/$snapshot
Suites: trixie-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
Check-Valid-Until: no
SOURCES
apt-get update -q
# deploy/build-packages, then the tests': make and node (host rule tests and
# the interface logic), qmllint, an X server and Mesa (the OpenGL path and
# the behaviour harness), Python and e2fsprogs.
packages=$(grep -v '^#' services/mun-shell/deploy/build-packages | tr '\n' ' ')
# shellcheck disable=SC2086
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $packages \
    make nodejs python3 qt6-declarative-dev-tools xvfb xauth libgl1-mesa-dri libglx-mesa0 \
    e2fsprogs ca-certificates
