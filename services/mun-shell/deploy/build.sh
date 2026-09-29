#!/bin/sh
# Compile MUN Shell out of tree. No installation, no root needed:
#
#   deploy/build.sh SRC_DIR BUILD_DIR BUILD_ID
#
# BUILD_ID is the opaque identifier the system info screen shows: the MUN OS
# build id in an image build.
set -eu
SRC="${1:?usage: build.sh SRC_DIR BUILD_DIR BUILD_ID}"
BUILD_DIR="${2:?usage: build.sh SRC_DIR BUILD_DIR BUILD_ID}"
BUILD_ID="${3:?usage: build.sh SRC_DIR BUILD_DIR BUILD_ID}"
[ -f "$SRC/CMakeLists.txt" ] || { echo "source tree not found at $SRC" >&2; exit 1; }
cmake -S "$SRC" -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/opt/mun/shell -DMUN_BUILD_ID="$BUILD_ID" >/dev/null
cmake --build "$BUILD_DIR" --parallel
