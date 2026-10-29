import { DurableObject } from "cloudflare:workers";
import {
  addSchema, evidence, fingerprint, matchQuery, readJson, searchSchema, ServiceError,
  retrievalIntent, settings, sha256, sourceChunks, terms, VERSION,
  type AddRequest, type Chunk, type SearchRequest, type Settings,
} from "./core";
import { Models, type ModelSecrets } from "./models";

// Binding types are generated from wrangler.jsonc; only secret types are added here.
type RuntimeEnv = Env & ModelSecrets & { MEMORY_API_KEY?: string };
type Result = { status: number; body: unknown };
type StoredChunk = Chunk & {
  id: string; session_id: string; request_id: string; embedding: ArrayBuffer | null;
};

function failed(error: unknown): Result {
  return error instanceof ServiceError
    ? { status: error.status, body: { detail: error.message } }
    : { status: 503, body: { detail: "Memory storage unavailable" } };
}

function validBearer(value: string, key: string): boolean {
  const separator = value.indexOf(" ");
  if (separator <= 0 || value.slice(0, separator).toLowerCase() !== "bearer") return false;
  let credentialStart = separator;
  while (value.charCodeAt(credentialStart) === 0x20) credentialStart++;
  if (credentialStart === value.length) return false;
  const actual = new TextEncoder().encode(value.slice(credentialStart));
  const expected = new TextEncoder().encode(key);
  return actual.byteLength === expected.byteLength && crypto.subtle.timingSafeEqual(actual, expected);
}

export class UserMemory extends DurableObject<RuntimeEnv> {
  private busy = false;
  private sql: SqlStorage;

