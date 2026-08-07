#!/usr/bin/env python3
"""Compare ORE report CSVs: score one or more candidates against a gold reference.

Both ``main.py batch`` and ``run_native.py`` write per-ontology report CSVs that
share the join key ``(task, profile, ontology)`` and a comparable ``answer``
(``true``/``false`` for consistency, a normalised axiom-set hash for
classification / realisation) plus ``status`` and ``seconds``. Given a *gold*
report (treated as correct) and one or more *candidate* reports, this script
reports, per candidate:

* **Agreement** -- correct / incorrect vs gold, plus where it could not be
  checked (candidate errored/timed out, or gold had no definite answer).
* **Performance** -- wall-clock total / mean / median / std / min / max, and the
  candidate/gold speed ratio (mean, median, geometric mean, std) over the
  ontologies both solved *and agreed on*.

With several candidates it also prints a leaderboard and can render a cactus
(survival) plot as a self-contained SVG.

Example::

    ./compare.py results/base.csv results/pykonclude.csv results/other.csv \\
        --gold-label konclude-native --diagram results/cactus.svg --out diff.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

Key = Tuple[str, str, str]  # (task, profile, ontology)

#: statuses that mean the reasoner did not deliver a checkable answer
_NO_ANSWER = {"error", "timeout"}

#: validated categorical palette (dataviz default), fixed order; [light, dark]
_PALETTE = [
    ("#2a78d6", "#3987e5"),  # blue
    ("#eb6834", "#d95926"),  # orange
    ("#1baf7a", "#199e70"),  # aqua
    ("#eda100", "#c98500"),  # yellow
    ("#e87ba4", "#d55181"),  # magenta
    ("#4a3aa7", "#9085e9"),  # violet
]


# --------------------------------------------------------------------------- #
# Loading + scoring
# --------------------------------------------------------------------------- #

def load_report(path: str) -> Dict[Key, dict]:
    """Load a report CSV keyed by (task, profile, ontology)."""
    rows: Dict[Key, dict] = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["task"], r["profile"], r["ontology"])] = r
    return rows


def answer_of(row: dict) -> Optional[str]:
    """The comparable answer of a report row, or None if it has no answer.

    Uses the explicit ``answer`` column when present; otherwise falls back to the
    consistency verdict implied by ``status``.
    """
    ans = (row.get("answer") or "").strip()
    if ans:
        return ans
    status = (row.get("status") or "").strip()
    if status == "consistent":
        return "true"
    if status == "inconsistent":
        return "false"
    return None


def seconds_of(row: Optional[dict]) -> Optional[float]:
    if not row:
        return None
    try:
        return float(row["seconds"])
    except (KeyError, ValueError, TypeError):
        return None


def is_solved(row: Optional[dict]) -> bool:
    """Whether a report row represents a delivered answer (not timeout/error)."""
    return (row is not None
            and (row.get("status") or "") not in _NO_ANSWER
            and answer_of(row) is not None)


@dataclass
class Comparison:
    key: Key
    verdict: str          # correct | incorrect | cand-missing | cand-error |
                          # cand-timeout | no-gold
    gold_answer: Optional[str]
    cand_answer: Optional[str]
    gold_seconds: Optional[float]
    cand_seconds: Optional[float]


def compare(gold: Dict[Key, dict], cand: Dict[Key, dict]) -> List[Comparison]:
    """Score every gold problem against a candidate report."""
    out: List[Comparison] = []
    for key, grow in gold.items():
        gans = answer_of(grow)
        crow = cand.get(key)

        if crow is None:
            verdict = "cand-missing"
        elif (crow.get("status") or "") == "timeout":
            verdict = "cand-timeout"
        elif not is_solved(crow):
            verdict = "cand-error"
        elif gans is None:
            verdict = "no-gold"
        else:
            verdict = "correct" if answer_of(crow) == gans else "incorrect"

        out.append(Comparison(key, verdict, gans,
                              answer_of(crow) if crow else None,
                              seconds_of(grow), seconds_of(crow)))
    return out


# --------------------------------------------------------------------------- #
# Per-candidate summary
# --------------------------------------------------------------------------- #

def _stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation, or 0 for fewer than two points."""
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def print_candidate(comps: List[Comparison], cand: Dict[Key, dict],
                    gold_label: str, cand_label: str, gold: Dict[Key, dict]) -> None:
    counts: Dict[str, int] = {}
    for c in comps:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1
    checkable = counts.get("correct", 0) + counts.get("incorrect", 0)

    print(f"\n=== {cand_label} vs {gold_label} (gold) — {len(comps)} problems ===")
    for v in ("correct", "incorrect", "cand-timeout", "cand-error",
              "cand-missing", "no-gold"):
        if counts.get(v):
            print(f"  {v:14} {counts[v]}")
    if checkable:
        acc = 100.0 * counts.get("correct", 0) / checkable
        print(f"  {'agreement':14} {counts.get('correct', 0)}/{checkable} "
              f"checkable ({acc:.1f}%)")
    only_cand = set(cand) - set(gold)
    if only_cand:
        print(f"  {'note':14} {len(only_cand)} problem(s) only in {cand_label}")

    _print_incorrect(comps, cand)
    _print_timing(comps, gold_label, cand_label)


