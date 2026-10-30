// Paired synthetic measurement of SQL work; no cloud or provider calls.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
const output = resolve(process.argv[2]);
await mkdir(output, { recursive: false });
const key = "synthetic-efficiency-key-never-real-secret";
const config = JSON.parse(await readFile(join(import.meta.dirname, "wrangler.jsonc"), "utf8"));
const workers = new Map(), stubs = new Map();
let outbound = 0;
const records = {};
const post = async (name, route, body) => {
  const start = performance.now();
  const r = await workers.get(name).dispatchFetch(`https://local.invalid/v1/memories/${route}`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  assert.equal(r.status, 200, `${name} ${route}`);
  return { data, ms: performance.now() - start };
};
const add = (start, count) => ({
  user_id: "paired-load", request_id: `r${start}`, session_id: "s",
  messages: Array.from({ length: count }, (_, i) => ({
    role: "user", content: `Marker${start + i} violetquartz original exact evidence. ` +
      "No generated facts or rewritten content. ".repeat(10),
  })),
});
const summarize = async (name, ms) => {
  const s = await stubs.get(name).testStats();
  return { operations: s.operations, rowsRead: s.rowsRead, rowsWritten: s.rowsWritten, ms };
};
try {
  for (const name of ["BASE", "GOLD"]) {
    const bundle = join(output, `${name}.mjs`);
    await build({
      entryPoints: [join(import.meta.dirname, name === "BASE" ? "baseline-harness.ts" : "local-harness.ts")],
      outfile: bundle, bundle: true, format: "esm", platform: "neutral", target: "es2022",
      conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"],
    });
    const mf = new Miniflare({
      ...convertV4MiniflareOptions({
        name: `efficiency-${name}`, modules: true, modulesRoot: output, scriptPath: bundle,
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
        bindings: { ...config.vars, SUBMISSION_ID: "paired", MAX_CHUNKS_PER_USER: "10000", MEMORY_API_KEY: key },
        durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
        outboundService: () => { outbound++; return new Response("blocked", { status: 502 }); },
      }),
      resourcePersistencePath: join(output, `state-${name}`),
    });
    workers.set(name, mf);
    const ns = await mf.getDurableObjectNamespace("MEMORY");
    stubs.set(name, ns.get(ns.idFromName(JSON.stringify(["paired", "paired-load"]))));
    records[name] = {};
  }
  for (const name of workers.keys()) {
    let ms = 0;
    for (let i = 0; i < 9000; i += 500) ms += (await post(name, "add", add(i, 500))).ms;
    records[name].build9000 = await summarize(name, ms);
    records[name].append = await summarize(name, (await post(name, "add", add(9000, 1))).ms);
    records[name].replay = await summarize(name, (await post(name, "add", add(9000, 1))).ms);
  }
  for (const top of [1, 100]) {
    const responses = {};
    for (const name of workers.keys()) {
      responses[name] = await post(name, "search", {
        user_id: "paired-load", query: "violetquartz", top_k: top,
      });
      records[name][`search${top}`] = await summarize(name, responses[name].ms);
    }
    assert.deepEqual(responses.GOLD.data, responses.BASE.data);
  }
  assert.ok(records.GOLD.append.rowsRead < records.BASE.append.rowsRead / 20);
  assert.ok(records.GOLD.search100.operations < records.BASE.search100.operations / 5);
  assert.equal(outbound, 0);
  await writeFile(join(output, "summary.json"), JSON.stringify({
    status: "passed", records, exact_search_parity: true, outbound,
    caveat: "Local workerd SQL counters and wall times; not cloud billing or latency promises.",
  }, null, 2), { flag: "wx" });
  console.log(JSON.stringify(records));
} finally {
  await Promise.all([...workers.values()].map((w) => w.dispose()));
}
