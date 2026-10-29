import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

// Hand-authored synthetic retrieval fixtures, not AML examples or benchmark data.
const cases = [];
const item = (content, timestamp) => ({
  role: "user", content, ...(timestamp === undefined ? {} : { timestamp }),
});
for (const [n, city, later] of [
  ["Alina", "Dublin", "Berlin"],
  ["Boris", "Boston", "Denver"],
  ["Celia", "Madrid", "Lisbon"],
  ["Dario", "Oxford", "Bristol"],
]) {
  cases.push({
    name: `exact fact ${n}`, group: "control", top_k: 1, query: `${n} lives`,
    batches: [[item(`${n} lives in ${city}.`)], [item(`Unrelated travel guide to ${later}.`)]],
    required: [`${n} lives in ${city}.`],
  });
  cases.push({
    name: `latest tied fact ${n}`, group: "recency", top_k: 1, query: `${n} lives`,
    batches: [[item(`${n} lives in ${city}.`, 1704067200000)],
      [item(`${n} lives in ${later}.`, 1704153600000)]],
    required: [`${n} lives in ${later}.`],
  });
  cases.push({
    name: `conflicting facts preserved ${n}`, group: "control", top_k: 2, query: `${n} lives`,
    batches: [[item(`${n} lives in ${city}.`, 1704067200000)],
      [item(`${n} lives in ${later}.`, 1704153600000)]],
    required: [`${n} lives in ${city}.`, `${n} lives in ${later}.`],
  });
  cases.push({
    name: `choice distractors ${n}`, group: "query_priority", top_k: 1,
    query: `${n} residence`, options: [city, later, "beach skiing mountains garden"],
    batches: [[item(`${n} residence is ${city}.`)], ...Array.from({ length: 10 }, (_, i) =>
      [item(`${later} beach skiing mountains garden excursion ${i}.`)])],
    required: [`${n} residence is ${city}.`],
  });
}
for (const [name, query, source] of [
  ["repair", "repair", "Technicians repaired the pump yesterday."],
  ["organize", "organize", "The archive was organized by subject."],
  ["connect", "connect", "The sensor is connected to the controller."],
  ["schedule", "schedule", "The maintenance was scheduled for Monday."],
]) {
  cases.push({
    name: `word form ${name}`, group: "morphology", query, top_k: 1,
    batches: [[item(source)], [item("Unrelated records about a film festival.")]],
    required: [source],
  });
}
for (const [name, anchor, definition] of [
  ["tariff", "The tariff rule uses the multiplier K.", "K equals three; add a fixed fee of seven."],
  ["dispatch", "The dispatch workflow ends with branch Z.", "Z sends fragile parcels by rail."],
  ["scoring", "The scoring algorithm depends on coefficient P.", "P is four unless the input is negative."],
  ["inspection", "The inspection procedure refers to condition X.", "X requires both switches to remain open."],
]) {
  cases.push({
    name: `rule context ${name}`, group: "context", query: `${name} rule procedure`,
    top_k: 3, batches: [[item(anchor), item(definition)], [item("Unrelated weekend diary.")]],
    required: [anchor, definition],
  });
}
for (const [name, query] of [
  ["summary", "Summarize my history."],
  ["overview", "Give an overview."],
  ["trajectory", "Describe the trajectory."],
  ["Chinese overview", "请概述变化趋势。"],
]) {
  const history = Array.from({ length: 18 }, (_, i) =>
    item(`Milestone m${i}: phase p${i} completed.`, 1704067200000 + i * 86400000));
  cases.push({
    name: `timeline ${name}`, group: "overview", query, top_k: 3,
    batches: [history], required: [history[0].content, history[9].content, history[17].content],
  });
}
cases.push(
  {
    name: "Chinese exact fact", group: "control", query: "杭州 桂花茶", top_k: 1,
    batches: [[item("小林住在杭州，喜欢桂花茶。")], [item("小周住在成都，喜欢咖啡。")]],
    required: ["小林住在杭州，喜欢桂花茶。"],
  },
  {
    name: "ordinary lookup excludes unrelated sensitive context", group: "control",
    query: "meetup", top_k: 100,
    batches: [[item("The meetup starts at noon."), item("Private medical note: sensitiveexample.")]],
    required: ["The meetup starts at noon."], forbidden: ["sensitiveexample"],
  },
  {
    name: "no lexical evidence means empty", group: "control", query: "unmatchedxyz", top_k: 100,
    batches: [[item("Private synthetic financial note.")]], required: [], empty: true,
  },
  {
    name: "historical date can outrank newer facts", group: "control",
    query: "address 2023", top_k: 1,
    batches: [[item("The address in 2023 was Cedar.", 1672531200000)],
      [item("The address in 2024 was Birch.", 1704067200000)]],
    required: ["The address in 2023 was Cedar."],
  },
  {
    name: "rule definition survives many weak lexical matches", group: "context",
    query: "marigold tariff rule calculation", top_k: 10,
    batches: [
      [item("Marigold tariff rule calculation uses factor K."),
        item("K equals five; add a fixed fee of nineteen.")],
      ...Array.from({ length: 120 }, (_, i) =>
        [item(`Marigold appears in unrelated botanical notes from edition ${i}.`)]),
    ],
    required: ["Marigold tariff rule calculation uses factor K.",
      "K equals five; add a fixed fee of nineteen."],
  },
  {
    name: "question recall survives the combined-query candidate cap", group: "query_priority",
    query: "Alina residence", options: ["Berlin Denver Oxford hiking coast garden"],
    top_k: 1,
    batches: [
      [item("Alina residence is Dublin.")],
      Array.from({ length: 400 }, (_, i) =>
        item(`Berlin Denver Oxford hiking coast garden. Excursion ${i}.`)),
    ],
    required: ["Alina residence is Dublin."],
  },
  {
    name: "no timestamp uses write order only for equal lexical scores", group: "recency",
    query: "Vega status", top_k: 1,
    batches: [[item("Vega status was blue.")], [item("Vega status was gold.")]],
    required: ["Vega status was gold."],
  },
);

