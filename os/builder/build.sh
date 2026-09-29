#!/bin/sh
# Build a MUN OS image inside a provisioned, disposable builder. Runs as root:
#
#   sudo os/builder/build.sh --profile qemu-dev --out OUT_DIR --source-info FILE [--recipes DIR]
#
# FILE is the host's description of the source tree it copied in (commit,
# clean status, archive hash). DIR holds game recipes the host copied in
# (os/builder/recipes.py): games this repository does not carry, compiled
# beside the image. The build works on a copy of os/mkosi and writes the
# image, the games built beside it, BUILD-INFO.json and SHA256SUMS to
# OUT_DIR. Nothing here reads the host or a previous build.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../.." && pwd)"
PROFILE=qemu-dev
OUT=
SOURCE_INFO=
RECIPES=
while [ $# -gt 0 ]; do
    case "$1" in
        --profile) PROFILE="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --source-info) SOURCE_INFO="$2"; shift 2 ;;
        --recipes) RECIPES="$2"; shift 2 ;;
        *) echo "build.sh: unknown argument $1" >&2; exit 64 ;;
    esac
done
[ -n "$OUT" ] && [ -f "$SOURCE_INFO" ] || { echo "usage: build.sh --profile P --out DIR --source-info FILE [--recipes DIR]" >&2; exit 64; }
[ -d "$SRC/os/mkosi/mkosi.profiles/$PROFILE" ] || { echo "build.sh: no profile $PROFILE" >&2; exit 64; }
[ "$(id -u)" -eq 0 ] || { echo "build.sh must run as root" >&2; exit 1; }

inputs() { python3 "$HERE/inputs.py" get "$1"; }
VERSION="$(inputs version)"
COMMIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$SOURCE_INFO")"
COMMIT_TIME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit_time"])' "$SOURCE_INFO")"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(printf %.12s "$COMMIT")"

WORK=/srv/mun/work
rm -rf "$WORK"
mkdir -p "$WORK/inputs" "$WORK/builddir" "$WORK/ws" "$WORK/pkgcache" "$WORK/image" "$OUT/logs" "$OUT/games"
cp -a "$SRC/os/mkosi" "$WORK/mkosi"
mkdir -p "$WORK/mkosi/mkosi.sandbox/etc/apt/sources.list.d" "$WORK/mkosi/mkosi.sandbox/etc/apt/apt.conf.d"
python3 "$HERE/inputs.py" sources > "$WORK/mkosi/mkosi.sandbox/etc/apt/sources.list.d/mkosi.sources"
printf 'Acquire::Retries "5";\n' > "$WORK/mkosi/mkosi.sandbox/etc/apt/apt.conf.d/80-mun"

SOURCES="$SRC:mun"
WITH_RECIPES=0
if [ -n "$RECIPES" ]; then
    # Each recipe's sources at their pinned commits, its patches checked and
    # applied; the build step compiles them (mkosi.build.chroot).
    python3 "$HERE/recipes.py" fetch "$RECIPES" "$WORK/recipes"
    SOURCES="$SOURCES,$WORK/recipes:recipes"
    WITH_RECIPES=1
fi

echo "== mkosi $(mkosi --version 2>/dev/null | awk '{print $2}') profile $PROFILE, build $BUILD_ID"
cd "$WORK/mkosi"
# A pipeline loses the first command's status in sh: keep it in a file.
set +e
{ mkosi --profile "$PROFILE" \
    --image-version "$VERSION" \
    --output-directory "$WORK/image" \
    --build-directory "$WORK/builddir" \
    --workspace-directory "$WORK/ws" \
    --package-cache-directory "$WORK/pkgcache" \
    --build-sources "$SOURCES" \
    --source-date-epoch "$COMMIT_TIME" \
    --environment "MUN_PROFILE=$PROFILE" \
    --environment "MUN_BUILD_ID=$BUILD_ID" \
    --environment "MUN_SOURCE_COMMIT=$COMMIT" \
    --environment "MUN_SNAPSHOT=$(inputs debian.snapshot)" \
    --environment "MUN_WITH_RECIPES=$WITH_RECIPES" \
    --force build 2>&1; echo "$?" > "$WORK/mkosi.status"; } | tee "$OUT/logs/mkosi.log"
set -e
[ "$(cat "$WORK/mkosi.status")" = 0 ] || { echo "mkosi failed (exit $(cat "$WORK/mkosi.status")); see logs/mkosi.log" >&2; exit 1; }
[ -f "$WORK/image/mun-os.raw" ] || { echo "mkosi produced no image; see logs/mkosi.log" >&2; exit 1; }

echo "== artifacts"
ENVIRONMENT="$(sed -n 's/^ *MUN_ENVIRONMENT=//p' "$SRC/os/mkosi/mkosi.profiles/$PROFILE/mkosi.conf")"
IMAGE="mun-os-$VERSION-${ENVIRONMENT:-$PROFILE}.qcow2"
qemu-img convert -O qcow2 -c -o compression_type=zstd "$WORK/image/mun-os.raw" "$OUT/$IMAGE"
cp "$WORK/image/mun-os.manifest" "$OUT/manifest.json"
cp "$WORK/image/initrd.manifest" "$OUT/initrd-manifest.json"
# mkosi gives each image its own subdirectory of the build directory.
ARTIFACTS="$(find "$WORK/builddir" -mindepth 1 -maxdepth 2 -type d -name artifacts | head -n 1)"
[ -n "$ARTIFACTS" ] || { echo "the build step left no artifacts" >&2; exit 1; }
cp -a "$ARTIFACTS/games/." "$OUT/games/"
[ ! -f "$ARTIFACTS/recipes.json" ] || cp "$ARTIFACTS/recipes.json" "$OUT/recipes.json"
python3 "$HERE/build_info.py" \
    --inputs "$SRC/os/inputs.json" --source-info "$SOURCE_INFO" --profile "$PROFILE" \
    --build-id "$BUILD_ID" --started "$STARTED" --image "$OUT/$IMAGE" --raw "$WORK/image/mun-os.raw" \
    --manifest "$OUT/manifest.json" --initrd-manifest "$OUT/initrd-manifest.json" \
    --initrd "$(readlink -f "$WORK/image/initrd")" --builder-packages "$OUT/builder-packages.tsv" \
    --toolchain "$ARTIFACTS/toolchain.tsv" --config "$WORK/mkosi" --out "$OUT" \
    > "$OUT/BUILD-INFO.json"
(cd "$OUT" && find . -type f ! -name SHA256SUMS ! -path './logs/*' | LC_ALL=C sort | sed 's|^\./||' | xargs sha256sum > SHA256SUMS)
echo "== done: $OUT/$IMAGE ($(du -h "$OUT/$IMAGE" | cut -f1)), build $BUILD_ID"
