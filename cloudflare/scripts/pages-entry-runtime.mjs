import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { runAuthenticatedSuite } from "./authenticated-suite.mjs";

const directory = await mkdtemp(join(tmpdir(), "sixteen-pages-entry-"));
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const entryConfig = JSON.parse(await readFile(
  new URL("../pages-entry/wrangler.jsonc", import.meta.url), "utf8",
));
const key = "synthetic-pages-entry-key-only-no-provider";
const options = convertV4MiniflareOptions({
  workers: [
    {
      name: entryConfig.name,
      modules: true,
      scriptPath: new URL("../pages-entry/public/_worker.js", import.meta.url).pathname,
      compatibilityDate: entryConfig.compatibility_date,
      serviceBindings: { MEMORY_SERVICE: config.name },
    },
    {
      name: config.name,
      modules: true,
      scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
      compatibilityDate: config.compatibility_date,
      compatibilityFlags: config.compatibility_flags,
      bindings: { ...config.vars, SOURCE_COMMIT: "a".repeat(40), MEMORY_API_KEY: key },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
    },
  ],
});
const mf = new Miniflare({ ...options, resourcePersistencePath: directory });
try {
  const report = await runAuthenticatedSuite({
    send: (url, init) => mf.dispatchFetch(url.toString(), init),
    baseUrl: "https://sixteen-memory-entry.pages.dev",
    key,
  });
  const summary = {
    status: report.status,
    checks: report.checks.length,
    attempts: report.http_attempts,
    records: report.load?.records,
    max_concurrency: report.max_concurrency,
    status_counts: report.status_counts,
    failure: report.failure,
    model_calls: report.model_calls,
    cloud_requests: 0,
  };
  console.log(JSON.stringify(summary, null, 2));
  assert.equal(report.status, "passed");
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
