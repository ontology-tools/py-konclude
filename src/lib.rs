//! py-horned-owl reasoner plugin wrapping the native [`konclude_rs`] crate.
//!
//! This is a thin adapter: [`konclude_rs::KoncludeReasoner`] does the actual
//! work; here it is exposed through py-horned-owl's `Reasoner`/`OntologyIndex`
//! traits and the `create_reasoner` plugin entry point.

use horned_owl::model::{
    AnnotatedComponent, ArcAnnotatedComponent, ArcStr, Class, ClassExpression, Component,
};
use horned_owl::ontology::indexed::OntologyIndex;
use horned_owl::ontology::set::SetOntology;

use konclude_rs::{KoncludeError, KoncludeReasoner};
use pyhornedowlreasoner::{export_py_reasoner, PyReasoner, Reasoner, ReasonerError};

pub use konclude_rs::KONCLUDE_LIBRARY_ENV;

fn to_reasoner_error(error: KoncludeError) -> ReasonerError {
    ReasonerError::Other(error.to_string())
}

/// py-horned-owl reasoner backed by a native [`KoncludeReasoner`].
pub struct PyKoncludeReasoner(KoncludeReasoner);

export_py_reasoner!(PyKoncludeReasoner);

impl PyReasoner for PyKoncludeReasoner {
    fn create_reasoner(ontology: SetOntology<ArcStr>) -> Self {
        PyKoncludeReasoner(KoncludeReasoner::new(ontology))
    }
}

impl OntologyIndex<ArcStr, ArcAnnotatedComponent> for PyKoncludeReasoner {
    fn index_insert(&mut self, cmp: ArcAnnotatedComponent) -> bool {
        self.0.insert((*cmp).clone())
    }

    fn index_remove(&mut self, cmp: &AnnotatedComponent<ArcStr>) -> bool {
        self.0.remove(cmp)
    }
}

impl Reasoner<ArcStr, ArcAnnotatedComponent> for PyKoncludeReasoner {
    fn get_name(&self) -> String {
        "PyKonclude".to_string()
    }

    fn flush(&mut self) -> Result<(), ReasonerError> {
        self.0.flush().map_err(to_reasoner_error)
    }

    fn inferred_axioms(&self) -> Box<dyn Iterator<Item = Component<ArcStr>>> {
        // The trait offers no error channel here; classification failures
        // surface as an empty result (and as errors on the other queries).
        Box::new(self.0.inferred_axioms().unwrap_or_default().into_iter())
    }

    fn is_consistent(&self) -> Result<bool, ReasonerError> {
        self.0.is_consistent().map_err(to_reasoner_error)
    }

    fn is_entailed(&self, cmp: &Component<ArcStr>) -> Result<bool, ReasonerError> {
        self.0.is_entailed(cmp).map_err(to_reasoner_error)
    }

    fn get_subclasses<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        Ok(Box::new(
            self.0.subclasses(cmp).map_err(to_reasoner_error)?.into_iter(),
        ))
    }

    fn get_superclasses<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        Ok(Box::new(
            self.0
                .superclasses(cmp)
                .map_err(to_reasoner_error)?
                .into_iter(),
        ))
    }

    fn get_equivalent_classes<'a>(
        &'a self,
        cmp: &'a ClassExpression<ArcStr>,
    ) -> Result<Box<dyn Iterator<Item = Class<ArcStr>> + 'a>, ReasonerError> {
        Ok(Box::new(
            self.0
                .equivalent_classes(cmp)
                .map_err(to_reasoner_error)?
                .into_iter(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use horned_owl::model::{Build, DeclareClass, MutableOntology, SubClassOf};
    use horned_owl::vocab;

    /// Smoke test that the trait wrapper reaches the native reasoner; the
    /// full reasoning behaviour is covered by konclude-rs' own tests.
    #[test]
    fn test_wrapper_get_subclasses() {
        let build = Build::<ArcStr>::new();
        let mut ontology = SetOntology::new();
        ontology.insert(DeclareClass(build.class("https://example.com/A")));
        ontology.insert(DeclareClass(build.class("https://example.com/B")));
        ontology.insert(SubClassOf {
            sub: build.class("https://example.com/B").into(),
            sup: build.class("https://example.com/A").into(),
        });

        let mut reasoner = PyKoncludeReasoner::create_reasoner(ontology);
        reasoner.flush().unwrap();
        assert!(reasoner.is_consistent().unwrap());

        let mut expected: Vec<Class<ArcStr>> = vec![
            build.class("https://example.com/A"),
            build.class("https://example.com/B"),
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
}
