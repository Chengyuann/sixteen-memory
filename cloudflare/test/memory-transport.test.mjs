import assert from "node:assert/strict";
import { test } from "node:test";
import https from "node:https";
import { EventEmitter } from "node:events";
import { checkHealth, makePublicLookup, memoryOrigin, memoryRequest, MemoryTransport, publicAddress }
  from "../scripts/memory-transport.mjs";

test("valid GOLD health is accepted, unlike the old0.2-only checker", () => {
  for (const version of ["0.2.5", "0.3.0"]) {
    checkHealth({ status: "ok", version, source_commit: "a".repeat(40),
      mode: "lexical", facts: false, authenticated_endpoints_configured: true },
    { version, commit: "a".repeat(40) });
  }
});
test("pinned health rejects a wrong backend or commit", () => {
  const h = { status: "ok", version: "0.3.0", source_commit: "a".repeat(40),
    mode: "lexical", facts: false, authenticated_endpoints_configured: true };
  assert.throws(() => checkHealth(h, { version: "0.2.5" }));
  assert.throws(() => checkHealth(h, { commit: "b".repeat(40) }));
  assert.throws(() => checkHealth({ ...h, authenticated_endpoints_configured: false }));
});
test("operator connector cannot contact an arbitrary host or official-submit API", () => {
  for (const origin of ["http://sixteen-memory-entry.pages.dev", "https://evil.test",
    "https://sixteen-memory-entry.pages.dev.evil.test", "https://user:secret@sixteen-memory-entry.pages.dev",
    "https://sixteen-memory-entry.pages.dev:444", "https://sixteen-memory-entry.pages.dev/path",
    "https://sixteen-memory-entry.pages.dev?secret=x"]) assert.throws(() => memoryOrigin(origin));
  assert.throws(() => memoryRequest("/api/v1/evaluations", { method: "POST", body: {} }));
  assert.throws(() => memoryRequest("/v1/memories/add", { method: "GET" }));
  assert.throws(() => memoryRequest("/health", { method: "POST" }));
  assert.throws(() => memoryRequest("/v1/memories/add", { method: "POST", body: "x".repeat(2 ** 21) }));
});
test("no local/private DNS targets are accepted", () => {
  for (const ip of ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1",
    "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "::ffff:127.0.0.1",
    "fc00::1", "fe80::1", "ff02::1"]) assert.equal(publicAddress(ip), false, ip);
  for (const ip of ["172.66.44.173", "2606:4700:310c::ac42:2cad"]) assert.equal(publicAddress(ip), true);
});
test("DNS adapter preserves multiple public families and filters private entries", async () => {
  const fake = (host, options, callback) => callback(null, [
    { address: "10.0.0.1", family: 4 },
    { address: "172.66.44.173", family: 4 },
    { address: "2606:4700:310c::ac42:2cad", family: 6 },
  ]);
  const all = await new Promise((resolve, reject) =>
    makePublicLookup(fake)("owned.test", { all: true },
      (error, result) => error ? reject(error) : resolve(result)));
  assert.equal(all.length, 2);
  assert.deepEqual(all.map((r) => r.family), [4, 6]);
  const selected = await new Promise((resolve, reject) =>
    makePublicLookup(fake)("owned.test", { family: 6 },
      (error, address, family) => error ? reject(error) : resolve({ address, family })));
  assert.equal(selected.family, 6);
});
test("connector constructor performs no network and rejects unsafe input", () => {
  assert.throws(() => new MemoryTransport("https://sixteen-memory-entry.pages.dev", { family: 5 }));
  assert.throws(() => new MemoryTransport("https://sixteen-memory-entry.pages.dev", { apiKey: "a\nb" }));
  const client = new MemoryTransport("https://sixteen-memory-entry.pages.dev");
  assert.throws(() => client.request("/api/v1/evaluations", { method: "POST", body: {} }));
  client.close();
});

test("a failed POST is never replayed and diagnostics contain no credential/body", async (t) => {
  let calls = 0;
  const secret = "synthetic-private-credential";
  t.mock.method(https, "request", (url, options) => {
    calls++;
    assert.equal(options.rejectUnauthorized, true);
    assert.equal(options.headers.Authorization, "Bearer " + secret);
    const request = new EventEmitter();
    request.end = () => queueMicrotask(() => {
      const error = new Error("synthetic failure after sending");
      error.code = "ECONNRESET";
      request.emit("error", error);
    });
    request.destroy = (error) => request.emit("error", error);
    return request;
  });
  const client = new MemoryTransport("https://sixteen-memory-entry.pages.dev", { apiKey: secret });
  try {
    await assert.rejects(client.request("/v1/memories/add", { method: "POST",
      body: { request_id: "w1", content: "private-synthetic-content" } }), (error) => {
      assert.equal(error.code, "ECONNRESET");
      assert.equal(error.diagnostics.http_attempts, 1);
      assert.equal(JSON.stringify(error).includes(secret), false);
      assert.equal(JSON.stringify(error).includes("private-synthetic-content"), false);
      return true;
    });
    assert.equal(calls, 1);
  } finally { client.close(); }
});

test("redirects never forward credentials to a second origin", async (t) => {
  let calls = 0;
  t.mock.method(https, "request", (url, options, callback) => {
    calls++;
    const request = new EventEmitter();
    request.end = () => queueMicrotask(() => {
      const response = new EventEmitter();
      response.statusCode = 302;
      response.headers = { location: "https://untrusted.invalid/" };
      callback(response);
      response.emit("end");
    });
    request.destroy = (error) => request.emit("error", error);
    return request;
  });
  const client = new MemoryTransport("https://sixteen-memory-entry.pages.dev",
    { apiKey: "synthetic-credential" });
  try {
    const response = await client.request("/v1/memories/search", { method: "POST", body: {} });
    assert.equal(response.status, 302);
    assert.equal(response.redirected, true);
    assert.equal(calls, 1);
  } finally { client.close(); }
});

test("certificate failures remain failures without insecure retry", async (t) => {
  let calls = 0;
  t.mock.method(https, "request", (url, options) => {
    calls++;
    assert.equal(options.rejectUnauthorized, true);
    const request = new EventEmitter();
    request.end = () => queueMicrotask(() => {
      const error = new Error("certificate not trusted");
      error.code = "CERT_HAS_EXPIRED";
      request.emit("error", error);
    });
    request.destroy = (error) => request.emit("error", error);
    return request;
  });
  const client = new MemoryTransport("https://sixteen-memory-entry.pages.dev");
  try {
    await assert.rejects(client.request("/health"), { code: "CERT_HAS_EXPIRED" });
    assert.equal(calls, 1);
  } finally { client.close(); }
});

test("total request timeout aborts once with no automatic retry", async (t) => {
  let calls = 0, aborted = 0;
  t.mock.method(https, "request", () => {
    calls++;
    const request = new EventEmitter();
    request.end = () => {};
    request.destroy = (error) => { aborted++; request.emit("error", error); };
    return request;
  });
  const client = new MemoryTransport("https://sixteen-memory-entry.pages.dev");
  try {
    await assert.rejects(client.request("/health", { timeoutMs: 100 }), { code: "EDEADLINE" });
    assert.equal(calls, 1);
    assert.equal(aborted, 1);
  } finally { client.close(); }
});
