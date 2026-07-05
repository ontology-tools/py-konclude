//! Translation of horned-owl constructs into Konclude builder calls.

use std::collections::HashMap;
use std::os::raw::c_int;

use horned_owl::model::{
    ArcStr, ClassExpression, Component, DataRange, Individual, Literal, ObjectPropertyExpression,
    SubObjectPropertyExpression,
};
use pyhornedowlreasoner::ReasonerError;

use crate::konclude::{self, ExprPtr, KoncludeKb};

const XSD_STRING: &str = "http://www.w3.org/2001/XMLSchema#string";
const RDF_PLAIN_LITERAL: &str = "http://www.w3.org/1999/02/22-rdf-syntax-ns#PlainLiteral";

/// Translates horned-owl components into expressions of one Konclude
/// builder session. Entity expressions are cached per session; the cache
/// (and all translated expressions) become invalid when the knowledge base
/// is flushed.
pub struct Translator<'a> {
    kb: &'a KoncludeKb,
    entity_cache: HashMap<(c_int, String), ExprPtr>,
}

impl<'a> Translator<'a> {
    pub fn new(kb: &'a KoncludeKb) -> Self {
        Translator {
            kb,
            entity_cache: HashMap::new(),
        }
    }

    /// Translates a component into Konclude axiom expressions. Components
    /// without logical meaning (annotations, imports, ontology metadata)
    /// yield no axioms; unsupported logical axioms yield an error.
    pub fn axioms(&mut self, component: &Component<ArcStr>) -> Result<Vec<ExprPtr>, ReasonerError> {
        let kb = self.kb;
        let axiom = match component {
            // ontology metadata and annotations carry no logical meaning
            Component::OntologyID(_)
            | Component::DocIRI(_)
            | Component::OntologyAnnotation(_)
            | Component::DeclareAnnotationProperty(_)
            | Component::AnnotationAssertion(_)
            | Component::SubAnnotationPropertyOf(_)
            | Component::AnnotationPropertyDomain(_)
            | Component::AnnotationPropertyRange(_) => return Ok(vec![]),

            // imports have to be resolved by the caller
            Component::Import(_) => return Ok(vec![]),

            Component::DeclareClass(d) => {
                let entity = self.entity(konclude::ENTITY_CLASS, &d.0 .0)?;
                kb.expression(konclude::AXIOM_DECLARATION, &[entity], 0)?
            }
            Component::DeclareObjectProperty(d) => {
                let entity = self.entity(konclude::ENTITY_OBJECT_PROPERTY, &d.0 .0)?;
                kb.expression(konclude::AXIOM_DECLARATION, &[entity], 0)?
            }
            Component::DeclareDataProperty(d) => {
                let entity = self.entity(konclude::ENTITY_DATA_PROPERTY, &d.0 .0)?;
                kb.expression(konclude::AXIOM_DECLARATION, &[entity], 0)?
            }
            Component::DeclareNamedIndividual(d) => {
                let entity = self.entity(konclude::ENTITY_NAMED_INDIVIDUAL, &d.0 .0)?;
                kb.expression(konclude::AXIOM_DECLARATION, &[entity], 0)?
            }
            Component::DeclareDatatype(d) => {
                let entity = self.entity(konclude::ENTITY_DATATYPE, &d.0 .0)?;
                kb.expression(konclude::AXIOM_DECLARATION, &[entity], 0)?
            }

            Component::SubClassOf(axiom) => {
                let sub = self.class_expression(&axiom.sub)?;
                let sup = self.class_expression(&axiom.sup)?;
                kb.expression(konclude::AXIOM_SUB_CLASS_OF, &[sub, sup], 0)?
            }
            Component::EquivalentClasses(axiom) => {
                let expressions = self.class_expressions(&axiom.0)?;
                kb.expression(konclude::AXIOM_EQUIVALENT_CLASSES, &expressions, 0)?
            }
            Component::DisjointClasses(axiom) => {
                let expressions = self.class_expressions(&axiom.0)?;
                kb.expression(konclude::AXIOM_DISJOINT_CLASSES, &expressions, 0)?
            }
            Component::DisjointUnion(axiom) => {
                // decomposed: C = Union(...) plus pairwise disjointness
                let class = self.entity(konclude::ENTITY_CLASS, &axiom.0 .0)?;
                let expressions = self.class_expressions(&axiom.1)?;
                let union = kb.expression(konclude::EXPR_OBJECT_UNION_OF, &expressions, 0)?;
                let equivalent =
                    kb.expression(konclude::AXIOM_EQUIVALENT_CLASSES, &[class, union], 0)?;
                let disjoint =
                    kb.expression(konclude::AXIOM_DISJOINT_CLASSES, &expressions, 0)?;
                return Ok(vec![equivalent, disjoint]);
            }

            Component::SubObjectPropertyOf(axiom) => {
                let sub = match &axiom.sub {
                    SubObjectPropertyExpression::ObjectPropertyExpression(ope) => {
                        self.object_property_expression(ope)?
                    }
                    SubObjectPropertyExpression::ObjectPropertyChain(chain) => {
                        let links = chain
                            .iter()
                            .map(|ope| self.object_property_expression(ope))
                            .collect::<Result<Vec<_>, _>>()?;
                        kb.expression(konclude::EXPR_OBJECT_PROPERTY_CHAIN, &links, 0)?
                    }
                };
                let sup = self.object_property_expression(&axiom.sup)?;
                kb.expression(konclude::AXIOM_SUB_OBJECT_PROPERTY_OF, &[sub, sup], 0)?
            }
            Component::EquivalentObjectProperties(axiom) => {
                let expressions = self.object_property_expressions(&axiom.0)?;
                kb.expression(konclude::AXIOM_EQUIVALENT_OBJECT_PROPERTIES, &expressions, 0)?
            }
            Component::DisjointObjectProperties(axiom) => {
                let expressions = self.object_property_expressions(&axiom.0)?;
                kb.expression(konclude::AXIOM_DISJOINT_OBJECT_PROPERTIES, &expressions, 0)?
            }
            Component::InverseObjectProperties(axiom) => {
                let first = self.entity(konclude::ENTITY_OBJECT_PROPERTY, &axiom.0 .0)?;
                let second = self.entity(konclude::ENTITY_OBJECT_PROPERTY, &axiom.1 .0)?;
                kb.expression(konclude::AXIOM_INVERSE_OBJECT_PROPERTIES, &[first, second], 0)?
            }
            Component::ObjectPropertyDomain(axiom) => {
                let property = self.object_property_expression(&axiom.ope)?;
                let class = self.class_expression(&axiom.ce)?;
                kb.expression(konclude::AXIOM_OBJECT_PROPERTY_DOMAIN, &[property, class], 0)?
            }
            Component::ObjectPropertyRange(axiom) => {
                let property = self.object_property_expression(&axiom.ope)?;
                let class = self.class_expression(&axiom.ce)?;
                kb.expression(konclude::AXIOM_OBJECT_PROPERTY_RANGE, &[property, class], 0)?
            }
            Component::FunctionalObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_FUNCTIONAL_OBJECT_PROPERTY, &[property], 0)?
            }
            Component::InverseFunctionalObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(
                    konclude::AXIOM_INVERSE_FUNCTIONAL_OBJECT_PROPERTY,
                    &[property],
                    0,
                )?
            }
            Component::ReflexiveObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_REFLEXIVE_OBJECT_PROPERTY, &[property], 0)?
            }
            Component::IrreflexiveObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_IRREFLEXIVE_OBJECT_PROPERTY, &[property], 0)?
            }
            Component::SymmetricObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_SYMMETRIC_OBJECT_PROPERTY, &[property], 0)?
            }
            Component::AsymmetricObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_ASYMMETRIC_OBJECT_PROPERTY, &[property], 0)?
            }
            Component::TransitiveObjectProperty(axiom) => {
                let property = self.object_property_expression(&axiom.0)?;
                kb.expression(konclude::AXIOM_TRANSITIVE_OBJECT_PROPERTY, &[property], 0)?
            }

            Component::SubDataPropertyOf(axiom) => {
                let sub = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.sub.0)?;
                let sup = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.sup.0)?;
                kb.expression(konclude::AXIOM_SUB_DATA_PROPERTY_OF, &[sub, sup], 0)?
            }
            Component::EquivalentDataProperties(axiom) => {
                let expressions = self.data_properties(&axiom.0)?;
                kb.expression(konclude::AXIOM_EQUIVALENT_DATA_PROPERTIES, &expressions, 0)?
            }
            Component::DisjointDataProperties(axiom) => {
                let expressions = self.data_properties(&axiom.0)?;
                kb.expression(konclude::AXIOM_DISJOINT_DATA_PROPERTIES, &expressions, 0)?
            }
            Component::DataPropertyDomain(axiom) => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.dp.0)?;
                let class = self.class_expression(&axiom.ce)?;
                kb.expression(konclude::AXIOM_DATA_PROPERTY_DOMAIN, &[property, class], 0)?
            }
            Component::DataPropertyRange(axiom) => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.dp.0)?;
                let range = self.data_range(&axiom.dr)?;
                kb.expression(konclude::AXIOM_DATA_PROPERTY_RANGE, &[property, range], 0)?
            }
            Component::FunctionalDataProperty(axiom) => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.0 .0)?;
                kb.expression(konclude::AXIOM_FUNCTIONAL_DATA_PROPERTY, &[property], 0)?
            }

            Component::SameIndividual(axiom) => {
                let individuals = self.individuals(&axiom.0)?;
                kb.expression(konclude::AXIOM_SAME_INDIVIDUAL, &individuals, 0)?
            }
            Component::DifferentIndividuals(axiom) => {
                let individuals = self.individuals(&axiom.0)?;
                kb.expression(konclude::AXIOM_DIFFERENT_INDIVIDUALS, &individuals, 0)?
            }
            Component::ClassAssertion(axiom) => {
                let class = self.class_expression(&axiom.ce)?;
                let individual = self.individual(&axiom.i)?;
                kb.expression(konclude::AXIOM_CLASS_ASSERTION, &[class, individual], 0)?
            }
            Component::ObjectPropertyAssertion(axiom) => {
                let property = self.object_property_expression(&axiom.ope)?;
                let from = self.individual(&axiom.from)?;
                let to = self.individual(&axiom.to)?;
                kb.expression(
                    konclude::AXIOM_OBJECT_PROPERTY_ASSERTION,
                    &[property, from, to],
                    0,
                )?
            }
            Component::NegativeObjectPropertyAssertion(axiom) => {
                let property = self.object_property_expression(&axiom.ope)?;
                let from = self.individual(&axiom.from)?;
                let to = self.individual(&axiom.to)?;
                kb.expression(
                    konclude::AXIOM_NEGATIVE_OBJECT_PROPERTY_ASSERTION,
                    &[property, from, to],
                    0,
                )?
            }
            Component::DataPropertyAssertion(axiom) => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.dp.0)?;
                let from = self.individual(&axiom.from)?;
                let to = self.literal(&axiom.to)?;
                kb.expression(
                    konclude::AXIOM_DATA_PROPERTY_ASSERTION,
                    &[property, from, to],
                    0,
                )?
            }
            Component::NegativeDataPropertyAssertion(axiom) => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &axiom.dp.0)?;
                let from = self.individual(&axiom.from)?;
                let to = self.literal(&axiom.to)?;
                // fixed order: individual, literal, property
                kb.expression(
                    konclude::AXIOM_NEGATIVE_DATA_PROPERTY_ASSERTION,
                    &[from, to, property],
                    0,
                )?
            }

            unsupported => {
                return Err(ReasonerError::Other(format!(
                    "Component not supported by the Konclude mapping: {:?}",
                    unsupported
                )))
            }
        };
        Ok(vec![axiom])
    }

    fn entity(&mut self, kind: c_int, iri: &impl AsRef<str>) -> Result<ExprPtr, ReasonerError> {
        let iri = iri.as_ref();
        if let Some(expression) = self.entity_cache.get(&(kind, iri.to_string())) {
            return Ok(*expression);
        }
        let expression = self.kb.entity(kind, iri)?;
        self.entity_cache.insert((kind, iri.to_string()), expression);
        Ok(expression)
    }

    fn class_expression(
        &mut self,
        ce: &ClassExpression<ArcStr>,
    ) -> Result<ExprPtr, ReasonerError> {
        let expression = match ce {
            ClassExpression::Class(class) => self.entity(konclude::ENTITY_CLASS, &class.0)?,
            ClassExpression::ObjectIntersectionOf(operands) => {
                let expressions = self.class_expressions(operands)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_INTERSECTION_OF, &expressions, 0)?
            }
            ClassExpression::ObjectUnionOf(operands) => {
                let expressions = self.class_expressions(operands)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_UNION_OF, &expressions, 0)?
            }
            ClassExpression::ObjectComplementOf(operand) => {
                let expression = self.class_expression(operand)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_COMPLEMENT_OF, &[expression], 0)?
            }
            ClassExpression::ObjectOneOf(individuals) => {
                let expressions = self.individuals(individuals)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_ONE_OF, &expressions, 0)?
            }
            ClassExpression::ObjectSomeValuesFrom { ope, bce } => {
                let property = self.object_property_expression(ope)?;
                let filler = self.class_expression(bce)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_SOME_VALUES_FROM, &[property, filler], 0)?
            }
            ClassExpression::ObjectAllValuesFrom { ope, bce } => {
                let property = self.object_property_expression(ope)?;
                let filler = self.class_expression(bce)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_ALL_VALUES_FROM, &[property, filler], 0)?
            }
            ClassExpression::ObjectHasValue { ope, i } => {
                let property = self.object_property_expression(ope)?;
                let individual = self.individual(i)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_HAS_VALUE, &[property, individual], 0)?
            }
            ClassExpression::ObjectHasSelf(ope) => {
                let property = self.object_property_expression(ope)?;
                self.kb
                    .expression(konclude::EXPR_OBJECT_HAS_SELF, &[property], 0)?
            }
            ClassExpression::ObjectMinCardinality { n, ope, bce } => {
                self.object_cardinality(konclude::EXPR_OBJECT_MIN_CARDINALITY, *n, ope, bce)?
            }
            ClassExpression::ObjectMaxCardinality { n, ope, bce } => {
                self.object_cardinality(konclude::EXPR_OBJECT_MAX_CARDINALITY, *n, ope, bce)?
            }
            ClassExpression::ObjectExactCardinality { n, ope, bce } => {
                self.object_cardinality(konclude::EXPR_OBJECT_EXACT_CARDINALITY, *n, ope, bce)?
            }
            ClassExpression::DataSomeValuesFrom { dp, dr } => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &dp.0)?;
                let range = self.data_range(dr)?;
                self.kb
                    .expression(konclude::EXPR_DATA_SOME_VALUES_FROM, &[property, range], 0)?
            }
            ClassExpression::DataAllValuesFrom { dp, dr } => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &dp.0)?;
                let range = self.data_range(dr)?;
                self.kb
                    .expression(konclude::EXPR_DATA_ALL_VALUES_FROM, &[property, range], 0)?
            }
            ClassExpression::DataHasValue { dp, l } => {
                let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &dp.0)?;
                let literal = self.literal(l)?;
                self.kb
                    .expression(konclude::EXPR_DATA_HAS_VALUE, &[property, literal], 0)?
            }
            ClassExpression::DataMinCardinality { n, dp, dr } => {
                self.data_cardinality(konclude::EXPR_DATA_MIN_CARDINALITY, *n, dp, dr)?
            }
            ClassExpression::DataMaxCardinality { n, dp, dr } => {
                self.data_cardinality(konclude::EXPR_DATA_MAX_CARDINALITY, *n, dp, dr)?
            }
            ClassExpression::DataExactCardinality { n, dp, dr } => {
                self.data_cardinality(konclude::EXPR_DATA_EXACT_CARDINALITY, *n, dp, dr)?
            }
        };
        Ok(expression)
    }

    fn object_cardinality(
        &mut self,
        expr_type: c_int,
        n: u32,
        ope: &ObjectPropertyExpression<ArcStr>,
        bce: &ClassExpression<ArcStr>,
    ) -> Result<ExprPtr, ReasonerError> {
        let property = self.object_property_expression(ope)?;
        let filler = self.class_expression(bce)?;
        self.kb
            .expression(expr_type, &[property, filler], n as i32)
    }

    fn data_cardinality(
        &mut self,
        expr_type: c_int,
        n: u32,
        dp: &horned_owl::model::DataProperty<ArcStr>,
        dr: &DataRange<ArcStr>,
    ) -> Result<ExprPtr, ReasonerError> {
        let property = self.entity(konclude::ENTITY_DATA_PROPERTY, &dp.0)?;
        let range = self.data_range(dr)?;
        self.kb.expression(expr_type, &[property, range], n as i32)
    }

    fn class_expressions(
        &mut self,
        ces: &[ClassExpression<ArcStr>],
    ) -> Result<Vec<ExprPtr>, ReasonerError> {
        ces.iter().map(|ce| self.class_expression(ce)).collect()
    }

    fn object_property_expression(
        &mut self,
        ope: &ObjectPropertyExpression<ArcStr>,
    ) -> Result<ExprPtr, ReasonerError> {
        match ope {
            ObjectPropertyExpression::ObjectProperty(property) => {
                self.entity(konclude::ENTITY_OBJECT_PROPERTY, &property.0)
            }
            ObjectPropertyExpression::InverseObjectProperty(property) => {
                let entity = self.entity(konclude::ENTITY_OBJECT_PROPERTY, &property.0)?;
                self.kb
                    .expression(konclude::EXPR_INVERSE_OBJECT_PROPERTY_OF, &[entity], 0)
            }
        }
    }

    fn object_property_expressions(
        &mut self,
        opes: &[ObjectPropertyExpression<ArcStr>],
    ) -> Result<Vec<ExprPtr>, ReasonerError> {
        opes.iter()
            .map(|ope| self.object_property_expression(ope))
            .collect()
    }

    fn data_properties(
        &mut self,
        dps: &[horned_owl::model::DataProperty<ArcStr>],
    ) -> Result<Vec<ExprPtr>, ReasonerError> {
        dps.iter()
            .map(|dp| self.entity(konclude::ENTITY_DATA_PROPERTY, &dp.0))
            .collect()
    }

    fn individual(&mut self, individual: &Individual<ArcStr>) -> Result<ExprPtr, ReasonerError> {
        match individual {
            Individual::Named(named) => {
                self.entity(konclude::ENTITY_NAMED_INDIVIDUAL, &named.0)
            }
            Individual::Anonymous(anonymous) => {
                self.entity(konclude::ENTITY_ANONYMOUS_INDIVIDUAL, &anonymous.0)
            }
        }
    }

    fn individuals(
        &mut self,
        individuals: &[Individual<ArcStr>],
    ) -> Result<Vec<ExprPtr>, ReasonerError> {
        individuals
            .iter()
            .map(|individual| self.individual(individual))
            .collect()
    }

    fn literal(&mut self, literal: &Literal<ArcStr>) -> Result<ExprPtr, ReasonerError> {
        match literal {
            Literal::Simple { literal } => self.kb.literal(literal, XSD_STRING),
            Literal::Language { literal, lang } => self
                .kb
                .literal(&format!("{}@{}", literal, lang), RDF_PLAIN_LITERAL),
            Literal::Datatype {
                literal,
                datatype_iri,
            } => self.kb.literal(literal, datatype_iri),
        }
    }

    fn data_range(&mut self, dr: &DataRange<ArcStr>) -> Result<ExprPtr, ReasonerError> {
        let expression = match dr {
            DataRange::Datatype(datatype) => {
                self.entity(konclude::ENTITY_DATATYPE, &datatype.0)?
            }
            DataRange::DataIntersectionOf(operands) => {
                let expressions = self.data_ranges(operands)?;
                self.kb
                    .expression(konclude::EXPR_DATA_INTERSECTION_OF, &expressions, 0)?
            }
            DataRange::DataUnionOf(operands) => {
                let expressions = self.data_ranges(operands)?;
                self.kb
                    .expression(konclude::EXPR_DATA_UNION_OF, &expressions, 0)?
            }
            DataRange::DataComplementOf(operand) => {
                let expression = self.data_range(operand)?;
                self.kb
                    .expression(konclude::EXPR_DATA_COMPLEMENT_OF, &[expression], 0)?
            }
            DataRange::DataOneOf(literals) => {
                let expressions = literals
                    .iter()
                    .map(|literal| self.literal(literal))
                    .collect::<Result<Vec<_>, _>>()?;
                self.kb
                    .expression(konclude::EXPR_DATA_ONE_OF, &expressions, 0)?
            }
            DataRange::DatatypeRestriction(datatype, facet_restrictions) => {
                let mut expressions =
                    vec![self.entity(konclude::ENTITY_DATATYPE, &datatype.0)?];
                for facet_restriction in facet_restrictions {
                    let facet =
                        self.entity(konclude::ENTITY_DATA_FACET, &facet_restriction.f)?;
                    let literal = self.literal(&facet_restriction.l)?;
                    expressions.push(self.kb.expression(
                        konclude::EXPR_DATA_FACET_RESTRICTION,
                        &[literal, facet],
                        0,
                    )?);
                }
                self.kb
                    .expression(konclude::EXPR_DATATYPE_RESTRICTION, &expressions, 0)?
            }
        };
        Ok(expression)
    }

    fn data_ranges(&mut self, drs: &[DataRange<ArcStr>]) -> Result<Vec<ExprPtr>, ReasonerError> {
        drs.iter().map(|dr| self.data_range(dr)).collect()
    }
}
