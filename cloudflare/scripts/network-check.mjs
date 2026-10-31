// No model keys, no authenticated traffic, no evaluation jobs and no deployments.
import { lookup } from "node:dns/promises";
import { writeFile } from "node:fs/promises";
import { MemoryTransport, checkHealth } from "./memory-transport.mjs";

const targets = [
  { origin: "https://sixteen-memory-entry.pages.dev", version: "0.2.5",
    commit: "a3520ccb7843156e2dd51e92e29caf0f6dafafa4" },
  { origin: "https://96c2aae8.sixteen-memory-entry.pages.dev", version: "0.3.0",
    commit: "89eaca3952052c314b4c8e87f637ecc68c7fec0e" },
];
// Optional diagnostic only: this hostname is NOT the registered AML entry.
// Checking it from an independent network helps distinguish local DNS faults.
if (process.env.INCLUDE_WORKERS_ORIGIN === "1") {
  targets.push({ origin: "https://sixteen-memory-v030.mcy23.workers.dev", version: "0.3.0",
    commit: "89eaca3952052c314b4c8e87f637ecc68c7fec0e" });
}
const report = { at: new Date().toISOString(), status: "running", targets: [],
  official_jobs: 0, authenticated_requests: 0, automatic_http_retries: 0,
  note: "Address fallback is operator-side only; passing does not certify AML connectivity." };
for (const target of targets) {
  const client = new MemoryTransport(target.origin, { keepAlive: false });
  const item = { ...target, addresses: await lookup(new URL(target.origin).hostname, { all: true }),
    requests: [] };
  try {
    for (let i = 0; i < 8; i++) {
      try {
        const r = await client.request("/health", { timeoutMs: 15000 });
        if (r.status !== 200) throw new Error("Unexpected HTTP status");
        checkHealth(JSON.parse(r.body), target);
        item.requests.push({ ...r.diagnostics, valid: true });
      } catch (error) {
        item.requests.push({ ...(error.diagnostics ?? {}), valid: false,
          error: error.code ?? error.name });
      }
    }
    for (const route of ["add", "search"]) {
      try {
        const r = await client.request("/v1/memories/" + route, { method: "POST", body: {} });
        item.requests.push({ ...r.diagnostics,
          valid: r.status === 401 && JSON.parse(r.body).detail === "Invalid credentials" });
      } catch (error) {
        item.requests.push({ ...(error.diagnostics ?? {}), valid: false, error: error.code ?? error.name });
      }
    }
  } finally { client.close(); }
  report.targets.push(item);
}
report.status = report.targets.every((t) => t.requests.every((r) => r.valid)) ? "passed" : "failed";
if (process.env.NETWORK_REPORT) {
  await writeFile(process.env.NETWORK_REPORT, JSON.stringify(report, null, 2), { flag: "wx" });
}
console.log(JSON.stringify(report, null, 2));
if (report.status !== "passed") process.exitCode = 1;
