use std::collections::HashSet;
use std::ffi::CString;
use std::fs::File;
use std::io::{BufReader, BufWriter};
use std::os::raw::{c_char, c_int};
use std::path::Path;
use std::sync::OnceLock;

use horned_owl::io::ParserConfiguration;
use horned_owl::model::{
    AnnotatedComponent, ArcAnnotatedComponent, ArcStr, Class, ClassExpression, Component,
    MutableOntology, SubClassOf,
};
use horned_owl::ontology::component_mapped::ComponentMappedOntology;
use horned_owl::ontology::indexed::OntologyIndex;
use horned_owl::ontology::set::SetOntology;
use horned_owl::{model as ho, vocab};

use libloading::{Library, Symbol};
use pyhornedowlreasoner::{PyReasoner, Reasoner, ReasonerError, export_py_reasoner};

/// Environment variable pointing to the Konclude shared library
/// (`libKonclude.so` / `Konclude.dll` / `libKonclude.dylib`). If unset, the
/// platform default library name is resolved through the regular dynamic
/// linker search path.
pub const KONCLUDE_LIBRARY_ENV: &str = "KONCLUDE_LIBRARY_PATH";

type KoncludeRunFn = unsafe extern "C" fn(c_int, *const *const c_char) -> c_int;

/// The Konclude library is loaded once per process and never unloaded: the
/// first `konclude_run` call creates a QCoreApplication and worker threads
/// inside the library which must stay alive for the whole process lifetime.
static KONCLUDE_LIBRARY: OnceLock<Result<Library, String>> = OnceLock::new();

fn konclude_library() -> Result<&'static Library, ReasonerError> {
    KONCLUDE_LIBRARY
        .get_or_init(|| {
            let path = std::env::var_os(KONCLUDE_LIBRARY_ENV)
                .unwrap_or_else(|| libloading::library_filename("Konclude"));
            unsafe { Library::new(&path) }.map_err(|e| {
                format!(
                    "Failed to load Konclude library '{}' (set {} to its location): {}",
                    path.to_string_lossy(),
                    KONCLUDE_LIBRARY_ENV,
                    e
                )
            })
        })
        .as_ref()
        .map_err(|e| ReasonerError::Other(e.clone()))
}

/// Runs Konclude in-process with command-line style arguments (without a
/// program name), e.g. `["classification", "-i", "in.owl.xml", "-o", "out.owl.xml"]`.
fn konclude_run(args: &[&str]) -> Result<(), ReasonerError> {
    let library = konclude_library()?;
    let run: Symbol<KoncludeRunFn> = unsafe { library.get(b"konclude_run\0") }
        .map_err(|e| ReasonerError::Other(format!("Failed to resolve 'konclude_run': {}", e)))?;

    let c_args: Vec<CString> = args
        .iter()
        .map(|s| {
            CString::new(*s)
                .map_err(|e| ReasonerError::Other(format!("Invalid argument '{}': {}", s, e)))
        })
        .collect::<Result<_, _>>()?;
    let c_argv: Vec<*const c_char> = c_args.iter().map(|s| s.as_ptr()).collect();

    let ret = unsafe { run(c_argv.len() as c_int, c_argv.as_ptr()) };
    if ret != 0 {
        return Err(ReasonerError::Other(format!(
            "Konclude returned non-zero exit code {} for arguments {:?}",
            ret, args
        )));
    }
    Ok(())
}

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
    /// Writes the current ontology to a temporary file, runs Konclude on it
    /// (consistency check + classification) and parses the results back.
    fn synchronise(&mut self) -> Result<(), ReasonerError> {
        let dir = tempfile::tempdir()
            .map_err(|e| ReasonerError::Other(format!("Failed to create temp dir: {}", e)))?;
        let input = dir.path().join("ontology.owl.xml");

        self.write_ontology(&input)?;
        let input_str = path_str(&input)?.to_string();

        // Consistency: Konclude writes a file containing "true" or "false".
        let consistency_output = dir.path().join("consistency.txt");
        // Note: "-w AUTO" (worker count) is required. With the default
        // configuration (ProcessorCount=1, AdaptThreadPoolSizeProcessorCount
        // =TRUE, BlockingThreadPoolThreadsCount=1) Konclude blocks the only
        // thread of the Qt global thread pool and deadlocks as soon as the
        // backend representative memory cache dispatches work to the pool.
        konclude_run(&[
            "consistency",
            "-w",
            "AUTO",
            "-i",
            &input_str,
            "-o",
            path_str(&consistency_output)?,
        ])?;
        let consistent = std::fs::read_to_string(&consistency_output)
            .map_err(|e| {
                ReasonerError::Other(format!("Failed to read consistency result: {}", e))
            })?
            .trim()
            .parse::<bool>()
            .map_err(|e| {
                ReasonerError::Other(format!("Failed to parse consistency result: {}", e))
            })?;
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

        // Classification: Konclude writes the inferred subclass hierarchy as
        // an OWL 2 XML ontology consisting of SubClassOf/EquivalentClasses
        // axioms between named classes.
        let hierarchy_output = dir.path().join("hierarchy.owl.xml");
        konclude_run(&[
            "classification",
            "-w",
            "AUTO",
            "-i",
            &input_str,
            "-o",
            path_str(&hierarchy_output)?,
        ])?;

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

    fn write_ontology(&self, path: &Path) -> Result<(), ReasonerError> {
        let file = File::create(path)
            .map_err(|e| ReasonerError::Other(format!("Failed to create temp file: {}", e)))?;
        let ontology: ComponentMappedOntology<ArcStr, ArcAnnotatedComponent> =
            ComponentMappedOntology::from(self.loaded_ontology.clone());
        horned_owl::io::owx::writer::write(BufWriter::new(file), &ontology, None)?;
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

fn path_str(path: &Path) -> Result<&str, ReasonerError> {
    path.to_str().ok_or_else(|| {
        ReasonerError::Other(format!("Non UTF-8 temp file path: {}", path.display()))
    })
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
}
