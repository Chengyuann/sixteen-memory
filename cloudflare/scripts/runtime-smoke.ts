import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const directory = await mkdtemp(join(tmpdir(), "sixteen-worker-test-"));
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const key = "synthetic-runtime-test-key-not-a-real-secret";
const origin = "http://localhost";

function create(extra = {}) {
  const options = convertV4MiniflareOptions({
    name: "sixteen-memory-test",
    modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: "2026-10-19",
    compatibilityFlags: ["nodejs_compat"],
    bindings: { ...config.vars, MEMORY_API_KEY: key, ...extra },
    durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
  });
  return new Miniflare({
    ...options, resourcePersistencePath: directory, unsafeInspectDurableObjects: true,
  });
}

const body = {
  request_id: "write-1", user_id: "scope-1", session_id: "session-1",
  messages: [{ role: "user", timestamp: 1704067200000, content: "Alice lives in Paris." }],
};
const query = { query: "Alice", user_id: "scope-1", top_k: 100 };

async function post(mf: Miniflare, path: string, data: unknown, authorization: boolean | string = true) {
  const response = await mf.dispatchFetch(origin + "/v1/memories/" + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authorization === false ? {} : {
        Authorization: authorization === true ? "Bearer " + key : authorization,
      }),
    },
    body: JSON.stringify(data),
  });
  return { status: response.status, body: await response.json() as Record<string, any> };
}

let mf = create();
let checks = 0;
try {
  const health = await mf.dispatchFetch(origin + "/health");
  assert.equal(health.status, 200); checks++;
  assert.equal((await post(mf, "add", body, false)).status, 401); checks++;
  assert.deepEqual(await post(mf, "add", body), {
    status: 200, body: {
      success: true, request_id: "write-1", user_id: "scope-1", session_id: "session-1",
    },
  }); checks++;
  const result = await post(mf, "search", query);
  assert.equal(result.status, 200);
  assert.equal(result.body.data.length, 1);
  assert.ok(result.body.data[0].content.endsWith("Alice lives in Paris."));
  assert.equal(result.body.data[0].created_at, "2024-01-01T00:00:00.000Z"); checks++;
  for (const authorization of [
    "bearer " + key, "BEARER " + key, "bEaReR " + key, "Bearer   " + key,
  ]) {
    const added = await post(mf, "add", body, authorization);
    assert.equal(added.status, 200, "Add must accept the case-insensitive Bearer scheme");
    assert.equal(added.body.request_id, body.request_id);
    assert.deepEqual(await post(mf, "search", query, authorization), result);
    checks++;
  }
  for (const authorization of [
    "", "Bearer", "Bearer ", "Token " + key, "Basic " + key, "Bearer\t" + key,
    "Bearer wrong", "Bearer " + key.toUpperCase(), `Bearer ${key}, Bearer ${key}`,
  ]) {
    for (const [path, data] of [["add", body], ["search", query]] as const) {
      assert.deepEqual(await post(mf, path, data, authorization), {
        status: 401, body: { detail: "Invalid credentials" },
      });
    }
    checks++;
  }
  assert.equal((await post(mf, "add", body)).status, 200);
  assert.deepEqual((await post(mf, "search", query)).body, result.body); checks++;
  assert.equal((await post(mf, "add", {
    ...body, messages: [{ role: "user", content: "Alice moved to Rome." }],
  })).status, 409); checks++;
  assert.deepEqual((await post(mf, "search", { ...query, user_id: "other" })).body, { data: [] }); checks++;
  assert.equal((await post(mf, "add", {
    ...body, user_id: "other", messages: [{ role: "user", content: "Bob lives in Rome." }],
  })).status, 200);
  assert.deepEqual((await post(mf, "search", { ...query, query: "Rome" })).body, { data: [] }); checks++;
  assert.equal((await post(mf, "search", { ...query, top_k: 101 })).status, 422); checks++;
  assert.equal((await post(mf, "search", {
    ...query, query: "Which city?", options: ["Paris", "Rome"],
  })).body.data.length, 1); checks++;
  assert.equal((await post(mf, "add", {
    ...body, request_id: "w2", messages: [{ role: "user", content: "Alice now lives in Berlin.", timestamp: 1704153600000 }],
  })).status, 200);
  assert.equal((await post(mf, "search", query)).body.data.length, 2); checks++;
  const malicious = await post(mf, "search", { ...query, query: '" OR 1=1; --' });
  assert.equal(malicious.status, 200); checks++;
  const invalid = await post(mf, "add", {
    ...body, messages: [{ role: "system", content: "private-invalid-content" }],
  });
  assert.equal(invalid.status, 422);
  assert.ok(!JSON.stringify(invalid.body).includes("private-invalid-content")); checks++;
  const repeats = await Promise.all(Array.from({ length: 5 }, () => post(mf, "add", body)));
  assert.ok(repeats.every((item) => [200, 429].includes(item.status)));
  assert.equal((await post(mf, "search", query)).body.data.length, 2); checks++;
  const persisted = await mf.unsafeGetDurableObjectStorage("sixteen-memory-test", "UserMemory", {
    name: JSON.stringify([config.vars.SUBMISSION_ID, body.user_id]),
  });
  await persisted.exec("UPDATE chunks SET expires=0");
  await persisted.exec("UPDATE writes SET expires=0");
  assert.deepEqual((await post(mf, "search", query)).body, { data: [] }); checks++;
  assert.equal((await post(mf, "add", body)).status, 200);
  assert.equal((await post(mf, "search", query)).body.data.length, 1);
  const indexRows = await persisted.exec<{ n: number }>("SELECT COUNT(*) AS n FROM search_index");
  assert.equal(indexRows[0].n, 2); checks++;
  await mf.dispose();
  mf = create();
  assert.equal((await post(mf, "search", query)).body.data.length, 1); checks++;
  await mf.dispose();
  mf = create({ CHUNK_CHARS: "1000" });
  assert.equal((await post(mf, "search", query)).status, 409); checks++;
  await mf.dispose();
  mf = create({ MAX_CHUNKS_PER_USER: "1" });
  assert.equal((await post(mf, "add", {
    ...body, user_id: "capacity", messages: [{ role: "user", content: "large ".repeat(500) }],
  })).status, 422);
  assert.deepEqual((await post(mf, "search", { ...query, user_id: "capacity" })).body, { data: [] }); checks++;
  const oversized = await mf.dispatchFetch(origin + "/v1/memories/add", {
    method: "POST", headers: { Authorization: "Bearer " + key }, body: "x".repeat(2 * 1024 * 1024 + 1),
  });
  assert.equal(oversized.status, 413); checks++;
  console.log(`PASS: ${checks} real Workers/SQLite checks, restart durability, no provider calls.`);
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
