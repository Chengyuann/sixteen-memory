# Isolated custom-domain entry — November1,2026

The existing `sixteen-memory-entry` Pages project must remain unchanged. A separate
`sixteen-memory-api` project forwards requests to the existing GOLD Worker,
`sixteen-memory-v030`, through a Cloudflare Service Binding. It contains no
credentials, stores no memory and does not make public-network backend requests.
The API implementation remains pinned to `89eaca3952052c314b4c8e87f637ecc68c7fec0e`.

The custom hostname `api.sixteenmemory.com` has been associated with the isolated
project, but it is **not active until DNS ownership and TLS provisioning succeed**.
Registering a hostname is not evidence of a network repair.

External DNS record required:

| Type | Name | Target |
|---|---|---|
| CNAME | api | sixteen-memory-api.pages.dev |

Do not change the domain's nameservers, existing `www` site, apex, email MX,
SPF/DKIM/TXT, or other records. The DNS provider is separate from Cloudflare.
Do not point this hostname to the mutable baseline project.

The proxy source is byte-identical to the previously tested frozen GOLD Pages
entry. The new `api-entry-runtime.mjs` test runs the real GOLD worker in local
workerd, verifies18 authenticated/protocol test groups and asserts zero outbound
network calls. This test makes no cloud, model or official evaluation calls.

The initial local test had an incorrect Miniflare module-root configuration.
Explicit module roots corrected the test harness; the deployed backend was not
changed. Wrangler4.155.0 also attempted to delegate new Pages projects to Workers.
The explicitly documented-in-source `--force` option here selects the Pages
product; it does not overwrite another project or disable permission checks.

Only after the CNAME is present and Cloudflare reports the domain active should
operators test DNS, TLS, native IPv4/IPv6 and authenticated Add/Search from several
networks. Keep ordinary-client results separate from address-fallback tests.
No official Smoke/Full or registered endpoint change is authorized by this setup.
