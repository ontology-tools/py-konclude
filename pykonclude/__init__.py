from importlib.metadata import PackageNotFoundError, version as _version

try:
    #: Single-sourced from the installed distribution metadata, which maturin
    #: fills in from pyproject.toml -- so there is one version to bump.
    __version__ = _version("py-konclude")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "unknown"

#: Environment variable through which the Konclude shared library is located.
KONCLUDE_LIBRARY_ENV = "KONCLUDE_LIBRARY_PATH"

#: Shared-library suffixes across the platforms wheels are built for.
_LIB_SUFFIXES = (".so", ".dylib", ".dll")


def _find_bundled_konclude():
    """Path of the Konclude shared library bundled with this package, or None."""
    from os import path, listdir

    libdir = path.join(path.dirname(__file__), "lib")
    if path.isdir(libdir):
        for name in sorted(listdir(libdir)):
            if "Konclude" in name and any(s in name for s in _LIB_SUFFIXES):
                return path.abspath(path.join(libdir, name))
    return None


def _find_plugin():
    """Path of the reasoner plugin cdylib, raising if it was never built."""
    from os import path, listdir

    plugin_dir = path.join(path.dirname(__file__), "pykonclude")
    names = sorted(
        f for f in listdir(plugin_dir) if f.endswith(_LIB_SUFFIXES)
    ) if path.isdir(plugin_dir) else []
    if not names:
        raise RuntimeError(
            f"py-konclude is installed without its reasoner plugin: no shared "
            f"library in {plugin_dir}. This happens when the package was built "
            f"from a source tree that had not been compiled; install one of the "
            f"published wheels, or build with `make wheel`."
        )
    return path.abspath(path.join(plugin_dir, names[0]))


def _add_vendored_dlls_to_path():
    """Put the wheel's vendored DLLs on the Windows loader's search path.

    delvewheel repairs the wheel by name-mangling Qt and the MSVC runtime into
    a sibling ``py_konclude.libs/`` and patching the import tables of both DLLs
    to match. Its own loader patch calls ``os.add_dll_directory``, which only
    covers loads that pass ``LOAD_LIBRARY_SEARCH_*``; the plugin and
    libKonclude are loaded through ``libloading`` with no flags, whose standard
    search order does read PATH. No-op everywhere else.
    """
    from os import environ, name, path, pathsep

    if name != "nt":
        return
    libs = path.join(path.dirname(path.dirname(path.abspath(__file__))),
                     "py_konclude.libs")
    if path.isdir(libs) and libs not in environ.get("PATH", "").split(pathsep):
        environ["PATH"] = libs + pathsep + environ.get("PATH", "")


def create_reasoner(ontology):
    """
    Create a reasoner instance.
    """
    from pyhornedowl.reasoning import create_reasoner
    from os import environ

    # point the reasoner plugin at the bundled Konclude shared library
    # unless the user overrides the location explicitly
    if KONCLUDE_LIBRARY_ENV not in environ:
        bundled = _find_bundled_konclude()
        if bundled is None:
            raise RuntimeError(
                f"no Konclude shared library found. Wheels from PyPI bundle one; "
                f"a package built from source (e.g. `pip install .` or an sdist) "
                f"only does when Konclude was built into pykonclude/lib first -- "
                f"see `make bundle`. Otherwise build Konclude as a shared library "
                f"and point ${KONCLUDE_LIBRARY_ENV} at it."
            )
        environ[KONCLUDE_LIBRARY_ENV] = bundled

    _add_vendored_dlls_to_path()
    return create_reasoner(_find_plugin(), ontology)
