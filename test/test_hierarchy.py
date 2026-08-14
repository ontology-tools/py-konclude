"""Soundness checks for the hierarchy queries the plugin adapts.

Konclude's reasoning is tested in its own repository and in konclude-rs; what
is worth covering here is that each trait method on the adapter reaches it and
returns the right shape of answer across the plugin ABI boundary.
"""

import unittest

from test_base import simple_ontology
from pykonclude import create_reasoner
from pyhornedowl.model import *


class SuperclassTestCase(unittest.TestCase):
    def test_superclasses(self):
        o = simple_ontology()

        r = create_reasoner(o)

        # :D <= :B <= :A, so everything above :D comes back, and :C -- which
        # sits in an unrelated branch -- does not
        actual = r.get_superclasses(o.class_(":D"))

        self.assertIn(o.class_(":A"), actual)
        self.assertIn(o.class_(":B"), actual)
        self.assertIn(o.class_(":D"), actual)
        self.assertNotIn(o.class_(":C"), actual)

    def test_superclasses_dual_to_subclasses(self):
        """``B`` is a superclass of ``A`` exactly when ``A`` is a subclass of ``B``."""
        o = simple_ontology()

        r = create_reasoner(o)

        for name in (":A", ":B", ":C", ":D"):
            for sup in r.get_superclasses(o.class_(name)):
                self.assertIn(o.class_(name), r.get_subclasses(sup),
                              f"{name} <= {sup} but not reported the other way")


class EquivalentClassesTestCase(unittest.TestCase):
    def test_equivalence_is_reflexive(self):
        o = simple_ontology()

        r = create_reasoner(o)

        self.assertIn(o.class_(":A"), r.get_equivalent_classes(o.class_(":A")))

    def test_asserted_equivalence_is_found(self):
        o = simple_ontology()
        o.add_axiom(EquivalentClasses([o.class_(":B"), o.class_(":C")]))

        r = create_reasoner(o)

        actual = r.get_equivalent_classes(o.class_(":B"))
        self.assertIn(o.class_(":C"), actual)
        self.assertNotIn(o.class_(":A"), actual)


class InferredAxiomsTestCase(unittest.TestCase):
    def _subsumptions(self, reasoner):
        return {(str(a.sub), str(a.sup))
                for a in reasoner.inferred_axioms() if isinstance(a, SubClassOf)}

    def test_inferred_axioms_are_returned(self):
        o = simple_ontology()

        r = create_reasoner(o)

        self.assertTrue(list(r.inferred_axioms()),
                        "classification should yield inferred axioms")

    def test_inferred_axioms_are_the_transitive_reduction(self):
        """Only *direct* subsumption edges come back, per the documented contract.

        ``:D <= :A`` is entailed through ``:B`` but is not a direct edge, so a
        reasoner reporting it would mean the hierarchy is no longer reduced.
        """
        o = simple_ontology()

        r = create_reasoner(o)
        subsumptions = self._subsumptions(r)

        A, B, D = (str(o.class_(x)) for x in (":A", ":B", ":D"))
        self.assertIn((B, A), subsumptions)
        self.assertIn((D, B), subsumptions)
        self.assertNotIn((D, A), subsumptions)

    def test_inferred_axioms_track_flush(self):
        """A change is reflected only after ``flush``, and then it is."""
        o = simple_ontology()

        r = create_reasoner(o)
        before = self._subsumptions(r)

        o.add_axiom(SubClassOf(o.class_(":C"), o.class_(":A")))
        self.assertEqual(before, self._subsumptions(r))

        r.flush()
        after = self._subsumptions(r)

        self.assertIn((str(o.class_(":C")), str(o.class_(":A"))), after)

    def test_inferred_axioms_are_stable(self):
        """Two calls without an intervening change agree.

        The result is a set, so only its contents are meaningful -- iteration
        order differs between two calls even on one classification.
        """
        o = simple_ontology()

        r = create_reasoner(o)

        self.assertSetEqual(r.inferred_axioms(), r.inferred_axioms())


if __name__ == "__main__":
    unittest.main()
