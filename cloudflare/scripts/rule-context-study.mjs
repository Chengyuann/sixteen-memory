import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const root = resolve(import.meta.dirname, "..");
const output = resolve(process.env.STUDY_OUTPUT ?? "../../../outputs/v026-analysis-20261029");
const baseline = "a3520ccb7843156e2dd51e92e29caf0f6dafafa4";
const config = JSON.parse(await readFile(join(root, "wrangler.jsonc"), "utf8"));
await mkdir(output, { recursive: true });
const key = "offline-rule-context-synthetic-test-key";
const cases = [];
const batch = (request_id, session_id, texts) => ({ request_id, session_id,
  messages: texts.map((content) => ({ role: "user", content })) });
for (let i = 0; i < 12; i++) {
  const theme = `Freight${i}`;
  const first = `${theme} tariff protocol calculation requires coefficient A${i}.`;
  const second = `${theme} tariff protocol requires adjustment B${i}.`;
  const definition = `B${i} equals ${i + 5}; apply it after the initial step.`;
  const concentrated = Array.from({ length: 8 }, () => first);
  const padding = Array.from({ length: 40 }, (_, j) => `Unrelated neutral entry x${j}.`);
  const noise = Array.from({ length: 125 }, (_, j) => `${theme} appears in the index of catalog ${j}.`);
  for (const layout of ["separate", "distant"]) {
    const firstSession = "rules-a";
    const secondSession = layout === "distant" ? firstSession : "rules-b";
    cases.push({ name: `${layout}-${i}`, group: layout, query: `${theme} tariff protocol calculation`,
      batches: [
        batch("first", firstSession, concentrated),
        batch("padding", firstSession, padding),
        batch("second", secondSession, [second, definition]),
        batch("noise", "unrelated", noise),
        batch("private", "private", [`Private secret hidden${i}.`]),
      ], required: [first, second, definition], forbidden: [`Private secret hidden${i}.`],
    });
  }
  cases.push({
    name: `ordinary-${i}`, group: "ordinary", query: `Freight${i} location`,
    batches: [batch("a", "s", [`Freight${i} location is the riverside depot.`, `Private hidden${i}.`])],
    required: [`Freight${i} location is the riverside depot.`], forbidden: [`Private hidden${i}.`],
  });
  cases.push({
    name: `weak-anchor-${i}`, group: "weak-anchor", query: `${theme} tariff protocol calculation`,
    batches: [
      batch("first", "rules", [first, `A${i} equals three.`]),
      batch("index", "unrelated", [
        `${theme} appears in a catalog.`,
        `Private unrelated medical detail hidden${i}.`,
      ]),
    ],
    required: [first, `A${i} equals three.`], forbidden: [`Private unrelated medical detail hidden${i}.`],
  });
}
cases.push({ name: "no-rule-match", group: "empty", query: "unmatchedxyz protocol",
  batches: [batch("a", "s", ["Private medical synthetic content."])], required: [],
  forbidden: ["Private medical synthetic content."] });
async function bundle(name) {
  const dir = join(root, "dist-rule-study", name);
  await mkdir(dir, { recursive: true });
  for (const file of ["index.ts", "core.ts", "models.ts"]) {
    const text = name === "baseline"
      ? execFileSync("git", ["show", `${baseline}:cloudflare/src/${file}`], { cwd: root, encoding: "utf8" })
      : await readFile(join(root, "src", file), "utf8");
    await writeFile(join(dir, file), text);
  }
  await build({ entryPoints: [join(dir, "index.ts")], outfile: join(dir, "index.js"), bundle: true,
    format: "esm", target: "es2022", platform: "neutral", conditions: ["workerd", "worker", "browser"],
    external: ["cloudflare:workers"] });
  return join(dir, "index.js");
}
async function evaluate(name) {
  const dir = await mkdtemp(join(tmpdir(), "sixteen-rule-study-"));
  const mf = new Miniflare({ ...convertV4MiniflareOptions({
    name: "rules", modules: true, scriptPath: await bundle(name),
    compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
    bindings: { ...config.vars, MEMORY_API_KEY: key },
    durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
  }), resourcePersistencePath: dir });
  const post = async (route, body) => {
    const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 200);
    return response.json();
  };
  const rows = [];
  try {
    for (const c of cases) {
      for (const b of c.batches) await post("add", { user_id: c.name, ...b });
      const data = (await post("search", { user_id: c.name, query: c.query, top_k: 100 })).data;
      const sources = data.map((d) => d.content.slice(d.content.indexOf("\n") + 1));
      const found = c.required.filter((r) => sources.includes(r)).length;
      const forbidden = c.forbidden.filter((r) => sources.includes(r)).length;
      assert.ok(data.length <= 100);
      assert.ok(data.every((r, i) => i === 0 || data[i - 1].score >= r.score));
      const originals = new Set(c.batches.flatMap((b) => b.messages.map((m) => m.content)));
      assert.ok(sources.every((s) => originals.has(s)));
      rows.push({ name: c.name, group: c.group, found, required: c.required.length,
        forbidden, passed: found === c.required.length && forbidden === 0,
        ranks: c.required.map((r) => sources.indexOf(r) + 1), returned: data.length });
      assert.deepEqual(await post("search", { user_id: "unwritten", query: c.query, top_k: 100 }), { data: [] });
    }
    return { name, baseline, cases: rows, total: rows.length, passed: rows.filter((r) => r.passed).length };
  } finally { await mf.dispose(); await rm(dir, { recursive: true, force: true }); }
}
const before = await evaluate("baseline");
const after = await evaluate("covered-windows");
const result = { created_at: new Date().toISOString(), synthetic_only: true, model_calls: 0,
  before, after, regressions: before.cases.filter((r, i) => r.passed && !after.cases[i].passed).map((r) => r.name) };
await writeFile(join(output, process.env.STUDY_RESULT ?? "rule-context-study.json"), JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
console.log(JSON.stringify({ baseline: before.passed, candidate: after.passed, total: after.total, regressions: result.regressions }));
assert.ok(after.passed === after.total && after.passed > before.passed && result.regressions.length === 0);
