# 100-mechanism memory research Harness

Offline research, **not a production replacement**. The live Pages entry stays
on v0.2.5. No CLI here deploys, submits official evaluations or calls a model.

## Experiment contract

Ten directions × ten separate mechanisms. Each experiment has a hypothesis,
implementation, limitation, tests and exactly one hook relative to the pinned
v0.2.5 baseline (`a3520ccb7843156e2dd51e92e29caf0f6dafafa4`).
Experiments are not accumulated, nor selected by varying one numeric parameter.

| Direction | Scope |
|---|---|
| D01 | Query decomposition |
| D02 | Lexical normalization and aliases |
| D03 | Source/index representations |
| D04 | Structural segmentation |
| D05 | Session and parent/child retrieval |
| D06 | Temporal/update retrieval |
| D07 | Source-linked multi-hop associations |
| D08 | Relevance reranking |
| D09 | Evidence organization and extraction |
| D10 | Least disclosure and abstention |

`types.py` keeps source messages/documents immutable. Retrieval receives only
the original query and a source-only Context. Gold answers, relevance labels and
benchmark categories never enter hooks. Session IDs are opaque to mechanisms.
`engine.py` validates returned substrings and offsets against original messages,
including returned documents beyond the top100 before truncation.

## Score and limitations

**Evidence score** = 100 × macro-average message recall@20 across dataset/type
strata. The output additionally reports R@10/R@100, all-evidence@100, MRR,
16,000-source-character-budget recall, source coverage, changed output cases,
improvements/regressions, errors, length and observed latency.

This is neither QA accuracy nor an AML score. An extract can touch a labelled
message without retaining the answer-bearing span; inspect source coverage and
perform a fixed-answerer test before claiming end-to-end improvement.
Baseline parity covers R@10/R@20/R@100 on 72 archived Workers-runtime cases,
not byte-identical runtime behavior. Python source offsets count Unicode code
points; JavaScript counts UTF-16 code units. Astral characters at chunk boundaries
can differ. No production performance or complete cross-runtime equivalence is claimed.
Synthetic unrelated-record hits and privacy-category hits are separate fields.
Empty retrieval on an unanswerable question is a diagnostic, not a calibrated
abstention model. No approved answer-model credential was available in this run.

New public LongMemEval inputs exclude previously used question IDs and labelled
evidence sessions. Development and validation are fixed before outcomes.
Distractor histories can overlap; public data is not guaranteed independent of
the official AML suite. Previously used LoCoMo data is explicitly regression.
Independent synthetic diagnostic fixtures do not constitute benchmark gains.

All100 validation results are published, but only the one nominee per direction
selected on development is treated as a preselected comparison. Exploratory
bootstrap intervals for those nominees do not correct for all100 comparisons.
No improvement or broad safety guarantee is promised.

## October 29 study status

All100 mechanisms were evaluated on 1,125 questions each, plus the fixed
baseline. A first-pass protocol defect was found and repaired: source-only
spawned workers, separate indices, invalidation on errors, and hash-linked
selection/report checks. Original nominees and all100 mechanisms were preserved;
the reused validation split is **not a fresh blind holdout**. A subsequent
no-op-resume fix was verified by a real subprocess test and a complete rerun.
See [study-20261029.md](study-20261029.md),
[study-20261029.csv](study-20261029.csv) and the
[study manifest](study-20261029-manifest.json) for the final curated results.

Hooks are reviewed, trusted Python code, not untrusted sandboxed plugins.
The facade blocks accidental mutation; arbitrary Python reflection or opening
files is not a security boundary. No submitted hook accesses gold files.

## Reproduce

Python 3.11+ with SQLite FTS5. This Harness adds no third-party runtime dependency.
Use an environment containing pytest to run its tests:

```sh
python -m pytest harness/tests -q
ruff check --select E4,E7,E9,F harness
```

The repository CI runs these separately; research snapshots are excluded from
the broader repository style check to avoid post-score formatting changes.

From the repository root, with the task workspace's existing public source files:

```sh
python -m harness.prepare --workspace ../.. --output ../../outputs/harness100-new
python -m harness.parity --workspace ../.. \
  --output ../../outputs/harness100-new/baseline-parity.json
python -m harness.run --data ../../outputs/harness100-new/data.json \
  --output ../../outputs/harness100-new --split development --workers 6
python -m harness.select --output ../../outputs/harness100-new
python -m harness.run --data ../../outputs/harness100-new/data.json \
  --output ../../outputs/harness100-new --split validation --workers 6
python -m harness.run --data ../../outputs/harness100-new/data.json \
  --output ../../outputs/harness100-new --split regression --workers 4
python -c 'import json; from harness.diagnostics import cases; print(json.dumps({"schema":1,"conversations":cases()}))' \
  > ../../outputs/harness100-new/diagnostic-data.json
python -m harness.run --data ../../outputs/harness100-new/diagnostic-data.json \
  --output ../../outputs/harness100-new --split diagnostic --workers 4
python -m harness.report --output ../../outputs/harness100-new
```

For another compatible dataset, supply:

```json
{"schema":1,"conversations":[{
  "id":"opaque-case","dataset":"public-name","split":"development",
  "batches":[{"request_id":"r1","session_id":"s1","messages":[
    {"role":"user","content":"Original source text.","timestamp":1704067200000}
  ]}],
  "queries":[{"id":"q1","query":"Source question?","category":"evaluation-only",
    "required":[{"request_id":"r1","position":0}]}]
}]}
```

Spawned workers receive only source messages, queries and experiment IDs. Gold
references, benchmark categories and split labels stay in the parent evaluator.
Each experiment receives its own index and a read-only Context facade. Workers
return source coordinates and checksums, which the parent verifies before
scoring. Complete conversation results are appended and flushed before marking
them done. Resume requires unchanged data hash, core code hash and experiment
list; fatal records are retried and only the latest successful record is used.
Completed resumes preserve run/summary bytes, so selection hashes remain valid.
Use a new output directory for changed code and preserve the failed run. A
runtime error invalidates the score rather than disappearing from its denominator.

Outputs: per-conversation JSONL, per-split summaries, frozen selection receipt,
100-row CSV/JSON, Markdown report. Raw public datasets and long result files
remain outside the repository. `prepare.py` never downloads private data.

## Before any production proposal

Require actual activation, heldout benefit, no relevant safety or source-coverage
regression, runtime/contract validation on Workers, a fixed authorized answerer
comparison, then explicit review. At most a separately justified final Smoke;
never submit 100 official jobs or substitute local retrieval scores for them.
