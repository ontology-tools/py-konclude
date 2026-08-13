#!/usr/bin/env bash
#
# Build Konclude as a shared library with its C interface enabled and install
# the result into pykonclude/lib/, where pyproject.toml's [tool.maturin]
# include picks it up and bundles it into the wheel.
#
# Invoked by cibuildwheel as `before-all` (see [tool.cibuildwheel] in
# pyproject.toml). On Linux that happens *inside* the manylinux container, so
# that libKonclude and the Qt libraries auditwheel vendors alongside it match
# the glibc the wheel is tagged for. Windows has its own script.
#
# Overridable: KONCLUDE_DIR (default ./Konclude), QMAKE.

set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
konclude_dir=${KONCLUDE_DIR:-$project_root/Konclude}
dest_dir=$project_root/pykonclude/lib

case $(uname -s) in
    Darwin)
        lib_name=libKonclude.dylib
        jobs=$(sysctl -n hw.ncpu)
        ;;
    *)
        lib_name=libKonclude.so
        jobs=$(nproc)
        ;;
esac

# The workflow caches pykonclude/lib keyed on the Konclude revision -- a cold
# build is ~1300 translation units and takes upwards of 20 minutes, so a cache
# hit should short-circuit the whole script.
if [ -f "$dest_dir/$lib_name" ]; then
    echo "$dest_dir/$lib_name already present -- skipping Konclude build"
    exit 0
fi

if [ ! -f "$konclude_dir/KoncludeCLIB.pro" ]; then
    echo "error: no Konclude checkout at $konclude_dir" >&2
    exit 1
fi

# --- Qt 5 ------------------------------------------------------------------
# Konclude needs Qt 5.11+ (core, xml, network, concurrent -- all of qtbase).
# It does *not* build against Qt 6: it carries patched copies of Qt 5's
# container internals (Source/Utilities/Container/CQtManagedRestricted*).

find_qmake() {
    local candidate
    for candidate in \
        "${QMAKE:-}" \
        qmake-qt5 \
        /usr/lib64/qt5/bin/qmake \
        "$(brew --prefix qt@5 2>/dev/null || true)/bin/qmake" \
        qmake
    do
        [ -n "$candidate" ] || continue
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

install_qt5() {
    if command -v dnf >/dev/null 2>&1; then
        # manylinux_2_28 is AlmaLinux 8; qtbase lives in AppStream
        dnf -y install qt5-qtbase-devel
    elif command -v yum >/dev/null 2>&1; then
        yum -y install qt5-qtbase-devel
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y qtbase5-dev
    elif command -v brew >/dev/null 2>&1; then
        brew install qt@5
    else
        echo "error: no known package manager to install Qt 5 with" >&2
        return 1
    fi
}

qmake_bin=$(find_qmake || true)
if [ -z "$qmake_bin" ]; then
    echo "--- no qmake found, installing Qt 5"
    install_qt5
    qmake_bin=$(find_qmake) || {
        echo "error: Qt 5 installed but qmake still not on PATH" >&2
        exit 1
    }
fi

qt_version=$("$qmake_bin" -query QT_VERSION)
echo "--- using $qmake_bin (Qt $qt_version)"
case $qt_version in
    5.*) ;;
    *)
        echo "error: Konclude requires Qt 5, found Qt $qt_version at $qmake_bin" >&2
        exit 1
        ;;
esac

# --- build -----------------------------------------------------------------

cd "$konclude_dir"
"$qmake_bin" -o Makefile-clib KoncludeCLIB.pro
make -f Makefile-clib -j"$jobs"

# --- install ---------------------------------------------------------------
# qmake emits the real library as libKonclude.so.1.0.0 (libKonclude.1.0.0.dylib
# on macOS) behind a chain of version symlinks. konclude-rs dlopens the
# unversioned name (libloading::library_filename("Konclude")), so copy the
# resolved file -- `cp -L` -- into place under that name.

mkdir -p "$dest_dir"
cp -L "Release-clib/$lib_name" "$dest_dir/$lib_name"

echo "--- installed $dest_dir/$lib_name"
ls -l "$dest_dir/$lib_name"