def _print_incorrect(comps: List[Comparison], cand: Dict[Key, dict]) -> None:
    wrong = [c for c in comps if c.verdict == "incorrect"]
    if not wrong:
        return
    print(f"\n  disagreements ({len(wrong)}):")
    for c in wrong[:15]:
        task, profile, onto = c.key
        g = c.gold_answer[:12] if c.gold_answer else "-"
        a = c.cand_answer[:12] if c.cand_answer else "-"
        print(f"    {profile}/{onto} [{task}]: gold={g} cand={a}")
    if len(wrong) > 15:
        print(f"    ... and {len(wrong) - 15} more (see --out CSV)")


def _print_timing(comps: List[Comparison], gold_label: str,
                  cand_label: str) -> None:
    # Only where both solved AND agreed -- otherwise we'd time different work.
    solved = [c for c in comps if c.verdict == "correct"
              and c.gold_seconds is not None and c.cand_seconds is not None]
    if not solved:
        return
    g = [c.gold_seconds for c in solved]
    c = [c.cand_seconds for c in solved]

    print(f"\n  timing on {len(solved)} agreed-and-solved problem(s):")
    width = max(len(gold_label), len(cand_label), 8)
    print(f"    {'':{width}}  {'total':>9} {'mean':>8} {'median':>8} "
          f"{'std':>8} {'min':>8} {'max':>8}")
    for label, xs in ((gold_label, g), (cand_label, c)):
        print(f"    {label:{width}}  {sum(xs):8.2f}s {statistics.mean(xs):7.3f}s "
              f"{statistics.median(xs):7.3f}s {_stdev(xs):7.3f}s "
              f"{min(xs):7.3f}s {max(xs):7.3f}s")

    ratios = sorted(((cs / gs, key) for cs, gs, key in
                     ((cc.cand_seconds, cc.gold_seconds, cc.key[2]) for cc in solved)
                     if gs > 0), key=lambda t: t[0])
    if not ratios:
        return
    vals = [r for r, _ in ratios]
    overall = sum(c) / sum(g) if sum(g) > 0 else float("nan")
    print(f"\n  speedup ({cand_label} / {gold_label}, per ontology; "
          f">1 = {cand_label} slower):")
    print(f"    mean {statistics.mean(vals):.2f}x   "
          f"median {statistics.median(vals):.2f}x   "
          f"geomean {statistics.geometric_mean(vals):.2f}x   "
          f"std {_stdev(vals):.2f}")
    print(f"    min {ratios[0][0]:.2f}x ({ratios[0][1]})   "
          f"max {ratios[-1][0]:.2f}x ({ratios[-1][1]})   "
          f"overall {overall:.2f}x")


# --------------------------------------------------------------------------- #
# Leaderboard across candidates
# --------------------------------------------------------------------------- #

