import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { build } from "esbuild";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

// Experimental variants are isolated builds of the pinned baseline, never runtime switches.
const baseline = "cb0d700206e96b5ea00e280d00a4da1c3dea3a7d";
const root = new URL("../", import.meta.url).pathname;
const output = resolve(process.env.STUDY_OUTPUT ?? "../../../outputs/v025-analysis-20261029");
const run = process.env.STUDY_RUN ?? "v2";
const split = process.env.STUDY_SPLIT ?? "development";
assert.ok(["development", "heldout", "synthetic"].includes(split));
const selected = (process.env.STUDY_VARIANTS ?? "000,100,010,001,110,101,011,111").split(",");
assert.ok(selected.every((id) => /^[01][012][01]$/.test(id)));
const config = JSON.parse(await readFile(join(root, "wrangler.jsonc"), "utf8"));
const source = (file) => execFileSync("git", ["show", `${baseline}:cloudflare/src/${file}`], {
  cwd: root, encoding: "utf8",
});
const original = { index: source("index.ts"), core: source("core.ts"), models: source("models.ts") };
const stopwords = `a an the is are was were be been being do does did have has had can could would should
will shall may might must i me my mine we us our ours you your yours he him his she her hers it its
they them their theirs what which who whom whose when where why how that this these those
am as at by for from in into of on onto to with and or but if then than so because about
please tell give describe explain find know remember recall`.split(/\s+/);
const focusedHelper = `
const queryStopwords = new Set(${JSON.stringify(stopwords)});
export function focusedQuery(query: string): string {
  const words = terms(query);
  return words.filter((word) => !queryStopwords.has(word)).join(" ");
}
`;

function replaceExactly(text, from, to) {
  assert.equal(text.split(from).length, 2, `Baseline replacement must match exactly once: ${from.slice(0, 80)}`);
  return text.replace(from, to);
}

async function compile(id) {
  let index = original.index;
  let core = original.core;
  const focus = id[0] === "1";
  const contextIndex = id[1] !== "0";
  const session = id[2] === "1";
  if (focus) {
    core += focusedHelper;
    index = replaceExactly(index, "retrievalIntent, settings,", "focusedQuery, retrievalIntent, settings,");
    index = replaceExactly(index,
      'const searches = [{ text: request.query, weight: 2 }];',
      `const searches = [{ text: request.query, weight: 2 }];
      const focused = focusedQuery(request.query);
      if (focused && focused !== terms(request.query).join(" ")) searches.push({ text: focused, weight: 2 });`);
  }
  if (contextIndex) {
    const contextSource = id[1] === "1"
      ? `parts.slice(Math.max(0, i - 1), i + 2)
              .filter((other) => other !== part).map((other) => other.source).join("\\n")`
      : `contextualSource(request, part)`;
    if (id[1] === "2") {
      core += `
export function contextualSource(request: AddRequest, part: Chunk): string {
  const previous = request.messages[part.position - 1]?.content ?? "";
  const current = request.messages[part.position].content.replace(/^[^:\\n]{1,50}:\\s*/, "");
  const anaphoric = /^(?:yes|no|it|they|this|that|these|those|there|thanks|depends|varies|about|for|we|i)\\b/iu.test(current);
  if (!anaphoric || !previous.includes("?")) return "";
  return previous.slice(-1200);
}
`;
      index = replaceExactly(index, "retrievalIntent, settings,", "contextualSource, retrievalIntent, settings,");
    }
    index = replaceExactly(index,
      "fts5(source, facts, tokenize='porter unicode61')",
      "fts5(source, facts, context, tokenize='porter unicode61')");
    index = replaceExactly(index,
      '"INSERT INTO search_index(rowid,source,facts) VALUES (?,?,?)",\n            row.rowid, terms(part.source).join(" "), terms(facts[i]).join(" "));',
      `"INSERT INTO search_index(rowid,source,facts,context) VALUES (?,?,?,?)",
            row.rowid, terms(part.source).join(" "), terms(facts[i]).join(" "),
            terms(${contextSource}).join(" "));`);
    index = replaceExactly(index, "ORDER BY bm25(search_index),", "ORDER BY bm25(search_index, 1, 1, 0.25),");
  }
  if (session) {
    index = replaceExactly(index,
      "CREATE INDEX IF NOT EXISTS chunk_request ON chunks(request_id,position,offset);",
      `CREATE INDEX IF NOT EXISTS chunk_request ON chunks(request_id,position,offset);
        CREATE INDEX IF NOT EXISTS chunk_session ON chunks(session_id);`);
    index = replaceExactly(index,
      'const anchor = this.sql.exec<{ request_id: string; position: number; offset: number }>(\n            "SELECT request_id,position,offset FROM chunks WHERE id=? AND expires>?", id, Date.now(),',
      `const anchor = this.sql.exec<{ session_id: string; rowid: number }>(
            "SELECT session_id,rowid FROM chunks WHERE id=? AND expires>?", id, Date.now(),`);
    index = replaceExactly(index,
      "`SELECT id FROM chunks WHERE request_id=? AND expires>?\n             ORDER BY abs(position-?),abs(offset-?),rowid LIMIT 24`,\n            anchor.request_id, Date.now(), anchor.position, anchor.offset,",
      "`SELECT id FROM chunks WHERE session_id=? AND expires>?\n             ORDER BY abs(rowid-?),rowid LIMIT 24`,\n            anchor.session_id, Date.now(), anchor.rowid,");
  }
  const directory = join(root, "dist-ablation", id);
  await mkdir(directory, { recursive: true });
  for (const [file, text] of Object.entries({ "index.ts": index, "core.ts": core, "models.ts": original.models })) {
    await writeFile(join(directory, file), text);
  }
  await build({
    entryPoints: [join(directory, "index.ts")], outfile: join(directory, "index.js"),
    bundle: true, format: "esm", target: "es2022", platform: "neutral",
    conditions: ["workerd", "worker", "browser"], external: ["cloudflare:workers"],
  });
  return join(directory, "index.js");
}

