//! Safe wrapper around the Konclude C interface (loaded via dlopen).

use std::ffi::CString;
use std::os::raw::{c_char, c_int, c_void};
use std::sync::OnceLock;

use libloading::Library;
use pyhornedowlreasoner::ReasonerError;

/// Environment variable pointing to the Konclude shared library
/// (`libKonclude.so` / `Konclude.dll` / `libKonclude.dylib`). If unset, the
/// platform default library name is resolved through the regular dynamic
/// linker search path.
pub const KONCLUDE_LIBRARY_ENV: &str = "KONCLUDE_LIBRARY_PATH";

pub type KbPtr = *mut c_void;
pub type ExprPtr = *mut c_void;

// entity kinds (KoncludeCInterface.h)
pub const ENTITY_CLASS: c_int = 1;
pub const ENTITY_OBJECT_PROPERTY: c_int = 2;
pub const ENTITY_DATA_PROPERTY: c_int = 3;
pub const ENTITY_NAMED_INDIVIDUAL: c_int = 4;
pub const ENTITY_ANONYMOUS_INDIVIDUAL: c_int = 5;
pub const ENTITY_DATATYPE: c_int = 6;
pub const ENTITY_DATA_FACET: c_int = 7;

// expression types
pub const EXPR_OBJECT_INTERSECTION_OF: c_int = 101;
pub const EXPR_OBJECT_UNION_OF: c_int = 102;
pub const EXPR_OBJECT_COMPLEMENT_OF: c_int = 103;
pub const EXPR_OBJECT_ONE_OF: c_int = 104;
pub const EXPR_OBJECT_SOME_VALUES_FROM: c_int = 105;
pub const EXPR_OBJECT_ALL_VALUES_FROM: c_int = 106;
pub const EXPR_OBJECT_HAS_VALUE: c_int = 107;
pub const EXPR_OBJECT_HAS_SELF: c_int = 108;
pub const EXPR_OBJECT_MIN_CARDINALITY: c_int = 109;
pub const EXPR_OBJECT_MAX_CARDINALITY: c_int = 110;
pub const EXPR_OBJECT_EXACT_CARDINALITY: c_int = 111;
pub const EXPR_INVERSE_OBJECT_PROPERTY_OF: c_int = 120;
pub const EXPR_OBJECT_PROPERTY_CHAIN: c_int = 121;
pub const EXPR_DATA_INTERSECTION_OF: c_int = 130;
pub const EXPR_DATA_UNION_OF: c_int = 131;
pub const EXPR_DATA_COMPLEMENT_OF: c_int = 132;
pub const EXPR_DATA_ONE_OF: c_int = 133;
pub const EXPR_DATATYPE_RESTRICTION: c_int = 134;
pub const EXPR_DATA_FACET_RESTRICTION: c_int = 135;
pub const EXPR_DATA_SOME_VALUES_FROM: c_int = 136;
pub const EXPR_DATA_ALL_VALUES_FROM: c_int = 137;
pub const EXPR_DATA_HAS_VALUE: c_int = 138;
pub const EXPR_DATA_MIN_CARDINALITY: c_int = 139;
pub const EXPR_DATA_MAX_CARDINALITY: c_int = 140;
pub const EXPR_DATA_EXACT_CARDINALITY: c_int = 141;

