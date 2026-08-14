"""How the package locates the Konclude shared library.

This is the part of py-konclude that is not a thin pass-through to the native
reasoner, and the part that produces the confusing failures when a package is
built without the bundled library.
"""

import os
import unittest

import pykonclude
from test_base import simple_ontology


class BundledLibraryTestCase(unittest.TestCase):
    def test_bundled_library_is_found(self):
        """A wheel-installed package carries the library it dlopens."""
        bundled = pykonclude._find_bundled_konclude()

        if bundled is None:
            self.skipTest("no bundled libKonclude (source build; "
                          f"{pykonclude.KONCLUDE_LIBRARY_ENV} must be set)")
        self.assertTrue(os.path.isfile(bundled))
        self.assertIn("Konclude", os.path.basename(bundled))

    def test_plugin_is_found(self):
        plugin = pykonclude._find_plugin()

        self.assertTrue(os.path.isfile(plugin))
        self.assertTrue(plugin.endswith((".so", ".dylib", ".dll")))

    def test_bundled_library_is_not_the_plugin(self):
        """The two libraries are distinct; conflating them breaks the dlopen."""
        bundled = pykonclude._find_bundled_konclude()

        if bundled is None:
            self.skipTest("no bundled libKonclude")
        self.assertNotEqual(bundled, pykonclude._find_plugin())


class EnvironmentOverrideTestCase(unittest.TestCase):
    def setUp(self):
        self.env = pykonclude.KONCLUDE_LIBRARY_ENV
        self.saved = os.environ.get(self.env)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop(self.env, None)
        else:
            os.environ[self.env] = self.saved

    def test_explicit_path_is_left_alone(self):
        """An explicit setting wins over the bundled library, as documented.

        Only the resolution is checked, not the load: the native library is
        dlopened once per process, so by the time this runs alongside the other
        tests the path in the environment is no longer consulted.
        """
        os.environ[self.env] = "/nonexistent/libKonclude.so"

        pykonclude.create_reasoner(simple_ontology())

        self.assertEqual(os.environ[self.env], "/nonexistent/libKonclude.so")

    def test_unset_falls_back_to_bundled(self):
        if pykonclude._find_bundled_konclude() is None:
            self.skipTest("no bundled libKonclude")
        os.environ.pop(self.env, None)

        pykonclude.create_reasoner(simple_ontology())

        self.assertEqual(os.environ[self.env],
                         pykonclude._find_bundled_konclude())


if __name__ == "__main__":
    unittest.main()