def print_leaderboard(gold_label: str, gold: Dict[Key, dict],
                      candidates: List[Tuple[str, List[Comparison]]]) -> None:
    n = len(gold)
    gold_solved = [seconds_of(r) for r in gold.values() if is_solved(r)]
    gold_total = sum(s for s in gold_solved if s is not None)

    print(f"\n=== leaderboard ({n} problems, gold = {gold_label}) ===")
    hdr = (f"  {'reasoner':16} {'correct':>8} {'wrong':>6} {'timeout':>8} "
           f"{'error':>6} {'agree%':>7} {'total_s':>9} {'geomean':>8}")
    print(hdr)
    # gold row
    print(f"  {gold_label:16} {len(gold_solved):>8} {'-':>6} {'-':>8} {'-':>6} "
          f"{'-':>7} {gold_total:>8.1f}s {'1.00x':>8}")
    for label, comps in candidates:
        counts: Dict[str, int] = {}
        for c in comps:
            counts[c.verdict] = counts.get(c.verdict, 0) + 1
        checkable = counts.get("correct", 0) + counts.get("incorrect", 0)
        acc = 100.0 * counts.get("correct", 0) / checkable if checkable else 0.0
        solved = [c for c in comps if c.verdict == "correct"
                  and c.cand_seconds is not None]
        total = sum(c.cand_seconds for c in solved)
        ratios = [c.cand_seconds / c.gold_seconds for c in solved
                  if c.gold_seconds and c.gold_seconds > 0]
        geo = statistics.geometric_mean(ratios) if ratios else float("nan")
        print(f"  {label:16} {counts.get('correct', 0):>8} "
              f"{counts.get('incorrect', 0):>6} {counts.get('cand-timeout', 0):>8} "
              f"{counts.get('cand-error', 0):>6} {acc:>6.1f}% {total:>8.1f}s "
              f"{geo:>7.2f}x")


# --------------------------------------------------------------------------- #
# Diff CSV (long format: one row per candidate x problem)
# --------------------------------------------------------------------------- #

def write_diff(path: str, gold_label: str, gold: Dict[Key, dict],
               candidates: List[Tuple[str, List[Comparison]]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "task", "profile", "ontology", "verdict",
                    "gold_answer", "cand_answer", "gold_seconds", "cand_seconds",
                    "speed_ratio"])
        for label, comps in candidates:
            for c in comps:
                task, profile, onto = c.key
                ratio = ""
                if c.gold_seconds and c.cand_seconds is not None and c.gold_seconds > 0:
                    ratio = f"{c.cand_seconds / c.gold_seconds:.3f}"
                w.writerow([label, task, profile, onto, c.verdict,
                            c.gold_answer or "", c.cand_answer or "",
                            _num(c.gold_seconds), _num(c.cand_seconds), ratio])


def _num(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.4f}"


# --------------------------------------------------------------------------- #
# Cactus (survival) plot -> self-contained SVG
# --------------------------------------------------------------------------- #

def ordered_series(gold: Dict[Key, dict], gold_label: str,
                   candidates: List[Tuple[str, Dict[Key, dict]]]
                   ) -> List[Tuple[str, List[Optional[float]]]]:
    """Align every reasoner's times to one shared x-order.

    The x-order is the ontologies gold solved, sorted by gold's increasing time
    (so the gold curve is monotonic), followed by any gold did not solve. Every
    series then reports its own time for the *same* ontology at each x, or None
    where it produced no answer -- so a given x is the same file for all lines.
    """
    gold_solved = sorted((k for k in gold if is_solved(gold[k])),
                         key=lambda k: seconds_of(gold[k]))
    gold_unsolved = sorted(k for k in gold if not is_solved(gold[k]))
    order = gold_solved + gold_unsolved

    series: List[Tuple[str, List[Optional[float]]]] = []
    for label, report in [(gold_label, gold), *candidates]:
        times = [seconds_of(report.get(k)) if is_solved(report.get(k)) else None
                 for k in order]
        series.append((label, times))
    return series


