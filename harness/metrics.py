"""Evidence metrics; never an answer-quality estimate."""
from collections import defaultdict
from statistics import mean


def evaluate(hits, question):
    required = {f'{r["request_id"]}:{r["position"]}' for r in question.get("required", [])}
    forbidden = {f'{r["request_id"]}:{r["position"]}' for r in question.get("forbidden", [])}
    ids = [h.document.message_id for h in hits[:100]]
    result = {"answerable": bool(required), "returned": len(ids),
              "characters": sum(len(h.document.text) for h in hits[:100]),
              "forbidden_hits": len(forbidden.intersection(ids)),
              "abstain_correct": int(not ids) if question.get("abstain") else None}
    for k in (10, 20, 100):
        found = len(required.intersection(ids[:k]))
        result[f"recall{k}"] = found / len(required) if required else None
        result[f"all{k}"] = int(found == len(required)) if required else None
    first = next((i for i, identifier in enumerate(ids) if identifier in required), None)
    result["mrr"] = 0 if first is None else 1 / (first + 1)
    budget_ids, chars = [], 0
    for h in hits:
        if chars + len(h.document.text) > 16000:
            break
        chars += len(h.document.text)
        budget_ids.append(h.document.message_id)
    result["recall16k"] = len(required.intersection(budget_ids)) / len(required) if required else None
    coverages = []
    for evidence in question.get("required", []):
        source = evidence.get("source")
        if not source:
            continue
        message_id = f'{evidence["request_id"]}:{evidence["position"]}'
        intervals = sorted((h.document.offset, h.document.offset + len(h.document.text))
                           for h in hits[:100] if h.document.message_id == message_id)
        end = covered = 0
        for left, right in intervals:
            covered += max(0, min(len(source), right) - max(end, left))
            end = max(end, right)
        coverages.append(covered / len(source))
    result["source_coverage100"] = mean(coverages) if coverages else result["recall100"]
    return result


def aggregate(rows):
    valid = [r for r in rows if r.get("status") == "ok"]
    answered = [r for r in valid if r["answerable"]]
    by_stratum = defaultdict(list)
    for r in answered:
        by_stratum[(r["dataset"], r["category"])].append(r)
    def macro(field):
        values = [mean(r[field] for r in group) for group in by_stratum.values()]
        return mean(values) if values else None
    times = sorted(r["search_ms"] for r in valid)
    result = {
        "questions": len(rows), "ok": len(valid), "errors": len(rows) - len(valid),
        "strata": len(by_stratum),
        "evidence_score": None if not answered else 100 * macro("recall20"),
        **{k: macro(k) for k in ["recall10", "recall20", "recall100", "all100", "mrr",
                                 "recall16k", "source_coverage100"]},
        "mean_characters": mean(r["characters"] for r in valid) if valid else None,
        "p95_ms": times[max(0, int(len(times) * .95 + .999) - 1)] if times else None,
        "triggered": sum(r.get("triggered", False) for r in valid),
        "forbidden_hits": sum(r["forbidden_hits"] for r in valid),
        "privacy_forbidden_hits": sum(r["forbidden_hits"] for r in valid
                                      if r["category"] == "privacy_minimal_disclosure"),
        "abstention_cases": sum(r["abstain_correct"] is not None for r in valid),
        "correct_abstentions": sum(r["abstain_correct"] or 0 for r in valid),
    }
    if result["errors"]:
        for key in ("evidence_score", "recall10", "recall20", "recall100", "all100",
                    "mrr", "recall16k", "source_coverage100"):
            result[key] = None
    return result
