"""Evaluation metric for ORE tasks: compare results against expected answers.

The ORE competition scores a reasoner by *correctness* against an expected
answer per problem, counting problems solved (and the time taken on them).
This module provides the pieces to do the same locally:

* a task-independent **signature** of a result — ``true``/``false`` for
  consistency, or a stable hash of the inferred axiom set for classification /
  realisation — so two reasoners' answers can be compared without keeping the
  full result ontologies around;
* reading/writing an **expected-results CSV** (the gold file); and
* classifying each outcome into an ORE-style :class:`Correctness` verdict.

The gold file is deliberately reasoner-agnostic: generate it once from a
trusted reasoner (or import the ORE verified test set) and score any reasoner
against it later.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

#: Inferred axiom component types that define each task's result.
TASK_AXIOMS = {
    "classification": ("SubClassOf", "EquivalentClasses"),
    "realisation": ("ClassAssertion",),
    "instantiation": ("ClassAssertion",),
}


OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
OWL_NOTHING = "http://www.w3.org/2002/07/owl#Nothing"


class Correctness:
    """ORE-style verdicts for a single problem."""
    CORRECT = "correct"      # matched the expected answer
    INCORRECT = "incorrect"  # produced an answer, but the wrong one
    UNEXPECTED = "unexpected"  # ran fine, but no expected answer to check
    ERROR = "error"          # the reasoner failed to produce an answer
    TIMEOUT = "timeout"      # the reasoner ran out of time


# --------------------------------------------------------------------------- #
# Result signatures
# --------------------------------------------------------------------------- #

class _Equivalences:
    """Union-find over class IRIs, with a canonical (smallest-IRI) representative.

    A reasoner is free to pick *any* member of an equivalence group as the
    representative when it emits ``SubClassOf`` edges, so the same result can be
    serialised with different-looking axioms across runs (or reasoners). Folding
    every class onto the lexicographically smallest IRI in its group makes the
    inferred hierarchy invariant under that choice.
    """

    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        parent = self._parent
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:
            x, parent[x] = parent[x], root
        return root

    def union(self, members: Iterable[str]) -> None:
        members = list(members)
        for m in members:
            self._parent.setdefault(m, m)
        root = min(self.find(m) for m in members)  # smallest IRI wins
        for m in members:
            self._parent[self.find(m)] = root
        self._parent[root] = root

    def rep(self, x: str) -> str:
        return self.find(x) if x in self._parent else x


def axioms_signature(components: Iterable) -> Tuple[str, int]:
    """Return ``(hash, count)`` for a set of inferred axioms.

    The signature is normalised so it identifies the *logical* inference result,
    not one particular serialisation of it, matching the ORE competition's
    normal form so results from different reasoners are comparable:

    * classes are quotiented by inferred equivalence and every axiom is rewritten
      in terms of canonical (smallest-IRI) representatives;
    * trivially-entailed edges are dropped -- reflexive ones, ``X ⊑ owl:Thing``,
      and ``owl:Nothing ⊑ X`` (a reasoner is free to emit or omit these, and in
      particular unsatisfiable classes are subclasses of everything).

    The set is then sorted and hashed; ``count`` is the number of distinct
    normalised axioms.
    """
    components = list(components)

    # First pass: collect equivalence groups so representatives are known.
    eq = _Equivalences()
    for c in components:
        if type(c).__name__ == "EquivalentClasses":
            eq.union(str(m) for m in c.first)
    nothing, thing = eq.rep(OWL_NOTHING), eq.rep(OWL_THING)

    lines = set()
    for c in components:
        kind = type(c).__name__
        if kind == "EquivalentClasses":
            group = sorted(str(m) for m in c.first)
            lines.add("EquivalentClasses\t" + "\t".join(group))
        elif kind == "SubClassOf":
            sub, sup = eq.rep(str(c.sub)), eq.rep(str(c.sup))
            if sub != sup and sub != nothing and sup != thing:
                lines.add(f"SubClassOf\t{sub}\t{sup}")
        elif kind == "ClassAssertion":
            ce = eq.rep(str(c.ce))
            if ce != thing:  # everything is an instance of owl:Thing
                lines.add(f"ClassAssertion\t{ce}\t{c.i}")
        else:  # fallback: stable rendering
            lines.add(f"{kind}\t{c}")

    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return digest, len(lines)


#: Signature line emitted for a hierarchy task (classification / realisation /
#: instantiation) when the ontology is inconsistent. Such an ontology entails
#: everything and so has no hierarchy to compare -- the verdict is the answer.
#: ``run_native.py`` reports the same case as ``status=inconsistent`` with an
#: empty ``answer``; both sides must agree or a hash would be scored against a
#: verdict and could never match.
INCONSISTENT_LINE = "inconsistent"


def emit_line(task: str, reasoner) -> str:
    """Machine-readable one-line result for the ``run --signature`` protocol.

    ``consistency``  -> ``consistent true`` / ``consistent false``
    other tasks      -> ``axioms <count> <sha256>``, or :data:`INCONSISTENT_LINE`
                        when the ontology is inconsistent
    """
    if task == "consistency":
        return f"consistent {'true' if reasoner.is_consistent() else 'false'}"
    if not reasoner.is_consistent():
        return INCONSISTENT_LINE
    wanted = set(TASK_AXIOMS[task])
    components = [c for c in reasoner.inferred_axioms()
                 if type(c).__name__ in wanted]
    digest, count = axioms_signature(components)
    return f"axioms {count} {digest}"


def parse_line(task: str, line: str) -> Optional[str]:
    """Extract the comparable answer from a ``run --signature`` line.

    Returns ``true``/``false`` for consistency or the axiom-set hash for other
    tasks, or ``None`` if the line carries no comparable answer -- which covers
    both a malformed line and :data:`INCONSISTENT_LINE`, since an inconsistent
    ontology has no hierarchy to hash. Callers must therefore test for
    :data:`INCONSISTENT_LINE` themselves before treating ``None`` as an error.
    """
    parts = line.strip().split()
    if task == "consistency":
        if len(parts) == 2 and parts[0] == "consistent":
            return parts[1]
        return None
    if len(parts) == 3 and parts[0] == "axioms":
        return parts[2]
    return None


# --------------------------------------------------------------------------- #
# Expected-results (gold) file
# --------------------------------------------------------------------------- #

@dataclass
class Expectation:
    """The expected answer (and optional reference time) for one problem."""
    expected: str  # "true"/"false" for consistency, else the axiom-set hash
    count: Optional[int] = None            # inferred-axiom count, if known
    reference_seconds: Optional[float] = None  # a reference reasoner's time

_EXPECTED_FIELDS = ["task", "profile", "ontology", "expected", "count",
                    "reference_seconds"]

#: A gold file is keyed by (task, profile, ontology).
ExpectedKey = Tuple[str, str, str]


def load_expected(path: str) -> Dict[ExpectedKey, Expectation]:
    """Load an expected-results CSV into a lookup keyed by (task, profile, name)."""
    expected: Dict[ExpectedKey, Expectation] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["task"], row["profile"], row["ontology"])
            expected[key] = Expectation(
                expected=row["expected"],
                count=int(row["count"]) if row.get("count") else None,
                reference_seconds=(float(row["reference_seconds"])
                                   if row.get("reference_seconds") else None),
            )
    return expected


def write_expected(path: str, rows: List[dict]) -> None:
    """Write expected-results rows (dicts with :data:`_EXPECTED_FIELDS`)."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_EXPECTED_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def verdict(answer: Optional[str], expectation: Optional[Expectation]) -> str:
    """Compare a produced ``answer`` against the gold ``expectation``."""
    if answer is None:
        return Correctness.ERROR
    if expectation is None:
        return Correctness.UNEXPECTED
    return Correctness.CORRECT if answer == expectation.expected \
        else Correctness.INCORRECT
