import { z } from "zod";

export const VERSION = "0.2.8";
export const MAX_BODY = 2 * 1024 * 1024;

const identifier = z.string().min(1).max(512);
const message = z.strictObject({
  role: z.enum(["user", "assistant"]),
  content: z.string().min(1).max(1_000_000).refine((text) => text.trim().length > 0),
  timestamp: z.number().int().min(0).max(253402300799000).nullable().optional(),
});

export const addSchema = z.strictObject({
  request_id: identifier,
  user_id: identifier,
  session_id: identifier,
  messages: z.array(message).min(1).max(1000),
});

export const searchSchema = z.strictObject({
  query: z.string().min(1).max(32000).refine((text) => text.trim().length > 0),
  user_id: identifier,
  top_k: z.number().int().min(1).max(100),
  options: z.array(z.string().max(8000)).max(100).nullable().optional(),
});

export type AddRequest = z.infer<typeof addSchema>;
export type SearchRequest = z.infer<typeof searchSchema>;
export type MemoryMessage = z.infer<typeof message>;

export type Chunk = {
  position: number;
  offset: number;
  source: string;
  role: "user" | "assistant";
  timestamp: number | null;
};

export type Settings = {
  mode: "lexical" | "hybrid";
  facts: boolean;
  chunkChars: number;
  maxChunks: number;
  retentionDays: number;
  dimensions: number;
  embeddingUrl: string;
  embeddingModel: string;
  llmUrl: string;
  llmModel: string;
  submission: string;
};

export class ServiceError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function integer(value: string | undefined, min: number, max: number): number {
  if (!value || !/^\d+$/.test(value)) throw new ServiceError(503, "Invalid service configuration");
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < min || result > max) {
    throw new ServiceError(503, "Invalid service configuration");
  }
  return result;
}

export function settings(vars: object): Settings {
  const text = (key: string): string => {
    const value: unknown = Reflect.get(vars, key);
    return typeof value === "string" ? value : "";
  };
  const mode = text("MEMORY_MODE");
  if (mode !== "lexical" && mode !== "hybrid") {
    throw new ServiceError(503, "Invalid retrieval mode");
  }
  if (!["true", "false"].includes(text("ENABLE_FACTS"))) {
    throw new ServiceError(503, "Invalid fact configuration");
  }
  const facts = text("ENABLE_FACTS") === "true";
  if (text("EMBEDDING_MODEL") !== "text-embedding-v4" || text("LLM_MODEL") !== "gpt-4o-mini") {
    throw new ServiceError(503, "Only the declared competition models are supported");
  }
  if ((mode === "hybrid" && !text("EMBEDDING_API_KEY")) || (facts && !text("LLM_API_KEY"))) {
    throw new ServiceError(503, "Required model credentials are not configured");
  }
  if (facts && mode !== "hybrid") throw new ServiceError(503, "Facts require hybrid mode");
  for (const key of ["EMBEDDING_BASE_URL", "LLM_BASE_URL"]) {
    let url: URL;
    try { url = new URL(text(key)); } catch { throw new ServiceError(503, "Invalid provider URL"); }
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
      throw new ServiceError(503, "Invalid provider URL");
    }
  }
  const dimensions = integer(text("EMBEDDING_DIMENSIONS"), 64, 2048);
  if (![64, 128, 256, 512, 768, 1024, 1536, 2048].includes(dimensions)) {
    throw new ServiceError(503, "Invalid embedding dimensions");
  }
  const submission = text("SUBMISSION_ID");
  if (!submission || submission.length > 128) throw new ServiceError(503, "Invalid submission ID");
  return {
    mode, facts, dimensions, submission,
    chunkChars: integer(text("CHUNK_CHARS"), 200, 2000),
    maxChunks: integer(text("MAX_CHUNKS_PER_USER"), 1, 10000),
    retentionDays: integer(text("RETENTION_DAYS"), 1, 30),
    embeddingUrl: text("EMBEDDING_BASE_URL"),
    embeddingModel: text("EMBEDDING_MODEL"),
    llmUrl: text("LLM_BASE_URL"),
    llmModel: text("LLM_MODEL"),
  };
}

