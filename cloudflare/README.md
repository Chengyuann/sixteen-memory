# Sixteen Memory on Cloudflare

Version **0.2.1**. A Worker exposes the AML textual Add/Search contract. Each
`(SUBMISSION_ID, user_id)` gets a separate SQLite-backed Durable Object. This is
a serverless adapter, not a change to the original Python v0.1.0 baseline.

## Initial deployment mode

- `MEMORY_MODE=lexical`, `ENABLE_FACTS=false`.
- SQLite FTS5's BM25 ranks tokenized source chunks. CJK characters are separated.
- Original source, source timestamps, message order and provenance are retained.
- No LLM or embedding API is called in lexical mode.
- Retrieval is functional but no competition effectiveness has been measured.
- Hybrid mode adds text-embedding-v4 and RRF. Optional source-linked fact
  extraction uses gpt-4o-mini. Both need approved credentials and live testing
  before being described as enabled or suitable for formal evaluation.

The FTS5 BM25 implementation differs from the Python `rank-bm25` implementation.
Scores between these versions must not be treated as identical.

## Local verification

```bash
npm ci --registry=https://registry.npmjs.org
npm run types
npm run typecheck
npm test
npx wrangler deploy --dry-run --outdir dist
npm run test:runtime
npm run test:extended
npm run test:pages-entry
npm run test:deep
npm audit --registry=https://registry.npmjs.org --audit-level=high
```

The test runtime version matches the Miniflare release used by pinned Wrangler.
All tests use synthetic data and fake or disabled model providers.
Runtime tests verify actual Workers SQLite, FTS5, retries, isolation, persistence
across restarts, expired-record exclusion, capacity errors and body limits.
The extended suite exercises 1,000 synthetic records and at most four concurrent
requests. The long-source regression preserves identical text at different
message positions and character offsets; v0.2.1 fixes over-aggressive deduplication.
The deep local suite exercises the declared 10,000-live-chunk boundary and
overflow rollback, and invokes an actual local Durable Object alarm to verify
physical deletion of source rows, retry rows and FTS rows.
The Pages-entry suite runs the same bounded authenticated checks through a
transparent Service binding entry. It verifies that this alternate hostname
preserves the existing Worker's authentication, persistence and response
semantics without copying its Durable Object storage or embedding a service key.

### Authenticated public tests

The manual `Authenticated Synthetic API Test` workflow runs the same bounded
suite against the actual service. It is restricted to the repository owner's
manual invocation on `main`. It never runs on a pull request and has no model keys.
An operator must first configure a temporary service credential and send that
credential to the repository's `SIXTEEN_TEMP_TEST_KEY` Actions secret.
After the run, restore the original service credential, run the `revoked` phase
to verify rejection of the temporary key, and delete the temporary Actions secret.
Do not copy the original service key to CI or pass any credential as an input/URL.
Synthetic test namespaces use the configured TTL; test output contains only
synthetic identifiers, counts, statuses and timings.

Passing a small synthetic run is not proof of unlimited capacity, benchmark
retrieval quality, provider retention compliance or official AML Smoke success.

## Deploy

The account owner must authorize Wrangler before deployment. No GitHub OAuth
app or GitHub Actions secret is required; CI does not deploy.

```bash
npx wrangler whoami
npx wrangler deploy --var SOURCE_COMMIT:$(git rev-parse HEAD)
npx wrangler secret put MEMORY_API_KEY
```

Choose a cryptographically random API key with at least 32 ASCII characters.
Enter it through stdin or the protected secret prompt. Never place it in
`wrangler.jsonc`, Git, URLs, reports, logs, screenshots or emails.
Use the URL actually returned by deployment, never an invented example.

Public health: `GET /health`. Protected routes:

- `POST /v1/memories/add`
- `POST /v1/memories/search`

Both require `Authorization: Bearer <MEMORY_API_KEY>`.
Health returns 503 until the required configuration and API credential exist.

The initial deployment fails closed before the key is set. `workers.dev` is
enabled and preview URLs are disabled. No domain registration, paid subscription,
existing Worker changes, model invocation or formal evaluation is automated.

### Alternate public entry

`pages-entry/` contains a minimal Cloudflare Pages advanced-mode entry used when
a client network cannot reach `workers.dev`. It forwards the original request to
the existing `sixteen-memory` Worker through a Cloudflare Service binding.
It does not use public `fetch`, inject credentials, store data, log bodies or
change the memory implementation. Deploy it as a separate Pages project and
verify the actual returned hostname before updating any approved AML binding.

## Consistency and limits

- Add commits source records, FTS rows and its idempotency record together.
- It awaits storage durability and alarm registration before returning success.
- A concurrent same-user Add returns 429, with `Retry-After: 2`.
- Search sees committed records. Search during an unfinished Add may see the
  prior state; callers must await Add success before searching new memory.
- Repeated write IDs with identical normalized payloads are no-ops. Changed
  payloads return 409; expired retries replace old rows without stale FTS entries.
- 2 MiB HTTP bodies, up to 1,000 messages and 1,000,000 UTF-16 code units per
  message. Unsupported inputs are rejected, never silently truncated.
- Default 1,200 Unicode code-point chunks, with 160-code-point overlap.
- Default 10,000 live chunks per user. Exceeding capacity returns 422.
- Search supports at most 512 distinct lexical terms, including options.
- Top-k is 1-100; lexical and vector candidate pools are at most 300 each.
- Hybrid Add has at most 40 combined model requests and uses embedding batches
  of 10. Larger model workloads return 422 before provider calls.
- Optional vectors are scanned exactly within the current user. Full-size
  history CPU time and storage quota have not been benchmarked.
- Missing provider credentials, malformed responses and changed index settings
  fail explicitly. No model substitution or silent lexical fallback occurs.

For a changed representation (mode/model/chunking), use a new `SUBMISSION_ID`.
Never mix index formats or mutate a submitted system while an official run is
active. Adjust limits only after load tests and disclose them to AML.

## Privacy and retention

Worker request logging and observability are disabled. Do not add body/prompt
logging, query-string keys, unapproved model proxies or training-data capture.

Records expire seven days after each Add by default. Search excludes expired
data immediately. A Durable Object alarm physically deletes expired rows and
their FTS entries. Retries reschedule the alarm without extending record expiry.

**Important:** Cloudflare SQLite Durable Objects provide platform-managed
point-in-time recovery for up to 30 days. Application-row deletion is not proof
of immediate erasure of all recovery copies. Before sending private official
evaluation data, confirm whether this retention is accepted by AML and establish
a cleanup procedure covering Cloudflare recovery history and provider retention.
This initial deployment is for synthetic integration checks only.

This project does not claim retention compliance solely from the seven-day TTL.
Do not submit official benchmark content until this issue is resolved.

## References

- AML: https://agentmemoryleaderboard.ai/api-guide
- Cloudflare SQLite storage:
  https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/
- SQLite FTS5: https://www.sqlite.org/fts5.html

Original memory-system design and source are by the sixteen team with AI coding
assistance. FTS5/BM25, standard RRF, Zod, Cloudflare tooling and separately
provided models are attributed upstream; no novelty or leaderboard result is
asserted.