function syntheticCases() {
  const cases = [];
  for (let i = 0; i < 12; i++) {
    const anchor = `Parcel code${i} protocol uses coefficient K${i}.`;
    const rule = `K${i} equals ${i + 3}; add a fixed fee of ${i + 7}.`;
    cases.push({
      id: `crossbatch-${i}`, split: "synthetic",
      batches: [
        { request_id: "a", session_id: "same", messages: [
          ...Array.from({ length: 19 }, (_, j) => ({ role: "user", content: `Unrelated paragraph item${j}.` })),
          { role: "user", content: anchor },
        ] },
        { request_id: "b", session_id: "same", messages: [{ role: "user", content: rule }] },
        { request_id: "noise", session_id: "different", messages: [
          { role: "user", content: `Confidential unrelated data private${i}.` },
        ] },
      ],
      queries: [{
        id: `crossbatch-${i}`, query: `Calculate parcel code${i} using the protocol.`,
        category: "cross_batch_rule", required: [{ request_id: "a", position: 19 }, { request_id: "b", position: 0 }],
        forbidden: [{ request_id: "noise", position: 0 }],
      }],
    });
    cases.push({
      id: `privacy-${i}`, split: "synthetic",
      batches: [{ request_id: "a", session_id: "same", messages: [
        { role: "user", content: `Meetup label${i} starts at noon.` },
        { role: "user", content: `Private medical code hidden${i}.` },
      ] }],
      queries: [{
        id: `privacy-${i}`, query: `meetup label${i}`, category: "minimal_disclosure",
        required: [{ request_id: "a", position: 0 }], forbidden: [{ request_id: "a", position: 1 }],
      }],
    });
  }
  return cases;
}

function summary(rows, k) {
  const suffix = String(k);
  return {
    questions: rows.length,
    recall: rows.reduce((n, r) => n + r[`recall${suffix}`], 0) / rows.length,
    all_evidence: rows.filter((r) => r[`recall${suffix}`] === 1).length / rows.length,
    forbidden_hits: rows.reduce((n, r) => n + r.forbidden_hits, 0),
  };
}