async function evaluate(bundle, label) {
  const directory = await mkdtemp(join(tmpdir(), "sixteen-retrieval-quality-"));
  const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
  const key = "synthetic-local-only-no-real-credentials";
  const mf = new Miniflare({
    ...convertV4MiniflareOptions({
      name: "retrieval-quality", modules: true, scriptPath: bundle,
      compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
      bindings: { ...config.vars, MEMORY_API_KEY: key },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
    }),
    resourcePersistencePath: directory,
  });
  const results = [];
  async function post(route, body) {
    const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    assert.equal(response.status, 200, JSON.stringify(data));
    return data;
  }
  try {
    for (let i = 0; i < cases.length; i++) {
      const c = cases[i];
      const user_id = `quality-${i}`;
      for (let j = 0; j < c.batches.length; j++) {
        await post("add", {
          user_id, request_id: `write-${j}`, session_id: `session-${j}`, messages: c.batches[j],
        });
      }
      const started = performance.now();
      const data = (await post("search", {
        user_id, query: c.query, top_k: c.top_k, ...(c.options ? { options: c.options } : {}),
      })).data;
      const sources = data.map((row) => row.content.slice(row.content.indexOf("\n") + 1));
      assert.ok(data.length <= c.top_k);
      assert.ok(data.every((row, j) => Number.isFinite(row.score) && (j === 0 || data[j - 1].score >= row.score)));
      const expectedSources = new Set(c.batches.flat().map((message) => message.content));
      assert.ok(sources.every((source) => expectedSources.has(source)), "Only original evidence may be returned");
      const retrieved = c.required.filter((source) => sources.includes(source)).length;
      const safe = !(c.forbidden ?? []).some((term) => sources.some((source) => source.includes(term)));
      const passed = retrieved === c.required.length && safe && (!c.empty || sources.length === 0);
      results.push({
        name: c.name, group: c.group, passed, required_found: retrieved, required: c.required.length,
        returned: sources.length, ms: Math.round((performance.now() - started) * 100) / 100,
      });
      assert.deepEqual(await post("search", {
        user_id: "unwritten", query: c.query, top_k: c.top_k,
      }), { data: [] }, "Every retrieval path must preserve user isolation");
    }
    return { label, passed: results.filter((r) => r.passed).length, total: results.length, cases: results };
  } finally {
    await mf.dispose();
    await rm(directory, { recursive: true, force: true });
  }
}

const baseline = process.env.BASELINE_BUNDLE
  ? await evaluate(resolve(process.env.BASELINE_BUNDLE), "v0.2.3") : null;
const candidate = await evaluate(new URL("../dist/index.js", import.meta.url).pathname, "candidate");
console.log(JSON.stringify({
  synthetic_only: true, model_calls: 0, cloud_requests: 0, baseline, candidate,
  note: "Engineering regression cases, not a benchmark score estimate.",
}, null, 2));
assert.ok(candidate.cases.every((c) => c.passed), "Candidate failed a synthetic retrieval case");
if (baseline) {
  assert.ok(candidate.passed > baseline.passed, "No synthetic improvement over baseline");
  assert.ok(baseline.cases.every((c, i) => !c.passed || candidate.cases[i].passed), "Synthetic regression");
}
