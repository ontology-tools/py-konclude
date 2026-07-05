import unittest

from test_base import simple_ontology
from pykonclude import create_reasoner
from pyhornedowl.model import *


class InferTestCase(unittest.TestCase):
    def test_simple_infer(self):
        o = simple_ontology()
        
        r = create_reasoner(o)
        
        expected = {o.clazz(":A"), o.clazz(":B"), o.clazz(":D"), o.clazz("owl:Nothing")}
        actual = r.get_subclasses(o.clazz(":A"))
        
        self.assertSetEqual(actual, expected, "Subclasses of :A should be :A, :B, :D, and owl:Nothing.")
        
    def test_simple_eq_infer(self):
        o = simple_ontology()
        o.add_axiom(EquivalentClasses([o.clazz(":B"), o.clazz(":C")]))
        
        r = create_reasoner(o)
        
        expected = {o.clazz(":A"), o.clazz(":B"), o.clazz(":D"), o.clazz(":C"), o.clazz("owl:Nothing")}
        actual = r.get_subclasses(o.clazz(":A"))
        
        self.assertSetEqual(actual, expected, "Subclasses of :A should be :A, :B, :D, and owl:Nothing.")
        
    def test_property_infer(self):
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
        
        o.add_axiom(SubObjectPropertyOf(s, r))
        
        o.add_axiom(EquivalentClasses([A, r.some(C)]))
        o.add_axiom(EquivalentClasses([B, s.some(C)]))
        
        r = create_reasoner(o)
        
        actual = r.get_subclasses(A)
        expected = {A, B, o.clazz("owl:Nothing")}
        
        self.assertSetEqual(actual, expected)