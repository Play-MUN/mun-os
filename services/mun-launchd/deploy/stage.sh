#!/bin/sh
# Install mun-launchd's files under DESTDIR, the image root in an image
# build (os/mkosi). The launcher,
# the cleanup helper, the exec tmpfs, the cleanup and restore units, the
# launcher unit, the users it needs and the content mount point with its
# earlier name (deploy/tmpfiles.conf). No packages or activation here.
#
#   deploy/stage.sh SRC_DIR DESTDIR
set -eu
SRC="${1:?usage: stage.sh SRC_DIR DESTDIR}"
DESTDIR="${2:?usage: stage.sh SRC_DIR DESTDIR}"
PREFIX=/opt/mun/launchd
[ -f "$SRC/launchd.py" ] || { echo "launchd.py not found in $SRC" >&2; exit 1; }

install -d "$DESTDIR$PREFIX" "$DESTDIR/usr/local/libexec" "$DESTDIR/etc/systemd/system" "$DESTDIR/usr/lib/sysusers.d" \
    "$DESTDIR/usr/lib/tmpfiles.d"
install -m 0755 "$SRC/launchd.py" "$DESTDIR$PREFIX/launchd.py"
install -m 0644 "$SRC/README.md" "$DESTDIR$PREFIX/README.md"
install -m 0755 "$SRC/deploy/mun-launch-cleanup" "$DESTDIR/usr/local/libexec/mun-launch-cleanup"
for unit in run-mun-launch.mount mun-shell-restore.service mun-launch-cleanup@.service mun-launchd.service; do
    install -m 0644 "$SRC/deploy/$unit" "$DESTDIR/etc/systemd/system/$unit"
done
install -m 0644 "$SRC/deploy/sysusers.conf" "$DESTDIR/usr/lib/sysusers.d/mun-launchd.conf"
install -m 0644 "$SRC/deploy/tmpfiles.conf" "$DESTDIR/usr/lib/tmpfiles.d/mun-launchd.conf"
{
    echo "component=mun-launchd"
    echo "source_sha256=$(cd "$SRC" && cat launchd.py deploy/* | sha256sum | cut -d' ' -f1)"
} > "$DESTDIR$PREFIX/BUILD-INFO"
