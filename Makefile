# Location of the Konclude checkout used to build/bundle libKonclude.so.
# Default: ./Konclude inside this repository (clone or symlink it there, as CI
# does) -- same default as ci/build-konclude.{sh,bat}. Override to build from a
# checkout elsewhere: make KONCLUDE_DIR=/path/to/Konclude bundle
KONCLUDE_DIR ?= Konclude

# Shared-library naming differs per platform, and ci/build-konclude.sh installs
# under the platform name; keep the two in step or `bundle` silently no-ops on
# a stale library and `test` loads the wrong one.
ifeq ($(shell uname -s),Darwin)
KONCLUDE_LIB_NAME = libKonclude.dylib
else
KONCLUDE_LIB_NAME = libKonclude.so
endif

# The library `make test` loads: the bundled one produced by `make bundle`
# whenever it is there, otherwise the in-place build in the checkout.
BUNDLED_LIB = pykonclude/lib/$(KONCLUDE_LIB_NAME)
KONCLUDE_LIB ?= $(if $(wildcard $(BUNDLED_LIB)),$(BUNDLED_LIB),$(KONCLUDE_DIR)/Release-clib/$(KONCLUDE_LIB_NAME))

# Where the python tests load the plugin cdylib from, and what builds it.
PLUGIN_DIR = pykonclude/pykonclude
PLUGIN_LIB = target/release/libpykonclude.so
PYTEST ?= $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,pytest)

.PHONY: all konclude bundle plugin wheel test clean

all: wheel

# build Konclude as a shared library with its C interface enabled and install
# it into pykonclude/lib/, so that it is bundled into the wheel (see the
# [tool.maturin] include in pyproject.toml). This is the same script CI runs
# through cibuildwheel's before-all, so local and CI builds cannot drift.
#
# The script no-ops when the library is already installed (CI restores it from
# a cache); drop it first so that `make bundle` after a Konclude source change
# does rebuild. The underlying make is incremental, so this stays cheap.
bundle:
	rm -f $(BUNDLED_LIB)
	KONCLUDE_DIR=$(abspath $(KONCLUDE_DIR)) ci/build-konclude.sh

konclude: bundle

# build the reasoner plugin and put it where pykonclude/__init__.py looks for
# it. Required after every Rust change; the python tests load this copy, not
# anything under target/.
plugin:
	cargo build --release
	mkdir -p $(PLUGIN_DIR)
	cp $(PLUGIN_LIB) $(PLUGIN_DIR)/

# build the wheel and vendor libKonclude.so's own shared library
# dependencies (Qt, ICU, ...) into it for manylinux compliance
wheel: bundle
	rm -rf target/wheels
	maturin build --release
	uvx auditwheel repair target/wheels/py_konclude-*.whl -w wheelhouse

# the python suite is the whole test suite: this crate is a trait adapter with
# no logic of its own, and the reasoning it delegates to is tested in
# konclude-rs. Exercising it through py-horned-owl is what actually covers the
# plugin ABI boundary.
test: bundle plugin
	KONCLUDE_LIBRARY_PATH=$(abspath $(KONCLUDE_LIB)) $(PYTEST) test/

clean:
	rm -rf wheelhouse target/wheels pykonclude/lib $(PLUGIN_DIR)
