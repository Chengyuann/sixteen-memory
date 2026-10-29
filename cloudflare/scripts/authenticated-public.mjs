import { appendFile, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { runAuthenticatedSuite } from "./authenticated-suite.mjs";

const baseUrl = "https://sixteen-memory-v023-entry.pages.dev";
const key = process.env.SIXTEEN_TEMP_TEST_KEY;
if (!key || !/^[A-Za-z0-9_-]{43}$/.test(key)) throw new Error("Temporary test key not configured");
const post = async (path, body) => {
  const response = await fetch(baseUrl + path, {
    method: "POST", redirect: "error", signal: AbortSignal.timeout(20_000),
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  return { response, data };
};
if (process.env.TEST_PHASE === "revoked") {
  for (const path of ["/v1/memories/add", "/v1/memories/search"]) {
    const { response } = await post(path, {});
    assert.equal(response.status, 401);
  }
  console.log("PASS: temporary test credential revoked on both protected endpoints.");
  process.exit(0);
}
if (process.env.TEST_PHASE === "persistence") {
  const prefix = process.env.TEST_NAMESPACE_PREFIX ?? "";
  assert.match(prefix, /^synthetic-[0-9a-f-]{36}$/);
  for (const [scope, query, suffix] of [
    ["a", "marigold", "Alice lives in Paris. Her unique marker is marigold."],
    ["unicode", "杭州 桂花茶", "小林住在杭州，喜欢桂花茶。纪念标记：🌸。"],
    ["load", "record999", "Catalog item record999 has shelf shelf13. Synthetic inventory evidence 999."],
  ]) {
    const { response, data } = await post("/v1/memories/search", {
      user_id: `${prefix}:${scope}`, query, top_k: 5,
    });
    assert.equal(response.status, 200);
    assert.equal(data.data.length, 1);
    assert.ok(data.data[0].content.endsWith(suffix));
  }
  console.log("PASS: prior synthetic data survived Worker/key version changes and remained isolated/searchable.");
  process.exit(0);
}
if (process.env.TEST_PHASE && process.env.TEST_PHASE !== "test") throw new Error("Invalid test phase");
const report = await runAuthenticatedSuite({ send: fetch, baseUrl, key });
const saved = JSON.stringify(report, null, 2);
if (saved.includes(key)) throw new Error("Secret found in report; output blocked");
await writeFile("authenticated-public-results.json", saved + "\n");
console.log(saved);
if (process.env.GITHUB_STEP_SUMMARY) await appendFile(
  process.env.GITHUB_STEP_SUMMARY,
  `## Synthetic public API test: ${report.status}\n\n`
  + `- Passed check groups: ${report.checks.length}\n`
  + `- HTTP attempts: ${report.http_attempts}\n`
  + `- Model calls: 0\n`
  + `- Deployed source: \`${report.deployed_source_commit ?? "unknown"}\`\n`
  + `- No official AML evaluation or performance claim.\n`,
);
if (report.status !== "passed") process.exitCode = 1;
