# Sixteen Memory

A small, source-linked memory service for the Agent Memory Leaderboard (AML)
open-source textual track. Team: **sixteen**. Maintainer: **Chengyuan Ma**.

**Status:** source implementation, not an official AML submission. The Python
v0.1.0 baseline is preserved; a separate Cloudflare v0.2.0 adapter is available.
No live model provider, official Smoke, Full, ranking or performance is claimed.
See [cloudflare/README.md](cloudflare/README.md) for the serverless deployment.

## Method

- Preserve source chunks, roles, conversation identifiers and source timestamps.
- Optionally extract explicit source-linked facts with `gpt-4o-mini`.
- Retrieve using BM25 over source text and optional facts, plus
  `text-embedding-v4` cosine similarity over source text.
- Fuse the two rankings using reciprocal-rank fusion, then deduplicate.
- Return original source evidence in rank order, not generated answers.

Old and new statements are retained independently; timestamps are evidence, not
an automatic "newest always wins" rule. Fact extraction never removes original
text. Exact quotes are checked, but this does not prove every extracted fact is
semantically correct. The current implementation does not perform temporal query
reasoning, graph traversal or neighbor expansion.

This is a baseline to evaluate, not a claim of a novel or superior algorithm.
The implementation was developed with AI coding assistance and requires human
review and benchmark validation.

## Runtime modes

| Mode | Provider calls | Intended use |
| --- | --- | --- |
| `lexical` | None | Offline contract tests and a BM25 baseline |
| `hybrid`, facts off | `text-embedding-v4` | Candidate retrieval baseline |
| `hybrid`, facts on | Above + `gpt-4o-mini` | Source-linked fact ablation |

Mode selection is explicit. Missing credentials fail startup in hybrid mode;
the service never silently substitutes lexical retrieval or another model.
Only user-authorized providers should receive evaluation data. Do not point the
service at an unapproved proxy or a provider that retains data for training.

## Local offline check

Python 3.11+ is required; Python 3.12 is the tested target.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock -e '.[dev]'
ruff check .
pytest -q
python scripts/offline_smoke.py
export MEMORY_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export MEMORY_MODE=lexical
export DATABASE_PATH=data/local.sqlite3
uvicorn sixteen_memory.app:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log
```

Do not use an offline test as evidence of a successful live model integration.

## AML endpoints

`GET /health` is public and exposes version/mode, never keys or stored memories.
Both POST endpoints require `Authorization: Bearer <MEMORY_API_KEY>`.

`POST /v1/memories/add`

```json
{
  "request_id": "example-write-1",
  "user_id": "example-scope",
  "session_id": "example-session",
  "messages": [
    {"role": "user", "timestamp": 1704067200000, "content": "Alice lives in Paris."}
  ]
}
```

```json
{
  "success": true,
  "request_id": "example-write-1",
  "user_id": "example-scope",
  "session_id": "example-session"
}
```

`POST /v1/memories/search`

```json
{
  "query": "Where does Alice live?",
  "user_id": "example-scope",
  "top_k": 100
}
```

The response has `data: [{id, content, score, created_at?}]`. No match produces
`{"data":[]}`. Multiple-choice `options` are optional at the request top level;
they are used only as retrieval terms, not selected or answered.

## Deployment

For Cloudflare, use [cloudflare/README.md](cloudflare/README.md). Its runtime uses
per-user SQLite Durable Objects and SQLite FTS5 BM25, not a persistent local disk.
The initial Cloudflare configuration uses lexical retrieval with no model calls.

See [deployment.md](docs/deployment.md) for the required public HTTPS host,
model credentials, request limits, version freeze and retention requirements.
The included Compose file exposes only localhost. A separately configured TLS
reverse proxy must publish the approved endpoints. This repository does not
provision a server, buy resources or expose an internal development machine.

## Correctness and data handling

- Every read is scoped by the exact supplied `user_id`; storage also records
  request and session provenance. No cross-scope search or cache exists.
- Add is synchronous. A success response requires durable storage and completed
  indexing; an upstream error produces no partially acknowledged write.
- Repeated `(user_id, request_id)` with the same body is a no-op. A different body
  returns 409 rather than replacing memory.
- The index configuration is persisted. Changing its model, mode or chunking
  requires a new database, preventing mixed-version vectors.
- Payloads are not silently shortened. Unsupported inputs receive explicit 4xx.
- No request/response body logging is implemented. Avoid proxy access/body logs,
  APM content capture, prompt retention and untracked database snapshots.
- An expiry loop deletes records; startup and queries also respect expiration.
  The operator remains responsible for deleting the database, backups, snapshots
  and any provider-held data within the event's required retention window.
- Test fixtures are synthetic. Do not put private evaluation data in Git,
  issues, reports, traces or model training sets.

## Attribution

This is an independent implementation, not a fork of an existing memory agent.
Retrieval uses the `rank-bm25` library by Dorian Brown and standard BM25 and RRF:

- Brown, *rank_bm25*: https://github.com/dorianbrown/rank_bm25
- Cormack, Clarke and Buettcher (2009), *Reciprocal Rank Fusion Outperforms
  Condorcet and Individual Rank Learning Methods*.
- `text-embedding-v4`: Alibaba Cloud Model Studio, supplied separately.
- `gpt-4o-mini`: OpenAI, supplied separately when facts are enabled.

Model/service terms and dependency licenses are separate from this repository's
MIT license.

Official protocol and rules (checked 2026-10-19):
- https://agentmemoryleaderboard.ai/api-guide
- https://agentmemoryleaderboard.ai/rules
- https://agentmemoryleaderboard.ai/competition/
