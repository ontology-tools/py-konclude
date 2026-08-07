#!/usr/bin/env python3
"""Download the ORE 2015 *verified test set* as a gold evaluation dataset.

The ORE 2015 competition framework ships a small, verified test set: a handful
of ontologies together with the expected (correct) answer for each task and a
reference reasoner's timing. This is the only per-ontology gold data that
survives publicly (the competition pool itself was published without answers).

This script fetches that test set from the framework's GitHub repository and
lays it out in the dataset format ``main.py`` understands:

    <out>/files/<ontology>                      the ontologies
    <out>/<profile>/<task>/fileorder.txt        the run order per profile/task
    <out>/expected/<task>.csv                   the gold answers + reference times

Then evaluate against it, e.g.::

    python main.py batch consistency --dataset dataset/verified \\
        --profile el --expected dataset/verified/expected/consistency.csv

Consistency answers (true/false) match exactly. Classification / realisation
gold is stored as a normalised inferred-axiom hash computed with
:mod:`evaluation`; it only matches a reasoner that produces the same inference
closure, so treat those as best-effort (consistency is the reliable check).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Dict, List, Optional

import evaluation

REPO = "ykazakov/ore-2015-competition-framework"
BRANCH = "master"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
#: task -> (framework query suffix, our task-dir name)
TASKS = {
    "consistency": ("cons", "consistency"),
    "classification": ("classify", "classification"),
    "realisation": ("realise", "instantiation"),
}


@lru_cache(maxsize=1)
def _token() -> Optional[str]:
    """A GitHub token from the environment or the local ``gh`` CLI, if any."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _get(url: str, api: bool = False) -> bytes:
    headers = {"Accept": "application/vnd.github+json"} if api else {}
    if api and _token():
        headers["Authorization"] = f"Bearer {_token()}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _blob(path: str) -> bytes:
    """File contents by repo path, via raw.githubusercontent (not API-limited)."""
    return _get(f"{RAW}/{urllib.parse.quote(path)}")


def _tree() -> List[dict]:
    head = json.loads(_get(f"{API}/branches/{BRANCH}",
                           api=True))["commit"]["commit"]["tree"]["sha"]
    return json.loads(_get(f"{API}/git/trees/{head}?recursive=1", api=True))["tree"]


def _safe_name(path: str) -> str:
    """Filesystem-safe basename (the corpus has %2F-encoded names)."""
    return os.path.basename(path).replace("%2F", "_").replace("/", "_")


def fetch(out_dir: str) -> None:
    tree = _tree()
    blobs = {t["path"]: t["sha"] for t in tree if t["type"] == "blob"}

    files_dir = os.path.join(out_dir, "files")
    os.makedirs(files_dir, exist_ok=True)

    # 1. ontologies: data/ontologies/test/<profile>/<file>
    ontologies: Dict[str, Dict[str, str]] = {}  # profile -> {orig_basename: safe_name}
    for path, sha in blobs.items():
        parts = path.split("/")
        if len(parts) == 5 and path.startswith("data/ontologies/test/"):
            profile, basename = parts[3], parts[4]
            safe = _safe_name(basename)
            with open(os.path.join(files_dir, safe), "wb") as f:
                f.write(_blob(path))
            ontologies.setdefault(profile, {})[basename] = safe
            print(f"  ontology {profile}/{safe}", file=sys.stderr)

    # 2. expectations: data/expectations/relative/<task>/test/<profile>/<ont>-<sfx>.dat/*
    #    -> gold answer (query-result-data.owl) + reference time (query-response.dat)
    fileorder: Dict[str, Dict[str, List[str]]] = {}   # task -> profile -> [safe_name]
    expected_rows: Dict[str, List[dict]] = {t: [] for t in TASKS}
    for task, (suffix, _task_dir) in TASKS.items():
        base = f"data/expectations/relative/{task}/test"
        dat_dirs = {p.rsplit("/", 1)[0] for p in blobs
                    if p.startswith(base) and p.endswith("/query-result-data.owl")}
        for dat in sorted(dat_dirs):
            profile = dat.split("/")[5]
            ont_file = _safe_name(dat.split("/")[-1][: -len(f"-{suffix}.dat")])
            answer, count = _expectation(task, blobs, dat, files_dir, ont_file)
            if answer is None:
                continue
            fileorder.setdefault(task, {}).setdefault(profile, []).append(ont_file)
            expected_rows[task].append({
                "task": task, "profile": profile, "ontology": ont_file,
                "expected": answer, "count": "" if count is None else count,
                "reference_seconds": _reference_seconds(blobs, dat),
            })
            print(f"  expected {task}/{profile}/{ont_file} = "
                  f"{answer if task == 'consistency' else answer[:12] + '...'}",
                  file=sys.stderr)

    # 3. write fileorder.txt and expected/<task>.csv
    for task, (_suffix, task_dir) in TASKS.items():
        for profile, names in fileorder.get(task, {}).items():
            d = os.path.join(out_dir, profile, task_dir)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "fileorder.txt"), "w") as f:
                f.write("\n".join(names) + "\n")
        exp_dir = os.path.join(out_dir, "expected")
        os.makedirs(exp_dir, exist_ok=True)
        evaluation.write_expected(os.path.join(exp_dir, f"{task}.csv"),
                                  expected_rows[task])

    total = sum(len(rows) for rows in expected_rows.values())
    print(f"\nFetched {len(os.listdir(files_dir))} ontologies and {total} "
          f"expectations into {out_dir}", file=sys.stderr)


