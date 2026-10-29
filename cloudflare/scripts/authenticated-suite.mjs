import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const rounded = (value) => Math.round(value * 100) / 100;

function percentiles(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  if (!sorted.length) return null;
  const at = (p) => rounded(sorted[Math.min(sorted.length - 1, Math.ceil(p * sorted.length) - 1)]);
  return { samples: sorted.length, p50_ms: at(0.5), p95_ms: at(0.95), max_ms: at(1) };
}

/**
 * Synthetic integration suite. No model calls, arbitrary destination, secret output,
 * benchmark content, or unbounded load. `send` can be real fetch or Miniflare.
 */
export async function runAuthenticatedSuite({
  send, baseUrl, key, loadRecords = 1000, maxAttempts = 180, loadQueries = 20,
}) {
  assert.ok(Number.isInteger(loadRecords) && loadRecords >= 100 && loadRecords <= 1000);
  assert.ok(Number.isInteger(maxAttempts) && maxAttempts >= 1 && maxAttempts <= 180);
  const started = Date.now();
  const prefix = "synthetic-" + randomUUID();
  const report = {
    status: "running", started_at: new Date(started).toISOString(),
    test_namespace_prefix: prefix, model_calls: 0, max_concurrency: 4,
    http_attempts: 0, checks: [], requests: [], load: null,
    note: "Synthetic integration only; no official AML score, no Full capacity guarantee.",
  };
  let lastCheck = "initialization";

  const check = async (name, callback) => {
    lastCheck = name;
    await callback();
    report.checks.push(name);
  };
  async function request(path, body, { auth = "valid", raw, method = "POST", retry = false } = {}) {
    for (let attempt = 0; attempt < 3; attempt++) {
      if (++report.http_attempts > maxAttempts) throw new Error("Request budget exhausted");
      const headers = { "Content-Type": "application/json" };
      if (auth !== "none") headers.Authorization = `Bearer ${auth === "valid" ? key : "invalid-test-key"}`;
      const before = performance.now();
      const response = await send(new URL(path, baseUrl), {
        method, headers, redirect: "error", signal: AbortSignal.timeout(30_000),
        ...(method === "GET" ? {} : { body: raw ?? JSON.stringify(body) }),
      });
      const text = await response.text();
      // Never print request headers or response bodies. Assertions only summarize.
      let data;
      try { data = JSON.parse(text); } catch { data = null; }
      const ms = rounded(performance.now() - before);
      report.requests.push({ path, status: response.status, ms, bytes: text.length });
      if (response.status === 429 && retry && attempt < 2) {
        const seconds = Number(response.headers.get("Retry-After"));
        assert.ok(Number.isFinite(seconds) && seconds >= 0 && seconds <= 5);
        await sleep(Math.max(100, seconds * 1000));
        continue;
      }
      return { status: response.status, data, ms, headers: response.headers };
    }
    throw new Error("Retry bound exhausted");
  }
  const writeBody = (scope, id, content, timestamp = 1704067200000) => ({
    request_id: id, user_id: `${prefix}:${scope}`, session_id: `${prefix}:session-${scope}`,
    messages: [{ role: "user", content, ...(timestamp === null ? {} : { timestamp }) }],
  });
  const add = (body, options) => request("/v1/memories/add", body, options);
  const search = (scope, query, top_k = 100, extra = {}) => request("/v1/memories/search", {
    user_id: `${prefix}:${scope}`, query, top_k, ...extra,
  });
  const ok = (response) => {
    assert.equal(response.status, 200);
    assert.ok(response.data && typeof response.data === "object");
    return response.data;
  };

  try {
    await check("health declares lexical mode, disabled facts and fixed source commit", async () => {
      const health = ok(await request("/health", null, { method: "GET", auth: "none" }));
      assert.equal(health.status, "ok");
      assert.equal(health.mode, "lexical");
      assert.equal(health.facts, false);
      assert.equal(health.authenticated_endpoints_configured, true);
      assert.match(health.source_commit, /^[0-9a-f]{40}$/);
      report.deployed_source_commit = health.source_commit;
      report.version = health.version;
    });
    await check("missing/wrong keys rejected on Add and Search", async () => {
      for (const route of ["add", "search"]) for (const auth of ["none", "wrong"]) {
        assert.equal((await request(`/v1/memories/${route}`, {}, { auth })).status, 401);
      }
    });
    const first = writeBody("a", "write-1", "Alice lives in Paris. Her unique marker is marigold.");
    await check("Add acknowledges exact IDs after durable write", async () => {
      assert.deepEqual(ok(await add(first)), {
        success: true, request_id: first.request_id, user_id: first.user_id, session_id: first.session_id,
      });
    });
    let initial;
    await check("immediate Search returns original evidence with timestamp", async () => {
      initial = ok(await search("a", "marigold")).data;
      assert.equal(initial.length, 1);
      assert.ok(initial[0].content.endsWith(first.messages[0].content));
      assert.equal(Date.parse(initial[0].created_at), first.messages[0].timestamp);
      assert.match(initial[0].id, /^mem_[0-9a-f]{64}$/);
    });
    await check("retries and JSON property reordering are idempotent", async () => {
      for (let i = 0; i < 2; i++) ok(await add({
        messages: first.messages, session_id: first.session_id, user_id: first.user_id, request_id: first.request_id,
      }));
      assert.deepEqual(ok(await search("a", "marigold")).data, initial);
    });
    await check("conflicting request ID returns 409 and does not overwrite", async () => {
      assert.equal((await add(writeBody("a", "write-1", "Alice lives in Rome."))).status, 409);
      assert.deepEqual(ok(await search("a", "marigold")).data, initial);
    });
    await check("same request ID in a different user is isolated", async () => {
      assert.deepEqual(ok(await search("b", "marigold")), { data: [] });
      ok(await add(writeBody("b", "write-1", "Bob lives in Rome. Marker: bluebird.")));
      assert.deepEqual(ok(await search("a", "bluebird")), { data: [] });
      assert.deepEqual(ok(await search("b", "marigold")), { data: [] });
      assert.equal(ok(await search("b", "bluebird")).data.length, 1);
    });
    await check("new session preserves old and new time-stamped statements", async () => {
      const update = writeBody("a", "write-2", "Alice now lives in Berlin.", 1704153600000);
      update.session_id = `${prefix}:another-session`;
      ok(await add(update));
      const memories = ok(await search("a", "Alice")).data;
      assert.equal(memories.length, 2);
      assert.deepEqual(memories.map((row) => Date.parse(row.created_at)).sort(),
        [1704067200000, 1704153600000]);
      assert.equal(ok(await search("a", "Alice", 1)).data.length, 1);
    });
    await check("multiple-choice options are retrieval terms, not generated answers", async () => {
      const memories = ok(await search("a", "Which destination?", 100, { options: ["Paris", "Berlin"] })).data;
      assert.equal(memories.length, 2);
      assert.ok(memories.every((row) => row.content.includes("Alice")));
    });
    await check("Chinese text, emoji and missing timestamp preserved", async () => {
      const text = "小林住在杭州，喜欢桂花茶。纪念标记：🌸。";
      ok(await add(writeBody("unicode", "w", text, null)));
      const memories = ok(await search("unicode", "杭州 桂花茶")).data;
      assert.equal(memories.length, 1);
      assert.ok(memories[0].content.endsWith(text));
      assert.equal("created_at" in memories[0], false);
    });
    await check("long source chunks reconstruct without losing Unicode", async () => {
      const text = "recallanchor 🌸 memory segment ".repeat(150);
      ok(await add(writeBody("long", "w", text)));
      const memories = ok(await search("long", "recallanchor")).data;
      assert.ok(memories.length > 1 && memories.length < 100);
      const reconstructed = [];
      for (const row of memories) {
        const split = row.content.indexOf("\n");
        const metadata = JSON.parse(row.content.slice(0, split));
        Array.from(row.content.slice(split + 1)).forEach((char, i) => {
          reconstructed[metadata.character_offset + i] = char;
        });
      }
      assert.equal(reconstructed.join(""), text);
    });
    await check("concurrent identical writes settle without duplicates", async () => {
      const payload = writeBody("concurrent", "w", "Concurrent memory with unique token amberstone.");
      const responses = await Promise.all(Array.from({ length: 4 }, () => add(payload)));
      assert.ok(responses.every((item) => item.status === 200 || item.status === 429));
      for (const item of responses) if (item.status === 429) assert.equal(item.headers.get("Retry-After"), "2");
      ok(await add(payload, { retry: true }));
      assert.equal(ok(await search("concurrent", "amberstone")).data.length, 1);
    });
    await check("four concurrent user scopes remain isolated", async () => {
      const subjects = Array.from({ length: 4 }, (_, i) => i);
      await Promise.all(subjects.map(async (i) => {
        ok(await add(writeBody(`parallel-${i}`, "shared-id", `Parallel marker violet${i}.`)));
        const result = ok(await search(`parallel-${i}`, "Parallel")).data;
        assert.equal(result.length, 1);
        assert.ok(result[0].content.endsWith(`Parallel marker violet${i}.`));
      }));
    });
    await check("invalid payloads fail without returning their contents", async () => {
      const response = await add({
        ...first, messages: [{ role: "system", content: "DO_NOT_ECHO_987" }],
      });
      assert.equal(response.status, 422);
      assert.ok(!JSON.stringify(response.data).includes("DO_NOT_ECHO_987"));
      for (const top_k of [0, 101, "2"]) assert.equal((await search("a", "Alice", top_k)).status, 422);
      assert.equal((await search("a", " ")).status, 422);
      assert.equal((await add({ ...first, unexpected: "unknown" })).status, 422);
      assert.equal((await request("/v1/memories/add", null, { raw: "not-json" })).status, 400);
    });
    await check("long questions and options remain searchable without lexical-term rejection", async () => {
      const query = ["marigold", ...Array.from({ length: 1999 }, (_, i) => `term${i}`)].join(" ");
      const options = [Array.from({ length: 512 }, (_, i) => `option${i}`).join(" ")];
      const memories = ok(await search("a", query, 100, { options })).data;
      assert.equal(memories.length, 1);
      assert.ok(memories[0].content.endsWith(first.messages[0].content));
      const cjk = "杭州" + Array.from({ length: 513 }, (_, i) =>
        String.fromCodePoint(0x4e00 + i)).join("");
      assert.equal(ok(await search("unicode", cjk)).data.length, 1);
    });
    await check("oversized bodies reject explicitly, not by truncation", async () => {
      assert.equal((await request("/v1/memories/add", null, {
        raw: "x".repeat(2 * 1024 * 1024 + 1),
      })).status, 413);
    });
    await check("FTS syntax does not escape into SQL and data remains intact", async () => {
      ok(await search("a", '" OR 1=1; DROP TABLE chunks; --'));
      assert.deepEqual(ok(await search("a", "marigold")).data, initial);
    });
    await check("bounded 1000-record synthetic capacity and latency sample", async () => {
      const addTimings = [];
      for (let begin = 0; begin < loadRecords; begin += 100) {
        const messages = Array.from({ length: Math.min(100, loadRecords - begin) }, (_, i) => {
          const index = begin + i;
          return {
            role: "user",
            timestamp: 1704067200000 + index * 1000,
            content: `Catalog item record${index} has shelf shelf${index % 17}. Synthetic inventory evidence ${index}.`,
          };
        });
        const result = await add({
          request_id: `batch-${begin}`, user_id: `${prefix}:load`,
          session_id: `${prefix}:load-session`, messages,
        }, { retry: true });
        ok(result);
        addTimings.push(result.ms);
      }
      const searchTimings = [];
      for (let i = 0; i < loadQueries; i++) {
        const index = (i * 47) % loadRecords;
        const result = await search("load", `record${index}`, 5);
        const rows = ok(result).data;
        assert.equal(rows.length, 1);
        assert.ok(rows[0].content.includes(`record${index} `));
        searchTimings.push(result.ms);
      }
      const broad = ok(await search("load", "Catalog", 100)).data;
      assert.equal(broad.length, 100);
      assert.ok(broad.every((row, index) => index === 0 || broad[index - 1].score >= row.score));
      const parallelTimings = [];
      for (let batch = 0; batch < 2; batch++) await Promise.all(Array.from({ length: 4 }, async (_, i) => {
        const result = await search("load", `record${(batch * 4 + i) % loadRecords}`, 5);
        assert.equal(ok(result).data.length, 1);
        parallelTimings.push(result.ms);
      }));
      report.load = {
        records: loadRecords, short_record_characters: "about 100",
        add_batch_size: 100, write_latency: percentiles(addTimings),
        sequential_search_latency: percentiles(searchTimings),
        concurrency_4_search_latency: percentiles(parallelTimings),
        units: "end-to-end wall time including network; not Worker CPU time",
      };
    });
    report.status = "passed";
  } catch (error) {
    report.status = "failed";
    // Do not serialize assertion actual/expected values, headers or payloads.
    report.failure = { check: lastCheck, kind: error?.name ?? "Error" };
  }
  report.duration_ms = Date.now() - started;
  report.status_counts = report.requests.reduce((counts, item) => {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
    return counts;
  }, {});
  return report;
}
