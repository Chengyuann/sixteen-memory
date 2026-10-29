import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createHash } from "node:crypto";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const root = resolve(import.meta.dirname, "..");
const output = resolve(process.env.STUDY_OUTPUT ?? "../../../outputs/v026-analysis-20261029");
const split = process.env.STUDY_SPLIT ?? "development";
assert.ok(["development", "heldout"].includes(split));
const variants = (process.env.STUDY_VARIANTS ?? "fixed1200,boundary1200,fixed800,boundary800").split(",");
assert.ok(variants.every((v) => /^(fixed|boundary)(800|1200)$/.test(v) || v === "windows1200"));
const baseline = "a3520ccb7843156e2dd51e92e29caf0f6dafafa4";
const config = JSON.parse(await readFile(join(root, "wrangler.jsonc"), "utf8"));
const datasetText = await readFile(process.env.STUDY_DATA ?? join(output, "study-data.json"), "utf8");
const conversations = JSON.parse(datasetText).conversations.filter((c) => c.split === split);
assert.ok(conversations.length);

// Text-only transformation of a pinned source; the production bundle is never changed here.
const boundaryFunction = `export function chunkText(text: string, size: number): { offset: number; source: string }[] {
  const chars = Array.from(text);
  const overlap = Math.min(160, Math.floor(size / 5));
  const result = [];
  let start = 0;
  while (start < chars.length) {
    let end = Math.min(start + size, chars.length);
    if (end < chars.length) {
      const minimum = start + Math.floor(size / 2);
      for (let i = end; i >= minimum; i--) {
        const previous = chars[i - 1];
        const next = chars[i] ?? "";
        if (previous === "\\n" || (/[.!?\\u3002\\uff01\\uff1f]/u.test(previous) && /\\s/u.test(next))) {
          end = i;
          break;
        }
      }
    }
    result.push({ offset: start, source: chars.slice(start, end).join("") });
    if (end === chars.length) break;
    start = end - overlap;
  }
  return result;
}
`;