def render_cactus_svg(series: List[Tuple[str, List[Optional[float]]]], path: str,
                      title: str, x_order_label: str) -> bool:
    """Plot each reasoner's per-problem time against a shared x-order.

    x is the ontology index in the shared order (see :func:`ordered_series`); the
    same x is the same file for every line. y is that reasoner's time on a log
    scale; a line breaks where the reasoner produced no answer for that file.
    Returns False if there is nothing to plot.
    """
    all_t = [t for _, ts in series for t in ts if t and t > 0]
    if not all_t:
        return False

    W, H, ml, mr, mt, mb = 880, 500, 66, 150, 58, 60
    pw, ph = W - ml - mr, H - mt - mb
    lo = math.floor(math.log10(min(all_t)))
    hi = math.ceil(math.log10(max(all_t)))
    if hi <= lo:
        hi = lo + 1
    xmax = max((len(ts) for _, ts in series), default=1) or 1

    def sx(i: float) -> float:
        return ml + pw * (i / xmax)

    def sy(t: float) -> float:
        t = max(t, 10.0 ** lo)
        return mt + ph * (1 - (math.log10(t) - lo) / (hi - lo))

    body: List[str] = []
    # y grid + labels (log decades)
    for e in range(lo, hi + 1):
        y = sy(10.0 ** e)
        body.append(f'<line class="grid" x1="{ml}" y1="{y:.1f}" '
                    f'x2="{ml + pw}" y2="{y:.1f}"/>')
        body.append(f'<text class="tick" x="{ml - 8}" y="{y + 4:.1f}" '
                    f'text-anchor="end">{_decade(e)}</text>')
    # x ticks
    step = _nice_step(xmax)
    for xt in range(0, xmax + 1, step):
        x = sx(xt)
        body.append(f'<line class="axis" x1="{x:.1f}" y1="{mt + ph}" '
                    f'x2="{x:.1f}" y2="{mt + ph + 5}"/>')
        body.append(f'<text class="tick" x="{x:.1f}" y="{mt + ph + 20}" '
                    f'text-anchor="middle">{xt}</text>')
    # axes
    body.append(f'<line class="axis" x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}"/>')
    body.append(f'<line class="axis" x1="{ml}" y1="{mt + ph}" '
                f'x2="{ml + pw}" y2="{mt + ph}"/>')
    # axis titles
    body.append(f'<text class="axtitle" x="{ml + pw / 2:.0f}" y="{H - 16}" '
                f'text-anchor="middle">problems (ordered by {_esc(x_order_label)} '
                f'time)</text>')
    body.append(f'<text class="axtitle" transform="translate(16,{mt + ph / 2:.0f}) '
                f'rotate(-90)" text-anchor="middle">time (s, log scale)</text>')

    # series: draw each contiguous solved run as a polyline; lone points as dots
    for idx, (label, ts) in enumerate(series):
        for run in _runs(ts):
            if len(run) == 1:
                i, t = run[0]
                body.append(f'<circle class="d{idx}" cx="{sx(i):.1f}" '
                            f'cy="{sy(t):.1f}" r="2.2"/>')
            else:
                pts = " ".join(f"{sx(i):.1f},{sy(t):.1f}" for i, t in run)
                body.append(f'<polyline class="s{idx}" fill="none" points="{pts}"/>')

    # legend (swatch = series colour, text = ink)
    lx, ly = ml + pw + 18, mt + 4
    for idx, (label, ts) in enumerate(series):
        yy = ly + idx * 22
        solved = sum(1 for t in ts if t is not None)
        body.append(f'<line class="s{idx}" x1="{lx}" y1="{yy}" '
                    f'x2="{lx + 22}" y2="{yy}"/>')
        body.append(f'<text class="legend" x="{lx + 30}" y="{yy + 4}">'
                    f'{_esc(label)} ({solved})</text>')

    css = _svg_css(len(series))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'font-family="system-ui,sans-serif">\n<style>{css}</style>\n'
           f'<rect class="surface" x="0" y="0" width="{W}" height="{H}"/>\n'
           f'<text class="title" x="{ml}" y="30">{_esc(title)}</text>\n'
           + "\n".join(body) + "\n</svg>\n")
    with open(path, "w") as f:
        f.write(svg)
    return True


