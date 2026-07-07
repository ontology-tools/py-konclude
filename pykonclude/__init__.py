__version__ = "0.1.0"

#: Environment variable through which the Konclude shared library is located.
KONCLUDE_LIBRARY_ENV = "KONCLUDE_LIBRARY_PATH"


def _find_bundled_konclude():
    """Path of the Konclude shared library bundled with this package, or None."""
    from os import path, listdir

    libdir = path.join(path.dirname(__file__), "lib")
    if path.isdir(libdir):
        for name in sorted(listdir(libdir)):
            if "Konclude" in name and any(
                suffix in name for suffix in (".so", ".dylib", ".dll")
            ):
                return path.abspath(path.join(libdir, name))
    return None


def create_reasoner(ontology):
    """
    Create a reasoner instance.
    """
    from pyhornedowl.reasoning import create_reasoner
    from os import path, listdir, environ

    # point the reasoner plugin at the bundled Konclude shared library
    # unless the user overrides the location explicitly
    if KONCLUDE_LIBRARY_ENV not in environ:
        bundled = _find_bundled_konclude()
        if bundled is not None:
            environ[KONCLUDE_LIBRARY_ENV] = bundled

    dir = path.join(path.dirname(__file__), "pykonclude")
    libname = [f for f in listdir(dir) if any(f.endswith(s) for s in [".so", ".dylib", ".dll"])][0]
    libpath = path.abspath(path.join(dir, libname))

    return create_reasoner(libpath, ontology)
