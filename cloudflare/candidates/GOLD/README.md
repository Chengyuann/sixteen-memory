# GOLD / v0.3.0 — capacity-first release candidate

Local release candidate, not a proven accuracy improvement and not Full-ready
until the account capacity and final deployment gates pass.

## Design and provenance

Uses frozen v0.2.5 source chunking, lexical retrieval, ordering and provenance.
Does **not** include T04 message penalties, C02 compaction, summaries, graphs,
learned ranking, generated answers, a new provider, or cross-user memory.

Changes:

- Batch-fetch source rows, reconstructing the same baseline order explicitly.
- Durable transaction-safe chunk count; due-only expiry cleanup.
- Avoid redundant alarm writes; repair missing alarms on cold start.
- Raise the explicit supported ceiling from10,000 to100,000 chunks/user.
  Full-capacity claims still require account limits and workload verification.

Research informs reliability and source-preservation principles only. This
does not implement or claim paper accuracy for Claude Code, Codex, Pi,
DeepSeek Harness, MemoryTax, AgentMemRec, RLMs, or hybrid episodic/graph memory.
See `../../../docs/golden-research-20261031.md` for primary sources and caveats.

## Local checks

From `cloudflare/`, always choose a new output directory:

```sh
npm run typecheck
npm test
OUTPUT=/absolute/new/capacity-dir node candidates/GOLD/runtime.mjs
node candidates/GOLD/parity.mjs /absolute/source-only-jobs.json /absolute/new/parity-dir
```

`local-harness.ts` adds test-only RPC methods and SQL counters. It is **not**
the production entrypoint and must never be deployed. External fetch is blocked
in local tests; fixtures and credentials are synthetic.

The new production Worker is named `sixteen-memory-v030` with its own Durable
Object namespace. The original service and older candidates must remain
unchanged. Evaluations use the approved stable Pages URLs only while routed to
the frozen candidate. Never switch the shared route during an active run.

No automatic Full, repeated Smoke, paid upgrade, key or permission change.

## Pre-deployment results (October31)

- Final production source:330 public source scopes /1,175 queries, exact BASE
  response equality including content, identifiers, ranking and scores.
- Local capacity:100,000 records ×1,100 source characters;307,941,376 database
  bytes.115 synthetic checks, cold restart, expiry, alarm recovery, overflow,
  idempotence, conflict and isolation passed.
- Paired9,000-record test:append SQL rows read9,007→10;top100 query SQL
  operations103→5. The latter reads27,105→27,205 rows;append writes9→10.
  Fewer calls do not imply fewer billed rows or a guaranteed cost reduction.
- Broad, overview, rule queries and16 concurrent exact queries passed on the
  local100k database. No external fetch. Local wall-clock measurements are not
  cloud CPU limits, availability, or private Full capacity guarantees.
- Python1,020 tests, Node32 tests and TypeScript passed locally.
- Cloudflare account subscription remains unverified. Do not submit Full until
  actual plan, remaining usage and total workload capacity are confirmed.
