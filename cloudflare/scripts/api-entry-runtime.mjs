// Local workerd only: verifies the isolated custom-domain proxy against real GOLD.
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { runAuthenticatedSuite } from "./authenticated-suite.mjs";

const directory = await mkdtemp(join(tmpdir(), "sixteen-api-entry-"));
const cf = new URL("../", import.meta.url).pathname;
const config = JSON.parse(await readFile(join(cf, "candidates/GOLD/wrangler.jsonc"), "utf8"));
const entry = JSON.parse(await readFile(join(cf, "api-entry/wrangler.jsonc"), "utf8"));
const key = "synthetic-api-entry-local-only-key";
let outbound = 0;
await build({ entryPoints: [join(cf, "candidates/GOLD/index.ts")],
  outfile: join(directory, "gold.mjs"), bundle: true, format: "esm", platform: "neutral",
  target: "es2022", conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"] });
const mf = new Miniflare({ ...convertV4MiniflareOptions({
  workers: [
    { name: entry.name, modules: true, modulesRoot: join(cf, "api-entry/public"),
      scriptPath: join(cf, "api-entry/public/_worker.js"),
      compatibilityDate: entry.compatibility_date,
      serviceBindings: { MEMORY_SERVICE: config.name } },
    { name: config.name, modules: true, modulesRoot: directory, scriptPath: join(directory, "gold.mjs"),
      compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
      bindings: { ...config.vars, SOURCE_COMMIT: "a".repeat(40), MEMORY_API_KEY: key },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
      outboundService: () => { outbound++; return new Response("outbound blocked", { status: 502 }); } },
  ],
}), resourcePersistencePath: join(directory, "state") });
try {
  const result = await runAuthenticatedSuite({
    send: (url, init) => mf.dispatchFetch(url.toString(), init),
    baseUrl: "https://api.sixteenmemory.com", key, loadRecords: 100, loadQueries: 4,
  });
  console.log(JSON.stringify({ status: result.status, version: result.version,
    checks: result.checks.length, http_attempts: result.http_attempts,
    status_counts: result.status_counts, failure: result.failure, outbound,
    cloud_calls: 0, model_calls: 0 }, null, 2));
  assert.equal(result.status, "passed");
  assert.equal(result.version, "0.3.0");
  assert.equal(outbound, 0);
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
