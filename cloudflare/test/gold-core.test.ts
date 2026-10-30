import assert from "node:assert/strict";
import { test } from "node:test";
import { settings, MAX_CAPACITY, ServiceError, VERSION } from "../candidates/GOLD/core";
import { settings as original, fingerprint } from "../candidates/BASE/core";

const vars = {
  MEMORY_MODE: "lexical", ENABLE_FACTS: "false",
  EMBEDDING_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  EMBEDDING_MODEL: "text-embedding-v4", EMBEDDING_DIMENSIONS: "64",
  LLM_BASE_URL: "https://api.openai.com/v1", LLM_MODEL: "gpt-4o-mini",
  CHUNK_CHARS: "1200", MAX_CHUNKS_PER_USER: "10000",
  RETENTION_DAYS: "7", SUBMISSION_ID: "synthetic-gold",
};

test("GOLD changes only explicit version and supported capacity, not representation", () => {
  assert.equal(VERSION, "0.3.0");
  assert.deepEqual(settings(vars), original(vars));
  const bigger = settings({ ...vars, MAX_CHUNKS_PER_USER: String(MAX_CAPACITY) });
  assert.equal(bigger.maxChunks, 100000);
  assert.equal(fingerprint(bigger), fingerprint(original(vars)));
});

test("GOLD rejects malformed or unmeasured capacity and preserves other safeguards", () => {
  for (const value of ["", "100001", "0", "-1", "1.2", "NaN", "1e5", "9007199254740992"]) {
    assert.throws(() => settings({ ...vars, MAX_CHUNKS_PER_USER: value }), ServiceError);
  }
  assert.throws(() => settings({ ...vars, RETENTION_DAYS: "31" }), ServiceError);
  assert.throws(() => settings({ ...vars, MEMORY_MODE: "hybrid" }), ServiceError);
  assert.throws(() => settings({ ...vars, ENABLE_FACTS: "unknown" }), ServiceError);
});
