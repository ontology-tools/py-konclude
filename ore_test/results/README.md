# ORE 2015 published results (reference data)

Reference numbers from the **OWL Reasoner Evaluation (ORE) 2015 Competition**,
used to sanity-check and contextualise our own runs. These are *aggregate*
competition results — the competition did not publish per-ontology expected
answers or per-ontology reasoner times for the full pool (see "Expected
results" below).

## Tracks

Six tracks: three tasks (**consistency**, **classification**, **realisation**)
over two profiles (**OWL 2 DL**, **OWL 2 EL**). Problem counts per track:

| Task           | OWL 2 DL | OWL 2 EL |
|----------------|---------:|---------:|
| Consistency    | 306      | 298      |
| Classification | 306      | 298      |
| Realisation    | 264      | 109      |

## Winners

Out of the six tracks, **four were won by Konclude** (all three DL tasks plus
EL realisation) and **two by ELK** (EL consistency and EL classification).

| Track              | Winner   | Solved      |
|--------------------|----------|-------------|
| DL consistency     | Konclude | 305 / 306   |
| DL classification  | Konclude | 298 / 306   |
| DL realisation     | Konclude | 261 / 264   |
| EL realisation     | Konclude | 109 / 109   |
| EL consistency     | ELK      | 298 / 298   |
| EL classification  | ELK      | 298 / 298   |

## Full breakdown

[`ore2015_published_results.csv`](ore2015_published_results.csv) holds Table 3
of the report — solved / timeout / error counts for every reasoner and task,
split by profile (`NA` where a reasoner did not enter a profile). Empty cells
mean the same. Every row's success + timeout + error sums to the track total
above (used to verify the transcription).

Times: the report reports reasoner performance as wall-clock time on *solved*
problems, shown only as plots (Figure 3 ff.), so there are no per-reasoner
aggregate time numbers to tabulate here.

## Expected results

The competition computed the expected (correct) answer per problem by
**majority voting** among the entered reasoners at competition time; only the
aggregate scores above were published. The ontology corpus on Zenodo
(record 18578, `ore2015_sample.zip`, ~725 MB — our `dataset/pool_sample` is a
subset) contains **ontologies only**, no answers or times.

The only per-ontology gold data that survives publicly is the small **verified
test set** shipped with the competition framework (ontologies + expected
answers + a reference reasoner's times). Fetch it with
[`../fetch_verified.py`](../fetch_verified.py) and evaluate against it with
`main.py batch <task> --dataset dataset/verified --expected ...`.

## Sources

- B. Parsia, N. Matentzoglu, R. Gonçalves, B. Glimm, A. Steigmiller.
  *The OWL Reasoner Evaluation (ORE) 2015 Competition Report.*
  Journal of Automated Reasoning (2017). Preprint:
  <https://ceur-ws.org/Vol-1457/SSWS2015_paper1.pdf>
- ORE 2015 Reasoner Competition Corpus, Zenodo: <https://zenodo.org/records/18578>
- ORE 2015 Competition Framework (verified test set, expected answers):
  <https://github.com/ykazakov/ore-2015-competition-framework>
