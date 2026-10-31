# Network readiness and operator-side fixes

Checked October31,2026. The deployed GOLD memory implementation remains pinned to
`89eaca3952052c314b4c8e87f637ecc68c7fec0e`. Changes here affect operator checks
only. They do not change the memory service, official evaluator, account plan,
system resolver or deployment routing.

## Confirmed checker defect

`cloudflare/scripts/public-smoke.mjs` previously required `0.2.x`, incorrectly
rejecting a correctly deployed `0.3.0`. It now validates semantic numeric versions
and supports exact `EXPECTED_VERSION` and `EXPECTED_COMMIT`. An operational check
is not an official scored Smoke. The historical evaluator failure was not caused
by this local regex.

## Connection fault and mitigation

The shared Pages entry correctly uses an internal Service Binding. It does not
resolve or connect to the public `workers.dev` hostname to reach the backend.
Therefore locally broken `workers.dev` DNS is not evidence that the Pages binding
is broken.

Separate tests observed intermittent pre-HTTP IPv4 connection failures on the
existing Pages addresses. Network reachability differed between local, remote and
GitHub-hosted execution. No local proxy environment or hosts override was found.
No privileged OS network settings were changed.

`cloudflare/scripts/memory-transport.mjs` provides an operator-only HTTPS client:

- restricts destination origins to the existing owned memory endpoints;
- allows only health and memory Add/Search, never evaluation creation/resume;
- retains hostname verification, TLS trust checks and public-address filtering;
- explicitly enables Node's IPv4/IPv6 family autoselection, with a250ms address
  connection-attempt timeout and a bounded total request deadline;
- keeps connections reusable where requested, with32 maximum sockets;
- records first request outcome, address attempts and failures without credentials,
  request bodies or returned memory;
- never automatically retries an HTTP operation or follows redirects.

TCP address fallback happens before transmitting HTTP. It is not an Add replay.
An uncertain Add outcome remains an error requiring explicit idempotence
reconciliation. A successful address fallback does not mean that the first IP was
healthy or that the underlying route was repaired.

Node primary references:

- https://nodejs.org/docs/latest-v22.x/api/net.html#socketconnectoptions-connectlistener
- https://nodejs.org/docs/latest-v22.x/api/https.html#class-httpsagent
- https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/http/

## Checks

```sh
node --test cloudflare/test/memory-transport.test.mjs
node cloudflare/scripts/network-check.mjs
PUBLIC_BASE_URL=https://96c2aae8.sixteen-memory-entry.pages.dev \
EXPECTED_VERSION=0.3.0 \
EXPECTED_COMMIT=89eaca3952052c314b4c8e87f637ecc68c7fec0e \
node cloudflare/scripts/public-smoke.mjs
```

`Bounded Network Check` is a manual GitHub workflow. It checks baseline and fixed
candidate endpoints with no secrets, authenticates nothing, writes no memories,
creates no official jobs, and uploads only sanitized health/network metadata.
It deliberately fails if a selected backend has changed.

## Not resolved by these changes

These changes cannot alter the official AML worker's resolver, proxy,
connection-pool or address selection. They cannot make a broken IPv4 route
universally reachable. Passing from other networks is useful evidence, not proof
of the failed official execution environment.

Do not automatically submit another Smoke/Full, rotate the shared endpoint,
change TLS verification, expose a corporate Devbox, purchase resources or claim
that paying for Workers fixes this network problem. A stable participant-owned
custom domain or evaluator-side connection correction may be needed, but neither
is performed by this tooling patch.
