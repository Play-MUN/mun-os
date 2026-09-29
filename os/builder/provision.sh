#!/bin/sh
# Prepare a fresh, disposable builder for an image build. Runs as root inside
# the builder VM, from the source tree the host copied in:
#
#   sudo os/builder/provision.sh OUT_DIR
#
# APT is pointed at the pinned Debian snapshot (os/inputs.json) and nothing
# else, the builder's installed packages are brought to that snapshot, then
# the image tools are installed from it. The result is the builder's complete
# package list in OUT_DIR/builder-packages.tsv, part of BUILD-INFO.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:?usage: provision.sh OUT_DIR}"
[ "$(id -u)" -eq 0 ] || { echo "provision.sh must run as root" >&2; exit 1; }
mkdir -p "$OUT"

echo "== apt: pinned snapshot $(python3 "$HERE/inputs.py" get debian.snapshot) only"
# The cloud image ships deb.debian.org sources; none may stay.
rm -f /etc/apt/sources.list
rm -f /etc/apt/sources.list.d/*
python3 "$HERE/inputs.py" sources > /etc/apt/sources.list.d/mun-snapshot.sources
cat > /etc/apt/apt.conf.d/80-mun-builder <<'EOF'
APT::Install-Recommends "false";
Acquire::Retries "5";
DPkg::Lock::Timeout "600";
EOF
# A builder is not a server: no background package runs may compete with the build.
systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y full-upgrade
# shellcheck disable=SC2046  # one package per line
apt-get -y install $(python3 "$HERE/inputs.py" get builder.packages)

dpkg-query -W -f '${Package}\t${Version}\t${Architecture}\n' | LC_ALL=C sort > "$OUT/builder-packages.tsv"
echo "== builder ready: mkosi $(dpkg-query -W -f '${Version}' mkosi), $(wc -l < "$OUT/builder-packages.tsv") packages, kernel $(uname -r)"
