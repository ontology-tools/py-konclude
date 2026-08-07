#!/usr/bin/env python3
"""Run ORE reasoning tasks with a pluggable OWL reasoner.

Subcommands:

* ``run`` executes a single ORE task on one ontology, following the ORE
  competition reasoner interface ``run <task> <ontology> [output]``.
* ``batch`` runs a task over a whole ORE dataset (driven by the
  ``fileorder.txt`` lists), timing each ontology and summarising the results.
  With ``--expected`` it scores each answer against a gold file (correct /
  incorrect / unexpected), mirroring the ORE competition metric.
* ``generate-expected`` runs a task over a dataset and writes an expected-results
  (gold) CSV from the current reasoner, for later scoring of other reasoners.

``task`` is one of ``consistency``, ``classification`` or ``realisation``
(``instantiation`` is accepted as an alias). Only the ``py-konclude`` reasoner
is wired up today; select a different one with ``--reasoner`` once it is
registered in :mod:`reasoners` (see ``--list-reasoners``).
"""

from __future__ import annotations

import argparse
import csv
import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, Sequence

import evaluation
import reasoners

DEFAULT_REASONER = "py-konclude"
DEFAULT_SERIALIZATION = "ofn"

TASK_NAMES = ("consistency", "classification", "realisation", "instantiation")

#: Exit codes for the ``run`` subcommand, so a caller (including ``batch``) can
#: tell the outcomes apart without parsing the result.
EXIT_OK = 0
EXIT_INCONSISTENT = 1  # consistency task only: ontology is inconsistent
EXIT_ERROR = 2  # the task could not be completed

#: OWL profiles present in the ORE ``pool_sample`` dataset.
PROFILES = ("el", "dl", "pure_dl")
#: Task name -> the dataset subdirectory holding its ``fileorder.txt``.
_DATASET_TASK_DIR = {
    "consistency": "consistency",
    "classification": "classification",
    "realisation": "instantiation",
    "instantiation": "instantiation",
}


# --------------------------------------------------------------------------- #
# Per-subprocess memory limit
# --------------------------------------------------------------------------- #

def parse_memory(text: str) -> int:
    """Parse a memory size (``4G``, ``512M``, ``2048K``, or plain bytes) to bytes.

    A bare number is bytes; a ``K``/``M``/``G``/``T`` suffix (optionally followed
    by ``B``) multiplies by the matching power of 1024.
    """
    s = text.strip().upper().rstrip("B")
    if not s:
        raise argparse.ArgumentTypeError(f"invalid memory size: {text!r}")
    units = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    factor = units.get(s[-1], 1)
    number = s[:-1] if s[-1] in units else s
    try:
        value = int(float(number) * factor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid memory size: {text!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(f"memory size must be positive: {text!r}")
    return value


def memory_limit_preexec(max_bytes: Optional[int]) -> Optional[Callable[[], None]]:
    """Return a ``preexec_fn`` capping the child's address space, or ``None``.

    Run in the forked child just before ``exec``, it sets ``RLIMIT_AS`` so an
    ontology that exhausts memory only kills its own subprocess (surfacing as an
    ``error`` result) rather than inviting the OOM killer to take down the whole
    batch run.
    """
    if not max_bytes:
        return None

    def _apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))

    return _apply


# --------------------------------------------------------------------------- #
# Single-ontology run
# --------------------------------------------------------------------------- #

def _write(text: str, output: Optional[str]) -> None:
    """Write ``text`` to ``output`` (a path) or to standard output."""
    if output is None:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        with open(output, "w") as f:
            f.write(text)


def _inferred_ontology(components) -> str:
    """Serialise ``components`` as a standalone OWL ontology (functional syntax)."""
    import pyhornedowl

    onto = pyhornedowl.PyIndexedOntology()
    onto.prefix_mapping.add_default_prefix_names()
    for component in components:
        onto.add_component(component)
    return onto.save_to_string(DEFAULT_SERIALIZATION)


