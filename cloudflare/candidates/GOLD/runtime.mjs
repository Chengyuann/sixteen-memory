// Synthetic, local workerd only. Original source evidence must stay byte-identical.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { performance } from "node:perf_hooks";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";
import { runAuthenticatedSuite } from "../../scripts/authenticated-suite.mjs";

const cf = resolve(import.meta.dirname, "../..");
const output = resolve(process.env.OUTPUT ?? join(cf, "../../golden-20261031/local-runtime"));
await mkdir(output, { recursive: false });
const key = "synthetic-gold-local-key-never-a-real-credential";
const config = JSON.parse(await readFile(join(import.meta.dirname, "wrangler.jsonc"), "utf8"));
const workers = new Map();
const optionsByName = new Map();
const sourceHashes = {};
const metrics = [];
let outbound = 0;
let checks = 0;
const check = (condition) => { assert.ok(condition); checks++; };
const save = (name, body) => writeFile(join(output, name), JSON.stringify(body, null, 2), { flag: "wx" });
const post = async (name, route, body) => {
  const start = performance.now();
  const response = await workers.get(name).dispatchFetch(`https://local.invalid/v1/memories/${route}`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.json(), ms: performance.now() - start };
};
const stats = async (name, user) => {
  const namespace = await workers.get(name).getDurableObjectNamespace("MEMORY");
  return namespace.get(namespace.idFromName(JSON.stringify(["gold-local", user])));
};
const query = (user_id, text, top_k = 100) => ({ user_id, query: text, top_k });
function batch(user_id, start, count, length = 180) {
  return {
    user_id, request_id: `batch-${start}`, session_id: `session-${Math.floor(start / 1000)}`,
    messages: Array.from({ length: count }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: (`Unique synthetic record${start + i} violetquartz. ` +
        "This source preserves precise history and original evidence. ".repeat(30)).slice(0, length),
      timestamp: 1704067200000 + (start + i) * 1000,
    })),
  };
}

async function startWorker(name) {
  const source = name === "BASE" ? "../BASE/index.ts" : "./local-harness.ts";
  const bundle = join(output, `${name}.mjs`);
  const result = await build({
    entryPoints: [resolve(import.meta.dirname, source)], outfile: bundle, bundle: true,
    metafile: true, format: "esm", target: "es2022", platform: "neutral",
    conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"],
  });
  sourceHashes[name] = createHash("sha256").update(await readFile(bundle)).digest("hex");
  const options = {
    ...convertV4MiniflareOptions({
      name: `gold-local-${name}`, modules: true, modulesRoot: output, scriptPath: bundle,
      compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
      bindings: {
        ...config.vars, SUBMISSION_ID: "gold-local", MEMORY_API_KEY: key,
        SOURCE_COMMIT: "a".repeat(40), MAX_CHUNKS_PER_USER: name === "BASE" ? "10000" : "100000",
      },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
      outboundService: () => { outbound++; return new Response("blocked", { status: 502 }); },
    }),
    resourcePersistencePath: join(output, `state-${name}`),
  };
  optionsByName.set(name, options);
  workers.set(name, new Miniflare(options));
  return result;
}

