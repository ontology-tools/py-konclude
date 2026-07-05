import unittest

from test_base import simple_ontology
from pykonclude import create_reasoner
from pyhornedowl.model import *


class ConsistencyTestCase(unittest.TestCase):
    def test_consistency(self):
        o = simple_ontology()
        
        r = create_reasoner(o)
        
        # Check if the ontology is consistent
        self.assertTrue(r.is_consistent(), "The ontology should be consistent.")
        
    def test_simple_inconsistency(self):
        o = simple_ontology()
        o.add_axiom(SubClassOf(o.clazz("owl:Thing"), o.clazz("owl:Nothing")))
        
        r = create_reasoner(o)
        
        # Check if the ontology is consistent
        self.assertFalse(r.is_consistent(), "The ontology should be inconsistent.")
        
    def test_inconsistency(self):
        o = simple_ontology()
        o.add_axiom(SubClassOf(o.clazz("owl:Thing"), o.clazz(":C")))
        o.add_axiom(SubClassOf(o.clazz(":C"), o.clazz("owl:Nothing")))
        
        r = create_reasoner(o)
        
        # Check if the ontology is consistent
        self.assertFalse(r.is_consistent(), "The ontology should be inconsistent.")