// axiom types
pub const AXIOM_DECLARATION: c_int = 201;
pub const AXIOM_SUB_CLASS_OF: c_int = 202;
pub const AXIOM_EQUIVALENT_CLASSES: c_int = 203;
pub const AXIOM_DISJOINT_CLASSES: c_int = 204;
pub const AXIOM_SUB_OBJECT_PROPERTY_OF: c_int = 205;
pub const AXIOM_EQUIVALENT_OBJECT_PROPERTIES: c_int = 206;
pub const AXIOM_DISJOINT_OBJECT_PROPERTIES: c_int = 207;
pub const AXIOM_INVERSE_OBJECT_PROPERTIES: c_int = 208;
pub const AXIOM_OBJECT_PROPERTY_DOMAIN: c_int = 209;
pub const AXIOM_OBJECT_PROPERTY_RANGE: c_int = 210;
pub const AXIOM_FUNCTIONAL_OBJECT_PROPERTY: c_int = 211;
pub const AXIOM_INVERSE_FUNCTIONAL_OBJECT_PROPERTY: c_int = 212;
pub const AXIOM_REFLEXIVE_OBJECT_PROPERTY: c_int = 213;
pub const AXIOM_IRREFLEXIVE_OBJECT_PROPERTY: c_int = 214;
pub const AXIOM_SYMMETRIC_OBJECT_PROPERTY: c_int = 215;
pub const AXIOM_ASYMMETRIC_OBJECT_PROPERTY: c_int = 216;
pub const AXIOM_TRANSITIVE_OBJECT_PROPERTY: c_int = 217;
pub const AXIOM_SUB_DATA_PROPERTY_OF: c_int = 218;
pub const AXIOM_EQUIVALENT_DATA_PROPERTIES: c_int = 219;
pub const AXIOM_DISJOINT_DATA_PROPERTIES: c_int = 220;
pub const AXIOM_DATA_PROPERTY_DOMAIN: c_int = 221;
pub const AXIOM_DATA_PROPERTY_RANGE: c_int = 222;
pub const AXIOM_FUNCTIONAL_DATA_PROPERTY: c_int = 223;
pub const AXIOM_CLASS_ASSERTION: c_int = 224;
pub const AXIOM_OBJECT_PROPERTY_ASSERTION: c_int = 225;
pub const AXIOM_NEGATIVE_OBJECT_PROPERTY_ASSERTION: c_int = 226;
pub const AXIOM_DATA_PROPERTY_ASSERTION: c_int = 227;
pub const AXIOM_NEGATIVE_DATA_PROPERTY_ASSERTION: c_int = 228;
pub const AXIOM_SAME_INDIVIDUAL: c_int = 229;
pub const AXIOM_DIFFERENT_INDIVIDUALS: c_int = 230;

/// Mirrors KoncludeClassHierarchyCallbacks from KoncludeCInterface.h.
#[repr(C)]
struct KoncludeClassHierarchyCallbacks {
    user_data: *mut c_void,
    node: unsafe extern "C" fn(*mut c_void, *const *const c_char, c_int),
    edge: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char),
}

/// The classified subclass hierarchy: equivalence groups of class IRIs (the
/// first IRI of a group is its representative) and direct subsumption edges
/// between group representatives.
#[derive(Debug, Default)]
pub struct ClassHierarchy {
    pub nodes: Vec<Vec<String>>,
    pub edges: Vec<(String, String)>,
}

/// Resolved function pointers of the Konclude C interface.
struct KoncludeApi {
    kb_create: unsafe extern "C" fn() -> KbPtr,
    kb_release: unsafe extern "C" fn(KbPtr),
    kb_entity: unsafe extern "C" fn(KbPtr, c_int, *const c_char) -> ExprPtr,
    kb_literal: unsafe extern "C" fn(KbPtr, *const c_char, *const c_char) -> ExprPtr,
    kb_expression: unsafe extern "C" fn(KbPtr, c_int, *const ExprPtr, c_int, c_int) -> ExprPtr,
    kb_tell: unsafe extern "C" fn(KbPtr, ExprPtr) -> c_int,
    kb_retract: unsafe extern "C" fn(KbPtr, ExprPtr) -> c_int,
    kb_flush: unsafe extern "C" fn(KbPtr) -> c_int,
    kb_is_consistent: unsafe extern "C" fn(KbPtr) -> c_int,
    kb_class_hierarchy: unsafe extern "C" fn(KbPtr, *const KoncludeClassHierarchyCallbacks) -> c_int,
}

/// The Konclude library is loaded once per process and never unloaded: the
/// first call creates a QCoreApplication and worker threads inside the
/// library which must stay alive for the whole process lifetime.
static KONCLUDE_API: OnceLock<Result<KoncludeApi, String>> = OnceLock::new();

