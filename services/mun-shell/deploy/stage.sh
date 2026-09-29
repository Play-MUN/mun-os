#!/bin/sh
# Install a compiled MUN Shell and its system files under DESTDIR, the image
# root in an image build (os/mkosi).
# The binary, the unit, the power helper and its sudoers rule, the user, and
# the licence texts of the typefaces compiled into the binary. No packages,
# boot configuration or activation here.
#
#   deploy/stage.sh SRC_DIR BUILD_DIR DESTDIR
set -eu
SRC="${1:?usage: stage.sh SRC_DIR BUILD_DIR DESTDIR}"
BUILD_DIR="${2:?usage: stage.sh SRC_DIR BUILD_DIR DESTDIR}"
DESTDIR="${3:?usage: stage.sh SRC_DIR BUILD_DIR DESTDIR}"
PREFIX=/opt/mun/shell
[ -f "$BUILD_DIR/CMakeCache.txt" ] || { echo "no build in $BUILD_DIR; run deploy/build.sh first" >&2; exit 1; }

DESTDIR="$DESTDIR" cmake --install "$BUILD_DIR" >/dev/null
install -d "$DESTDIR/usr/local/libexec" "$DESTDIR/etc/sudoers.d" "$DESTDIR/etc/systemd/system" "$DESTDIR/usr/lib/sysusers.d"
install -m 0644 "$SRC/README.md" "$DESTDIR$PREFIX/README.md"
# The SIL OFL requires its text to go with the fonts, which the binary embeds.
install -d "$DESTDIR/usr/share/doc/mun-shell"
for licence in "$SRC"/fonts/*-OFL.txt; do
    install -m 0644 "$licence" "$DESTDIR/usr/share/doc/mun-shell/$(basename "$licence")"
done
install -m 0755 "$SRC/deploy/mun-power" "$DESTDIR/usr/local/libexec/mun-power"
install -m 0440 "$SRC/deploy/mun-shell.sudoers" "$DESTDIR/etc/sudoers.d/mun-shell"
install -m 0644 "$SRC/deploy/mun-shell.service" "$DESTDIR/etc/systemd/system/mun-shell.service"
install -m 0644 "$SRC/deploy/sysusers.conf" "$DESTDIR/usr/lib/sysusers.d/mun-shell.conf"
{
    echo "component=mun-shell"
    echo "build_id=$(sed -n 's/^MUN_BUILD_ID:STRING=//p' "$BUILD_DIR/CMakeCache.txt")"
    echo "source_sha256=$(cd "$SRC" && find src qml fonts CMakeLists.txt deploy -type f | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
} > "$DESTDIR$PREFIX/BUILD-INFO"