def _runs(times: List[Optional[float]]) -> List[List[Tuple[int, float]]]:
    """Split an x-aligned series into contiguous runs of solved (index, time)."""
    runs: List[List[Tuple[int, float]]] = []
    cur: List[Tuple[int, float]] = []
    for i, t in enumerate(times, 1):
        if t and t > 0:
            cur.append((i, t))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _svg_css(n: int) -> str:
    light = "".join(
        f".s{i}{{stroke:{_PALETTE[i % len(_PALETTE)][0]};stroke-width:2}}"
        f".d{i}{{fill:{_PALETTE[i % len(_PALETTE)][0]}}}" for i in range(n))
    dark = "".join(
        f".s{i}{{stroke:{_PALETTE[i % len(_PALETTE)][1]}}}"
        f".d{i}{{fill:{_PALETTE[i % len(_PALETTE)][1]}}}" for i in range(n))
    return (
        ".surface{fill:#fcfcfb}.title{fill:#0b0b0b;font-size:16px;font-weight:600}"
        ".axtitle{fill:#52514e;font-size:12px}.legend{fill:#0b0b0b;font-size:12px}"
        ".tick{fill:#52514e;font-size:11px}.grid{stroke:#e7e6e2;stroke-width:1}"
        ".axis{stroke:#8a897f;stroke-width:1}polyline{stroke-linejoin:round}"
        + light +
        "@media(prefers-color-scheme:dark){"
        ".surface{fill:#1a1a19}.title{fill:#fff}.axtitle{fill:#c3c2b7}"
        ".legend{fill:#fff}.tick{fill:#c3c2b7}.grid{stroke:#33322f}"
        ".axis{stroke:#6f6e66}" + dark + "}"
    )


def _decade(e: int) -> str:
    v = 10.0 ** e
    return f"{v:g}" if e >= 0 else f"{v:.{-e}f}"


def _nice_step(xmax: int) -> int:
    for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if xmax / s <= 8:
            return s
    return max(1, xmax // 8)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _label(path: str, report: Dict[Key, dict]) -> str:
    for row in report.values():
        if row.get("reasoner"):
            return row["reasoner"]
        break
    return os.path.splitext(os.path.basename(path))[0]


def _unique(labels: List[str], paths: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for lab, p in zip(labels, paths):
        if labels.count(lab) > 1:
            lab = f"{lab}:{os.path.splitext(os.path.basename(p))[0]}"
        out.append(lab)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gold", help="gold/reference report CSV (treated as correct)")
    parser.add_argument("candidate", nargs="+",
                        help="one or more candidate report CSVs to score")
    parser.add_argument("--gold-label", default=None, help="name for the gold reasoner")
    parser.add_argument("--out", default=None, help="write a per-ontology diff CSV")
    parser.add_argument("--diagram", default=None,
                        help="write a cactus (survival) plot SVG to this path")
    parser.add_argument("--title", default="ORE reasoner comparison — cactus plot",
                        help="diagram title")
    args = parser.parse_args(argv)

    gold = load_report(args.gold)
    if not gold:
        parser.error(f"no rows in gold report {args.gold!r}")
    gold_label = args.gold_label or _label(args.gold, gold)

    cand_reports = [load_report(p) for p in args.candidate]
    cand_labels = _unique([_label(p, r) for p, r in zip(args.candidate, cand_reports)],
                          args.candidate)

    candidates: List[Tuple[str, List[Comparison]]] = []
    for label, report in zip(cand_labels, cand_reports):
        comps = compare(gold, report)
        candidates.append((label, comps))
        print_candidate(comps, report, gold_label, label, gold)

    if len(candidates) > 1:
        print_leaderboard(gold_label, gold, candidates)

    if args.out:
        write_diff(args.out, gold_label, gold, candidates)
        print(f"\nWrote per-ontology diff to {args.out}")

    if args.diagram:
        series = ordered_series(gold, gold_label,
                                list(zip(cand_labels, cand_reports)))
        if render_cactus_svg(series, args.diagram, args.title, gold_label):
            print(f"Wrote plot to {args.diagram}")
        else:
            print("No timing data to plot", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
