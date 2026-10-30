// Public regression input contains only source messages, opaque scope IDs and queries.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { performance } from "node:perf_hooks";

const [inputArg, outputArg] = process.argv.slice(2);
assert.ok(inputArg && outputArg, "Provide source-only input and new output directory");
const input = resolve(inputArg), output = resolve(outputArg);
await mkdir(output, { recursive: false });
const cf = resolve(import.meta.dirname, "../..");
const raw = await readFile(input);
const jobs = JSON.parse(raw);
const config = JSON.parse(await readFile(join(import.meta.dirname, "wrangler.jsonc"), "utf8"));
const sha = (data) => createHash("sha256").update(data).digest("hex");
const key = "synthetic-parity-no-real-credential-ever";
const workers = new Map();
const sourceHashes = {};
let outbound = 0, queries = 0, posts = 0;
const durations = { BASE: { add: [], search: [] }, GOLD: { add: [], search: [] } };
const write = (name, value) => writeFile(join(output, name), JSON.stringify(value, null, 2), { flag: "wx" });
const post = async (name, kind, body) => {
  const started = performance.now();
  const response = await workers.get(name).dispatchFetch(`https://local.invalid/v1/memories/${kind}`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  durations[name][kind].push(performance.now() - started);
  posts++;
  assert.equal(response.status, 200, `${name} ${kind} ${response.status}`);
  return response.json();
};
let handle;
try {
  for (const name of ["BASE", "GOLD"]) {
    const source = join(cf, "candidates", name, "index.ts"), bundle = join(output, `${name}.mjs`);
    const result = await build({
      entryPoints: [source], outfile: bundle, bundle: true, metafile: true,
      format: "esm", target: "es2022", platform: "neutral",
      conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"],
    });
    sourceHashes[name] = { bundle: sha(await readFile(bundle)), files: {} };
    for (const path of Object.keys(result.metafile.inputs)) {
      if (path.includes("node_modules")) continue;
      sourceHashes[name].files[path] = sha(await readFile(path));
    }
    workers.set(name, new Miniflare({
      ...convertV4MiniflareOptions({
        name: `gold-parity-${name}`, modules: true, modulesRoot: output, scriptPath: bundle,
        compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
        bindings: {
          ...config.vars, SUBMISSION_ID: "same-source-parity", MEMORY_API_KEY: key,
          SOURCE_COMMIT: "a".repeat(40), MAX_CHUNKS_PER_USER: "10000",
        },
        durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
        outboundService: () => { outbound++; return new Response("blocked", { status: 502 }); },
      }),
      resourcePersistencePath: join(output, `state-${name}`),
    }));
  }
  await write("manifest.json", {
    input_sha256: sha(raw), sourceHashes, scopes: jobs.length,
    deployment: false, source_only: true, private_evaluation_data: false,
  });
  handle = await open(join(output, "comparisons.jsonl"), "wx");
  for (const [i, job] of jobs.entries()) {
    assert.deepEqual(Object.keys(job).sort(), ["batches", "case_id", "experiments", "queries"]);
    assert.match(job.case_id, /^source-[0-9a-f]{16}$/);
    assert.deepEqual(job.experiments, ["BASE"]);
    for (const batch of job.batches) {
      assert.ok(Object.keys(batch).every((k) => ["request_id", "session_id", "timestamp", "messages"].includes(k)));
      const payload = {
        user_id: job.case_id, request_id: batch.request_id, session_id: batch.session_id,
        messages: batch.messages.map((m) => {
          assert.ok(Object.keys(m).every((k) => ["role", "content", "timestamp"].includes(k)));
          return { ...m, ...(m.timestamp === undefined && batch.timestamp !== undefined
            ? { timestamp: batch.timestamp } : {}) };
        }),
      };
      const a = await post("BASE", "add", payload), b = await post("GOLD", "add", payload);
      assert.deepEqual(b, a);
    }
    for (const [q, text] of job.queries.entries()) {
      assert.equal(typeof text, "string");
      const payload = { user_id: job.case_id, query: text, top_k: 100 };
      const a = await post("BASE", "search", payload), b = await post("GOLD", "search", payload);
      assert.deepEqual(b, a, `${job.case_id}, query index ${q}`);
      queries++;
      await handle.write(JSON.stringify({
        case: job.case_id, query_index: q, results: b.data.length,
        response_sha256: sha(JSON.stringify(b)), exact_match: true,
      }) + "\n");
    }
    console.log(JSON.stringify({ completed_scopes: i + 1, total: jobs.length, queries, mismatches: 0 }));
  }
  await handle.sync(); await handle.close(); handle = undefined;
  assert.equal(outbound, 0);
  const metric = (values) => {
    const sorted = values.toSorted((a, b) => a - b);
    return { count: values.length, mean_ms: values.reduce((a, b) => a + b, 0) / values.length,
      p50_ms: sorted[Math.floor(sorted.length * .5)], p95_ms: sorted[Math.floor(sorted.length * .95)],
      max_ms: sorted.at(-1) };
  };
  await write("summary.json", {
    status: "passed", scopes: jobs.length, queries, mismatches: 0, posts,
    exact_source_metadata_ids_order_scores: true, sourceHashes, input_sha256: sha(raw),
    timing: Object.fromEntries(Object.entries(durations).map(([n, d]) =>
      [n, Object.fromEntries(Object.entries(d).map(([k, v]) => [k, metric(v)]))])),
    outbound, model_calls: 0, official_evaluations: 0,
    caveat: "Previously used public regression, not new blind answer accuracy.",
  });
} catch (error) {
  await write("error.json", { error: String(error).slice(0, 1000), queries, posts, outbound });
  throw error;
} finally {
  if (handle) await handle.close();
  await Promise.all([...workers.values()].map((w) => w.dispose()));
}
