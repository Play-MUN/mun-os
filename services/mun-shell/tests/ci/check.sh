#!/bin/sh
# MUN Shell built and exercised as the image builds it, for pull requests and
# for anyone with tests/ci/setup.sh's packages (or its Dockerfile):
#
#   sh services/mun-shell/tests/ci/check.sh    # from the repository's root
#
# 1. Every tool and library it needs is there, or it stops and names what is
#    missing: here a missing Qt is a failure, never a skipped test.
# 2. The host tests of tests/Makefile, the sound worker's exit test under
#    AddressSanitizer included (MUN_REQUIRE_QT=1 makes its skip a failure).
# 3. The shell compiled by deploy/build.sh, as the image build compiles it,
#    and the hardening its executable must carry (tests/ci/hardening_check.py).
# 4. qmllint over its QML, failing on a warning not already known
#    (tests/ci/qmllint_check.py).
# 5. Its behaviour harness on the real binary: the settings file and Home,
#    which runs Home's three ways (the previous design on the software
#    renderer, the orbital menu on it and on OpenGL through Xvfb). Durations
#    are recorded, not judged: a shared runner is no measurement
#    (docs/releasing.md).
#
# WORK (default: a new temporary directory) keeps the build and the
# harness's output.
set -eu
[ -f services/mun-shell/CMakeLists.txt ] || { echo "run from the repository's root" >&2; exit 1; }
missing=""
for tool in cmake ninja g++ make pkg-config python3 node Xvfb readelf; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
for module in Qt6Core Qt6Qml Qt6Quick alsa; do
    pkg-config --exists "$module" 2>/dev/null || missing="$missing $module(pkg-config)"
done
for tool in moc qmllint; do
    [ -x "/usr/lib/qt6/libexec/$tool" ] || [ -x "/usr/lib/qt6/bin/$tool" ] || command -v "$tool" >/dev/null 2>&1 \
        || missing="$missing $tool"
done
if [ -n "$missing" ]; then
    echo "missing:$missing (services/mun-shell/tests/ci/setup.sh installs them)" >&2
    exit 1
fi
WORK="${WORK:-$(mktemp -d)}"
echo "== host tests (MUN_REQUIRE_QT=1)"
make -s -C services/mun-shell/tests test MUN_REQUIRE_QT=1
echo "== build (deploy/build.sh)"
sh services/mun-shell/deploy/build.sh "$PWD/services/mun-shell" "$WORK/shell" ci
python3 services/mun-shell/tests/ci/hardening_check.py "$WORK/shell/mun-shell" --build-dir "$WORK/shell"
echo "== qmllint, against the warnings already known (tests/ci/qmllint-baseline.txt)"
cmake --build "$WORK/shell" --target all_qmllint > "$WORK/qmllint.log" 2>&1 || { cat "$WORK/qmllint.log" >&2; exit 1; }
python3 services/mun-shell/tests/ci/qmllint_check.py "$WORK/qmllint.log" services/mun-shell/tests/ci/qmllint-baseline.txt
echo "== behaviour: settings, home"
python3 services/mun-shell/tests/behaviour.py "$WORK/shell/mun-shell" --work "$WORK/behaviour" \
    --only settings --only home --timing record
