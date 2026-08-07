# py-konclude

Thin py-horned-owl reasoner plugin around the native `konclude-rs` crate
(path dependency, `../konclude-rs`). `src/lib.rs` is only a newtype adapter
(`PyKoncludeReasoner(KoncludeReasoner)`) implementing
`PyReasoner`/`Reasoner`/`OntologyIndex` and mapping `KoncludeError` →
`ReasonerError` (the orphan rule prevents a `From` impl). **Reasoning logic
belongs in konclude-rs, not here.**

## Build & test

```bash
make konclude   # build ../Konclude as shared lib (Release/libKonclude.so)
make test       # cargo test with KONCLUDE_LIBRARY_PATH set
make wheel      # bundle libKonclude.so + maturin build + auditwheel -> wheelhouse/
```

Python tests (`test/`, pytest, venv at `.venv`) load the plugin cdylib from
`pykonclude/pykonclude/*.so` — **after any Rust change** rebuild and copy it:

```bash
cargo build --release
cp target/release/libpykonclude.so pykonclude/pykonclude/
```

`pykonclude.create_reasoner()` points `KONCLUDE_LIBRARY_PATH` at the bundled
`pykonclude/lib/libKonclude.so` unless the env var is already set (env wins).

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