try {
  await startWorker("BASE");
  await startWorker("GOLD");
  const authenticated = await runAuthenticatedSuite({
    send: (url, init) => workers.get("GOLD").dispatchFetch(url.toString(), init),
    baseUrl: "https://local.invalid", key, loadRecords: 1000, loadQueries: 20,
  });
  assert.equal(authenticated.status, "passed", JSON.stringify(authenticated.failure));
  await save("authenticated.json", authenticated);

  const user = "synthetic-parity";
  for (let start = 0; start < 1000; start += 100) {
    const payload = batch(user, start, 100);
    for (const name of workers.keys()) check((await post(name, "add", payload)).status === 200);
  }
  const unicode = {
    user_id: user, request_id: "unicode", session_id: "history",
    messages: [
      { role: "user", content: "Atlas 中文😀 exact source. ".repeat(1600) },
      { role: "assistant", content: "Private confidential value. ".repeat(180) },
      { role: "user", content: "Where is the venue for Atlas?" },
      { role: "assistant", content: "It is at the violetquartz riverside." },
    ],
  };
  for (const name of workers.keys()) check((await post(name, "add", unicode)).status === 200);
  for (const text of ["record999", "violetquartz", "Atlas", "Atlas rules", "Summarize Atlas",
    "Where is the venue?", "中文😀", "absentword", "What is not approved?", "private value"]) {
    for (const top of [1, 5, 20, 100]) {
      const request = query(user, text, top);
      const a = await post("BASE", "search", request), b = await post("GOLD", "search", request);
      assert.deepEqual({ status: b.status, body: b.body }, { status: a.status, body: a.body });
      checks++;
    }
  }
  // Independent same-user concurrent writes; rejected requests must be retryable.
  const concurrent = Array.from({ length: 16 }, (_, i) => batch("concurrent", i, 1));
  const responses = await Promise.all(concurrent.map((p) => post("GOLD", "add", p)));
  for (const [i, r] of responses.entries()) {
    check([200, 429].includes(r.status));
    if (r.status === 429) check((await post("GOLD", "add", concurrent[i])).status === 200);
  }
  let stub = await stats("GOLD", "concurrent");
  let counts = await stub.testStats();
  check(counts.chunks === 16 && counts.counter === 16 && counts.indexed === 16);
  check((await post("GOLD", "search", query("different-user", "violetquartz"))).body.data.length === 0);
  const duplicate = await post("GOLD", "add", concurrent[0]);
  check(duplicate.status === 200);
  const changed = structuredClone(concurrent[0]); changed.messages[0].content = "different content";
  check((await post("GOLD", "add", changed)).status === 409);
  counts = await stub.testStats(); check(counts.counter === 16);

  // Expiry frees exactly the expired rows, including an expired replay.
  await stub.testExpire("batch-0");
  check((await post("GOLD", "search", query("concurrent", "record0"))).body.data.length === 0);
  check((await post("GOLD", "add", concurrent[0])).status === 200);
  counts = await stub.testStats(); check(counts.chunks === 16 && counts.counter === 16 && counts.indexed === 16);
  await stub.testExpire("batch-1"); await stub.testAlarm();
  counts = await stub.testStats(); check(counts.chunks === 15 && counts.counter === 15 && counts.indexed === 15);
  // Simulate an older persisted database and lost alarm before cold restart.
  await stub.testDeleteCounter(); await stub.testDeleteAlarm();
  await workers.get("GOLD").dispose();
  workers.set("GOLD", new Miniflare(optionsByName.get("GOLD")));
  check((await post("GOLD", "search", query("concurrent", "record15"))).body.data.length === 1);
  stub = await stats("GOLD", "concurrent"); counts = await stub.testStats();
  check(counts.counter === 15 && counts.chunks === 15 && counts.alarm > Date.now());

  // Realistic-size synthetic data: each record is one 1,100-character source block.
  const capacity = "capacity100k";
  const batches = [];
  for (let start = 0; start < 100000; start += 500) {
    const payload = batch(capacity, start, 500, 1100);
    const response = await post("GOLD", "add", payload);
    assert.equal(response.status, 200, JSON.stringify(response.body));
    batches.push(response.ms);
    if ([1000, 10000, 100000].includes(start + 500)) {
      stub = await stats("GOLD", capacity);
      const state = await stub.testStats();
      check(state.counter === start + 500 && state.chunks === state.counter && state.indexed === state.counter);
      const search = await post("GOLD", "search", query(capacity, `record${start + 499}`));
      check(search.status === 200 && search.body.data.length === 1);
      metrics.push({
        chunks: start + 500, ...state, last_batch_ms: response.ms, search_ms: search.ms,
        average_batch_ms: batches.reduce((a, b) => a + b, 0) / batches.length,
      });
      console.log(JSON.stringify(metrics.at(-1)));
    }
  }
  // Fail atomically beyond the declared ceiling; exact replay still succeeds.
  check((await post("GOLD", "add", batch(capacity, 100000, 1))).status === 422);
  check((await post("GOLD", "search", query(capacity, "record100000"))).body.data.length === 0);
  check((await post("GOLD", "add", batch(capacity, 0, 500, 1100))).status === 200);
  stub = await stats("GOLD", capacity); counts = await stub.testStats();
  check(counts.counter === 100000 && counts.chunks === 100000);
  await workers.get("GOLD").dispose();
  workers.set("GOLD", new Miniflare(optionsByName.get("GOLD")));
  check((await post("GOLD", "search", query(capacity, "record99999"))).body.data.length === 1);
  check((await post("GOLD", "search", query(capacity, "record0"))).body.data.length === 1);
  check(outbound === 0);
  await save("summary.json", {
    status: "passed", checks, authenticated_groups: authenticated.groups,
    metrics, sourceHashes, outboundAttempts: outbound,
    capacity_source_characters: 110000000, max_chunks_per_user: 100000,
    live_cloud_calls: 0, model_calls: 0, official_evaluations: 0,
    caveat: "Local workerd measurements are not Cloudflare billing or full-evaluation guarantees.",
  });
} catch (error) {
  await save("error.json", { error: String(error), stack: error.stack, checks, metrics });
  throw error;
} finally {
  await Promise.all([...workers.values()].map((w) => w.dispose()));
}
