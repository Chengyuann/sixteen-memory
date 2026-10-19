# Deployment and submission checklist

## Prerequisites

1. An explicitly authorized public Linux server and DNS name. This project does
   not assume permission to expose a company DevBox.
2. Docker/Compose or a supervised Python 3.12 process.
3. Approved `text-embedding-v4` credentials for hybrid retrieval, and optional
   `gpt-4o-mini` credentials for fact extraction, with a user-approved budget.
4. A persistent volume and a TLS reverse proxy. Keep the database private.

A 2 vCPU / 4 GiB instance is an initial sizing hypothesis, not a measured
capacity promise. Confirm memory, disk, model rate limits and longest-sample
latency with synthetic load before official evaluation. Do not purchase
resources automatically.

## Configuration

Create `.env` from `.env.example` on the deployment host and set real credentials.
Generate the memory API credential independently from model API credentials.
Set `SOURCE_COMMIT` to the exact reviewed Git commit, not a branch name.

```bash
docker compose config --quiet
docker compose up -d --build
curl --fail http://127.0.0.1:8080/health
```

Run **one worker and one service replica**. SQLite writes are serialized; this
baseline is not a horizontally scalable deployment.

Example Caddy configuration, replacing the example domain with an owned domain:

```caddyfile
memory.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Do not use an example or temporary URL in an AML application. Do not enable
body logging or public debugging. Only the authenticated Add/Search routes and
public health route need to be reachable.

## Declared limits

- Textual input only; no image or audio support.
- 2 MiB per HTTP body; no truncation, oversized requests receive 413.
- At most 1,000 messages per Add and 1,000,000 characters per message.
- Source chunks are 1,200 characters by default with 160-character overlap.
- Query at most 32,000 characters; at most 100 options of 8,000 characters each.
- `top_k` is between 1 and 100; fewer results are valid.
- Default 10,000 stored chunks per `user_id`. Capacity overflow returns 422
  before writing. Increase only after measurement, never silently drop memories.
- Default two in-flight Add/Search requests combined; overflow is 429 with
  `Retry-After: 2`.
- Provider timeout is 60 seconds per call. Add may make multiple batches.
- Embeddings are sent in batches of at most 10 using 1,024 dimensions.
- Search performs an exact per-user vector scan and rebuilds BM25 per query.
  This is intentionally a small baseline; longest-history latency is unmeasured.

Declare these limits in the submission. If the official workload exceeds them,
change and test the implementation before freezing it; do not claim unrestricted
capacity.

## Live verification before applying

1. Freeze the configuration and reviewed commit.
2. Confirm unauthenticated Add/Search return 401.
3. Write synthetic content through the public HTTPS Add endpoint and search it
   immediately through the public Search endpoint.
4. Repeat a write and verify no duplicate; verify a different user cannot read it.
5. Restart the service and verify synthetic data remains available.
6. Verify actual provider model identifiers, cost, quota and latency.
7. Record runtime configuration and measured concurrency without secrets.
8. Apply with actual URLs, credentials through the protected form, and repository
   plus exact commit. Do not email or publish access keys.
9. Run official Smoke only after access is approved. Obtain explicit authorization
   before spending a formal Full run or invoking large paid evaluations.

## Privacy and deletion

The application expires data 30 days after each write by default, purges hourly,
and excludes expired records immediately from Search. Prefer a shorter configured
retention (for example 7 days) when compatible with evaluation/reproduction.
The official requirement is deletion within 30 days after the evaluation ends.
Account for purge scheduling, retries, backups, snapshots and model providers;
local SQLite deletion alone is not a full retention guarantee.

For a finished run, arrange verified deletion of its data and associated
snapshots within that window. Do not run destructive cleanup without the
operator's explicit authorization. Do not stop the service during an active run.

## Publication status

The repository is source code, not evidence of a working public endpoint.
No live provider call, public Add/Search test, Docker build, official Smoke,
official Full or score has been verified at initial publication.
