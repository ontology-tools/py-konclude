# py-konclude
A wrapper around the [Konclude](https://github.com/konclude/Konclude) OWL DL reasoner to use it in Python via [py-horned-owl](https://github.com/ontology-tools/py-horned-owl/).

## Status: proof of concept
The current implementation is a file-based proof of concept: on every
synchronisation (`create_reasoner` / `flush`) the ontology is serialised to a
temporary OWL 2 XML file, Konclude is invoked **in-process** through its C
interface (`konclude_run`, loaded from the Konclude shared library via
`dlopen`), and the resulting subclass hierarchy is parsed back. There is no
direct mapping between horned-owl and Konclude constructs yet.

## Requirements
Konclude must be built as a shared library with its C interface enabled:

```bash
cd Konclude
qmake -o Makefile-clib KoncludeCLIB.pro
make -f Makefile-clib -j$(nproc)
# produces Release/libKonclude.so
```

At runtime, point `KONCLUDE_LIBRARY_PATH` at the shared library (or make it
resolvable through the regular dynamic linker search path, e.g.
`LD_LIBRARY_PATH`):

```bash
export KONCLUDE_LIBRARY_PATH=/path/to/Konclude/Release/libKonclude.so
```

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
Build the shared library with `cargo build --release` and install the Python
package with `pip install .`:

```bash
make
pip install .
```

## Tests
```bash
KONCLUDE_LIBRARY_PATH=/path/to/libKonclude.so cargo test
```
