mod konclude;
mod translate;

use std::collections::HashSet;
use std::fs::File;
use std::io::BufReader;

use horned_owl::io::ParserConfiguration;
use horned_owl::model::{
    AnnotatedComponent, ArcAnnotatedComponent, ArcStr, Class, ClassExpression, Component,
    MutableOntology, SubClassOf,
};
use horned_owl::ontology::indexed::OntologyIndex;
use horned_owl::ontology::set::SetOntology;
use horned_owl::{model as ho, vocab};

use pyhornedowlreasoner::{PyReasoner, Reasoner, ReasonerError, export_py_reasoner};

use crate::konclude::KoncludeKb;
use crate::translate::Translator;

pub use crate::konclude::KONCLUDE_LIBRARY_ENV;

pub struct PyKoncludeReasoner {
    loaded_ontology: SetOntology<ArcStr>,
    /// Named class subsumptions and equivalences parsed from Konclude's
    /// classification result (the written subclass hierarchy).
    inferred: Vec<Component<ArcStr>>,
    /// Consistency as of the last synchronisation (`None` if it never succeeded).
    consistent: Option<bool>,
    /// Error of a failed synchronisation during reasoner creation.
    creation_error: Option<String>,
}

export_py_reasoner!(PyKoncludeReasoner);

impl PyReasoner for PyKoncludeReasoner {
    fn create_reasoner(ontology: SetOntology<ArcStr>) -> Self {
        let mut reasoner = PyKoncludeReasoner {
            loaded_ontology: ontology,
            inferred: Vec::new(),
            consistent: None,
            creation_error: None,
        };
        if let Err(e) = reasoner.synchronise() {
            reasoner.creation_error = Some(e.to_string());
        }
        reasoner
    }
}

impl PyKoncludeReasoner {
    /// Builds the ontology in Konclude by mapping all horned-owl components
    /// to Konclude constructs, then checks consistency and classifies.
    fn synchronise(&mut self) -> Result<(), ReasonerError> {
        let kb = KoncludeKb::new()?;

        {
            let mut translator = Translator::new(&kb);
            for annotated_component in self.loaded_ontology.iter() {
                for axiom in translator.axioms(&annotated_component.component)? {
                    kb.tell(axiom)?;
                }
            }
        }
        kb.flush()?;

        let consistent = kb.is_consistent()?;
        self.consistent = Some(consistent);
        self.creation_error = None;
        self.inferred.clear();

        if !consistent {
            // An inconsistent ontology entails everything; Konclude cannot
            // produce a meaningful class hierarchy for it.
            let build = ho::Build::<ArcStr>::new();
            self.inferred.push(Component::SubClassOf(SubClassOf {
                sub: build.class(vocab::OWL::Thing.as_ref()).into(),
                sup: build.class(vocab::OWL::Nothing.as_ref()).into(),
            }));
            return Ok(());
        }

        // Konclude writes the inferred subclass hierarchy as an OWL 2 XML
        // ontology consisting of SubClassOf/EquivalentClasses axioms
        // between named classes.
        let dir = tempfile::tempdir()
            .map_err(|e| ReasonerError::Other(format!("Failed to create temp dir: {}", e)))?;
        let hierarchy_output = dir.path().join("hierarchy.owl.xml");
        kb.classify(&hierarchy_output)?;

        let file = File::open(&hierarchy_output).map_err(|e| {
            ReasonerError::Other(format!("Failed to open classification result: {}", e))
        })?;
        let mut reader = BufReader::new(file);
        let (result_ontology, _): (SetOntology<ArcStr>, _) =
            horned_owl::io::owx::reader::read(&mut reader, ParserConfiguration::default())?;

        self.inferred = result_ontology
            .into_iter()
            .map(|ac| ac.component)
            .filter(|c| {
                matches!(
                    c,
                    Component::SubClassOf(_) | Component::EquivalentClasses(_)
                )
            })
            .collect();

        Ok(())
    }

