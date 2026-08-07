#!/usr/bin/env python3
"""Run ORE tasks with a *native* reasoner binary, for comparable timings.

``main.py`` drives reasoners in-process through py-horned-owl (e.g. py-konclude,
which builds the knowledge base directly via Konclude's C API). To measure the
stand-alone reasoner as the ORE competition did -- the real executable, reading
an ontology file, on this machine's hardware -- use this script instead.

It reuses ``main.py``'s dataset iteration and ``evaluation``'s scoring, so the
report and gold files are the same shape and directly comparable to a
``main.py batch`` run.

Pipeline per ontology:

1. Determine the input file. When the reasoner reads the ontology's
   serialization natively (Konclude parses both OWL functional syntax and
   OWL/XML) the original file is used as-is; otherwise it is converted to
   OWL/XML with py-horned-owl, cached on disk. Note that the conversion is a
   last resort: py-horned-owl's OWL/XML writer abbreviates IRIs inside the
   ``IRI=`` attribute (invalid OWL/XML; ``abbreviatedIRI=`` would be correct),
   which a conforming parser reads as unknown relative IRIs -- datatypes like
   ``xsd:decimal`` silently lose their meaning and range axioms stop
   constraining anything, which has produced wrong "consistent" verdicts.
2. Invoke the native binary for the task, under a wall-clock timeout.
3. Parse the answer: the consistency verdict from the console, or the inferred
   axioms from the reasoner's output ontology (normalised to the same signature
   ``main.py`` uses, so answers are comparable across reasoners).

Only Konclude is wired up; add another native reasoner by registering a
:class:`NativeReasoner` (command templates + a consistency-verdict parser).

Examples::

    ./run_native.py consistency --profile el --limit 20 --report native.csv
    ./run_native.py classification --expected dataset/verified/expected/... \\
        --dataset dataset/verified --profile el
    ./run_native.py consistency --write-expected gold-consistency.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import evaluation
from main import (BatchItem, PROFILES, default_dataset, iter_dataset,
                  memory_limit_preexec, parse_memory, _profiles)

EXIT_OK = 0
EXIT_ERROR = 2


# --------------------------------------------------------------------------- #
# Native reasoner definitions
# --------------------------------------------------------------------------- #

@dataclass
class NativeReasoner:
    """How to drive a native reasoner binary for the ORE tasks.

    ``command`` builds the argv for a task given the resolved binary, the
    (converted) input path and an output path. ``consistency_verdict`` maps the
    process' combined output to ``"true"``/``"false"`` (or ``None`` if it cannot
    be determined). ``reasoning_ms`` optionally extracts the reasoner's own
    reported reasoning time (excluding parsing) from its output.
    """
    name: str
    command: Callable[[str, str, str, str], List[str]]
    consistency_verdict: Callable[[str], Optional[str]]
    reasoning_ms: Optional[Callable[[str], Optional[float]]] = None
    #: Output serialization the reasoner writes (for classification/realisation).
    output_format: str = "owx"
    #: Input serializations the binary parses itself; other inputs are
    #: converted to OWL/XML first (see the module docstring for the caveats).
    input_formats: frozenset = frozenset()


def _konclude_command(binary: str, task: str, inp: str, out: str) -> List[str]:
    # -w AUTO is required: the default single-thread mode deadlocks in precompute.
    verb = {"consistency": "consistency",
            "classification": "classification",
            "realisation": "realization",
            "instantiation": "realization"}[task]
    cmd = [binary, verb, "-w", "AUTO", "-i", inp]
    if task != "consistency":  # consistency reports to the console, no -o needed
        cmd += ["-o", out]
    return cmd


def _konclude_verdict(output: str) -> Optional[str]:
    if re.search(r"is inconsistent", output):
        return "false"
    if re.search(r"is consistent", output):
        return "true"
    return None


_KONCLUDE_MS = re.compile(r"Finished [\w ]+? in (\d+) ms")


def _konclude_reasoning_ms(output: str) -> Optional[float]:
    # Sum the reported phase times (preprocessing, precomputing, the task) --
    # these exclude ontology parsing, matching the ORE "reasoning time" notion.
    times = [int(m) for m in _KONCLUDE_MS.findall(output)]
    return float(sum(times)) if times else None


NATIVE_REASONERS: Dict[str, NativeReasoner] = {
    "konclude": NativeReasoner(
        name="konclude",
        command=_konclude_command,
        consistency_verdict=_konclude_verdict,
        reasoning_ms=_konclude_reasoning_ms,
        input_formats=frozenset({"ofn", "owx"}),
    ),
}


def default_konclude_binary() -> Optional[str]:
    """Locate the Konclude binary from the environment or the usual checkout."""
    env = os.environ.get("KONCLUDE_BINARY") or os.environ.get("KONCLUDE_BIN")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    guess = os.path.join(here, "..", "..", "Konclude", "Release", "Konclude")
    return guess if os.path.isfile(guess) else None


# --------------------------------------------------------------------------- #
# Conversion cache (functional syntax -> OWL/XML)
# --------------------------------------------------------------------------- #

def native_input(item: BatchItem, reasoner: NativeReasoner, cache_dir: str) -> str:
    """The input file to hand the reasoner binary for ``item``.

    The original file whenever the binary parses its serialization itself;
    a cached OWL/XML conversion otherwise.
    """
    from reasoners import detect_serialization

    if detect_serialization(item.path) in reasoner.input_formats:
        return item.path
    return ensure_owlxml(item, cache_dir)


def ensure_owlxml(item: BatchItem, cache_dir: str) -> str:
    """Return a cached OWL/XML copy of ``item``, converting it if needed."""
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{item.profile}__{item.name}.owx")
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(item.path):
        return out

    import pyhornedowl
    from reasoners import detect_serialization

    onto = pyhornedowl.open_ontology(item.path, detect_serialization(item.path))
    onto.save_to_file(out, "owx")
    return out


# --------------------------------------------------------------------------- #
# Running one ontology
# --------------------------------------------------------------------------- #

@dataclass
class NativeResult:
    profile: str
    name: str
    status: str            # consistent | inconsistent | ok | error | timeout
    seconds: float         # wall-clock of the reasoning process
    answer: Optional[str] = None
    count: Optional[int] = None
    reasoning_ms: Optional[float] = None
    detail: str = ""
    correctness: Optional[str] = None


def _read_axioms(path: str, task: str, fmt: str):
    import pyhornedowl

    onto = pyhornedowl.open_ontology(path, fmt)
    wanted = set(evaluation.TASK_AXIOMS[task])
    return [a.component for a in onto.get_axioms()
            if type(a.component).__name__ in wanted]


def process_item(item: BatchItem, task: str, reasoner: NativeReasoner,
                 binary: str, timeout: float, cache_dir: str,
                 out_dir: Optional[str],
                 memory_bytes: Optional[int] = None) -> NativeResult:
    """Resolve the input, run the native reasoner, and classify the outcome."""
    try:
        inp = native_input(item, reasoner, cache_dir)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001
        return NativeResult(item.profile, item.name, "error", 0.0,
                            detail=f"convert: {type(e).__name__}: {e}".splitlines()[0])

    results_dir = out_dir or os.path.join(cache_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(
        results_dir, f"{item.profile}__{item.name}.{task}.{reasoner.output_format}")
    cmd = reasoner.command(binary, task, inp, out_path)

    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              preexec_fn=memory_limit_preexec(memory_bytes))
    except subprocess.TimeoutExpired:
        return NativeResult(item.profile, item.name, "timeout", timeout,
                            detail=f">{timeout:g}s")
    elapsed = time.perf_counter() - start
    output = proc.stdout + proc.stderr
    reasoning_ms = reasoner.reasoning_ms(output) if reasoner.reasoning_ms else None

    if task == "consistency":
        answer = reasoner.consistency_verdict(output)
        if answer is None:
            return NativeResult(item.profile, item.name, "error", elapsed,
                                reasoning_ms=reasoning_ms,
                                detail=_error(output, proc.returncode))
        status = "consistent" if answer == "true" else "inconsistent"
        return NativeResult(item.profile, item.name, status, elapsed,
                            answer=answer, reasoning_ms=reasoning_ms)

    # classification / realisation: an inconsistent ontology has no hierarchy
    if reasoner.consistency_verdict(output) == "false":
        return NativeResult(item.profile, item.name, "inconsistent", elapsed,
                            reasoning_ms=reasoning_ms, detail="ontology inconsistent")
    if not os.path.isfile(out_path):
        return NativeResult(item.profile, item.name, "error", elapsed,
                            reasoning_ms=reasoning_ms,
                            detail=_error(output, proc.returncode))
    try:
        components = _read_axioms(out_path, task, reasoner.output_format)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001
        return NativeResult(item.profile, item.name, "error", elapsed,
                            reasoning_ms=reasoning_ms,
                            detail=f"parse: {type(e).__name__}: {e}".splitlines()[0])

    digest, count = evaluation.axioms_signature(components)
    return NativeResult(item.profile, item.name, "ok", elapsed, answer=digest,
                        count=count, reasoning_ms=reasoning_ms,
                        detail=f"{count} axioms")


def _error(output: str, returncode: int = 0) -> str:
    for line in output.splitlines():
        if "{error}" in line:
            return line.split("{error}", 1)[1].strip(" >").strip()
    if returncode < 0:  # killed by a signal (e.g. OOM under --max-memory)
        return f"killed by signal {-returncode}"
    return "no answer produced"


# --------------------------------------------------------------------------- #
# Batch driver
# --------------------------------------------------------------------------- #

def run(task: str, dataset: str, profiles: Sequence[str], reasoner: NativeReasoner,
        binary: str, timeout: float, limit: Optional[int], cache_dir: str,
        out_dir: Optional[str], report: Optional[str], expected: Optional[str],
        write_expected: Optional[str], memory_bytes: Optional[int] = None) -> int:
    gold = evaluation.load_expected(expected) if expected else None
    if out_dir is not None:
        os.makedirs(out_dir, exist_ok=True)

    items = list(iter_dataset(dataset, task, profiles, limit))
    if not items:
        print("no ontologies selected", file=sys.stderr)
        return EXIT_ERROR

    mem_note = f", max {memory_bytes // 1024 // 1024}MiB" if memory_bytes else ""
    print(f"Running native {reasoner.name} {task} on {len(items)} ontologies "
          f"(timeout {timeout:g}s{mem_note})"
          f"{' scoring against gold' if gold else ''}\n", file=sys.stderr)

    results: List[NativeResult] = []
    for i, item in enumerate(items, 1):
        r = process_item(item, task, reasoner, binary, timeout, cache_dir, out_dir,
                         memory_bytes)
        if gold is not None:
            r.correctness = _score(r, gold.get((task, item.profile, item.name)))
        results.append(r)
        label = r.correctness or r.status
        ms = f" [{r.reasoning_ms:.0f}ms reasoning]" if r.reasoning_ms is not None else ""
        print(f"[{i}/{len(items)}] {item.profile}/{item.name}: "
              f"{label} ({r.seconds:.2f}s{ms}) {r.detail}".rstrip(), file=sys.stderr)

    _summary(task, results, gold)
    if report is not None:
        _write_report(report, task, reasoner.name, results)
        print(f"\nWrote per-ontology report to {report}", file=sys.stderr)
    if write_expected is not None:
        _write_expected(write_expected, task, results)
        print(f"Wrote expected-results gold to {write_expected}", file=sys.stderr)
    return EXIT_OK


def _score(r: NativeResult, exp: Optional[evaluation.Expectation]) -> str:
    if r.status == "timeout":
        return evaluation.Correctness.TIMEOUT
    if r.status == "error":
        return evaluation.Correctness.ERROR
    if r.status == "inconsistent" and r.answer is None:
        # inconsistent under a hierarchy task: compare the verdict, not a hash
        return (evaluation.Correctness.CORRECT
                if exp is not None and exp.expected == "false"
                else evaluation.Correctness.UNEXPECTED if exp is None
                else evaluation.Correctness.INCORRECT)
    return evaluation.verdict(r.answer, exp)


def _summary(task: str, results: List[NativeResult], gold: Optional[dict]) -> None:
    key = (lambda r: r.correctness) if gold is not None else (lambda r: r.status)
    counts: Dict[str, int] = {}
    for r in results:
        counts[key(r) or "unknown"] = counts.get(key(r) or "unknown", 0) + 1
    total = sum(r.seconds for r in results)

    print(f"\n=== native {task}: {len(results)} ontologies ===")
    for name in sorted(counts):
        print(f"  {name:14} {counts[name]}")
    print(f"  {'total time':14} {total:.2f}s")
    if results:
        print(f"  {'avg time':14} {total / len(results):.2f}s")
    if gold is not None:
        correct = [r for r in results
                   if r.correctness == evaluation.Correctness.CORRECT]
        print(f"  {'score':14} {len(correct)}/{len(results)} correct")
        print(f"  {'solved time':14} {sum(r.seconds for r in correct):.2f}s")


def _write_report(path: str, task: str, reasoner: str,
                  results: List[NativeResult]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["reasoner", "profile", "ontology", "task", "status",
                    "correctness", "answer", "count", "seconds", "reasoning_ms",
                    "detail"])
        for r in results:
            w.writerow([reasoner, r.profile, r.name, task, r.status,
                        r.correctness or "", r.answer or "",
                        "" if r.count is None else r.count, f"{r.seconds:.4f}",
                        "" if r.reasoning_ms is None else f"{r.reasoning_ms:.0f}",
                        r.detail])


def _write_expected(path: str, task: str, results: List[NativeResult]) -> None:
    rows = []
    for r in results:
        answer = r.answer
        if answer is None and r.status == "inconsistent":
            answer = "false"
        if answer is None:
            continue  # only ontologies with a definite answer become gold
        rows.append({
            "task": task, "profile": r.profile, "ontology": r.name,
            "expected": answer,
            "count": "" if r.count is None else r.count,
            "reference_seconds": f"{r.seconds:.4f}",
        })
    evaluation.write_expected(path, rows)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task", choices=sorted(evaluation.TASK_AXIOMS.keys() |
                                               {"consistency"}),
                        help="the ORE task to run")
    parser.add_argument("--reasoner", choices=sorted(NATIVE_REASONERS),
                        default="konclude", help="native reasoner (default: konclude)")
    parser.add_argument("--konclude", "--binary", dest="binary",
                        default=default_konclude_binary(),
                        help="path to the reasoner binary "
                             "(default: $KONCLUDE_BINARY or ../../Konclude/Release/Konclude)")
    parser.add_argument("--dataset", default=default_dataset(),
                        help="dataset root (default: bundled pool_sample)")
    parser.add_argument("--profile", choices=[*PROFILES, "all"], default="all",
                        help="OWL profile subset (default: all)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-ontology wall-clock timeout in seconds (default: 300)")
    parser.add_argument("--max-memory", type=parse_memory, default=None,
                        metavar="SIZE",
                        help="cap each reasoner subprocess's address space (e.g. "
                             "8G, 512M); one that exceeds it fails as an error "
                             "instead of OOM-killing the whole run (default: "
                             "unlimited)")
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most this many ontologies")
    parser.add_argument("--owx-cache", default=None,
                        help="directory for cached OWL/XML conversions "
                             "(default: <dataset>/.owx-cache)")
    parser.add_argument("--output-dir", default=None,
                        help="directory to keep reasoner result ontologies")
    parser.add_argument("--report", default=None,
                        help="write a per-ontology CSV report to this path")
    parser.add_argument("--expected", default=None,
                        help="gold CSV to score answers against")
    parser.add_argument("--write-expected", default=None,
                        help="write this reasoner's answers as a gold CSV")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.binary or not os.path.isfile(args.binary):
        parser.error(f"reasoner binary not found: {args.binary!r}; pass --konclude "
                     "or set $KONCLUDE_BINARY")

    reasoner = NATIVE_REASONERS[args.reasoner]
    cache_dir = args.owx_cache or os.path.join(args.dataset, ".owx-cache")
    try:
        return run(args.task, args.dataset,
                   _profiles(args.dataset, args.task, args.profile), reasoner,
                   args.binary, args.timeout, args.limit, cache_dir,
                   args.output_dir, args.report, args.expected, args.write_expected,
                   args.max_memory)
    except FileNotFoundError as e:
        parser.error(str(e))


if __name__ == "__main__":
    raise SystemExit(main())