  constructor(ctx: DurableObjectState, env: RuntimeEnv) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    ctx.blockConcurrencyWhile(async () => {
      this.sql.exec(`
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS writes (
          request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
          rowid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL,
          session_id TEXT NOT NULL, position INTEGER NOT NULL, offset INTEGER NOT NULL,
          role TEXT NOT NULL, timestamp INTEGER, source TEXT NOT NULL, facts TEXT NOT NULL,
          embedding BLOB, expires INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunk_expiry ON chunks(expires);
        CREATE INDEX IF NOT EXISTS chunk_request ON chunks(request_id,position,offset);
        CREATE INDEX IF NOT EXISTS write_expiry ON writes(expires);
        CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(source, facts, tokenize='porter unicode61');
      `);
    });
  }

  private lockConfig(userId: string, config: Settings) {
    const expected: Record<string, string> = { owner: userId, config: fingerprint(config) };
    for (const [key, value] of Object.entries(expected)) {
      const stored = this.sql.exec<{ value: string }>("SELECT value FROM meta WHERE key=?", key).toArray()[0];
      if (stored && stored.value !== value) {
        throw new ServiceError(key === "owner" ? 403 : 409,
          key === "owner" ? "Forbidden user scope" : "Index configuration changed; use a new submission ID");
      }
    }
  }

  private existing(request: AddRequest, digest: string) {
    const row = this.sql.exec<{ digest: string }>(
      "SELECT digest FROM writes WHERE request_id=? AND expires>?", request.request_id, Date.now(),
    ).toArray()[0];
    if (row && row.digest !== digest) throw new ServiceError(409, "request_id already used by a different payload");
    return !!row;
  }

  private purgeRows() {
    const now = Date.now();
    this.ctx.storage.transactionSync(() => {
      this.sql.exec("DELETE FROM search_index WHERE rowid IN (SELECT rowid FROM chunks WHERE expires<=?)", now);
      this.sql.exec("DELETE FROM chunks WHERE expires<=?", now);
      this.sql.exec("DELETE FROM writes WHERE expires<=?", now);
    });
  }

  private async schedulePurge() {
    const next = this.sql.exec<{ expiry: number | null }>(
      "SELECT MIN(expires) AS expiry FROM writes",
    ).one().expiry;
    if (next === null) await this.ctx.storage.deleteAlarm();
    else await this.ctx.storage.setAlarm(Math.max(Date.now() + 1000, next));
  }

  async alarm() {
    this.purgeRows();
    await this.schedulePurge();
  }

  async add(request: AddRequest): Promise<Result> {
    if (this.busy) return { status: 429, body: { detail: "Another write in this scope is in progress" } };
    this.busy = true;
    try {
      const config = settings(this.env);
      this.lockConfig(request.user_id, config);
      const digest = await sha256(JSON.stringify(request));
      if (this.existing(request, digest)) {
        await this.schedulePurge();
        await this.ctx.storage.sync();
        return { status: 200, body: this.ack(request) };
      }
      const parts = sourceChunks(request, config.chunkChars);
      const providerCalls = config.mode === "hybrid" ? Math.ceil(parts.length / 10) : 0;
      if (providerCalls + (config.facts ? parts.length : 0) > 40) {
        throw new ServiceError(422, "This write exceeds the declared model subrequest budget");
      }
      const count = this.sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM chunks WHERE expires>?", Date.now(),
      ).one().n;
      if (count + parts.length > config.maxChunks) throw new ServiceError(422, "User chunk capacity exceeded");
      const models = new Models(config, this.env);
      const facts: string[] = [];
      for (const part of parts) facts.push(await models.extract(part.source));
      const vectors = await models.embed(parts.map((part) => part.source));
      const ids = await Promise.all(parts.map((part) => sha256(JSON.stringify([
        request.user_id, request.request_id, part.position, part.offset,
      ]))));
      const expires = Date.now() + config.retentionDays * 86_400_000;
      this.ctx.storage.transactionSync(() => {
        this.lockConfig(request.user_id, config);
        this.sql.exec("INSERT OR IGNORE INTO meta VALUES ('owner', ?)", request.user_id);
        this.sql.exec("INSERT OR IGNORE INTO meta VALUES ('config', ?)", fingerprint(config));
        // Expired retries must not leave duplicate FTS records behind.
        this.sql.exec("DELETE FROM search_index WHERE rowid IN (SELECT rowid FROM chunks WHERE request_id=?)",
          request.request_id);
        this.sql.exec("DELETE FROM chunks WHERE request_id=?", request.request_id);
        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          const row = this.sql.exec<{ rowid: number }>(
            `INSERT INTO chunks
              (id,request_id,session_id,position,offset,role,timestamp,source,facts,embedding,expires)
              VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING rowid`,
            "mem_" + ids[i], request.request_id, request.session_id, part.position, part.offset,
            part.role, part.timestamp, part.source, facts[i], vectors[i], expires,
          ).one();
          this.sql.exec("INSERT INTO search_index(rowid,source,facts) VALUES (?,?,?)",
            row.rowid, terms(part.source).join(" "), terms(facts[i]).join(" "));
        }
        this.sql.exec("INSERT OR REPLACE INTO writes VALUES (?,?,?)", request.request_id, digest, expires);
      });
      await this.schedulePurge();
      await this.ctx.storage.sync();
      return { status: 200, body: this.ack(request) };
    } catch (error) { return failed(error); }
    finally { this.busy = false; }
  }

  private ack(request: AddRequest) {
    return { success: true, request_id: request.request_id, user_id: request.user_id, session_id: request.session_id };
  }

  async search(request: SearchRequest): Promise<Result> {
    try {
      const config = settings(this.env);
      this.lockConfig(request.user_id, config);
      const query = [request.query, ...(request.options ?? [])].join("\n");
      const ranked = new Map<string, number>();
      const searches = [{ text: request.query, weight: 2 }];
      if (request.options?.length) searches.push({ text: query, weight: 1 });
      const lists = searches.map(({ text, weight }) => {
        const match = matchQuery(text);
        const found = match ? this.sql.exec<{ id: string }>(
          `SELECT c.id FROM search_index JOIN chunks c ON c.rowid=search_index.rowid
           WHERE search_index MATCH ? AND c.expires>?
           ORDER BY bm25(search_index), c.timestamp DESC, c.rowid DESC LIMIT 300`, match, Date.now(),
        ).toArray() : [];
        return { found, weight };
      }).filter(({ found }) => found.length);
      const totalWeight = lists.reduce((sum, list) => sum + list.weight, 0);
      for (const { found, weight } of lists) {
        found.forEach((row, index) =>
          ranked.set(row.id, (ranked.get(row.id) ?? 0) + weight / totalWeight / (61 + index)));
      }
      if (config.mode === "hybrid") {
        const count = this.sql.exec<{ n: number }>(
          "SELECT COUNT(*) AS n FROM chunks WHERE expires>?", Date.now(),
        ).one().n;
        if (count) {
          const vector = await new Models(config, this.env).queryVector(query);
          const similarities: { id: string; score: number }[] = [];
          // Exact scoped scan is a baseline, not a large-history performance claim.
          for (const row of this.sql.exec<{ id: string; embedding: ArrayBuffer }>(
            "SELECT id,embedding FROM chunks WHERE expires>?", Date.now(),
          )) {
            const values = new Float32Array(row.embedding);
            if (values.length !== vector.length) throw new ServiceError(503, "Invalid stored vector");
            let score = 0;
            for (let i = 0; i < values.length; i++) score += values[i] * vector[i];
            similarities.push({ id: row.id, score });
          }
          similarities.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
          similarities.slice(0, 300).forEach((row, index) =>
            ranked.set(row.id, (ranked.get(row.id) ?? 0) + 1 / (61 + index)));
        }
      }
      const intent = retrievalIntent(request.query);
      if (intent.overview) {
        // Cover the whole retained timeline, not only repeated query words.
        const timeline = this.sql.exec<{ id: string }>(
          "SELECT id FROM chunks WHERE expires>? ORDER BY timestamp, rowid", Date.now(),
        ).toArray();
        const count = Math.min(request.top_k, timeline.length);
        for (let i = 0; i < count; i++) {
          const index = count === 1 ? timeline.length - 1
            : Math.round(i * (timeline.length - 1) / (count - 1));
          const id = timeline[index].id;
          ranked.set(id, (ranked.get(id) ?? 0) + 1 / (61 + i));
        }
      }
      if (intent.context) {
        // Neighbouring source passages can define a matched rule or its variables.
        const anchors = [...ranked].sort((a, b) => b[1] - a[1]).slice(0, 4);
        for (const [id, score] of anchors) {
          const anchor = this.sql.exec<{ request_id: string; position: number; offset: number }>(
            "SELECT request_id,position,offset FROM chunks WHERE id=? AND expires>?", id, Date.now(),
          ).toArray()[0];
          if (!anchor) continue;
          const related = this.sql.exec<{ id: string }>(
            `SELECT id FROM chunks WHERE request_id=? AND expires>?
             ORDER BY abs(position-?),abs(offset-?),rowid LIMIT 24`,
            anchor.request_id, Date.now(), anchor.position, anchor.offset,
          ).toArray();
          related.forEach((row, index) => {
            const contextualScore = score * 0.95 / (1 + index * 0.05);
            ranked.set(row.id, Math.max(ranked.get(row.id) ?? 0, contextualScore));
          });
        }
      }
      const data = [];
      const seen = new Set<string>();
      for (const [id, score] of [...ranked].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
        const row = this.sql.exec<StoredChunk>(
          "SELECT * FROM chunks WHERE id=? AND expires>?", id, Date.now(),
        ).toArray()[0];
        if (!row) continue;
        // Identical text at different source positions is still distinct evidence.
        const identity = JSON.stringify([
          row.request_id, row.session_id, row.position, row.offset, row.role, row.timestamp, row.source,
        ]);
        if (seen.has(identity)) continue;
        seen.add(identity);
        data.push(evidence(id, row, score));
        if (data.length === request.top_k) break;
      }
      return { status: 200, body: { data } };
    } catch (error) { return failed(error); }
  }
}

