import assert from "node:assert/strict";

const base = new URL(process.env.PUBLIC_BASE_URL);
if (base.protocol !== "https:") throw new Error("Public smoke requires HTTPS");
const get = (path, init = {}) => fetch(new URL(path, base), {
  ...init, redirect: "error", signal: AbortSignal.timeout(20_000),
});
const response = await get("/health");
assert.equal(response.status, 200);
const health = await response.json();
assert.equal(health.status, "ok");
assert.match(health.version, /^0\.2\.\d+$/);
assert.equal(health.mode, "lexical");
assert.equal(health.facts, false);
assert.equal(health.authenticated_endpoints_configured, true);
assert.match(health.source_commit, /^[0-9a-f]{40}$/);
for (const route of ["/v1/memories/add", "/v1/memories/search"]) {
  const denied = await get(route, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  assert.equal(denied.status, 401);
  assert.deepEqual(await denied.json(), { detail: "Invalid credentials" });
}
assert.equal((await get("/not-a-route")).status, 404);
console.log(JSON.stringify({
  checked: base.origin, health, checks: 4,
  status: "passed", authenticated_add_search: "not checked; no secrets sent to CI",
}, null, 2));