def _run_task(task: str, reasoner: reasoners.Reasoner, output: Optional[str],
              signature: bool) -> int:
    """Execute one task with a single reasoning pass.

    Writes the ORE result (to ``output`` or, unless in signature mode, stdout)
    and/or prints a one-line machine-readable signature (for ``batch``). Returns
    an ``EXIT_*`` code.
    """
    write_result = output is not None or not signature

    if task == "consistency":
        consistent = reasoner.is_consistent()
        if write_result:
            _write("true" if consistent else "false", output)
        if signature:
            print(f"consistent {'true' if consistent else 'false'}")
        return EXIT_OK if consistent else EXIT_INCONSISTENT

    wanted = set(evaluation.TASK_AXIOMS[task])
    components = [c for c in reasoner.inferred_axioms()
                 if type(c).__name__ in wanted]
    if write_result:
        _write(_inferred_ontology(components), output)
    if signature:
        digest, count = evaluation.axioms_signature(components)
        print(f"axioms {count} {digest}")
    return EXIT_OK


def run_task(task: str, ontology: str, output: Optional[str],
             reasoner_name: str, input_format: Optional[str],
             signature: bool = False) -> int:
    """Load ``ontology`` with the chosen reasoner and run ``task``."""
    try:
        backend = reasoners.get_backend(reasoner_name)
        reasoner = backend(ontology, input_format)
        return _run_task(task, reasoner, output, signature)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001 - native panics derive from BaseException
        # pyo3's PanicException subclasses BaseException, so a reasoner crash
        # only becomes a clean exit code if we catch broadly here.
        print(f"error: {type(e).__name__}: {e}".splitlines()[0], file=sys.stderr)
        return EXIT_ERROR


# --------------------------------------------------------------------------- #
# Dataset iteration
# --------------------------------------------------------------------------- #

@dataclass
class BatchItem:
    """One ontology to process in a batch run."""
    profile: str
    name: str
    path: str


@dataclass
class BatchResult:
    """Outcome of processing a single ontology in a batch run."""
    profile: str
    name: str
    status: str  # consistent | inconsistent | ok | error | timeout
    seconds: float
    answer: Optional[str] = None   # comparable answer (verdict or axiom hash)
    count: Optional[int] = None    # inferred-axiom count, if applicable
    detail: str = ""               # human-readable note (count or error)
    correctness: Optional[str] = None  # set when scored against a gold file