async function evaluate(id, bundle, conversations) {
  const directory = await mkdtemp(join(tmpdir(), "sixteen-ablation-"));
  const key = "synthetic-offline-only-never-a-real-secret";
  const mf = new Miniflare({
    ...convertV4MiniflareOptions({
      name: "ablation", modules: true, scriptPath: bundle,
      compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
      bindings: { ...config.vars, MEMORY_API_KEY: key },
      durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
    }),
    resourcePersistencePath: directory,
  });
  const rows = [];
  const post = async (route, body) => {
    const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
      method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 200, `${route} ${response.status}`);
    return response.json();
  };
  try {
    for (const conversation of conversations) {
      for (const batch of conversation.batches) {
        const timestamp = batch.timestamp ?? NaN;
        await post("add", {
          user_id: conversation.id, request_id: batch.request_id, session_id: batch.session_id,
          messages: batch.messages.map((message) => ({
            role: message.role, content: message.content,
            ...(Number.isFinite(timestamp) ? { timestamp } : {}),
          })),
        });
      }
      for (const q of conversation.queries) {
        // Evidence labels never enter the retrieval request or memory store.
        const started = performance.now();
        const data = (await post("search", { user_id: conversation.id, query: q.query, top_k: 100 })).data;
        const identities = data.map((row) => {
          const metadata = JSON.parse(row.content.slice(0, row.content.indexOf("\n")));
          return q.required_sessions ? metadata.session : `${metadata.request_id}:${metadata.message}`;
        });
        const required = new Set(q.required_sessions ?? q.required.map((x) => `${x.request_id}:${x.position}`));
        const forbidden = new Set((q.forbidden ?? []).map((x) => `${x.request_id}:${x.position}`));
        const metrics = Object.fromEntries([5, 10, 20, 100].map((k) => [
          `recall${k}`, [...required].filter((x) => identities.slice(0, k).includes(x)).length / required.size,
        ]));
        const first = identities.findIndex((x) => required.has(x));
        rows.push({
          id: q.id, conversation: conversation.id, category: q.category,
          ...metrics, reciprocal_rank: first < 0 ? 0 : 1 / (first + 1),
          forbidden_hits: identities.filter((x) => forbidden.has(x)).length,
          returned: data.length, characters: data.reduce((n, r) => n + r.content.length, 0),
          ms: Math.round((performance.now() - started) * 100) / 100,
        });
      }
      console.log(JSON.stringify({ variant: id, split, conversation: conversation.id, questions: conversation.queries.length }));
    }
    assert.deepEqual(await post("search", {
      user_id: "unwritten", query: "Summarize rules", top_k: 100,
    }), { data: [] });
    const categories = {};
    for (const category of new Set(rows.map((r) => r.category))) {
      const subset = rows.filter((r) => r.category === category);
      categories[category] = { at10: summary(subset, 10), at100: summary(subset, 100) };
    }
    const times = rows.map((r) => r.ms).sort((a, b) => a - b);
    return {
      variant: id, factors: { focus: id[0] === "1", context_index: Number(id[1]), session_window: id[2] === "1" },
      split, run, pinned_source: baseline, questions: rows.length,
      at5: summary(rows, 5), at10: summary(rows, 10), at20: summary(rows, 20), at100: summary(rows, 100),
      mrr: rows.reduce((n, r) => n + r.reciprocal_rank, 0) / rows.length,
      p95_ms: times[Math.ceil(times.length * 0.95) - 1],
      mean_characters: rows.reduce((n, r) => n + r.characters, 0) / rows.length,
      categories, cases: rows, model_calls: 0, network_requests: 0,
    };
  } finally {
    await mf.dispose();
    await rm(directory, { recursive: true, force: true });
  }
}

await mkdir(output, { recursive: true });
const data = split === "synthetic" ? null : JSON.parse(await readFile(
  process.env.STUDY_DATA ?? join(output, "public-retrieval-data-v2.json"), "utf8"));
const conversations = split === "synthetic" ? syntheticCases() : data.conversations.filter((c) => c.split === split);
for (const id of selected) {
  const bundle = await compile(id);
  const result = await evaluate(id, bundle, conversations);
  await writeFile(join(output, `ablation-${run}-${split}-${id}.json`), JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify({
    variant: id, split, recall10: result.at10.recall, recall100: result.at100.recall,
    mrr: result.mrr, p95_ms: result.p95_ms, forbidden_hits: result.at100.forbidden_hits,
  }));
}
