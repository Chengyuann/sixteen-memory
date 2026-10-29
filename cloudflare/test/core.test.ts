import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addSchema, chunkText, evidence, fingerprint, matchQuery, readJson, searchSchema,
  ServiceError, settings, sourceChunks, terms,
} from "../src/core";
import { Models } from "../src/models";

export const base = {
  MEMORY_MODE: "lexical", ENABLE_FACTS: "false",
  EMBEDDING_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  EMBEDDING_MODEL: "text-embedding-v4", EMBEDDING_DIMENSIONS: "64",
  LLM_BASE_URL: "https://api.openai.com/v1", LLM_MODEL: "gpt-4o-mini",
  CHUNK_CHARS: "1200", MAX_CHUNKS_PER_USER: "10000",
  RETENTION_DAYS: "7", SUBMISSION_ID: "test-submission",
};

const write = {
  request_id: "w1", user_id: "u1", session_id: "s1",
  messages: [{ role: "user", content: "Alice lives in Paris." }],
};

test("protocol rejects invalid roles, unknown fields and coerced integers", () => {
  assert.ok(addSchema.safeParse(write).success);
  assert.equal(addSchema.safeParse({ ...write, leakedAnswer: "a" }).success, false);
  assert.equal(addSchema.safeParse({ ...write, messages: [{ role: "system", content: "x" }] }).success, false);
  assert.equal(addSchema.safeParse({ ...write, messages: [{ role: "user", content: " " }] }).success, false);
  for (const top_k of [0, 101, "1", 1.5]) {
    assert.equal(searchSchema.safeParse({ query: "a", user_id: "u", top_k }).success, false);
  }
  assert.ok(searchSchema.safeParse({ query: "a", user_id: "u", top_k: 100, options: ["A"] }).success);
});

test("unicode chunks preserve all code points and overlap provenance", () => {
  const original = "start " + "\u{1f600}\u5317\u4eac\n".repeat(500) + " end";
  const pieces = chunkText(original, 200);
  const result: string[] = [];
  for (const part of pieces) {
    Array.from(part.source).forEach((char, index) => { result[part.offset + index] = char; });
    assert.ok(Array.from(part.source).length <= 200);
  }
  assert.equal(result.join(""), original);
  assert.ok(pieces.length > 1);
});

test("timestamps and roles preserved without fabrication", () => {
  const parsed = addSchema.parse({
    ...write, messages: [{ role: "assistant", content: "Memory.", timestamp: 1704067200000 }],
  });
  const [part] = sourceChunks(parsed, 1200);
  assert.equal(part.role, "assistant");
  assert.equal(part.timestamp, 1704067200000);
  const result = evidence("id", { ...part, session_id: "s", request_id: "r" }, 0.1);
  assert.equal(result.created_at, "2024-01-01T00:00:00.000Z");
  assert.ok(result.content.endsWith("Memory."));
  assert.equal("created_at" in evidence("id", {
    ...part, timestamp: null, session_id: "s", request_id: "r",
  }, 0.1), false);
});

test("FTS query is tokenized, quoted and supports long ASCII and CJK queries", () => {
  assert.deepEqual(terms("ABC\u5317\u4eac 2026"), ["abc", "\u5317", "\u4eac", "2026"]);
  assert.equal(matchQuery('Alice" OR 1=1;'), '"alice" OR "or" OR "1"');
  assert.equal(matchQuery("...!!!"), null);
  assert.equal(matchQuery(Array.from({ length: 2000 }, (_, i) => `t${i}`).join(" "))!
    .split(" OR ").length, 2000);
  assert.equal(matchQuery(Array.from({ length: 513 }, (_, i) =>
    String.fromCodePoint(0x4e00 + i)).join(""))!.split(" OR ").length, 513);
});

test("configuration fails closed without changing models silently", () => {
  assert.equal(settings(base).mode, "lexical");
  assert.throws(() => settings({ ...base, MEMORY_MODE: "hybrid" }), ServiceError);
  assert.throws(() => settings({ ...base, EMBEDDING_MODEL: "other" }), ServiceError);
  assert.throws(() => settings({ ...base, RETENTION_DAYS: "31" }), ServiceError);
  assert.throws(() => settings({ ...base, ENABLE_FACTS: "falsee" }), ServiceError);
  assert.throws(() => settings({ ...base, LLM_BASE_URL: "https://user:pass@example.org" }), ServiceError);
  assert.equal(fingerprint(settings(base)), fingerprint(settings({ ...base, MAX_CHUNKS_PER_USER: "12" })));
  assert.notEqual(fingerprint(settings(base)), fingerprint(settings({ ...base, CHUNK_CHARS: "1000" })));
});

test("JSON reader rejects malformed and oversized data without echoing it", async () => {
  assert.deepEqual(await readJson(new Response('{"a":1}')), { a: 1 });
  await assert.rejects(readJson(new Response("private-not-json")), /Invalid JSON/);
  await assert.rejects(readJson(new Response("xxxxxxxx"), 4), /size limit/);
});

test("lexical mode never calls a model", async () => {
  const models = new Models(settings(base), {}, async () => { throw new Error("unexpected fetch"); });
  assert.deepEqual(await models.embed(["hello"]), [null]);
  assert.equal(await models.extract("hello"), "");
});

test("embedding request batches and validates response indices", async () => {
  const sizes: number[] = [];
  const config = settings({ ...base, MEMORY_MODE: "hybrid", EMBEDDING_API_KEY: "fake" });
  const models = new Models(config, { EMBEDDING_API_KEY: "fake" }, async (_, init) => {
    const body = JSON.parse(init!.body as string);
    sizes.push(body.input.length);
    assert.equal(body.model, "text-embedding-v4");
    return Response.json({ data: body.input.map((_: string, index: number) => ({
      index, embedding: Array.from({ length: 64 }, () => 1),
    })).reverse() });
  });
  const result = await models.embed(Array.from({ length: 23 }, () => "source"));
  assert.deepEqual(sizes, [10, 10, 3]);
  assert.equal(result.length, 23);
  assert.ok(Math.abs(Math.hypot(...new Float32Array(result[0]!)) - 1) < 1e-6);
});

test("malformed vectors and upstream errors do not leak response bodies", async () => {
  const config = settings({ ...base, MEMORY_MODE: "hybrid", EMBEDDING_API_KEY: "fake" });
  const broken = new Models(config, {}, async () => Response.json({
    data: [{ index: 0, embedding: Array.from({ length: 64 }, () => 0) }],
  }));
  await assert.rejects(broken.embed(["a"]), /norm/);
  const failure = new Models(config, {}, async () => new Response("private upstream details", { status: 500 }));
  await assert.rejects(failure.embed(["a"]), (error: unknown) =>
    error instanceof ServiceError && error.status === 503 && !error.message.includes("private"));
});

test("fact extraction accepts only exact source quotes", async () => {
  const config = settings({
    ...base, MEMORY_MODE: "hybrid", ENABLE_FACTS: "true", EMBEDDING_API_KEY: "fake", LLM_API_KEY: "fake",
  });
  const model = (quote: string) => new Models(config, {}, async () => Response.json({
    choices: [{ finish_reason: "stop", message: {
      content: JSON.stringify({ facts: [{ fact: "Alice residence: Paris", quote }] }),
    } }],
  }));
  assert.equal(await model("lives in Paris").extract("Alice lives in Paris."), "Alice residence: Paris");
  await assert.rejects(model("fabricated").extract("Alice lives in Paris."), /source-linked/);
});
