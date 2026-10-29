import assert from "node:assert/strict";
import { test } from "node:test";
import { entityPhrases, reorder, type Row } from "../candidates/rerank";

const row = (id: string, score: number, extra: Partial<Row> = {}): Row => ({
  id, score, request_id: "r", session_id: "s", position: Number(id), offset: 0,
  role: "user", source: "Atlas venue note.", ...extra,
});
const input = Array.from({ length: 100 }, (_, i) => row(String(i), 1 / (61 + i)));

test("candidate entities exclude pronouns and preserve meaningful names", () => {
  assert.deepEqual(entityPhrases("Which grocery store did I spend the most money at?"), []);
  assert.deepEqual(entityPhrases("Where is Alice in Project Atlas?"), ["Alice", "Project Atlas"]);
});
for (const mode of ["parent", "message", "entity"] as const) {
  test(`${mode}: exact set and source preservation, stable output and no input mutation`, () => {
    const before = structuredClone(input);
    const output = reorder("Where is Atlas venue?", input, mode);
    assert.deepEqual(input, before);
    assert.deepEqual(output.map((r) => r.id).sort(), input.map((r) => r.id).sort());
    for (const r of output) assert.deepEqual({ ...r, score: 0 }, { ...input.find((x) => x.id === r.id)!, score: 0 });
    assert.deepEqual(output, reorder("Where is Atlas venue?", input, mode));
    assert.ok(output.every((r, i) => i === 0 || output[i - 1].score >= r.score));
    assert.deepEqual(reorder("Atlas", [], mode), []);
  });
}
test("parent promotion is limited to query-linked non-private prompts already retrieved", () => {
  const rows = [row("2", 1, { role: "assistant", source: "It is Dublin." }),
    row("7", .9, { request_id: "else", source: "Atlas venue?" }),
    row("0", .7, { source: "Atlas venue?" }), row("1", .6, { source: "Private Atlas password?" })];
  const output = reorder("Atlas venue", rows, "parent");
  assert.equal(output.find((r) => r.id === "1")!.score, .6);
  assert.equal(output.find((r) => r.id === "7")!.score, .9);
  const valid = rows.filter((r) => r.id !== "1");
  assert.equal(reorder("Atlas venue", valid, "parent")[1].id, "0");
});
test("message penalty does not change the first five or remove later chunks", () => {
  const rows = input.map((r, i) => ({ ...r, position: i < 8 ? 0 : i }));
  const output = reorder("Atlas", rows, "message");
  assert.deepEqual(output.slice(0, 5), rows.slice(0, 5));
  assert.equal(output.find((r) => r.id === "5")!.score, rows[5].score * .75);
  assert.equal(output.length, rows.length);
});
test("entity support activates only for full token support and never for I", () => {
  const rows = [row("0", 1, { source: "Different venue" }), row("1", .95, { source: "Project Atlas venue" })];
  assert.equal(reorder("Where is Project Atlas?", rows, "entity")[0].id, "1");
  assert.deepEqual(reorder("Where did I go?", rows, "entity"), rows);
});
