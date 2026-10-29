import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { convertV4MiniflareOptions, Miniflare } from "miniflare";

const config = JSON.parse(await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8"));
const directory = await mkdtemp(join(tmpdir(), "sixteen-context-"));
const key = "synthetic-context-runtime-only-no-real-key";
const mf = new Miniflare({
  ...convertV4MiniflareOptions({
    name: "context-test", modules: true,
    scriptPath: new URL("../dist/index.js", import.meta.url).pathname,
    compatibilityDate: config.compatibility_date, compatibilityFlags: config.compatibility_flags,
    bindings: { ...config.vars, MEMORY_API_KEY: key },
    durableObjects: { MEMORY: { className: "UserMemory", useSQLite: true } },
  }),
  resourcePersistencePath: directory, unsafeInspectDurableObjects: true,
});
const checks = [];
const message = (content) => ({ role: "user", content });
const post = async (route, body) => {
  const response = await mf.dispatchFetch(`http://localhost/v1/memories/${route}`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  assert.equal(response.status, 200);
  return response.json();
};
const add = (user_id, request_id, session_id, texts) => post("add", {
  user_id, request_id, session_id, messages: texts.map(message),
});
const search = async (user_id, query, top_k = 100) =>
  (await post("search", { user_id, query, top_k })).data;
const sources = (rows) => rows.map((row) => row.content.slice(row.content.indexOf("\n") + 1));
try {
  const anchor = "The parcel tariff protocol uses coefficient K.";
  const definition = "K equals three; the fixed fee is seven.";
  await add("rules", "first", "same-session", [
    ...Array.from({ length: 19 }, (_, i) => `Unrelated paragraph item${i}.`), anchor,
  ]);
  await add("rules", "separate-session", "not-the-same", ["Private unrelated note."]);
  await add("rules", "second", "same-session", [definition]);
  const result = sources(await search("rules", "Calculate parcel tariff using the protocol."));
  assert.ok(result.includes(anchor) && result.includes(definition));
  assert.ok(!result.includes("Private unrelated note."));
  checks.push("rule context crosses Add boundaries but not session boundaries");

  await add("answer", "w", "s", [
    "Guide: Where is the stargazing meetup?",
    "Host: It is at the riverside pavilion.",
    "Private synthetic medical note.",
  ]);
  const answers = sources(await search("answer", "stargazing meetup"));
  assert.ok(answers.includes("Host: It is at the riverside pavilion."));
  assert.ok(!answers.includes("Private synthetic medical note."));
  checks.push("anaphoric answer inherits a previous question, not unrelated neighbours");
  assert.deepEqual(await search("answer", "unmatchedmarkerxyz"), []);
  assert.deepEqual(await search("unwritten", "Summarize rules"), []);
  checks.push("unmatched query and unwritten user remain empty");

  await add("facts", "w", "s", ["The meetup starts at noon.", "Private synthetic note."]);
  assert.deepEqual(sources(await search("facts", "meetup")), ["The meetup starts at noon."]);
  checks.push("ordinary matching does not attach arbitrary adjacent text");

  for (const layout of ["separate-session", "distant-same-session"]) {
    const user = `windows-${layout}`;
    const ruleA = "Freight tariff protocol calculation requires coefficient A.";
    const ruleB = "Freight tariff protocol requires adjustment B.";
    const ruleC = "Freight tariff protocol requires adjustment C.";
    const definitionB = "B equals five; apply it after the initial step.";
    const definitionC = "C equals seven; apply it after the second step.";
    await add(user, "a", "first", Array.from({ length: 8 }, () => ruleA));
    await add(user, "padding", "first", Array.from({ length: 40 }, (_, i) => `Neutral entry x${i}.`));
    const sessionB = layout === "separate-session" ? "second" : "first";
    await add(user, "b", sessionB, [ruleB, definitionB]);
    await add(user, "padding-b", sessionB, Array.from({ length: 40 }, (_, i) => `Other neutral entry y${i}.`));
    await add(user, "c", layout === "separate-session" ? "third" : "first", [ruleC, definitionC]);
    await add(user, "noise", "other", Array.from({ length: 125 }, (_, i) => `Freight appears in catalog ${i}.`));
    const found = sources(await search(user, "Freight tariff protocol calculation"));
    for (const original of [ruleA, ruleB, ruleC, definitionB, definitionC]) assert.ok(found.includes(original));
    checks.push(`duplicate anchors do not hide two further definitions: ${layout}`);
  }

  await add("weak-anchor", "rule", "rule", [
    "Freight tariff protocol calculation uses coefficient A.", "A equals three.",
  ]);
  await add("weak-anchor", "other", "other", [
    "Freight appears in a catalog.", "Private unrelated medical detail.",
  ]);
  const weak = sources(await search("weak-anchor", "Freight tariff protocol calculation"));
  assert.ok(weak.includes("A equals three."));
  assert.ok(!weak.includes("Private unrelated medical detail."));
  checks.push("a weak theme match does not expand an unrelated sensitive window");
  const weakStorage = await mf.unsafeGetDurableObjectStorage("context-test", "UserMemory", {
    name: JSON.stringify([config.vars.SUBMISSION_ID, "weak-anchor"]),
  });
  await weakStorage.exec("UPDATE chunks SET expires=0 WHERE request_id='rule'");
  const expiredRule = sources(await search("weak-anchor", "Freight tariff protocol calculation"));
  assert.deepEqual(expiredRule, ["Freight appears in a catalog."]);
  checks.push("an expired rule cannot seed a context window");

  const storage = await mf.unsafeGetDurableObjectStorage("context-test", "UserMemory", {
    name: JSON.stringify([config.vars.SUBMISSION_ID, "answer"]),
  });
  await storage.exec("UPDATE chunks SET expires=0");
  await storage.exec("UPDATE writes SET expires=0");
  assert.deepEqual(await search("answer", "stargazing meetup"), []);
  assert.deepEqual(await search("answer", "Summarize history"), []);
  checks.push("expired context-index candidates cannot be returned");

  const rulesStorage = await mf.unsafeGetDurableObjectStorage("context-test", "UserMemory", {
    name: JSON.stringify([config.vars.SUBMISSION_ID, "rules"]),
  });
  await rulesStorage.exec("UPDATE chunks SET expires=0 WHERE request_id='second'");
  assert.ok(!sources(await search("rules", "Calculate parcel tariff using the protocol.")).includes(definition));
  checks.push("session expansion excludes an expired neighbouring request");
  await rulesStorage.exec("UPDATE meta SET value=replace(value,'\"schema\":3','\"schema\":2') WHERE key='config'");
  const response = await mf.dispatchFetch("http://localhost/v1/memories/search", {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: "rules", query: "parcel", top_k: 100 }),
  });
  assert.equal(response.status, 409);
  checks.push("old representation fingerprints fail closed");
  console.log(JSON.stringify({ status: "passed", checks, cloud_requests: 0, model_calls: 0 }, null, 2));
} finally {
  await mf.dispose();
  await rm(directory, { recursive: true, force: true });
}