fn api() -> Result<&'static KoncludeApi, ReasonerError> {
    KONCLUDE_API
        .get_or_init(|| {
            let path = std::env::var_os(KONCLUDE_LIBRARY_ENV)
                .unwrap_or_else(|| libloading::library_filename("Konclude"));
            let library = unsafe { Library::new(&path) }.map_err(|e| {
                format!(
                    "Failed to load Konclude library '{}' (set {} to its location): {}",
                    path.to_string_lossy(),
                    KONCLUDE_LIBRARY_ENV,
                    e
                )
            })?;
            // the library is intentionally leaked, see above
            let library = Box::leak(Box::new(library));

            unsafe fn resolve<T: Copy>(
                library: &'static Library,
                name: &[u8],
            ) -> Result<T, String> {
                unsafe {
                    library
                        .get::<T>(name)
                        .map(|s| *s)
                        .map_err(|e| format!("Failed to resolve Konclude symbol: {}", e))
                }
            }

            unsafe {
                Ok(KoncludeApi {
                    kb_create: resolve(library, b"konclude_kb_create\0")?,
                    kb_release: resolve(library, b"konclude_kb_release\0")?,
                    kb_entity: resolve(library, b"konclude_kb_entity\0")?,
                    kb_literal: resolve(library, b"konclude_kb_literal\0")?,
                    kb_expression: resolve(library, b"konclude_kb_expression\0")?,
                    kb_tell: resolve(library, b"konclude_kb_tell\0")?,
                    kb_retract: resolve(library, b"konclude_kb_retract\0")?,
                    kb_flush: resolve(library, b"konclude_kb_flush\0")?,
                    kb_is_consistent: resolve(library, b"konclude_kb_is_consistent\0")?,
                    kb_class_hierarchy: resolve(library, b"konclude_kb_class_hierarchy\0")?,
                })
            }
        })
        .as_ref()
        .map_err(|e| ReasonerError::Other(e.clone()))
}

fn to_cstring(value: &str) -> Result<CString, ReasonerError> {
    CString::new(value)
        .map_err(|e| ReasonerError::Other(format!("Invalid string '{}': {}", value, e)))
}

/// A Konclude knowledge base. Expressions are opaque handles owned by the
/// knowledge base; they become invalid when the knowledge base is released
/// and after each flush (a new ontology revision is built afterwards).
pub struct KoncludeKb {
    ptr: KbPtr,
}

// all knowledge base operations are serialized inside the C interface
unsafe impl Send for KoncludeKb {}
unsafe impl Sync for KoncludeKb {}

impl KoncludeKb {
    pub fn new() -> Result<Self, ReasonerError> {
        let api = api()?;
        let ptr = unsafe { (api.kb_create)() };
        if ptr.is_null() {
            return Err(ReasonerError::Other(
                "Failed to create Konclude knowledge base".to_string(),
            ));
        }
        Ok(KoncludeKb { ptr })
    }

    pub fn entity(&self, kind: c_int, iri: &str) -> Result<ExprPtr, ReasonerError> {
        let api = api()?;
        let iri_c = to_cstring(iri)?;
        let expression = unsafe { (api.kb_entity)(self.ptr, kind, iri_c.as_ptr()) };
        if expression.is_null() {
            return Err(ReasonerError::Other(format!(
                "Konclude failed to create entity '{}' (kind {})",
                iri, kind
            )));
        }
        Ok(expression)
    }

    pub fn literal(&self, lexical: &str, datatype_iri: &str) -> Result<ExprPtr, ReasonerError> {
        let api = api()?;
        let lexical_c = to_cstring(lexical)?;
        let datatype_c = to_cstring(datatype_iri)?;
        let expression =
            unsafe { (api.kb_literal)(self.ptr, lexical_c.as_ptr(), datatype_c.as_ptr()) };
        if expression.is_null() {
            return Err(ReasonerError::Other(format!(
                "Konclude failed to create literal '{}'^^'{}'",
                lexical, datatype_iri
            )));
        }
        Ok(expression)
    }

