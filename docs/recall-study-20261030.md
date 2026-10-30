# October 30: source-recall study, no new official submission

## Decision

Five new mechanisms were implemented and screened offline against v0.2.5 and
the frozen v0.2.8/T04. R01, which does not penalize a repeated-message chunk
when it contributes a new query term, was selected **before** validation.
It remains an experimental candidate, not a deployed version.

The gain over T04 is too small and mixed to justify another Smoke solely on
these results. No official run, production change, paid model call or recurring
automation was started. A fixed, authorized answerer is still unavailable;
these measurements are not answer accuracy or predicted leaderboard scores.

## Why change the experiment?

The preceding official Smoke scored v0.2.8 at0.709375, compared with v0.2.5
at0.703559. Aggregated capability G improved, while C and Streaming regressed.
These are observations from one run, not proof of a causal ranking mechanism.
We did not inspect official private questions, answers or stored user memories.

Only three LongMemEval-S examples remained disjoint from previously used
question IDs and labelled sessions. Treating those as a new validation benchmark
would be misleading. Previously used LongMemEval and LoCoMo are now explicitly
regression tests.

Public CL-bench has no native retrieval-span labels. The new diagnostic takes
literal phrases quoted in its public rubrics, requires verbatim occurrence in
prior source messages, and excludes phrases copied in the current query.
The scorer keeps only source offsets; neither rubric text, quote text, labels,
categories nor dataset IDs enter retrieval workers. This is a **narrow
source-anchor diagnostic**, not a complete CL-bench evaluator or QA score.

Contexts were hash-split before results:90 distinct source contexts,
62 development and62 validation tasks,241 literal source anchors in total.
Whole context groups do not cross development/validation. Public data may
overlap an official benchmark; it is not claimed as a private unseen test.

## Fixed mechanisms

| ID | Mechanism |
|---|---|
| R01 | Preserve a later chunk if it adds a new focused-query term |
| R02 | Penalize only near-identical same-message content; preserve numeric/polarity differences |
| R03 | Protect top20, then allow up to20 previously unrepresented messages from the candidate pool |
| R04 | Retrieve360-character windows but return original complete parent chunks |
| R05 | Acquire an immediately preceding source question for a short relevant reply |

These are distinct hypotheses, not numeric sweeps. The only nominee was R01;
validation did not replace it with a different candidate. Each worker receives
only original messages and queries, creates a fresh index for each experiment,
and returns source coordinates that the parent evaluator verifies.

## Measurements

Primary: macro-average literal-anchor coverage within the first16,000 source
characters. The character budget is a local diagnostic, **not a claim about
the official answerer's context limit**.

| Split | v0.2.5 | v0.2.8/T04 | R01 |
|---|---:|---:|---:|
| Development,62 tasks | 77.1771% | 80.6927% | 82.2552% |
| Validation,62 tasks | 76.3644% | 78.1630% | 78.5858% |

On validation, R01 beats T04 by0.4228 percentage points, with2 improved and2
regressed tasks. R01 beats v0.2.5 by2.2214 points, with3 improved and0 regressed
tasks. All versions retain the same original top100 source set; validation
anchor@100 is97.4585% and source coverage is95.3386%.

On1,051 previously used regression questions, R01 and T04 have the same
macro recall@20:80.4692%, versus79.9453% for v0.2.5. But R01 loses three
budget-recall cases relative to T04. Therefore the result is mixed, not a clean
dominant replacement.

R04's development anchor-budget score was79.2604%, below T04; R02/R03/R05 did
not improve that diagnostic. Their validation was not used to search for a
different winner.

## Tests and runtime

-26 new Python tests;823 Harness tests and51 service tests pass.
-6 new TypeScript tests;25 Node tests, lint and typecheck pass.
-Actual local workerd/SQLite runs of BASE, T04 and R01 match Python aggregate
  metrics on62 validation and1,051 regression questions per version.
-The exact top100 source-span sets match across these three versions on both
  splits, and every returned span is checked against the source.
-120 synthetic diagnostics run without errors. Existing weak abstention and
  same-user sensitive-content diagnostics remain unchanged, not solved.
-Runtime failures in initial evaluation setup were retained: first an isolated
  bundle lacked a dependency path, then the evaluator incorrectly assumed
  UTF-16 offsets. The frozen Worker actually uses `Array.from` code-point
  offsets. Only the evaluator was repaired; candidate code was not tuned.

`cloudflare/experiments/recall-rerank.ts` is offline-only and imported by no live
Worker. Previously frozen `cloudflare/candidates/` and deployed source stay
byte-identical.

## Reproduction

```sh
python -m pytest harness/tests/test_recall_mechanisms.py harness/tests/test_recall_study.py -q
python -m harness.recall_prepare --workspace ../.. --output ../recall-study-new/data
python -m harness.recall_run --data ../recall-study-new/data/data.json \
  --output ../recall-study-new/screen --split development --workers 4
```

Freeze a development selection receipt before running validation. The runner
checks code, data and development-result hashes and permits only the preselected
nominee plus T04. It refuses incompatible resumes and invalidates results when
cases fail. Raw public data and run receipts are outside the repository in
`outputs/recall-study-20261030/`; the frozen selection is
`screen/selection-before-validation.json`.

## Competition deadline, checked October 30

The official competition page and participation guide both state:

- Materials and evaluation application: **October31,2026,23:59 UTC+8**.
- Evaluation closes: **November4,2026,23:59 UTC+8**.
- Formal results: mid-November2026.

The participation guide estimates a Full run at0.5–2days. Do not confuse the
later evaluation cutoff with the materials deadline. No Full was authorized
or started in this study.

Official primary sources:

- https://agentmemoryleaderboard.ai/competition/
- https://agentmemoryleaderboard.ai/ (participation guide)
- https://github.com/Tencent-Hunyuan/CL-bench
