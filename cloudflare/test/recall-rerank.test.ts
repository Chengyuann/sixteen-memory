import assert from "node:assert/strict";
import test from "node:test";
import { facetPreservingOrder } from "../experiments/recall-rerank";
import type { Row } from "../candidates/rerank";

function fixture(): Row[] {
  return Array.from({ length: 10 }, (_, i) => ({
    id: `d${i}`, request_id: `r${i}`, session_id: "s", position: 0,
    offset: 0, role: "user", source: "Atlas plan", score: 1 - i * 0.01,
  }));
}

test("R01 preserves new query facets in later chunks of a long message", () => {
  const rows = fixture();
  rows[5] = { ...rows[5], request_id: rows[0].request_id, source: "Atlas Dublin budget" };
  const result = facetPreservingOrder("Atlas Dublin budget", rows);
  assert.equal(result.find((r) => r.id === "d5")?.score, rows[5].score);
  const duplicate = facetPreservingOrder("Atlas", rows);
  assert.equal(duplicate.find((r) => r.id === "d5")?.score, rows[5].score * 0.75);
});

test("R01 leaves empty and stopword-only queries unchanged", () => {
  assert.deepEqual(facetPreservingOrder("Atlas", []), []);
  const rows = fixture();
  rows[5].request_id = rows[0].request_id;
  assert.deepEqual(facetPreservingOrder("what is it", rows), rows);
});

test("R01 does not merge different messages or parse opaque source IDs", () => {
  const rows = fixture();
  rows[5].request_id = "private:test:answer-never-interpret";
  const original = structuredClone(rows);
  assert.deepEqual(facetPreservingOrder("Atlas", rows), original);
});

test("R01 is deterministic, preserves sources and does not mutate input", () => {
  const rows = fixture();
  rows[5].request_id = rows[0].request_id;
  const original = structuredClone(rows);
  const first = facetPreservingOrder("Atlas", rows);
  const second = facetPreservingOrder("Atlas", rows);
  assert.deepEqual(first, second);
  assert.deepEqual(rows, original);
  assert.deepEqual(first.slice(0, 5), original.slice(0, 5));
  const source = ({ score: _score, ...row }: Row) => row;
  assert.deepEqual(first.map(source).sort((a, b) => a.id.localeCompare(b.id)),
    original.map(source).sort((a, b) => a.id.localeCompare(b.id)));
  assert.ok(first.every((row, i) => i === 0 || row.score <= first[i - 1].score));
});

test("R01 bounds output at100 without admitting unseen lower-ranked content", () => {
  const rows = Array.from({ length: 140 }, (_, i) => ({
    ...fixture()[0], id: `item${i}`, request_id: `req${i}`, score: 1 / (i + 1),
  }));
  const result = facetPreservingOrder("Atlas", rows);
  assert.equal(result.length, 100);
  assert.deepEqual(result.map((r) => r.id), rows.slice(0, 100).map((r) => r.id));
});

test("R01 token handling includes CJK and astral text without rewriting sources", () => {
  const rows = fixture();
  rows[0].source = "项目价格";
  rows[5] = { ...rows[5], request_id: rows[0].request_id, source: "项目地点🌸北京" };
  const result = facetPreservingOrder("项目地点北京", rows);
  assert.equal(result.find((r) => r.id === "d5")?.source, "项目地点🌸北京");
  assert.equal(result.find((r) => r.id === "d5")?.score, rows[5].score);
});
