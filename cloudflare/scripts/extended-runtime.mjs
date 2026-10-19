import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { runAuthenticatedSuite } from "./authenticated-suite.mjs";

const directory = await mkdtemp(join(tmpdir(), "sixteen-extended-"));
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const key = "synthetic-runtime-key-only-no-provider";
const options = convertV4MiniflareOptions({
  name: "sixteen-memory-extended", modules: true,
  scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
  compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
  bindings: { ...config.vars, SOURCE_COMMIT: "a".repeat(40), MEMORY_API_KEY: key },
  durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
});
const mf = new Miniflare({ ...options, resourcePersistencePath: directory });
try {
  const report = await runAuthenticatedSuite({
    send: (url, init) => mf.dispatchFetch(url.toString(), init),
    baseUrl: "http://localhost", key, loadRecords: 1000, loadQueries: 20,
  });
  console.log(JSON.stringify({
    status: report.status, checks: report.checks, attempts: report.http_attempts,
    failure: report.failure, load: report.load, status_counts: report.status_counts,
  }, null, 2));
  assert.equal(report.status, "passed");
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
