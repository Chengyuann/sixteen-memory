# Storage503 recovery and submission-gate repair — October 30, 2026

## Observations, not assumptions

The public health route validates configuration; it does **not** exercise Durable
Objects storage. All four backends returned health200 and authenticated Search503
on October30 at 08:22 Asia/Shanghai. Official evaluation was correctly withheld,
and shared routing remained at v0.2.5.

At 11:16 Asia/Shanghai all four backends returned Search200 **before any code,
deployment, billing, credential or routing change**. Subsequent checks passed:

- Each backend: 18 contract groups, 66 HTTP requests, including write/read,
  idempotence, conflicting writes, tenant isolation, Unicode and concurrency.
- Each backend: two rounds of the new low-write gate, one synthetic record.
- All four previously failing synthetic user scopes: Search200.
- Shared baseline reached through Devbox/curl: Search200 from the SJC edge.

Cloudflare GraphQL metrics report 104,933 SQL rows written on UTC October29,
compared with 3,101 at the first budget check on UTC October30. The documented
free-plan write allowance is 100,000 rows/day. This supports quota exhaustion as
a likely contributor, but metrics are not the billing quota counter, may lag,
and the account subscription API was not authorized. The persistence of503 after
08:00 and its exact cause are not fully explained. No claim of a confirmed
root-cause fix or guaranteed future availability is made.

Sources: Cloudflare Durable Objects pricing and GraphQL Analytics documentation.

## Repair

`scripts/storage_preflight.py` is an operational gate, not a backend change:

1. Measure account-wide UTC-day SQL reads/writes through the existing Wrangler
   login. No new credentials, permissions, products or plans are created.
2. Block at a conservative local ceiling of80,000 writes or4,000,000 reads.
   Missing, malformed, stale or failed metrics block rather than imply zero usage.
   These ceilings are a local safety policy, not a determination of account plan.
3. Read an empty synthetic scope before writing. Verify the expected version,
   frozen commit, authentication, exact Add acknowledgement, durable source
   readback, duplicate-write idempotence and cross-user isolation.
4. Repeat two rounds using **the same single synthetic record**, rather than
   repeatedly uploading1,000-record load fixtures to production.
5. Retry transient transport/429/502/503/504 failures at most three times, with a
   hard HTTP-request budget. An Add retry retains the same body and request ID.
   Persistent failure blocks. Official evaluation creation is never retried here.
6. Keep only step/status/timing/CF-Ray diagnostics, not credentials, raw returned
   memory, request bodies or arbitrary server error text.

The workspace serial runner now invokes the gate on the baseline and isolated
candidate before registration/routing, on the shared candidate before submission,
and checks the SQL budget again immediately before a Smoke POST. It no longer
waits for hours on a presumed midnight reset. Existing attempt receipts and
exactly-once official POST boundaries remain unchanged.

The candidate freeze remains commit `06c842a0ea34fcaff1daf2d741a3e0fb71110f53`.
Only operational gate/test/documentation paths may differ from that commit, and
the serial runner requires successful CI for the operational commit as well as
the original frozen source hashes and CI. Worker source, ranking, storage schema,
submission IDs, account settings and all deployment bindings remain unchanged.

## Tests and operation

Run without network or credentials:

```sh
pytest -q tests/test_storage_preflight.py
```

The function-based gate accepts an existing `httpx.Client`; callers must hold the
single-runner lock and verify there is no active official evaluation before any
synthetic write or routing change. Store its receipt on both success and failure.
Do not use it during an active evaluation, and do not treat a budget pass as proof
that an entire future workload fits: current metrics can lag and other workloads
share the account. Stop unneeded stress tests before official evaluation.

No new Smoke or Full was submitted as part of this repair. Automatic continuation
remains disabled. Detailed operational receipts live outside the source repository
in `outputs/storage-repair-20261030/`.
