# py-konclude
A wrapper around the [Konclude](https://github.com/konclude/Konclude) OWL DL reasoner to use it in Python via [py-horned-owl](https://github.com/ontology-tools/py-horned-owl/).

This package is a thin Python-binding layer over the native
[konclude-rs](https://github.com/ontology-tools/konclude-rs) crate: `konclude-rs` does the reasoning
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

## Known issues
Two correctness caveats inherited from Konclude itself affect this package:
unqualified data cardinality restrictions (`DataMaxCardinality(1 :p)` and
friends, written without a data range) are silently ignored and can make an
inconsistent ontology report as consistent, and classification is
nondeterministic on some ABox-bearing ontologies — repeated runs over the same
unchanged input can return contradictory hierarchies. Both are documented in
full, with reproductions, under [Known issues in
konclude-rs](https://github.com/ontology-tools/konclude-rs#known-issues).

## Requirements
Konclude must be built as a shared library with its C interface enabled.
Clone (or symlink) it as `Konclude/` inside this repository — the location
`make` and the CI build script both expect — and build it with `make
konclude`:

```bash
git clone https://github.com/ontology-tools/Konclude
# or, against an existing checkout elsewhere: ln -s /path/to/Konclude Konclude
make konclude
```

Note the repository: `ontology-tools/Konclude` is the fork that adds the
`KoncludeCLIB.pro` target — the C interface this package loads through
`dlopen`. Upstream `konclude/Konclude` does not ship that target, so a
checkout of it will not build here.

`Konclude/` is gitignored, so either form leaves the working tree clean. To
build from a checkout in another location without a symlink, point
`KONCLUDE_DIR` at it (`make KONCLUDE_DIR=/path/to/Konclude konclude`).

The equivalent by hand, if you would rather not go through `make`:

```bash
cd Konclude
qmake -o Makefile-clib KoncludeCLIB.pro
make -f Makefile-clib -j$(nproc)
# produces Release-clib/libKonclude.so
```

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
make konclude   # build libKonclude.so in $KONCLUDE_DIR (default ./Konclude)
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
Install the Python package directly with `pip install .`. This needs a Rust
toolchain, and it bundles Konclude only if `pykonclude/lib/libKonclude.so`
already exists (run `make bundle` first, see above). Without it the package
installs fine but `create_reasoner` refuses to run until
`KONCLUDE_LIBRARY_PATH` points at a Konclude shared library.

The same applies to the sdist on PyPI: it carries no Konclude, because
building one needs Qt 5 and the Konclude sources, neither of which can be
shipped usefully in a source distribution. **On any platform we publish a
wheel for, install the wheel** — the sdist is there for redistribution, not
for `pip install py-konclude`.

## Tests
```bash
make test     # builds Konclude + the plugin, then runs the pytest suite
```

The tests exercise the reasoner through py-horned-owl, which is the only way
to cover the plugin ABI boundary; there are no Rust unit tests (the reasoning
itself is tested in [konclude-rs](https://github.com/ontology-tools/konclude-rs)).
