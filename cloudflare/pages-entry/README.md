# Optional Pages entry

This directory is a separate transport entry, not a new memory implementation.
Its Service binding must point to the existing `sixteen-memory` Worker in the
same Cloudflare account.

`public/_worker.js` forwards requests through `MEMORY_SERVICE.fetch`, preserving
the path, query, method, body, headers and response. The destination is fixed.
The URL is normalized to the original Worker hostname, but Service bindings
do not resolve that hostname or connect to it over the public Internet.

The original Worker still validates Bearer authentication and owns its Durable
Objects. Do not add the memory key to this project, create replacement storage,
copy benchmark data, add body logging, or introduce caching. The entry must not
expose static files when the Function is unavailable.

## Local check

From `cloudflare/`, using the existing pinned dependencies:

```sh
npx wrangler deploy --dry-run --outdir dist
npm run test:pages-entry
```

This runs the original Worker bundle behind a local Service binding with a fake
credential and temporary SQLite storage. It makes no cloud or model requests.
The compatibility date matches the original Worker and the pinned local runtime.

## Deployment requires account-owner authorization

The sample project name is `sixteen-memory-entry`. Check for an existing project
before creating it; never overwrite an unrelated project.

From this directory, with a previously authorized Wrangler installation:

```sh
../node_modules/.bin/wrangler pages project create sixteen-memory-entry \
  --production-branch main --compatibility-date 2026-10-19
../node_modules/.bin/wrangler pages deploy public \
  --project-name sixteen-memory-entry --branch main
```

Use the hostname actually returned by deployment. Confirm the Service binding,
logging settings, production deployment and access policies before sending
authenticated data. Test public health, unauthorized boundaries and bounded
synthetic Add/Search from the networks that previously failed.

Do not change AML's approved endpoints until the new entry is verified. Follow
the platform's approved version/endpoint update process, and do not start Full
or retry a failed Smoke as part of deployment without authorization.

This entry does not resolve storage limits, provider recovery-history retention,
or benchmark effectiveness. Passing local tests does not establish public
reachability.
