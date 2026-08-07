# Location of the Konclude checkout used to build/bundle libKonclude.so
KONCLUDE_DIR ?= ../Konclude
KONCLUDE_LIB = $(KONCLUDE_DIR)/Release-clib/libKonclude.so

.PHONY: all konclude bundle wheel test clean

all: wheel

# build Konclude as a shared library with its C interface enabled
konclude:
	cd $(KONCLUDE_DIR) && qmake -o Makefile-clib KoncludeCLIB.pro
	$(MAKE) -C $(KONCLUDE_DIR) -f Makefile-clib

# copy the Konclude shared library into the python package so that it is
# bundled into the wheel (see [tool.maturin] include in pyproject.toml)
bundle:
	mkdir -p pykonclude/lib
	cp $(KONCLUDE_LIB) pykonclude/lib/libKonclude.so

# build the wheel and vendor libKonclude.so's own shared library
# dependencies (Qt, ICU, ...) into it for manylinux compliance
wheel: bundle
	rm -rf target/wheels
	maturin build --release
	uvx auditwheel repair target/wheels/py_konclude-*.whl -w wheelhouse

test:
	KONCLUDE_LIBRARY_PATH=$(abspath $(KONCLUDE_LIB)) cargo test

clean:
	rm -rf wheelhouse target/wheels pykonclude/lib
