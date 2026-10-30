/** Offline-only R01 prototype. No live Worker imports this module.
 *
 * Preserve each original top100 source span. Repeated chunks from a message
 * are demoted only when they add no new focused query token. This does not
 * establish semantic relevance, answer correctness, or a calibrated score.
 */
import { focusedQuery, terms } from "../candidates/BASE/core";
import type { Row } from "../candidates/rerank";

export function facetPreservingOrder<T extends Row>(query: string, input: T[]): T[] {
  const rows = input.slice(0, 100);
  const wanted = new Set(terms(focusedQuery(query)));
  if (!wanted.size) return rows;
  const seen = new Set<string>();
  const covered = new Map<string, Set<string>>();
  const scores = rows.map((row) => row.score);
  rows.forEach((row, i) => {
    const key = JSON.stringify([row.request_id, row.position]);
    const present = new Set(terms(row.source).filter((term) => wanted.has(term)));
    const previous = covered.get(key) ?? new Set<string>();
    const addsFacet = [...present].some((term) => !previous.has(term));
    if (i >= 5 && seen.has(key) && !addsFacet) scores[i] *= 0.75;
    seen.add(key);
    for (const term of present) previous.add(term);
    covered.set(key, previous);
  });
  return rows.map((row, i) => ({ row, i, score: scores[i] }))
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map(({ row, score }) => ({ ...row, score }));
}
