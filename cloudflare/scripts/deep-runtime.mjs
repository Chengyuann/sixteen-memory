import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const directory = await mkdtemp(join(tmpdir(), "sixteen-deep-test-"));
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const key = "synthetic-deep-test-only-not-a-real-secret";
const options = convertV4MiniflareOptions({
  name: "sixteen-memory-deep", modules: true,
  scriptPath: new URL("../dist-test/alarm-harness.js", import.meta.url).pathname,
  compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
  bindings: { ...config.vars, SOURCE_COMMIT: "b".repeat(40), MEMORY_API_KEY: key },
  durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
});
const mf = new Miniflare({ ...options, resourcePersistencePath: directory });
const post = async (route, data) => {
  const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
    method: "POST", headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return { status: response.status, data: await response.json() };
};
const write = (user_id, request_id, count, begin = 0) => ({
  user_id, request_id, session_id: "synthetic-session",
  messages: Array.from({ length: count }, (_, i) => ({
    role: "user", content: `Deep sample marker${begin + i} with synthetic memory evidence.`,
  })),
});
const query = (user_id, query) => ({ user_id, query, top_k: 100 });
let checks = 0;
try {
  // No Cloudflare request: all 10k rows are in the local simulated SQLite instance.
  for (let begin = 0; begin < 10000; begin += 1000) {
    assert.equal((await post("add", write("capacity", `batch-${begin}`, 1000, begin))).status, 200);
  }
  checks++;
  assert.equal((await post("search", query("capacity", "marker9999"))).data.data.length, 1); checks++;
  assert.equal((await post("add", write("capacity", "overflow", 1, 10000))).status, 422); checks++;
  assert.equal((await post("search", query("capacity", "marker10000"))).data.data.length, 0); checks++;
  assert.equal((await post("add", write("capacity", "batch-0", 1000))).status, 200); checks++;
  const ns = await mf.getDurableObjectNamespace("MEMORY");
  const capacityStub = ns.get(ns.idFromName(JSON.stringify([config.vars.SUBMISSION_ID, "capacity"])));
  const capacityCounts = JSON.parse(await capacityStub.testStorageCounts());
  assert.deepEqual(capacityCounts, {
    chunks: 10000, writes: 10, indexed: 10000,
    alarm: capacityCounts.alarm,
  }); checks++;
  assert.equal((await post("add", write("expiry", "w", 2))).status, 200);
  const expiryStub = ns.get(ns.idFromName(JSON.stringify([config.vars.SUBMISSION_ID, "expiry"])));
  const before = JSON.parse(await expiryStub.testStorageCounts());
  assert.equal(before.chunks, 2);
  assert.ok(before.alarm > Date.now()); checks++;
  await expiryStub.testExpireAndSchedule();
  assert.equal((await post("search", query("expiry", "Deep"))).data.data.length, 0); checks++;
  let after;
  for (let i = 0; i < 30; i++) {
    after = JSON.parse(await expiryStub.testStorageCounts());
    if (after.chunks === 0 && after.writes === 0 && after.indexed === 0 && after.alarm === null) break;
    await sleep(100);
  }
  assert.deepEqual(after, { chunks: 0, writes: 0, indexed: 0, alarm: null }); checks++;
  assert.equal((await post("add", write("expiry", "w", 1))).status, 200);
  assert.equal((await post("search", query("expiry", "marker0"))).data.data.length, 1); checks++;
  const afterReadd = JSON.parse(await expiryStub.testStorageCounts());
  assert.equal(afterReadd.indexed, 1); checks++;
  assert.equal((await post("search", query("capacity", "marker9999"))).data.data.length, 1); checks++;
  console.log(`PASS: ${checks} deep local checks; 10,000-row boundary, overflow atomicity, actual alarm cleanup. No cloud/model calls.`);
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
