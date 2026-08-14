#!/usr/bin/env python3
"""Verify that the reasoner plugin and py-horned-owl share a Rust ABI.

py-horned-owl loads this package's cdylib through a ``create_reasoner`` symbol
with a *Rust* (not ``extern "C"``) ABI. That ABI is not stable across compiler
releases, so the two binaries must be built by the same rustc. A mismatch does
not fail to load -- it corrupts results in ways that look impossible (``Ok``
and ``Err`` swapped, garbage strings, ``free()`` aborts), so it has to be
caught mechanically rather than by watching the test suite.

Both binaries carry the commit hash of the rustc that built them, in the
``/rustc/<hash>/library/...`` paths std's panic locations are remapped to; that
is what this compares. The human-readable ``rustc version X.Y.Z`` string is
only emitted into the ELF ``.comment`` section, so it is reported when present
(Linux) and left out otherwise (macOS, Windows). Run after installing the
wheel::

    python ci/check_abi.py

Exits non-zero on a mismatch, or when either commit hash cannot be found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional

#: Extension-module / cdylib suffixes across the platforms we build for.
SUFFIXES = (".so", ".dylib", ".dll", ".pyd")

_RUSTC_HASH = re.compile(rb"/rustc/([0-9a-f]{40})")
_RUSTC_VERSION = re.compile(rb"rustc version (\d+\.\d+\.\d+)")


def rustc_build(binary: Path) -> Optional[str]:
    """The rustc build that produced ``binary``, or None if it carries no hash.

    Several can appear when dependencies were built by different compilers;
    that itself is a mismatch, so return them all joined and let the comparison
    fail.
    """
    blob = binary.read_bytes()
    hashes = sorted({m.group(1).decode() for m in _RUSTC_HASH.finditer(blob)})
    if not hashes:
        return None
    versions = sorted({m.group(1).decode() for m in _RUSTC_VERSION.finditer(blob)})
    # the version reads better where it exists; the hash is the actual identity
    short = "+".join(h[:9] for h in hashes)
    return f"{'+'.join(versions)} ({short})" if versions else short


def find_library(package: str) -> Path:
    """The Rust library inside an installed ``package``.

    Searched recursively: py-horned-owl keeps its extension module at the
    package root, this package nests the plugin in ``pykonclude/pykonclude/``.
    The name must contain the package name, which is what separates the Rust
    plugin (``libpykonclude.so``) from the bundled C++ ``libKonclude.so`` next
    to it.
    """
    try:
        module = __import__(package)
    except ImportError as e:
        raise SystemExit(f"error: {package} is not installed ({e})")

    root = Path(module.__file__).parent
    candidates: List[Path] = sorted(
        p for p in root.rglob("*")
        if p.suffix in SUFFIXES and package in p.name and p.is_file()
    )
    if not candidates:
        raise SystemExit(f"error: no {package} native library under {root}")
    return candidates[0]


def main() -> int:
    host = find_library("pyhornedowl")
    plugin = find_library("pykonclude")

    builds = {}
    for label, lib in (("py-horned-owl", host), ("py-konclude", plugin)):
        build = rustc_build(lib)
        if build is None:
            print(f"error: no rustc commit hash in {lib}", file=sys.stderr)
            return 1
        builds[label] = build
        print(f"{label:14} rustc {build}  ({lib})")

    if len(set(builds.values())) > 1:
        print(
            "\nerror: Rust ABI mismatch -- the plugin and py-horned-owl were "
            "built by different compilers.\nLoading the reasoner is undefined "
            "behaviour; rebuild both with the same rustc (see rust-toolchain.toml).",
            file=sys.stderr,
        )
        return 1

    print("\nok: both built by the same rustc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
