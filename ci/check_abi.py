#!/usr/bin/env python3
"""Verify that the reasoner plugin and py-horned-owl share a Rust ABI.

py-horned-owl loads this package's cdylib through a ``create_reasoner`` symbol
with a *Rust* (not ``extern "C"``) ABI. That ABI is not stable across compiler
releases, so the two binaries must be built by the same rustc. A mismatch does
not fail to load -- it corrupts results in ways that look impossible (``Ok``
and ``Err`` swapped, garbage strings, ``free()`` aborts), so it has to be
caught mechanically rather than by watching the test suite.

Both binaries carry their compiler version as a literal ``rustc version X.Y.Z``
string; this compares them. Run after installing the wheel::

    python ci/check_abi.py

Exits non-zero on a mismatch, or when either version string cannot be found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional

#: Extension-module / cdylib suffixes across the platforms we build for.
SUFFIXES = (".so", ".dylib", ".dll", ".pyd")

_RUSTC = re.compile(rb"rustc version (\d+\.\d+\.\d+)")


def rustc_version(binary: Path) -> Optional[str]:
    """The rustc version stamped into ``binary``, or None if absent.

    Several versions can appear when dependencies were built by different
    compilers; that itself is a mismatch, so return them all joined and let the
    comparison fail.
    """
    found = sorted({m.group(1).decode() for m in _RUSTC.finditer(binary.read_bytes())})
    return "+".join(found) if found else None


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

    versions = {}
    for label, lib in (("py-horned-owl", host), ("py-konclude", plugin)):
        version = rustc_version(lib)
        if version is None:
            print(f"error: no rustc version string in {lib}", file=sys.stderr)
            return 1
        versions[label] = version
        print(f"{label:14} rustc {version}  ({lib})")

    if len(set(versions.values())) > 1:
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
