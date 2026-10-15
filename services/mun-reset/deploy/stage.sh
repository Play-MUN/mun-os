#!/bin/sh
# Install mun-reset's files under DESTDIR, the image root in an image build
# (os/mkosi): the helper, which the shell's sudoers rule names
# (services/mun-shell/deploy/mun-shell.sudoers), the unit that marks a reset
# and restarts, and the unit that carries it out at boot. Enabled by the image's preset; no packages here.
#
#   deploy/stage.sh SRC_DIR DESTDIR
set -eu
SRC="${1:?usage: stage.sh SRC_DIR DESTDIR}"
DESTDIR="${2:?usage: stage.sh SRC_DIR DESTDIR}"
PREFIX=/opt/mun/reset
[ -f "$SRC/reset.py" ] || { echo "reset.py not found in $SRC" >&2; exit 1; }

install -d "$DESTDIR$PREFIX" "$DESTDIR/usr/local/libexec" "$DESTDIR/etc/systemd/system"
install -m 0755 "$SRC/reset.py" "$DESTDIR/usr/local/libexec/mun-reset"
install -m 0644 "$SRC/README.md" "$DESTDIR$PREFIX/README.md"
for unit in mun-reset.service mun-reset-commit.service; do
    install -m 0644 "$SRC/deploy/$unit" "$DESTDIR/etc/systemd/system/$unit"
done
{
    echo "component=mun-reset"
    echo "source_sha256=$(cd "$SRC" && cat reset.py deploy/* | sha256sum | cut -d' ' -f1)"
} > "$DESTDIR$PREFIX/BUILD-INFO"
