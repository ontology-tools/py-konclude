"""Reasoner backends for the ORE task runner.

A *backend* knows how to turn an ontology file into a reasoner object that
exposes the (subset of the) py-horned-owl ``PyReasoner`` interface the ORE
tasks need:

* ``is_consistent() -> bool``
* ``inferred_axioms() -> set[Component]``

Backends are looked up by name through :func:`get_backend`. Only py-konclude
is available today; register additional reasoners with :func:`register` (or the
``@register`` decorator) so the CLI can select them by name without any other
change.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol


class Reasoner(Protocol):
    """The reasoner capabilities the ORE tasks rely on.

    This is intentionally the common subset of py-horned-owl's ``PyReasoner``;
    a backend for a non-py-horned-owl reasoner only has to provide these.
    """

    def is_consistent(self) -> bool: ...

    def inferred_axioms(self) -> "set[Any]": ...


#: A backend is a callable ``(ontology_path, serialization) -> Reasoner`` where
#: ``serialization`` is an explicit input format or ``None`` to auto-detect.
Backend = Callable[[str, Optional[str]], Reasoner]


def detect_serialization(ontology_path: str) -> Optional[str]:
    """Guess the OWL serialization of ``ontology_path`` from its first bytes.

    The ORE dataset ships ``.owl`` files that are actually OWL functional
    syntax, which the extension-based guesser mistakes for RDF/XML. Returns a
    py-horned-owl serialization name (``ofn``/``owx``/``rdf``) or ``None`` to
    let py-horned-owl decide.
    """
    import re

    with open(ontology_path, "r", errors="replace") as f:
        head = f.read(8192).lstrip()
    if head.startswith("Prefix(") or head.startswith("Ontology("):
        return "ofn"
    # XML family: distinguish OWL/XML (root <Ontology>) from RDF/XML (<rdf:RDF>),
    # both of which may open with an <?xml?> declaration and a doctype.
    if "<rdf:RDF" in head:
        return "rdf"
    if re.search(r"<Ontology[\s>]", head):
        return "owx"
    if head.startswith("<?xml"):
        return "rdf"
    return None

_BACKENDS: Dict[str, Backend] = {}


def register(*names: str) -> Callable[[Backend], Backend]:
    """Register ``backend`` under one or more names (first is canonical)."""

    def decorator(backend: Backend) -> Backend:
        for name in names:
            _BACKENDS[name.lower()] = backend
        return backend

    return decorator


def available() -> "list[str]":
    """Names under which a backend can be selected, sorted."""
    return sorted(_BACKENDS)


def get_backend(name: str) -> Backend:
    """Look up a backend by name, raising ``KeyError`` with a helpful message."""
    try:
        return _BACKENDS[name.lower()]
    except KeyError:
        raise KeyError(
            f"unknown reasoner {name!r}; available: {', '.join(available())}"
        ) from None


@register("py-konclude", "pykonclude", "konclude")
def _py_konclude(ontology_path: str, serialization: Optional[str] = None) -> Reasoner:
    """Load an ontology with py-horned-owl and reason over it with Konclude."""
    import pyhornedowl
    from pykonclude import create_reasoner

    if serialization is None:
        serialization = detect_serialization(ontology_path)
    ontology = pyhornedowl.open_ontology(ontology_path, serialization)
    return create_reasoner(ontology)