    /// Subsumption edges (sub, sup) between named classes from the parsed
    /// classification result. Equivalent classes yield edges in both
    /// directions.
    fn subsumption_edges(&self) -> Vec<(String, String)> {
        let mut edges = Vec::new();
        for component in &self.inferred {
            match component {
                Component::SubClassOf(SubClassOf {
                    sub: ClassExpression::Class(sub),
                    sup: ClassExpression::Class(sup),
                }) => {
                    edges.push((sub.0.to_string(), sup.0.to_string()));
                }
                Component::EquivalentClasses(ec) => {
                    let classes: Vec<String> = ec
                        .0
                        .iter()
                        .filter_map(|ce| match ce {
                            ClassExpression::Class(c) => Some(c.0.to_string()),
                            _ => None,
                        })
                        .collect();
                    for a in &classes {
                        for b in &classes {
                            if a != b {
                                edges.push((a.clone(), b.clone()));
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        edges
    }

    /// Reflexive-transitive closure over the subsumption edges, either
    /// downwards (all subclasses) or upwards (all superclasses) of `class_iri`.
    fn subsumption_closure(&self, class_iri: &str, downwards: bool) -> HashSet<String> {
        let edges = self.subsumption_edges();
        let mut result: HashSet<String> = HashSet::new();
        result.insert(class_iri.to_string());
        let mut changed = true;
        while changed {
            changed = false;
            for (sub, sup) in &edges {
                let (from, to) = if downwards { (sup, sub) } else { (sub, sup) };
                if result.contains(from.as_str()) && !result.contains(to.as_str()) {
                    result.insert(to.clone());
                    changed = true;
                }
            }
        }
        result
    }

    fn named_class_iri(cmp: &ClassExpression<ArcStr>) -> Result<String, ReasonerError> {
        match cmp {
            ClassExpression::Class(c) => Ok(c.0.to_string()),
            _ => Err(ReasonerError::Other(format!(
                "Only named classes are supported, got {:?}",
                cmp
            ))),
        }
    }
}

impl OntologyIndex<ArcStr, ArcAnnotatedComponent> for PyKoncludeReasoner {
    fn index_insert(&mut self, cmp: ArcAnnotatedComponent) -> bool {
        self.loaded_ontology.insert((*cmp).clone())
    }

    fn index_remove(&mut self, cmp: &AnnotatedComponent<ArcStr>) -> bool {
        self.loaded_ontology.remove(cmp)
    }
}

impl Reasoner<ArcStr, ArcAnnotatedComponent> for PyKoncludeReasoner {
    fn get_name(&self) -> String {
        "PyKonclude".to_string()
    }

    fn flush(&mut self) -> Result<(), ReasonerError> {
        self.synchronise()
    }

    fn inferred_axioms(&self) -> Box<dyn Iterator<Item = Component<ArcStr>>> {
        Box::new(self.inferred.clone().into_iter())
    }

    fn is_consistent(&self) -> Result<bool, ReasonerError> {
        match (self.consistent, &self.creation_error) {
            (Some(consistent), _) => Ok(consistent),
            (None, Some(e)) => Err(ReasonerError::Other(format!(
                "Reasoner initialisation failed: {}",
                e
            ))),
            (None, None) => Err(ReasonerError::Other(
                "Ontology has not been synchronised yet, call flush() first".to_string(),
            )),
        }
    }

    fn is_entailed(&self, cmp: &Component<ArcStr>) -> Result<bool, ReasonerError> {
        match cmp {
            Component::SubClassOf(SubClassOf {
                sub: ClassExpression::Class(sub),
                sup: ClassExpression::Class(sup),
            }) => {
                if self.is_consistent()? == false {
                    // An inconsistent ontology entails everything.
                    return Ok(true);
                }
                let sub_iri = sub.0.to_string();
                let sup_iri = sup.0.to_string();
                if sup_iri == vocab::OWL::Thing.as_ref() || sub_iri == vocab::OWL::Nothing.as_ref()
                {
                    return Ok(true);
                }
                Ok(self.subsumption_closure(&sub_iri, false).contains(&sup_iri))
            }
            c => Err(ReasonerError::Other(format!(
                "Cannot check entailment for component {:?}",
                c
            ))),
        }
    }

    fn get_subclasses<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        let build = ho::Build::<ArcStr>::new();
        let class_iri = Self::named_class_iri(cmp)?;
        let mut closure = self.subsumption_closure(&class_iri, true);
        // owl:Nothing is a subclass of every class.
        closure.insert(vocab::OWL::Nothing.as_ref().to_string());
        Ok(Box::new(
            closure.into_iter().map(move |iri| build.class(iri)),
        ))
    }

    fn get_superclasses<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        let build = ho::Build::<ArcStr>::new();
        let class_iri = Self::named_class_iri(cmp)?;
        let mut closure = self.subsumption_closure(&class_iri, false);
        // owl:Thing is a superclass of every class.
        closure.insert(vocab::OWL::Thing.as_ref().to_string());
        Ok(Box::new(
            closure.into_iter().map(move |iri| build.class(iri)),
        ))
    }

    fn get_equivalent_classes<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        let build = ho::Build::<ArcStr>::new();
        let class_iri = Self::named_class_iri(cmp)?;
        // Classes that subsume and are subsumed by the given class.
        let down = self.subsumption_closure(&class_iri, true);
        let up = self.subsumption_closure(&class_iri, false);
        Ok(Box::new(
            down.into_iter()
                .filter(move |iri| up.contains(iri))
                .map(move |iri| build.class(iri)),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use horned_owl::model::{
        Annotation, AnnotationAssertion, AnnotationSubject, AnnotationValue, Build, DeclareClass,
        Literal, SubClassOf,
    };

    fn get_ontology() -> (SetOntology<ArcStr>, Build<ArcStr>) {
        let build = Build::<ArcStr>::new();
        let mut ontology = SetOntology::new();
        ontology.insert(DeclareClass(build.class("https://example.com/A")));
        ontology.insert(DeclareClass(build.class("https://example.com/B")));
        ontology.insert(DeclareClass(build.class("https://example.com/C")));
        ontology.insert(DeclareClass(build.class("https://example.com/D")));
        ontology.insert(SubClassOf {
            sub: build.class("https://example.com/B").into(),
            sup: build.class("https://example.com/A").into(),
        });
        ontology.insert(SubClassOf {
            sub: build.class("https://example.com/D").into(),
            sup: build.class("https://example.com/B").into(),
        });
        ontology.insert(AnnotationAssertion {
            subject: AnnotationSubject::IRI(build.iri("https://example.com/A")),
            ann: Annotation {
                ap: build.annotation_property(vocab::RDFS::Label.to_string()),
                av: AnnotationValue::Literal(Literal::Simple {
                    literal: "ClassA".to_string(),
                }),
            }
            .into(),
        });

        (ontology, build)
    }

    #[test]
    fn test_simple_infer() {
        let (ontology, build) = get_ontology();

        let mut reasoner = PyKoncludeReasoner::create_reasoner(ontology);
        reasoner.flush().unwrap();

        assert!(reasoner.is_consistent().unwrap());

        let mut expected: Vec<Class<ArcStr>> = vec![
            build.class("https://example.com/A"),
            build.class("https://example.com/B"),
            build.class("https://example.com/D"),
            build.class(vocab::OWL::Nothing.as_ref()),
        ];

        let mut actual = reasoner
            .get_subclasses(&build.class("https://example.com/A").into())
            .unwrap()
            .collect::<Vec<_>>();

        expected.sort();
        actual.sort();

        assert_eq!(expected, actual);
    }

    #[test]
    fn test_entailment() {
        let (ontology, build) = get_ontology();

        let reasoner = PyKoncludeReasoner::create_reasoner(ontology);

        assert!(reasoner
            .is_entailed(&Component::SubClassOf(SubClassOf {
                sub: build.class("https://example.com/D").into(),
                sup: build.class("https://example.com/A").into(),
            }))
            .unwrap());

        assert!(!reasoner
            .is_entailed(&Component::SubClassOf(SubClassOf {
                sub: build.class("https://example.com/A").into(),
                sup: build.class("https://example.com/D").into(),
            }))
            .unwrap());
    }

    #[test]
    fn test_inconsistent() {
        let (mut ontology, build) = get_ontology();
        ontology.insert(SubClassOf {
            sub: build.class(vocab::OWL::Thing.as_ref()).into(),
            sup: build.class(vocab::OWL::Nothing.as_ref()).into(),
        });

        let reasoner = PyKoncludeReasoner::create_reasoner(ontology);

        assert!(!reasoner.is_consistent().unwrap());
    }

    #[test]
    fn test_expressions() {
        // exercises composite expressions through the construct mapping:
        // r subPropertyOf s, A = some r C, B = some s C  =>  A subclassof B
        let build = Build::<ArcStr>::new();
        let mut ontology = SetOntology::new();
        let a = build.class("https://example.com/A");
        let b = build.class("https://example.com/B");
        let c = build.class("https://example.com/C");
        let r = build.object_property("https://example.com/r");
        let s = build.object_property("https://example.com/s");
        ontology.insert(ho::SubObjectPropertyOf {
            sub: ho::SubObjectPropertyExpression::ObjectPropertyExpression(r.clone().into()),
            sup: s.clone().into(),
        });
        ontology.insert(ho::EquivalentClasses(vec![
            a.clone().into(),
            ClassExpression::ObjectSomeValuesFrom {
                ope: r.into(),
                bce: Box::new(c.clone().into()),
            },
        ]));
        ontology.insert(ho::EquivalentClasses(vec![
            b.clone().into(),
            ClassExpression::ObjectSomeValuesFrom {
                ope: s.into(),
                bce: Box::new(c.into()),
            },
        ]));

        let reasoner = PyKoncludeReasoner::create_reasoner(ontology);

        assert!(reasoner
            .is_entailed(&Component::SubClassOf(SubClassOf {
                sub: a.into(),
                sup: b.into(),
            }))
            .unwrap());
    }
}