    pub fn expression(
        &self,
        expr_type: c_int,
        args: &[ExprPtr],
        cardinality: i32,
    ) -> Result<ExprPtr, ReasonerError> {
        let api = api()?;
        let expression = unsafe {
            (api.kb_expression)(
                self.ptr,
                expr_type,
                args.as_ptr(),
                args.len() as c_int,
                cardinality as c_int,
            )
        };
        if expression.is_null() {
            return Err(ReasonerError::Other(format!(
                "Konclude failed to build expression of type {} with {} arguments",
                expr_type,
                args.len()
            )));
        }
        Ok(expression)
    }

    pub fn tell(&self, axiom: ExprPtr) -> Result<(), ReasonerError> {
        let api = api()?;
        if unsafe { (api.kb_tell)(self.ptr, axiom) } != 0 {
            return Err(ReasonerError::Other(
                "Konclude failed to tell axiom".to_string(),
            ));
        }
        Ok(())
    }

    pub fn retract(&self, axiom: ExprPtr) -> Result<(), ReasonerError> {
        let api = api()?;
        if unsafe { (api.kb_retract)(self.ptr, axiom) } != 0 {
            return Err(ReasonerError::Other(
                "Konclude failed to retract axiom".to_string(),
            ));
        }
        Ok(())
    }

    pub fn flush(&self) -> Result<(), ReasonerError> {
        let api = api()?;
        if unsafe { (api.kb_flush)(self.ptr) } != 0 {
            return Err(ReasonerError::Other(
                "Konclude failed to install the ontology revision".to_string(),
            ));
        }
        Ok(())
    }

    pub fn is_consistent(&self) -> Result<bool, ReasonerError> {
        let api = api()?;
        match unsafe { (api.kb_is_consistent)(self.ptr) } {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(ReasonerError::Other(
                "Konclude consistency check failed".to_string(),
            )),
        }
    }

    pub fn class_hierarchy(&self) -> Result<ClassHierarchy, ReasonerError> {
        let api = api()?;

        unsafe extern "C" fn node_callback(
            user_data: *mut c_void,
            iris: *const *const c_char,
            count: c_int,
        ) {
            let hierarchy = unsafe { &mut *(user_data as *mut ClassHierarchy) };
            let mut group = Vec::with_capacity(count as usize);
            for i in 0..count as usize {
                let iri = unsafe { std::ffi::CStr::from_ptr(*iris.add(i)) };
                group.push(iri.to_string_lossy().into_owned());
            }
            hierarchy.nodes.push(group);
        }

        unsafe extern "C" fn edge_callback(
            user_data: *mut c_void,
            sub: *const c_char,
            sup: *const c_char,
        ) {
            let hierarchy = unsafe { &mut *(user_data as *mut ClassHierarchy) };
            let sub = unsafe { std::ffi::CStr::from_ptr(sub) }
                .to_string_lossy()
                .into_owned();
            let sup = unsafe { std::ffi::CStr::from_ptr(sup) }
                .to_string_lossy()
                .into_owned();
            hierarchy.edges.push((sub, sup));
        }

        let mut hierarchy = ClassHierarchy::default();
        let callbacks = KoncludeClassHierarchyCallbacks {
            user_data: &mut hierarchy as *mut ClassHierarchy as *mut c_void,
            node: node_callback,
            edge: edge_callback,
        };
        if unsafe { (api.kb_class_hierarchy)(self.ptr, &callbacks) } != 0 {
            return Err(ReasonerError::Other(
                "Konclude classification failed".to_string(),
            ));
        }
        Ok(hierarchy)
    }
}

impl Drop for KoncludeKb {
    fn drop(&mut self) {
        if let Ok(api) = api() {
            unsafe { (api.kb_release)(self.ptr) };
        }
    }
}