def default_dataset() -> str:
    """Path of the bundled ORE ``pool_sample`` dataset, next to this script."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dataset", "pool_sample")


def iter_dataset(dataset: str, task: str, profiles: Sequence[str],
                 limit: Optional[int]) -> Iterator[BatchItem]:
    """Yield the ontologies for ``task`` in ``profiles`` per their fileorder."""
    task_dir = _DATASET_TASK_DIR[task]
    count = 0
    for profile in profiles:
        fileorder = os.path.join(dataset, profile, task_dir, "fileorder.txt")
        if not os.path.isfile(fileorder):
            raise FileNotFoundError(f"no fileorder for {profile}/{task}: {fileorder}")
        with open(fileorder) as f:
            for line in f:
                name = line.strip()  # fileorder.txt uses CRLF line endings
                if not name:
                    continue
                yield BatchItem(profile, name,
                                os.path.join(dataset, "files", name))
                count += 1
                if limit is not None and count >= limit:
                    return


def _first_error(proc: "subprocess.CompletedProcess[str]") -> str:
    """A short one-line reason from a failed subprocess.

    Prefer the ``run`` handler's own ``error: ...`` line over the raw native
    panic/backtrace the reasoner may also have dumped to stderr.
    """
    lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("error:"):
            return line
    if lines:
        return lines[0]
    if proc.returncode < 0:  # killed by a signal (e.g. OOM under --max-memory)
        return f"killed by signal {-proc.returncode}"
    return f"exit {proc.returncode}"


def process_item(item: BatchItem, task: str, reasoner_name: str,
                 timeout: float, output_dir: Optional[str],
                 memory_bytes: Optional[int] = None) -> BatchResult:
    """Run one ontology in an isolated subprocess and classify the outcome.

    Each ontology runs as ``main.py run --signature`` so a crash or hang in the
    native reasoner is contained to that ontology (and subject to ``timeout``,
    and to ``memory_bytes`` if set). The single reasoning pass yields both the
    comparable answer and, when ``output_dir`` is set, the persisted result
    ontology.
    """
    cmd = [sys.executable, os.path.abspath(__file__), "run", task, item.path,
           "--reasoner", reasoner_name, "--signature"]
    if output_dir is not None:
        ext = "txt" if task == "consistency" else "ofn"
        out_path = os.path.join(output_dir, f"{item.profile}__{item.name}.{task}.{ext}")
        cmd.append(out_path)

    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              preexec_fn=memory_limit_preexec(memory_bytes))
    except subprocess.TimeoutExpired:
        return BatchResult(item.profile, item.name, "timeout", timeout,
                           detail=f">{timeout:g}s")
    elapsed = time.perf_counter() - start

    return _interpret(item, task, proc, elapsed)


def _interpret(item: BatchItem, task: str,
               proc: "subprocess.CompletedProcess[str]",
               elapsed: float) -> BatchResult:
    """Turn a finished subprocess into a :class:`BatchResult`."""
    sig_line = _last_nonempty(proc.stdout)
    answer = evaluation.parse_line(task, sig_line) if sig_line else None

    if answer is None:
        return BatchResult(item.profile, item.name, "error", elapsed,
                           detail=_first_error(proc))

    if task == "consistency":
        status = "consistent" if answer == "true" else "inconsistent"
        return BatchResult(item.profile, item.name, status, elapsed, answer=answer)

    count = _axiom_count(sig_line)
    return BatchResult(item.profile, item.name, "ok", elapsed, answer=answer,
                       count=count, detail=f"{count} axioms")


def _last_nonempty(text: str) -> Optional[str]:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return None


def _axiom_count(sig_line: str) -> Optional[int]:
    parts = sig_line.split()
    if len(parts) == 3 and parts[0] == "axioms":
        return int(parts[1])
    return None


# --------------------------------------------------------------------------- #
# Batch run + scoring
# --------------------------------------------------------------------------- #

def run_batch(task: str, dataset: str, profiles: Sequence[str], reasoner_name: str,
              timeout: float, limit: Optional[int], output_dir: Optional[str],
              report: Optional[str], expected: Optional[str],
              memory_bytes: Optional[int] = None) -> int:
    """Run ``task`` over the dataset, print a summary and optional CSV report."""
    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)

    gold: Optional[Dict[evaluation.ExpectedKey, evaluation.Expectation]] = None
    if expected is not None:
        gold = evaluation.load_expected(expected)

    items = list(iter_dataset(dataset, task, profiles, limit))
    if not items:
        print("no ontologies selected", file=sys.stderr)
        return EXIT_ERROR

    mem_note = f", max {memory_bytes // 1024 // 1024}MiB" if memory_bytes else ""
    print(f"Running {task} on {len(items)} ontolog{'y' if len(items) == 1 else 'ies'} "
          f"with {reasoner_name} (timeout {timeout:g}s{mem_note})"
          f"{' scoring against gold' if gold else ''}\n", file=sys.stderr)

    results: List[BatchResult] = []
    for i, item in enumerate(items, 1):
        result = process_item(item, task, reasoner_name, timeout, output_dir,
                              memory_bytes)
        if gold is not None:
            key = (task, item.profile, item.name)
            result.correctness = _score(result, gold.get(key))
        results.append(result)
        label = result.correctness or result.status
        print(f"[{i}/{len(items)}] {item.profile}/{item.name}: "
              f"{label} ({result.seconds:.2f}s) {result.detail}".rstrip(),
              file=sys.stderr)

    _print_summary(task, results, gold)
    if report is not None:
        _write_report(report, task, results)
        print(f"\nWrote per-ontology report to {report}", file=sys.stderr)
    return EXIT_OK


def _score(result: BatchResult,
           expectation: Optional[evaluation.Expectation]) -> str:
    """Correctness verdict for a batch result against its expectation."""
    if result.status == "timeout":
        return evaluation.Correctness.TIMEOUT
    if result.status == "error":
        return evaluation.Correctness.ERROR
    return evaluation.verdict(result.answer, expectation)


def _print_summary(task: str, results: List[BatchResult],
                   gold: Optional[dict]) -> None:
    key = (lambda r: r.correctness) if gold is not None else (lambda r: r.status)
    counts: Dict[str, int] = {}
    for r in results:
        counts[key(r) or "unknown"] = counts.get(key(r) or "unknown", 0) + 1
    total_time = sum(r.seconds for r in results)

    print(f"\n=== {task}: {len(results)} ontologies ===")
    for name in sorted(counts):
        print(f"  {name:14} {counts[name]}")
    print(f"  {'total time':14} {total_time:.2f}s")
    if results:
        print(f"  {'avg time':14} {total_time / len(results):.2f}s")

    if gold is not None:
        _print_scoring(task, results, gold)


def _print_scoring(task: str, results: List[BatchResult], gold: dict) -> None:
    """ORE-style score: problems solved (correct) and time spent on them."""
    correct = [r for r in results
               if r.correctness == evaluation.Correctness.CORRECT]
    solved_time = sum(r.seconds for r in correct)
    print(f"  {'score':14} {len(correct)}/{len(results)} correct")
    print(f"  {'solved time':14} {solved_time:.2f}s")

    # Compare against the gold file's reference times where available.
    ref_pairs = [(r.seconds, gold[(task, r.profile, r.name)].reference_seconds)
                 for r in correct
                 if gold.get((task, r.profile, r.name)) is not None
                 and gold[(task, r.profile, r.name)].reference_seconds is not None]
    if ref_pairs:
        ours = sum(o for o, _ in ref_pairs)
        ref = sum(x for _, x in ref_pairs)
        ratio = ours / ref if ref else float("inf")
        print(f"  {'vs reference':14} {ours:.2f}s ours / {ref:.2f}s reference "
              f"({ratio:.2f}x) on {len(ref_pairs)} correct with a reference time")


def _write_report(path: str, task: str, results: List[BatchResult]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["profile", "ontology", "task", "status", "correctness",
                         "answer", "count", "seconds", "detail"])
        for r in results:
            writer.writerow([r.profile, r.name, task, r.status,
                             r.correctness or "", r.answer or "",
                             "" if r.count is None else r.count,
                             f"{r.seconds:.4f}", r.detail])


# --------------------------------------------------------------------------- #
# Expected-results (gold) generation
# --------------------------------------------------------------------------- #

def generate_expected(task: str, dataset: str, profiles: Sequence[str],
                      reasoner_name: str, timeout: float, limit: Optional[int],
                      out: str, memory_bytes: Optional[int] = None) -> int:
    """Run ``task`` over the dataset and write an expected-results gold CSV.

    The current reasoner's answers become the expectations and its measured
    times become the reference times, so other reasoners can be scored later.
    """
    items = list(iter_dataset(dataset, task, profiles, limit))
    if not items:
        print("no ontologies selected", file=sys.stderr)
        return EXIT_ERROR

    print(f"Generating expected {task} results for {len(items)} ontologies "
          f"with {reasoner_name}\n", file=sys.stderr)

    rows: List[dict] = []
    skipped = 0
    for i, item in enumerate(items, 1):
        result = process_item(item, task, reasoner_name, timeout, None,
                              memory_bytes)
        if result.answer is None:
            skipped += 1
            print(f"[{i}/{len(items)}] {item.profile}/{item.name}: "
                  f"{result.status} - skipped ({result.detail})", file=sys.stderr)
            continue
        rows.append({
            "task": task, "profile": item.profile, "ontology": item.name,
            "expected": result.answer,
            "count": "" if result.count is None else result.count,
            "reference_seconds": f"{result.seconds:.4f}",
        })
        print(f"[{i}/{len(items)}] {item.profile}/{item.name}: "
              f"{result.answer if task == 'consistency' else result.detail} "
              f"({result.seconds:.2f}s)", file=sys.stderr)

    evaluation.write_expected(out, rows)
    print(f"\nWrote {len(rows)} expectations to {out}"
          f"{f' ({skipped} skipped)' if skipped else ''}", file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _add_selection_args(p: argparse.ArgumentParser) -> None:
    """Dataset-selection arguments shared by batch and generate-expected."""
    p.add_argument("task", choices=sorted(TASK_NAMES), help="the ORE task to run")
    p.add_argument("--dataset", default=default_dataset(),
                   help="dataset root with <profile>/<task>/fileorder.txt and "
                        "files/ (default: bundled pool_sample)")
    p.add_argument("--profile", default="all",
                   help="OWL profile subdirectory to run, or 'all' to run every "
                        "profile present for the task (default: all)")
    p.add_argument("-r", "--reasoner", default=DEFAULT_REASONER,
                   help=f"reasoner backend to use (default: {DEFAULT_REASONER})")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="per-ontology timeout in seconds (default: 60)")
    p.add_argument("--max-memory", type=parse_memory, default=None, metavar="SIZE",
                   help="cap each ontology's subprocess address space (e.g. 4G, "
                        "512M); one that exceeds it fails as an error instead of "
                        "OOM-killing the whole run (default: unlimited)")
    p.add_argument("--limit", type=int, default=None,
                   help="process at most this many ontologies")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Note: the realisation/instantiation task only emits the inferred "
            "class assertions a backend reports through inferred_axioms(). The "
            "current py-konclude reasoner reports the class hierarchy only, so "
            "this task yields an empty result until instance queries land."
        ),
    )
    parser.add_argument("--list-reasoners", action="store_true",
                        help="list the available reasoner backends and exit")

    subparsers = parser.add_subparsers(dest="command")

    run_p = subparsers.add_parser(
        "run", help="run a task on a single ontology (ORE reasoner interface)")
    run_p.add_argument("task", choices=sorted(TASK_NAMES), help="the ORE task to run")
    run_p.add_argument("ontology", help="path to the input OWL ontology")
    run_p.add_argument("output", nargs="?",
                       help="path for the result file (default: standard output)")
    run_p.add_argument("-r", "--reasoner", default=DEFAULT_REASONER,
                       help=f"reasoner backend to use (default: {DEFAULT_REASONER})")
    run_p.add_argument("--input-format", choices=["ofn", "owx", "rdf", "owl"],
                       default=None,
                       help="input ontology serialization (default: auto-detect)")
    run_p.add_argument("--signature", action="store_true",
                       help="also print a one-line machine-readable result "
                            "(used by batch/generate-expected)")

    batch_p = subparsers.add_parser(
        "batch", help="run a task over an ORE dataset and summarise results")
    _add_selection_args(batch_p)
    batch_p.add_argument("--output-dir", default=None,
                         help="write each result into this directory")
    batch_p.add_argument("--report", default=None,
                         help="write a per-ontology CSV report to this path")
    batch_p.add_argument("--expected", default=None,
                         help="gold CSV to score answers against (correct/"
                              "incorrect/unexpected)")

    gen_p = subparsers.add_parser(
        "generate-expected",
        help="write an expected-results (gold) CSV from the current reasoner")
    _add_selection_args(gen_p)
    gen_p.add_argument("-o", "--out", required=True,
                       help="path for the expected-results CSV")

    return parser


def _profiles(dataset: str, task: str, arg: str) -> List[str]:
    """Resolve the ``--profile`` argument against the dataset on disk.

    ``all`` expands to every profile subdirectory that actually has a
    ``fileorder.txt`` for the task, so both the pool (el/dl/pure_dl) and the
    verified set (dl/el/rl) work without hard-coding profile names.
    """
    if arg != "all":
        return [arg]
    task_dir = _DATASET_TASK_DIR[task]
    found = [name for name in sorted(os.listdir(dataset))
             if os.path.isfile(os.path.join(dataset, name, task_dir, "fileorder.txt"))]
    return found or list(PROFILES)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    # --list-reasoners is informational and needs no subcommand.
    if "--list-reasoners" in argv:
        print("\n".join(reasoners.available()))
        return EXIT_OK

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_ERROR

    if args.command == "run":
        return run_task(args.task, args.ontology, args.output,
                        args.reasoner, args.input_format, args.signature)

    try:
        if not os.path.isdir(args.dataset):
            raise FileNotFoundError(f"dataset not found: {args.dataset}")
        profiles = _profiles(args.dataset, args.task, args.profile)
        if args.command == "batch":
            return run_batch(args.task, args.dataset, profiles,
                             args.reasoner, args.timeout, args.limit,
                             args.output_dir, args.report, args.expected,
                             args.max_memory)
        if args.command == "generate-expected":
            return generate_expected(args.task, args.dataset, profiles,
                                     args.reasoner, args.timeout, args.limit,
                                     args.out, args.max_memory)
    except FileNotFoundError as e:
        parser.error(str(e))

    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
