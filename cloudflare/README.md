# Sixteen Memory on Cloudflare

Version **0.2.4**. A Worker exposes the AML textual Add/Search contract. Each
`(SUBMISSION_ID, user_id)` gets a separate SQLite-backed Durable Object. This is
a serverless adapter, not a change to the original Python v0.1.0 baseline.

## Initial deployment mode

- `MEMORY_MODE=lexical`, `ENABLE_FACTS=false`.
- SQLite FTS5's BM25 ranks tokenized source chunks. CJK characters are separated.
- Original source, source timestamps, message order and provenance are retained.
- No LLM or embedding API is called in lexical mode.
- v0.2.3 completed one Textual Smoke (46/46, score 0.6860). This is not a
  Full score; the v0.2.4 candidate must pass a separate Smoke.
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
npm run test:long-query
npm run test:retrieval
npm audit --registry=https://registry.npmjs.org --audit-level=high
```

The test runtime version matches the Miniflare release used by pinned Wrangler.
All tests use synthetic data and fake or disabled model providers.
Runtime tests verify actual Workers SQLite, FTS5, retries, isolation, persistence
across restarts, expired-record exclusion, capacity errors and body limits.
The extended suite exercises 1,000 synthetic records and at most four concurrent
requests. The long-source regression preserves identical text at different
message positions and character offsets; v0.2.1 fixes over-aggressive deduplication.
Version 0.2.2 accepts the case-insensitive HTTP Bearer authentication scheme
while preserving exact constant-time comparison of the credential.
Version 0.2.3 accepts long textual questions and options up to the existing
bounded request-body and field limits instead of rejecting queries with more
than 512 distinct lexical terms.
Version 0.2.4 uses FTS5 Porter stemming, question-prioritized weighted rank
fusion, source-time/write-order tie-breaking, bounded rule context expansion,
and timeline coverage for explicit overview requests. It returns original
evidence, not generated summaries or answers. The schema fingerprint is bumped
because the tokenizer changes. Existing storage must not be reused.
Its configuration targets the separate `sixteen-memory-v024` Worker with a
new Durable Object namespace. The existing `sixteen-memory-entry.pages.dev`
address forwards to this Worker after the new AML version is registered using
the same Add/Search URLs. A new version does not require a new public address.
Previous Workers and their storage remain unchanged. Once the shared public
address serves a new version, never run an older-version evaluation through it.
The deep local suite exercises the declared 10,000-live-chunk boundary and
overflow rollback, and invokes an actual local Durable Object alarm to verify
physical deletion of source rows, retry rows and FTS rows.
The Pages-entry suite runs the same bounded authenticated checks through a
transparent Service binding entry. It verifies that this alternate hostname
preserves the existing Worker's authentication, persistence and response
semantics without copying its Durable Object storage or embedding a service key.
The retrieval-quality suite is hand-authored synthetic regression coverage,
not an estimate of AML scores. To compare an archived baseline bundle, place
it in the local `dist/` directory and set `BASELINE_BUNDLE=dist/baseline.js`.

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

Both require `Authorization: Bearer <MEMORY_API_KEY>`. The `Bearer` scheme name
is matched case-insensitively as required by HTTP; the key remains an exact,
constant-time byte comparison. Other schemes are rejected.
Health returns 503 until the required configuration and API credential exist.

The initial deployment fails closed before the key is set. `workers.dev` is
enabled and preview URLs are disabled. No domain registration, paid subscription,
existing Worker changes, model invocation or formal evaluation is automated.

### Alternate public entry

`pages-entry/` contains a minimal Cloudflare Pages advanced-mode entry used when
a client network cannot reach `workers.dev`. It forwards the original request to
the `sixteen-memory-v024` Worker through a Cloudflare Service binding.
It does not use public `fetch`, inject credentials, store data, log bodies or
change the memory implementation. Keep the existing Pages project and public
hostname. Before changing its backend, confirm that no evaluation is running,
register the new AML version with the same URLs, retain the old backend, and
test the new backend. Verify the public Health version and authenticated
Add/Search after deployment, then start a new Smoke under the new version ID.

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
- Search uses all distinct lexical terms from the question and options. Request
  size remains bounded by the 2 MiB JSON body limit, 32,000 UTF-16 code units in
  `query`, and up to 100 options of at most 8,000 UTF-16 code units each.
- Top-k is 1-100; each of the question-only and question+options lexical
  candidate lists contains at most 300 records. Nonempty lists receive relative
  weights 2:1; the vector candidate list remains at most 300.
- Rule/procedure queries may expand the top four anchors to up to 24 chunks
  from each anchor's own Add request. This heuristic does not classify or
  redact sensitive source content; unrelated ordinary queries do not expand.
- Explicit summaries/overviews add up to top-k evenly spaced source chunks
  across the retained user timeline. This is source sampling, not semantic
  summarization; topic-specific summaries can include unrelated history.
- English Porter stemming can conflate distinct word forms. Non-English text
  retains the existing Unicode/CJK handling. Equal lexical scores prefer newer
  source timestamps, then later writes; no prior source records are deleted.
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