export default {
  async fetch(request: Request, env: RuntimeEnv): Promise<Response> {
    const respond = (result: Result) => Response.json(result.body, {
      status: result.status,
      headers: { "Cache-Control": "no-store", ...(result.status === 429 ? { "Retry-After": "2" } : {}) },
    });
    try {
      const path = new URL(request.url).pathname;
      if (request.method === "GET" && path === "/health") {
        let ready = !!env.MEMORY_API_KEY && env.MEMORY_API_KEY.length >= 32;
        try { settings(env); } catch { ready = false; }
        return respond({ status: ready ? 200 : 503, body: {
          status: ready ? "ok" : "configuration_required", version: VERSION,
          mode: env.MEMORY_MODE, facts: String(env.ENABLE_FACTS) === "true",
          source_commit: env.SOURCE_COMMIT,
          authenticated_endpoints_configured: !!env.MEMORY_API_KEY && env.MEMORY_API_KEY.length >= 32,
        } });
      }
      if (path !== "/v1/memories/add" && path !== "/v1/memories/search") return respond({ status: 404, body: { detail: "Not found" } });
      if (request.method !== "POST") return respond({ status: 405, body: { detail: "POST required" } });
      if (!env.MEMORY_API_KEY || env.MEMORY_API_KEY.length < 32) throw new ServiceError(503, "API credentials not configured");
      const supplied = request.headers.get("Authorization") ?? "";
      if (!validBearer(supplied, env.MEMORY_API_KEY)) throw new ServiceError(401, "Invalid credentials");
      const config = settings(env);
      const raw = await readJson(request);
      if (path === "/v1/memories/add") {
        const parsed = addSchema.safeParse(raw);
        if (!parsed.success) throw new ServiceError(422, "Invalid AML textual Add request");
        const scope = JSON.stringify([config.submission, parsed.data.user_id]);
        return respond(await env.MEMORY.getByName(scope).add(parsed.data));
      }
      const parsed = searchSchema.safeParse(raw);
      if (!parsed.success) throw new ServiceError(422, "Invalid AML textual Search request");
      const scope = JSON.stringify([config.submission, parsed.data.user_id]);
      return respond(await env.MEMORY.getByName(scope).search(parsed.data));
    } catch (error) { return respond(failed(error)); }
  },
} satisfies ExportedHandler<RuntimeEnv>;