async function compile(variant) {
  const directory = join(root, "dist-chunk-study", variant);
  await mkdir(directory, { recursive: true });
  for (const file of ["index.ts", "core.ts", "models.ts"]) {
    let text = variant === "windows1200" ? await readFile(join(root, "src", file), "utf8")
      : execFileSync("git", ["show", `${baseline}:cloudflare/src/${file}`], { cwd: root, encoding: "utf8" });
    if (process.env.STUDY_DEBUG === "1" && file === "index.ts") {
      text = text.replace("function failed(error: unknown): Result {",
        "function failed(error: unknown): Result { console.error(String(error));");
    }
    if (file === "core.ts" && variant.startsWith("boundary")) {
      const start = text.indexOf("export function chunkText(");
      const end = text.indexOf("export function sourceChunks(", start);
      assert.ok(start > 0 && end > start);
      text = text.slice(0, start) + boundaryFunction + "\n" + text.slice(end);
    }
    await writeFile(join(directory, file), text);
  }
  await build({ entryPoints: [join(directory, "index.ts")], outfile: join(directory, "index.js"),
    bundle: true, format: "esm", target: "es2022", platform: "neutral",
    conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"] });
  return join(directory, "index.js");
}
const mean = (a) => a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;
function coverage(intervals, length) {
  let end = 0, covered = 0;
  for (const [a, b] of intervals.sort((a, b) => a[0] - b[0])) {
    covered += Math.max(0, Math.min(length, b) - Math.max(end, a));
    end = Math.max(end, b);
  }
  return covered / length;
}
async function evaluate(variant, bundle) {
  const dir = await mkdtemp(join(tmpdir(), "sixteen-chunk-study-"));
  const key = "offline-synthetic-key-32-characters-minimum";
  const size = variant.endsWith("1200") ? "1200" : "800";
  const mf = new Miniflare({
    ...convertV4MiniflareOptions({
      name: "chunk-study", modules: true, scriptPath: bundle,
      compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
      bindings: { ...config.vars, MEMORY_API_KEY: key, CHUNK_CHARS: size },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
    }), resourcePersistencePath: dir,
  });
  const post = async (route, body) => {
    const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status !== 200) {
      assert.fail(`${route}: ${response.status} ${body.user_id} ${body.request_id ?? ""} ${await response.text()}`);
    }
    return response.json();
  };
  const results = [];
  try {
    for (const c of conversations) {
      for (const b of c.batches) await post("add", {
        user_id: c.id, request_id: b.request_id, session_id: b.session_id,
        messages: b.messages.map((m) => ({ role: m.role, content: m.content,
          ...(Number.isFinite(b.timestamp) ? { timestamp: b.timestamp } : {}) })),
      });
      for (const q of c.queries) {
        const started = performance.now();
        const data = (await post("search", { user_id: c.id, query: q.query, top_k: 100 })).data;
        const items = data.map((d) => {
          const newline = d.content.indexOf("\n");
          return { metadata: JSON.parse(d.content.slice(0, newline)), source: d.content.slice(newline + 1), chars: d.content.length };
        });
        const required = new Set(q.required.map((r) => `${r.request_id}:${r.position}`));
        const calculate = (selected) => {
          const found = new Set(selected.map((d) => `${d.metadata.request_id}:${d.metadata.message}`));
          return {
            recall: [...required].filter((x) => found.has(x)).length / required.size,
            coverage: mean(q.required.map((r) => coverage(selected.filter((d) =>
              d.metadata.request_id === r.request_id && d.metadata.message === r.position)
              .map((d) => [d.metadata.character_offset, d.metadata.character_offset + Array.from(d.source).length]),
            Array.from(r.source).length))),
          };
        };
        const metrics = {};
        for (const k of [10, 20, 100]) {
          const m = calculate(items.slice(0, k));
          metrics[`recall${k}`] = m.recall; metrics[`coverage${k}`] = m.coverage;
        }
        for (const budget of [8000, 16000, 32000]) {
          let chars = 0;
          const selected = [];
          for (const item of items) {
            if (chars + item.chars > budget) break;
            chars += item.chars; selected.push(item);
          }
          const m = calculate(selected);
          metrics[`budget${budget}`] = m.recall; metrics[`coverageBudget${budget}`] = m.coverage;
        }
        results.push({ id: q.id, category: q.category, ...metrics,
          characters: data.reduce((s, d) => s + d.content.length, 0),
          ms: performance.now() - started,
          // Only public sources, with no answer text or model-generated summaries.
          evidence_ranks: q.required.map((r) => ({
            request_id: r.request_id, position: r.position,
            ranks: items.flatMap((d, i) => d.metadata.request_id === r.request_id
              && d.metadata.message === r.position ? [i + 1] : []),
          })),
        });
      }
      console.log(JSON.stringify({ variant, split, complete: results.length, total: conversations.length }));
    }
    const metrics = Object.keys(results[0]).filter((k) => typeof results[0][k] === "number");
    const aggregate = (rows) => Object.fromEntries(metrics.map((k) => [k, mean(rows.map((r) => r[k]))]));
    return { baseline, variant, split, dataset_sha256: createHash("sha256").update(datasetText).digest("hex"),
      questions: results.length, summary: aggregate(results),
      categories: Object.fromEntries([...new Set(results.map((r) => r.category))].map((category) =>
        [category, aggregate(results.filter((r) => r.category === category))])),
      cases: results, model_calls: 0, private_benchmark_access: false };
  } finally { await mf.dispose(); await rm(dir, { recursive: true, force: true }); }
}
await mkdir(output, { recursive: true });
for (const variant of variants) {
  const result = await evaluate(variant, await compile(variant));
  await writeFile(join(output, `${split}-${variant}.json`), JSON.stringify(result, null, 2) + "\n", { flag: "wx" });
  console.log(JSON.stringify({ variant, split, summary: result.summary }));
}
