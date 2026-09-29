#!/bin/sh
# Install mun-cardd's files under DESTDIR, the image root in an image build
# (os/mkosi). No packages, users or service activation here; those belong to
# whoever owns the target system.
#
#   deploy/stage.sh SRC_DIR CARD_PACKAGE_DIR DESTDIR
set -eu
SRC="${1:?usage: stage.sh SRC_DIR CARD_PACKAGE_DIR DESTDIR}"
PKG="${2:?usage: stage.sh SRC_DIR CARD_PACKAGE_DIR DESTDIR}"
DESTDIR="${3:?usage: stage.sh SRC_DIR CARD_PACKAGE_DIR DESTDIR}"
PREFIX=/opt/mun/cardd
[ -f "$SRC/cardd.py" ] || { echo "cardd.py not found in $SRC" >&2; exit 1; }
[ -f "$PKG/validate.py" ] || { echo "mun_card package not found at $PKG" >&2; exit 1; }

install -d "$DESTDIR$PREFIX" "$DESTDIR$PREFIX/lib/mun_card" "$DESTDIR/etc/systemd/system" "$DESTDIR/usr/lib/sysusers.d"
install -m 0644 "$PKG"/*.py "$DESTDIR$PREFIX/lib/mun_card/"
install -m 0755 "$SRC/cardd.py" "$DESTDIR$PREFIX/cardd.py"
install -m 0644 "$SRC/README.md" "$DESTDIR$PREFIX/README.md"
install -m 0644 "$SRC/deploy/mun-cardd.service" "$DESTDIR/etc/systemd/system/mun-cardd.service"
install -m 0644 "$SRC/deploy/sysusers.conf" "$DESTDIR/usr/lib/sysusers.d/mun-cardd.conf"
# What was staged, independent of when and where: the same sources give the same record.
{
    echo "component=mun-cardd"
    echo "source_sha256=$(cd "$SRC" && cat cardd.py deploy/* | sha256sum | cut -d' ' -f1)"
    echo "package_sha256=$(cat "$PKG"/*.py | sha256sum | cut -d' ' -f1)"
} > "$DESTDIR$PREFIX/BUILD-INFO"
