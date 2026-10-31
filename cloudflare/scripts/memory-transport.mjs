// Operator-side connector only. Never deployed to Workers; no automatic HTTP retries.
import https from "node:https";
import { lookup } from "node:dns";
import { isIP } from "node:net";
import { performance } from "node:perf_hooks";

export const OWNED_MEMORY_HOSTS = new Set([
  "sixteen-memory-entry.pages.dev",
  "96c2aae8.sixteen-memory-entry.pages.dev",
  "sixteen-memory-v030.mcy23.workers.dev",
]);

export function memoryOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || !OWNED_MEMORY_HOSTS.has(url.hostname)
      || url.username || url.password || url.port || url.pathname !== "/"
      || url.search || url.hash) throw new Error("Unapproved memory HTTPS origin");
  return url.origin;
}

export function publicAddress(address) {
  const family = isIP(address);
  if (family === 4) {
    const [a, b] = address.split(".").map(Number);
    return !(a === 0 || a === 10 || a === 127 || a >= 224
      || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31)
      || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127)
      || (a === 198 && (b === 18 || b === 19)));
  }
  // Public global unicast only. Reject mapped IPv4, loopback, ULA and link-local.
  return family === 6 && /^[23][0-9a-f]{3}:/i.test(address);
}

export function makePublicLookup(resolver = lookup) {
  return (hostname, options, callback) => {
    resolver(hostname, { all: true, verbatim: true }, (error, answers) => {
      if (error) return callback(error);
      const records = answers.filter((row) => publicAddress(row.address)
        && (!options.family || row.family === options.family));
      if (!records.length) {
        const blocked = new Error("No public address for memory endpoint");
        blocked.code = "ENOPUBLICADDRESS";
        return callback(blocked);
      }
      if (options.all) callback(null, records);
      else callback(null, records[0].address, records[0].family);
    });
  };
}

export function checkHealth(health, { version, commit } = {}) {
  if (health?.status !== "ok" || health.mode !== "lexical" || health.facts !== false
      || health.authenticated_endpoints_configured !== true
      || !/^\d+\.\d+\.\d+$/.test(health.version)
      || !/^[0-9a-f]{40}$/.test(health.source_commit)) throw new Error("Invalid memory health");
  if (version && health.version !== version) throw new Error("Unexpected memory version");
  if (commit && health.source_commit !== commit) throw new Error("Unexpected memory source commit");
}

export function memoryRequest(path, { method = "GET", body } = {}) {
  const valid = (path === "/health" && method === "GET" && body === undefined)
    || (["/v1/memories/add", "/v1/memories/search"].includes(path) && method === "POST");
  if (!valid) throw new Error("Only memory health/Add/Search operations are allowed");
  const data = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
  if (data && data.byteLength > 2 * 1024 * 1024) throw new Error("Request exceeds declared body limit");
  return { method, data };
}

export class MemoryTransportError extends Error {
  constructor(code, diagnostics) {
    super("Memory request failed: " + code);
    this.name = "MemoryTransportError";
    this.code = code;
    this.diagnostics = diagnostics;
  }
}

/** TCP family fallback happens before transmitting HTTP, not a replay of Add. */
export class MemoryTransport {
  #origin;
  #key;
  #agent;
  #family;
  constructor(origin, { apiKey, keepAlive = true, family = 0, attemptMs = 250 } = {}) {
    this.#origin = memoryOrigin(origin);
    if (![0, 4, 6].includes(family)) throw new Error("Invalid address family");
    if (!Number.isInteger(attemptMs) || attemptMs < 100 || attemptMs > 2000) {
      throw new Error("Invalid connection-attempt deadline");
    }
    if (apiKey !== undefined && (typeof apiKey !== "string" || /[\r\n]/.test(apiKey))) {
      throw new Error("Invalid credential");
    }
    this.#key = apiKey;
    this.#family = family;
    this.#agent = new https.Agent({
      keepAlive, maxSockets: 32, maxFreeSockets: 16, scheduling: "lifo",
      autoSelectFamily: family === 0, autoSelectFamilyAttemptTimeout: attemptMs,
      family, lookup: makePublicLookup(), rejectUnauthorized: true,
    });
  }
  close() { this.#agent.destroy(); }

  request(path, options = {}) {
    const { method, data } = memoryRequest(path, options);
    const timeoutMs = options.timeoutMs ?? 20_000;
    if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60_000) {
      throw new Error("Invalid total request deadline");
    }
    const headers = { Accept: "application/json" };
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = String(data.byteLength);
    }
    if (this.#key && path !== "/health") headers.Authorization = "Bearer " + this.#key;
    const start = performance.now();
    const diagnostics = {
      method, path, family_policy: this.#family || "auto", http_attempts: 1,
      connection_attempts: [], failed_addresses: [], address_timeouts: [],
      connected: false, tls_verified: false, reused_socket: false,
    };
    return new Promise((resolve, reject) => {
      let socket, done = false;
      const finish = () => {
        diagnostics.total_ms = Math.round((performance.now() - start) * 100) / 100;
        if (socket) {
          diagnostics.connected = Boolean(socket.remoteAddress) || diagnostics.connected;
          diagnostics.remote_family = socket.remoteFamily;
          diagnostics.tls_verified = socket.authorized === true || diagnostics.tls_verified;
          diagnostics.tcp_attempts = socket.autoSelectFamilyAttemptedAddresses ?? [];
        }
      };
      const request = https.request(new URL(path, this.#origin), {
        method, headers, agent: this.#agent, rejectUnauthorized: true,
      }, (response) => {
        diagnostics.status = response.statusCode;
        diagnostics.cf_ray = response.headers["cf-ray"];
        diagnostics.tls_verified = socket?.authorized === true;
        let bytes = 0;
        const chunks = [];
        response.on("data", (chunk) => {
          bytes += chunk.length;
          if (bytes > 4 * 1024 * 1024) {
            const error = new Error("Response exceeds diagnostic limit");
            error.code = "ERESPONSELIMIT";
            response.destroy(error);
            return;
          }
          chunks.push(chunk);
        });
        response.once("error", fail);
        response.once("end", () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          finish();
          resolve({ status: response.statusCode, body: Buffer.concat(chunks).toString("utf8"),
            diagnostics, redirected: response.statusCode >= 300 && response.statusCode < 400 });
        });
      });
      const fail = (error) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        finish();
        reject(new MemoryTransportError(error.code ?? "ETRANSPORT", diagnostics));
      };
      const timer = setTimeout(() => {
        const error = new Error("Total request deadline exceeded");
        error.code = "EDEADLINE";
        request.destroy(error);
      }, timeoutMs);
      request.once("socket", (value) => {
        socket = value;
        diagnostics.reused_socket = request.reusedSocket;
        if (request.reusedSocket) return;
        socket.on("connectionAttempt", (ip, port, family) => {
          diagnostics.connection_attempts.push({ ip, family, ms: Math.round(performance.now() - start) });
        });
        socket.on("connectionAttemptFailed", (ip, port, family) => {
          diagnostics.failed_addresses.push({ ip, family });
        });
        socket.on("connectionAttemptTimeout", (ip, port, family) => {
          diagnostics.address_timeouts.push({ ip, family });
        });
        socket.once("connect", () => {
          diagnostics.connected = true;
          diagnostics.tcp_ms = Math.round(performance.now() - start);
        });
        socket.once("secureConnect", () => {
          diagnostics.tls_verified = socket.authorized === true;
          diagnostics.tls_ms = Math.round(performance.now() - start);
        });
      });
      request.once("error", fail);
      request.end(data);
    });
  }
}
