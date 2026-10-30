// Frozen v0.2.5 representation and retrieval helpers; no summaries or new ranking.
export * from "../BASE/core";
import {
  ServiceError, settings as baselineSettings, type Settings,
} from "../BASE/core";

export const VERSION = "0.3.0";
export const MAX_CAPACITY = 100_000;

export function settings(vars: object): Settings {
  const raw: unknown = Reflect.get(vars, "MAX_CHUNKS_PER_USER");
  if (typeof raw !== "string" || !/^\d+$/.test(raw)) {
    throw new ServiceError(503, "Invalid service configuration");
  }
  const capacity = Number(raw);
  if (!Number.isSafeInteger(capacity) || capacity < 1 || capacity > MAX_CAPACITY) {
    throw new ServiceError(503, "Invalid service configuration");
  }
  // Reuse all baseline validation, changing only the explicit capacity ceiling.
  return { ...baselineSettings({ ...vars, MAX_CHUNKS_PER_USER: "10000" }), maxChunks: capacity };
}