export function fingerprint(config: Settings): string {
  // Capacity and retention can change; representation changes require a fresh namespace.
  return JSON.stringify({
    schema: 3, mode: config.mode, facts: config.facts, dimensions: config.dimensions,
    chunkChars: config.chunkChars, embeddingUrl: config.embeddingUrl,
    embeddingModel: config.embeddingModel, llmUrl: config.llmUrl, llmModel: config.llmModel,
    submission: config.submission,
  });
}

export function chunkText(text: string, size: number): { offset: number; source: string }[] {
  const chars = Array.from(text);
  const step = size - Math.min(160, Math.floor(size / 5));
  const result = [];
  for (let start = 0; start < chars.length; start += step) {
    const end = Math.min(start + size, chars.length);
    result.push({ offset: start, source: chars.slice(start, end).join("") });
    if (end === chars.length) break;
  }
  return result;
}

export function sourceChunks(request: AddRequest, size: number): Chunk[] {
  return request.messages.flatMap((msg, position) =>
    chunkText(msg.content, size).map((part) => ({
      ...part, position, role: msg.role, timestamp: msg.timestamp ?? null,
    })),
  );
}

export function terms(text: string): string[] {
  return text.toLowerCase()
    .replace(/([\u3400-\u9fff])/gu, " $1 ")
    .match(/[\p{L}\p{N}]+/gu) ?? [];
}

export function matchQuery(text: string): string | null {
  const words = [...new Set(terms(text))];
  return words.length ? words.map((word) => `"${word}"`).join(" OR ") : null;
}

export function retrievalIntent(query: string): { overview: boolean; context: boolean } {
  return {
    overview: /\b(?:summari[sz]e|summary|overview|overall|trajectory|trends?)\b|总结|概述|综述|总览|整体|变化趋势/iu.test(query),
    context: /\b(?:rules?|polic(?:y|ies)|procedures?|workflows?|instructions?|protocols?|algorithms?|steps?|calculate|compute|simulate|simulation|formula|formulas)\b|规则|政策|流程|步骤|指令|算法|计算|模拟|公式/iu.test(query),
  };
}

export async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export async function readJson(source: Request | Response, max = MAX_BODY): Promise<unknown> {
  const reader = source.body?.getReader();
  if (!reader) throw new ServiceError(400, "Expected JSON body");
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    total += next.value.byteLength;
    if (total > max) {
      await reader.cancel();
      throw new ServiceError(413, "Payload exceeds the declared size limit");
    }
    chunks.push(next.value);
  }
  const data = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(data)); }
  catch { throw new ServiceError(400, "Invalid JSON"); }
}

export function evidence(id: string, row: Chunk & { session_id: string; request_id: string }, score: number) {
  const timestamp = row.timestamp === null ? null : new Date(row.timestamp).toISOString();
  const metadata = JSON.stringify({
    session: row.session_id, request_id: row.request_id, message: row.position,
    character_offset: row.offset, role: row.role, source_time: timestamp,
  });
  return {
    id, content: `${metadata}\n${row.source}`, score,
    ...(timestamp === null ? {} : { created_at: timestamp }),
  };
}

const queryStopwords = new Set(["a","an","the","is","are","was","were","be","been","being","do","does","did","have","has","had","can","could","would","should","will","shall","may","might","must","i","me","my","mine","we","us","our","ours","you","your","yours","he","him","his","she","her","hers","it","its","they","them","their","theirs","what","which","who","whom","whose","when","where","why","how","that","this","these","those","am","as","at","by","for","from","in","into","of","on","onto","to","with","and","or","but","if","then","than","so","because","about","please","tell","give","describe","explain","find","know","remember","recall"]);
export function focusedQuery(query: string): string {
  const words = terms(query);
  return words.filter((word) => !queryStopwords.has(word)).join(" ");
}

export function contextualSource(request: AddRequest, part: Chunk): string {
  const previous = request.messages[part.position - 1]?.content ?? "";
  const current = request.messages[part.position].content.replace(/^[^:\n]{1,50}:\s*/, "");
  const anaphoric = /^(?:yes|no|it|they|this|that|these|those|there|thanks|depends|varies|about|for|we|i)\b/iu.test(current);
  if (!anaphoric || !previous.includes("?")) return "";
  return previous.slice(-1200);
}
