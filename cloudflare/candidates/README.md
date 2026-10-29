# Three isolated Smoke candidates — October 30, 2026

These experimental builds start from v0.2.5 commit
`a3520ccb7843156e2dd51e92e29caf0f6dafafa4`, not the rejected v0.2.6.
Production `sixteen-memory-entry.pages.dev` remains routed to v0.2.5.

| Variant | Version | Single mechanism | Fresh validation delta | Improved/regressed |
|---|---|---|---:|---:|
| T03 | 0.2.7 | Query-linked prompt promotion within existing top100 | +0.619 | 5/3 |
| T04 | 0.2.8 | Repeated source-message penalty after the first five hits | +0.789 | 3/0 |
| T05 | 0.2.9 | Exact meaningful-entity support, excluding pronouns | +0.263 | 1/0 |

Scores are local evidence-recall proxies, **not AML scores or QA accuracy**.
Seven new hypotheses were tested on 98 newly reserved development questions.
The three above were selected before reading 98 fresh validation questions.
Both exclude previously used question IDs and labelled evidence sessions;
distractor histories may still overlap. All previously used data remains regression-only.
The 855-question LoCoMo regression changed by 0, 0, and +1.587 respectively;
T05 improved 16 and regressed 1 regression question.

All three are permutations of the baseline top100 and preserve exact source
content, provenance, R@100, and source coverage. No model is called. The 120-case
synthetic diagnostics retain baseline privacy deficiencies: this is **not** a
privacy-safety guarantee. T03 has three validation regressions. No statistically
established answer-quality improvement is claimed.

## Reproduction

From `cloudflare/`, use `candidates/runtime-study.mjs` with `DATA`, `OUTPUT`,
`SPLIT` and optionally `VARIANTS=BASE,T03,T04,T05`. Data lives outside the repo.
`candidates/integration.mjs` exercises each candidate with 18 synthetic contract
checks and bounded 16-way concurrent reads/writes. Use `OUTPUT` to keep receipts.

Python screening uses `python -m harness.next_prepare` and
`python -m harness.next_run`; the original 100-experiment harness is unchanged.
The research code received whitespace-only formatting after scoring, with AST
identity checked; original source and hash receipts remain in the study output.
The final Worker sources were re-evaluated after formatting and packaging fixes.
`readiness.json` binds final source hashes to runtime result-set and metric parity.

Each candidate has a distinct Worker name, Durable Object namespace, submission
ID and optional Pages Service-binding entry. The service key is a deployment
secret, never source or configuration content. Do not deploy a candidate over
another candidate or the retained production Worker. Never mutate a deployment
while its official evaluation is active.

Official Smoke results will be reported after completion. Exactly three new
Smoke runs are authorized for this study; no Full or repeated score-seeking runs.
