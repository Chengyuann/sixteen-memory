import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const directory = await mkdtemp(join(tmpdir(), "sixteen-long-query-"));
const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const key = "synthetic-local-long-query-no-real-secret";
const mf = new Miniflare({
  ...convertV4MiniflareOptions({
    name: "sixteen-long-query", modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
    bindings: { ...config.vars, MEMORY_API_KEY: key },
    durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
  }),
  resourcePersistencePath: directory,
});
const report = { status: "running", corpus_rows: 10000, cloud_requests: 0, model_calls: 0, cases: [] };
const post = async (route, body) => {
  const started = performance.now();
  const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  assert.equal(response.status, 200, JSON.stringify(data));
  return { data, ms: Math.round((performance.now() - started) * 100) / 100 };
};
const search = (query, options, user_id = "long-query") => post("search", {
  user_id, query, top_k: 100, ...(options ? { options } : {}),
});
const generatedTerms = (count) => Array.from({ length: count }, (_, i) => `absent${i}`).join(" ");

try {
  for (let offset = 0; offset < 10000; offset += 1000) {
    await post("add", {
      user_id: "long-query", request_id: `batch${offset}`, session_id: "synthetic",
      messages: Array.from({ length: 1000 }, (_, i) => ({
        role: "user",
        content: `Synthetic marker${offset + i} shared evidence. 北京 ${
          offset + i === 9999 ? "needle" : "ordinary"
        }`,
      })),
    });
  }
  const baseline = (await search("needle")).data;
  assert.equal(baseline.data.length, 1);
  assert.ok(baseline.data[0].content.includes("marker9999"));
  const cjk = Array.from({ length: 2000 }, (_, i) => String.fromCodePoint(0x6000 + i)).join("");
  const longQuestion = generatedTerms(3000) + " needle";
  const choices = Array.from({ length: 100 }, (_, i) =>
    Array.from({ length: 750 }, (_, j) => `opt${i}w${j}`).join(" ") +
    (i === 99 ? " needle" : ""));
  assert.ok(longQuestion.length <= 32000);
  assert.ok(choices.every((x) => x.length <= 8000));
  const cases = [
    { name: "513 terms, relevant term last", query: generatedTerms(512) + " needle" },
    { name: "3001 terms, relevant term last", query: longQuestion },
    { name: "2001 CJK/ASCII terms", query: cjk + " needle" },
    { name: "duplicate words", query: "needle ".repeat(4000) },
    { name: "all 100 long options, relevant term in last option", query: "absent", options: choices },
  ];
  for (const c of cases) {
    const { data, ms } = await search(c.query, c.options);
    assert.deepEqual(data, baseline, c.name);
    assert.ok(ms < 20000, `${c.name} exceeded the local test budget`);
    report.cases.push({
      name: c.name, query_characters: c.query.length,
      option_characters: c.options?.reduce((n, s) => n + s.length, 0) ?? 0,
      result_count: data.data.length, ms,
    });
  }
  assert.deepEqual((await search(longQuestion, undefined, "unwritten")).data, { data: [] });
  assert.deepEqual((await search(generatedTerms(3000))).data, { data: [] });
  const common = (await search("shared")).data;
  assert.equal(common.data.length, 100);
  const concurrent = await Promise.all(Array.from({ length: 16 }, () =>
    search(generatedTerms(1999) + " needle")));
  assert.ok(concurrent.every((r) => JSON.stringify(r.data) === JSON.stringify(baseline)));
  assert.deepEqual((await search("shared")).data, common);
  report.concurrent_searches = 16;
  report.concurrent_max_ms = Math.max(...concurrent.map((r) => r.ms));
  report.short_query_order_preserved = true;
  report.unwritten_user_isolated = true;
  report.status = "passed";
  console.log(JSON.stringify(report, null, 2));
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
