import unittest

from test_base import simple_ontology
from pykonclude import create_reasoner
from pyhornedowl.model import *


class EntailmentTestCase(unittest.TestCase):
    def test_simple_entailment(self):
        o = simple_ontology()
        [A, B, C, D] = [o.clazz(f":{x}") for x in ["A", "B", "C", "D"]]
        
        r = create_reasoner(o)
        
        self.assertTrue(r.is_entailed(SubClassOf(D, A)))
        
    def test_role_entailment(self):
        o = simple_ontology()
        
        o.declare_class(":R_A")
        o.declare_class(":R_B")
        o.declare_class(":R_C")
        o.declare_object_property(":R_r")
        o.declare_object_property(":R_s")
        
        A = o.clazz(":R_A")
        B = o.clazz(":R_B")
        C = o.clazz(":R_C")
        r = o.object_property(":R_r")
        s = o.object_property(":R_s")
        
        o.add_axiom(SubObjectPropertyOf(r, s))
        
        reasoner = create_reasoner(o)
        
        # Complex entailment checks are not supported
        self.assertRaises(ValueError, reasoner.is_entailed, SubClassOf(A, r.some(C)))