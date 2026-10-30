// Re-open only this task's generated 100k synthetic database; never live/private data.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
const sourceState = resolve(process.argv[2]), output = resolve(process.argv[3]);
assert.ok(sourceState.includes("golden-20261031/local-runtime-final/state-GOLD"));
await mkdir(output, { recursive: false });
const config = JSON.parse(await readFile(join(import.meta.dirname, "wrangler.jsonc"), "utf8"));
const key = "synthetic-gold-local-key-never-a-real-credential";
const bundle = join(output, "worker.mjs");
await build({
  entryPoints: [join(import.meta.dirname, "index.ts")], outfile: bundle, bundle: true,
  format: "esm", platform: "neutral", target: "es2022",
  conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"],
});
let outbound = 0;
const mf = new Miniflare({
  ...convertV4MiniflareOptions({
    name: "gold-local-GOLD", modules: true, modulesRoot: output, scriptPath: bundle,
    compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
    bindings: { ...config.vars, SUBMISSION_ID: "gold-local", MEMORY_API_KEY: key, SOURCE_COMMIT: "a".repeat(40) },
    durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
    outboundService: () => { outbound++; return new Response("blocked", { status: 502 }); },
  }),
  resourcePersistencePath: sourceState,
});
try {
  const queries = ["violetquartz", "Summarize the violetquartz history",
    "What rules apply to violetquartz?", "record99999"];
  const records = [];
  for (const query of queries) {
    const start = performance.now();
    const r = await mf.dispatchFetch("https://local.invalid/v1/memories/search", {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "capacity100k", query, top_k: 100 }),
    });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.ok(body.data.length > 0 && body.data.length <= 100);
    records.push({ query, ms: performance.now() - start, results: body.data.length });
  }
  const concurrent = await Promise.all(Array.from({ length: 16 }, async (_, i) => {
    const start = performance.now();
    const r = await mf.dispatchFetch("https://local.invalid/v1/memories/search", {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "capacity100k", query: `record${99999 - i}`, top_k: 100 }),
    });
    const body = await r.json();
    assert.equal(r.status, 200); assert.equal(body.data.length, 1);
    return performance.now() - start;
  }));
  assert.equal(outbound, 0);
  await writeFile(join(output, "summary.json"), JSON.stringify({
    status: "passed", records, concurrent16_ms: concurrent, outbound, existing_synthetic_chunks: 100000,
    live_calls: 0, caveat: "Local wall-clock test, not measured Cloudflare CPU or uptime.",
  }, null, 2), { flag: "wx" });
  console.log(JSON.stringify({ records, concurrent16_max_ms: Math.max(...concurrent) }));
} finally { await mf.dispose(); }
