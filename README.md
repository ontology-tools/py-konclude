# py-konclude
A wrapper around the [Konclude](https://github.com/konclude/Konclude) OWL DL reasoner to use it in Python via [py-horned-owl](https://github.com/ontology-tools/py-horned-owl/).

This package is a thin Python-binding layer over the native
[konclude-rs](../konclude-rs) crate: `konclude-rs` does the reasoning
(loading Konclude, translating the ontology, querying results), while
py-konclude adapts its `KoncludeReasoner` to py-horned-owl's reasoner
interface and packages it as a Python wheel. To use Konclude directly from
Rust, depend on `konclude-rs` instead.

## Status: incremental direct construct and result mapping
The ontology is built directly in Konclude by mapping horned-owl components
to Konclude's ontology builder through the `konclude_kb_*` C interface
(loaded from the Konclude shared library via `dlopen`) — no input file is
written. The knowledge base is kept alive across flushes: changes (added and
removed axioms) are applied incrementally as Konclude ontology revisions, so
the translation cost of a flush is proportional to the change set, not the
ontology size. Results are mapped back directly as well: consistency is
queried as a boolean and the classified subclass hierarchy is reported
through in-memory callbacks (equivalence groups and direct subsumption
edges), from which the inferred `SubClassOf`/`EquivalentClasses` axioms are
constructed — no file exchange in either direction.

## Requirements
Konclude must be built as a shared library with its C interface enabled:

```bash
cd Konclude
qmake -o Makefile-clib KoncludeCLIB.pro
make -f Makefile-clib -j$(nproc)
# produces Release-clib/libKonclude.so
```

(or `make konclude` in this repository, with `KONCLUDE_DIR` pointing at the
Konclude checkout, default `../Konclude`).

Wheels built with `make wheel` bundle `libKonclude.so`, so nothing needs to
be installed separately at runtime. When the library is not bundled (e.g. a
plain `cargo test` or `pip install .` without bundling), point
`KONCLUDE_LIBRARY_PATH` at it (or make it resolvable through the regular
dynamic linker search path, e.g. `LD_LIBRARY_PATH`):

```bash
export KONCLUDE_LIBRARY_PATH=/path/to/Konclude/Release-clib/libKonclude.so
```

The environment variable always takes precedence over the bundled library.

## Usage
```python
from pyhornedowl import open_ontology
from pykonclude import create_reasoner

ontology = open_ontology("path/to/ontology.owl")
reasoner = create_reasoner(ontology)

reasoner.is_consistent()
reasoner.get_subclasses(ontology.clazz(":A"))
reasoner.inferred_axioms()
```

## Installation

### Building a publishable wheel
`make wheel` builds a self-contained manylinux wheel that bundles
`libKonclude.so` together with its shared library dependencies (Qt, ICU,
...). It requires [maturin](https://www.maturin.rs), `uvx` (for
[auditwheel](https://github.com/pypa/auditwheel)) and `patchelf`:

```bash
make konclude   # build libKonclude.so in $KONCLUDE_DIR (default ../Konclude)
make wheel      # bundle it and build + repair the wheel
pip install wheelhouse/py_konclude-*.whl
```

The repaired wheel in `wheelhouse/` is the artifact to upload to PyPI (e.g.
with `twine upload` or `maturin upload`).

**Note:** the reasoner plugin is loaded by py-horned-owl across a Rust ABI
boundary; the installed `py-horned-owl` wheel must be built with the same
Rust toolchain and the same `horned-owl`/`py-horned-owl-reasoner` crate
versions as this package, otherwise loading the reasoner is undefined
behaviour. Keep the py-konclude release in lockstep with the py-horned-owl
release it is built against.

### From source
Install the Python package directly with `pip install .` (the wheel then
only bundles Konclude if `pykonclude/lib/libKonclude.so` exists, see above;
otherwise set `KONCLUDE_LIBRARY_PATH` at runtime).

## Tests
```bash
KONCLUDE_LIBRARY_PATH=/path/to/libKonclude.so cargo test
```
