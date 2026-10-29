import { focusedQuery, terms } from "./BASE/core";

export type Mode = "parent" | "message" | "entity";
export type Row = { id: string; score: number; request_id: string; session_id: string;
  position: number; offset: number; role: "user" | "assistant"; source: string };
const pronouns = new Set("i me my mine we us our ours you your yours he him his she her hers it its they them their theirs who what where when why how which can could would should do does did is are was were has have had a an the this that these those yes no please".split(" "));
const privateSource = /\b(?:private|confidential|password|passcode|ssn|api[_ -]?key|do not share)\b/iu;

export function entityPhrases(query: string): string[] {
  const result: string[] = [], run: string[] = [];
  const matches = query.match(/[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*/gu) ?? [];
  for (const word of [...matches, ""]) {
    const eligible = word.length > 1 && !pronouns.has(word.toLowerCase())
      && (/[A-Z]/u.test(word[0]) || /[0-9]/u.test(word));
    if (eligible) run.push(word);
    else if (run.length) { result.push(run.join(" ")); run.length = 0; }
  }
  return result.slice(0, 4);
}

export function reorder<T extends Row>(query: string, input: T[], mode: Mode): T[] {
  // A permutation of the original top100: no acquisition, deletion, or source rewriting.
  const rows = input.slice(0, 100);
  const q = new Set(terms(focusedQuery(query)));
  const words = rows.map((r) => new Set(terms(r.source)));
  const scores = rows.map((r) => r.score);
  if (mode === "parent") {
    const parents = new Map<string, number[]>();
    rows.forEach((r, i) => {
      const key = JSON.stringify([r.request_id, r.session_id]);
      if (r.role === "user") parents.set(key, [...(parents.get(key) ?? []), i]);
    });
    for (let i = 0; i < Math.min(24, rows.length); i++) {
      const row = rows[i];
      if (row.role !== "assistant") continue;
      const candidates = (parents.get(JSON.stringify([row.request_id, row.session_id])) ?? [])
        .filter((j) => rows[j].position < row.position);
      if (!candidates.length) continue;
      // First baseline-ranked chunk wins a same-position tie, as in Python.
      const parent = candidates.reduce((best, j) => rows[j].position > rows[best].position ? j : best);
      if (!rows[parent].source.includes("?") || privateSource.test(rows[parent].source)
          || ![...q].some((t) => words[parent].has(t))) continue;
      scores[parent] = Math.max(scores[parent], row.score * 0.98);
    }
  } else if (mode === "message") {
    const seen = new Map<string, number>();
    rows.forEach((row, i) => {
      const key = JSON.stringify([row.request_id, row.position]);
      const count = seen.get(key) ?? 0;
      if (i >= 5 && count) scores[i] *= 0.75;
      seen.set(key, count + 1);
    });
  } else if (mode === "entity") {
    const phrases = entityPhrases(query).map((p) => new Set(terms(p)));
    if (!phrases.length) return rows;
    rows.forEach((_, i) => {
      const matches = phrases.filter((p) => [...p].every((t) => words[i].has(t))).length;
      scores[i] *= 1 + 0.2 * matches / phrases.length;
    });
  } else { throw new Error("Unknown frozen candidate"); }
  return rows.map((row, i) => ({ row, i, score: scores[i] }))
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map(({ row, score }) => ({ ...row, score }));
}