def _expectation(task: str, blobs: Dict[str, str], dat: str, files_dir: str,
                 ont_file: str):
    """Gold answer for a task: (answer, count) or (None, None) if unusable."""
    gold = _blob(f"{dat}/query-result-data.owl")
    if task == "consistency":
        return gold.decode("utf-8", "replace").strip().lower(), None

    # classification / realisation: hash the gold ontology's inferred axioms the
    # same way our own results are hashed, so they are directly comparable.
    import pyhornedowl
    text = _strip_ontology_iri(gold.decode("utf-8", "replace"))
    tmp = os.path.join(files_dir, f".gold-{ont_file}.owl")
    with open(tmp, "w") as f:
        f.write(text)
    try:
        onto = pyhornedowl.open_ontology(tmp, _sniff(gold))
        wanted = set(evaluation.TASK_AXIOMS[task])
        comps = [c for c in onto.get_axioms()
                 if type(getattr(c, "component", c)).__name__ in wanted]
        comps = [getattr(c, "component", c) for c in comps]
        digest, count = evaluation.axioms_signature(comps)
        return digest, count
    except BaseException as e:  # noqa: BLE001
        print(f"  ! could not hash gold for {ont_file}: {e}", file=sys.stderr)
        return None, None
    finally:
        os.remove(tmp)


def _strip_ontology_iri(text: str) -> str:
    """Drop the ontology IRI from a functional-syntax header.

    The gold files name the ontology after a Windows ``file:E:\\...`` path,
    which is not a valid RFC 3987 IRI and makes the parser reject the file.
    The ontology IRI is irrelevant to the inferred axioms, so remove it.
    """
    import re
    return re.sub(r"Ontology\(\s*<[^>]*>", "Ontology(", text, count=1)


def _sniff(data: bytes) -> Optional[str]:
    head = data.lstrip()[:64]
    if head.startswith(b"Prefix(") or head.startswith(b"Ontology("):
        return "ofn"
    if head.startswith(b"<?xml") or b"<rdf:RDF" in data[:512]:
        return "rdf"
    return None


def _reference_seconds(blobs: Dict[str, str], dat: str) -> str:
    """The reference reasoner's query-processing time (seconds) from the .dat."""
    key = f"{dat}/query-response.dat"
    if key not in blobs:
        return ""
    for line in _blob(key).decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if parts[0] == "ReasonerQueryProcessingTime" and len(parts) > 1:
            return f"{int(parts[1]) / 1000:.4f}"  # milliseconds -> seconds
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dataset", "verified"),
        help="output dataset directory (default: dataset/verified)")
    args = parser.parse_args()
    fetch(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
