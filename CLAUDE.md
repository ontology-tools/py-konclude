# py-konclude

Thin py-horned-owl reasoner plugin around the native `konclude-rs` crate
(git dependency; see "Working against a local konclude-rs" below).
`src/lib.rs` is only a newtype adapter
(`PyKoncludeReasoner(KoncludeReasoner)`) implementing
`PyReasoner`/`Reasoner`/`OntologyIndex` and mapping `KoncludeError` →
`ReasonerError` (the orphan rule prevents a `From` impl). **Reasoning logic
belongs in konclude-rs, not here.**

## Build & test

```bash
make bundle     # build ../Konclude as a shared lib -> pykonclude/lib/libKonclude.so
make test       # cargo test with KONCLUDE_LIBRARY_PATH set
make wheel      # bundle + maturin build + auditwheel -> wheelhouse/
```

`make bundle` (`make konclude` is an alias) delegates to
`ci/build-konclude.sh`, the same script CI runs — see "CI" below.

Python tests (`test/`, pytest, venv at `.venv`) load the plugin cdylib from
`pykonclude/pykonclude/*.so` — **after any Rust change** rebuild and copy it:

```bash
cargo build --release
cp target/release/libpykonclude.so pykonclude/pykonclude/
```

`pykonclude.create_reasoner()` points `KONCLUDE_LIBRARY_PATH` at the bundled
`pykonclude/lib/libKonclude.so` unless the env var is already set (env wins).

## Working against a local konclude-rs

`Cargo.toml` depends on `konclude-rs` **via git**, not `../konclude-rs`:
cibuildwheel builds the project in an isolated directory (a container, on
Linux), so a sibling checkout is not reachable and a path dependency breaks
every CI build at `cargo metadata`.

For co-development, override the git dependency with the local checkout via
a (gitignored) `.cargo/config.toml`:

```toml
paths = ["../konclude-rs"]
```

Verify which source is in use with:

```bash
cargo tree -i konclude-rs | head -1
# konclude-rs v0.1.0 (/home/bjoern/development/konclude-rs)          <- override active
# konclude-rs v0.1.0 (https://github.com/.../konclude-rs#63acb044)   <- git, as in CI
```

A path override only applies while the local crate's version and dependency
list match the git one — after changing konclude-rs's `Cargo.toml`, push it
and let CI resolve the new commit (`Cargo.lock` is not checked in, so CI
always takes konclude-rs `main` at build time).

## CI

`.github/workflows/CI.yaml` builds wheels with cibuildwheel for five targets
(linux x86_64/aarch64, macOS intel/arm, Windows x64); the cibuildwheel
settings themselves live in `[tool.cibuildwheel]` in `pyproject.toml`, so
`uvx cibuildwheel --print-build-identifiers` reproduces what CI selects.

- The wheel is `py3-none-<platform>`: the plugin is a cdylib loaded through
  `cffi.dlopen`, not a CPython extension module. `build = "cp312-*"` therefore
  builds **one** wheel per platform — every interpreter would emit the same
  file.
- `ci/build-konclude.sh` (`.bat` on Windows) builds `KoncludeCLIB.pro` and
  installs the result into `pykonclude/lib/`. It no-ops when the library is
  already there, which is what makes the cache and the two entry points below
  compose. Qt comes from `dnf` in the manylinux image, Homebrew on macOS, and
  `jurplel/install-qt-action` on Windows.
- macOS/Windows run that script through cibuildwheel's `before-all`. **Linux
  cannot**: `before-all` runs inside a container whose filesystem is discarded,
  so nothing would reach the cache. The workflow instead runs the script in the
  same manylinux image itself, with the workspace bind-mounted — keep
  `matrix.manylinux-image` and `[tool.cibuildwheel.linux].manylinux-*-image` in
  sync or libKonclude and the wheel get different glibcs.
- The manylinux images have no Rust toolchain (the macOS/Windows runners do),
  so `[tool.cibuildwheel.linux].before-all` installs rustup and `environment`
  puts `~/.cargo/bin` on `PATH`.
- Konclude needs **Qt 5** (5.11+, qtbase only — no Redland in the clib build).
  It will not build against Qt 6: it carries patched copies of Qt 5 container
  internals.
- The Konclude revision is pinned in the workflow's `KONCLUDE_REF`; bump it to
  pick up reasoner changes. `pykonclude/lib` is cached under that key because
  a cold Konclude build is ~1300 translation units and 20+ minutes.
- auditwheel/delocate/delvewheel vendor Qt next to the bundled library even
  though nothing links against it at build time — they scan every shared
  object in the wheel, not just extension modules. Expect ~90 MB wheels.

## Gotchas

- **Rust-ABI plugin boundary**: py-horned-owl loads the plugin through a
  `create_reasoner` symbol with a Rust (not extern "C") ABI. The host
  py-horned-owl wheel and this cdylib must be built by the **same rustc**,
  with matching `horned-owl` / `py-horned-owl-reasoner` crate versions.
  Mismatch symptoms look impossible (Ok/Err swapped, garbage strings,
  free() aborts). First check:
  `strings <lib> | grep -oE "rustc version [0-9.]+"` on both binaries.
  PyPI wheels (CI-pinned rustc) generally don't work with locally built
  plugins — build py-horned-owl locally too.
- **Stale wheels**: `target/wheels` must be cleared before a maturin build
  (the Makefile does). `uv pip install --reinstall` can hardlink a stale
  cached wheel for the same name+version even with `--no-cache` — verify
  site-packages file mtimes; if stale, uninstall and install the wheel file
  directly, or unzip it into site-packages.
- `Reasoner::inferred_axioms` has no error channel, so classification
  failures surface there as an empty result (and as errors on the other
  queries).
- Realisation produces no instances yet: only the class hierarchy comes back
  through `inferred_axioms()`.

## ORE benchmark (`ore_test/`)

Self-contained harness with its own README. Current standing vs the native
Konclude binary (200 DL consistency problems): 195/195 checkable agreement,
overall 1.75x slower (geomean 2.43x — small ontologies are dominated by
~50–150 ms Python-startup + parse overhead per subprocess). Known gaps: SWRL
`DLSafeRule` unsupported; one ontology with a non-UTF-8 IRI py-horned-owl
cannot parse.

When generating gold baselines, feed the native binary the **original**
dataset files: converting through py-horned-owl corrupts OWL/XML output (its
writer abbreviates IRIs inside the `IRI=` attribute — invalid OWL/XML, and
Konclude then silently drops datatype semantics). Also always run the native
binary with `-w AUTO` or it deadlocks.
