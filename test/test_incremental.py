import unittest

from test_base import simple_ontology
from pykonclude import create_reasoner
from pyhornedowl.model import *


class IncrementalTestCase(unittest.TestCase):
    def test_incremental(self):
        o = simple_ontology()
        
        r = create_reasoner(o)
        
        o.add_axiom(SubClassOf(o.clazz("owl:Thing"), o.clazz("owl:Nothing")))
        
        # Should be consistent until changes are flushed
        self.assertTrue(r.is_consistent())
        
        r.flush()
        
        self.assertFalse(r.is_consistent())