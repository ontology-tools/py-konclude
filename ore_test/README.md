# ORE task runner

Run the [ORE](https://www.w3.org/community/owled/ore-2015-workshop/) reasoning
tasks — **consistency**, **classification** and **realisation** (aka
instantiation) — with a pluggable OWL reasoner, and score the results against
gold answers. Only `py-konclude` is wired up today; other reasoners can be added
by registering a backend in [`reasoners.py`](reasoners.py) (see
`main.py --list-reasoners`).

## Single ontology

```bash
python main.py run consistency    path/to/ontology.owl            # prints true/false
python main.py run classification path/to/ontology.owl out.ofn   # inferred hierarchy
python main.py run realisation    path/to/ontology.owl out.ofn   # inferred instances
```

Exit code: `0` ok, `1` inconsistent (consistency task), `2` error. The input
serialization is auto-detected (ofn / owx / rdf); override with `--input-format`.

## Over a dataset

`batch` drives a whole dataset via its `fileorder.txt` lists, running each
ontology in an isolated subprocess (so a native crash or hang is contained and
subject to `--timeout`):

```bash
python main.py batch classification                      # bundled pool_sample, all profiles
python main.py batch consistency --profile el --limit 50 # a subset
python main.py batch classification --report out.csv     # per-ontology CSV
python main.py batch realisation --output-dir results/   # persist result ontologies
```

## Evaluation metric

Score answers against a gold file (`correct` / `incorrect` / `unexpected`,
plus `error` / `timeout`), ORE-style — problems solved and time on them, versus
a reference time:

```bash
python main.py batch consistency --expected gold.csv
```

Results are compared in a **normalised form** matching the ORE competition:
classes are quotiented by inferred equivalence, axioms use canonical
representatives, and trivially-entailed edges (`X ⊑ owl:Thing`,
`owl:Nothing ⊑ X`, reflexive) are dropped. This makes the comparison invariant
to how a reasoner serialises its result.

### Getting a gold file

There is **no per-ontology gold data for `pool_sample`** — the ORE 2015 pool was
published without answers (see [`results/README.md`](results/README.md)). Two
options:

1. **Verified test set (real external gold).** Download the small verified set
   the competition framework ships (ontologies + expected answers + reference
   times):

   ```bash
   python fetch_verified.py                 # -> dataset/verified/ (git-ignored)
   python main.py batch consistency    --dataset dataset/verified \
       --expected dataset/verified/expected/consistency.csv
   python main.py batch classification --dataset dataset/verified \
       --expected dataset/verified/expected/classification.csv
   ```

   Konclude scores 10/10 on both consistency and classification here, matching
   the reference reasoner's answers exactly.

2. **Generate one.** Produce a reusable gold CSV from a trusted reasoner (its
   answers become the expectations, its times the reference), then score any
   reasoner — including future ones — against it:

   ```bash
   python main.py generate-expected classification --profile dl -o gold.csv
   python main.py batch classification --profile dl --expected gold.csv
   ```

## Layout

```
main.py             CLI: run / batch / generate-expected
reasoners.py        reasoner backend registry (extension point)
evaluation.py       result signatures, gold-file IO, ORE normal form, scoring
fetch_verified.py   download the verified gold set into dataset/verified/
dataset/pool_sample ORE 2015 pool subset (ontologies + fileorder lists)
results/            published ORE 2015 competition results (reference data)
```

## Notes

- **Native baseline inputs** ([`run_native.py`](run_native.py)) are the
  original dataset files whenever the binary parses their serialization
  itself (Konclude reads both functional syntax and OWL/XML). Converting
  through py-horned-owl is a last resort only: its OWL/XML writer abbreviates
  IRIs inside the `IRI=` attribute (invalid OWL/XML — `abbreviatedIRI=` would
  be correct), so a conforming parser sees unknown relative IRIs; datatypes
  like `xsd:decimal` then lose their meaning and `DataPropertyRange` axioms
  stop constraining anything, which flipped genuinely inconsistent ontologies
  (float literal vs. decimal range, disjoint OWL 2 value spaces) to
  "consistent" in earlier baselines.
- **SWRL rules** (`DLSafeRule`) are out of scope on both sides: Konclude's
  functional-syntax parser rejects them and the py-konclude construct mapping
  reports them as unsupported.
- **Realisation** currently yields no instances: the py-konclude reasoner
  reports only the class hierarchy through `inferred_axioms()`, so
  `ClassAssertion`s are not produced yet. The task is wired correctly and will
  work once instance queries land.
- Classification/realisation gold from the verified set is stored as a
  normalised inferred-axiom hash; consistency gold is the exact `true`/`false`
  verdict.
