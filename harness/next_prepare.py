"""Prepare an auditable public-data split without sending labels to retrieval."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(text):
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


def prepare(root, output):
    output.mkdir(parents=True, exist_ok=True)
    source = root / "work/public-benchmarks/LongMemEval/data/longmemeval_s_cleaned.json"
    source_bytes = source.read_bytes()
    raw = json.loads(source_bytes)
    prior = {"e47becba"}
    inputs = [
        "outputs/v025-analysis-20261029/longmemeval-external-manifest.json",
        "outputs/v026-analysis-20261029/manifest.json",
        "outputs/v026-rule-validation-20261029/manifest.json",
        "outputs/harness100-final-20261029/data-manifest.json",
    ]
    for name in inputs:
        manifest = json.loads((root / name).read_text())
        prior.update(manifest.get("ids") or [r["id"] for r in (manifest.get("selected") or manifest.get("selection"))])
    evidence = {sid for r in raw if r["question_id"] in prior for sid in r["answer_session_ids"]}
    grouped = defaultdict(list)
    for r in raw:
        if r["question_id"] in prior or set(r["answer_session_ids"]) & evidence:
            continue
        group = r["question_type"] + ("_abs" if r["question_id"].endswith("_abs") else "")
        grouped[group].append(r)
    selections = []
    for group, rows in sorted(grouped.items()):
        rows.sort(key=lambda r: sha("sixteen-triple-smoke-20261030:" + r["question_id"]))
        accepted = []
        group_evidence = set(evidence)
        for row in rows:
            if set(row["answer_session_ids"]) & group_evidence:
                continue
            accepted.append(row)
            group_evidence.update(row["answer_session_ids"])
        accepted = accepted[:len(accepted) - len(accepted) % 2]
        for i, row in enumerate(accepted):
            evidence.update(row["answer_session_ids"])
            selections.append((row, "development" if i % 2 == 0 else "validation"))
    conversations = []
    for row, split in selections:
        batches, required = [], []
        for session_index, (sid, date, turns) in enumerate(zip(
                row["haystack_session_ids"], row["haystack_dates"], row["haystack_sessions"])):
            timestamp = int(datetime.strptime(date, "%Y/%m/%d (%a) %H:%M")
                            .replace(tzinfo=timezone.utc).timestamp() * 1000)
            # Opaque normalized IDs remove the public dataset's "answer_" prefix.
            session_id = f"session-{session_index}"
            messages, labels, words = [], [], 0
            def flush():
                nonlocal messages, labels, words
                if not messages:
                    return
                rid = f"batch-{len(batches)}"
                required.extend({"request_id": rid, "position": p} for p in labels)
                batches.append({"request_id": rid, "session_id": session_id,
                                "timestamp": timestamp, "messages": messages})
                messages, labels, words = [], [], 0
            for turn in turns:
                if not turn["content"].strip():
                    continue
                count = len(turn["content"].split())
                if messages and (len(messages) >= 20 or words + count > 2000):
                    flush()
                if turn.get("has_answer"):
                    labels.append(len(messages))
                messages.append({"role": turn["role"], "content": turn["content"]})
                words += count
            flush()
        abstain = row["question_id"].endswith("_abs")
        if abstain:
            required = []
        elif not required:
            raise ValueError("Missing message-level evidence")
        conversations.append({
            "id": row["question_id"], "dataset": "longmemeval-s-unseen-next",
            "split": split, "batches": batches,
            "queries": [{"id": row["question_id"], "query": row["question"],
                         "category": row["question_type"], "required": required, "abstain": abstain}],
        })
    # Prior LoCoMo holdouts are labelled regression, never advertised as new validation.
    locomo = json.loads((root / "outputs/v025-analysis-20261029/public-retrieval-data-v2.json").read_text())
    regression = [{**c, "dataset": "locomo-refined-used", "split": "regression"}
                  for c in locomo["conversations"]]
    data = {"schema": 1, "conversations": conversations + regression}
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sha(source_bytes), "prepared_sha256": sha(text),
        "source": "LongMemEval-S public cleaned dataset",
        "baseline_commit": "a3520ccb7843156e2dd51e92e29caf0f6dafafa4",
        "excluded_question_ids": sorted(prior),
        "selection": [{"id": r["question_id"], "category": r["question_type"], "split": split,
                       "abstain": r["question_id"].endswith("_abs")} for r, split in selections],
        "split_method": "Hash-sort within question type, alternate development/validation before outcomes",
        "counts": {split: sum(c["split"] == split for c in conversations)
                   for split in ("development", "validation")},
        "regression_queries": sum(len(c["queries"]) for c in regression),
        "metrics": {
            "primary": "evidence_score = 100 * mean over dataset/category strata of mean turn recall@20",
            "secondary": ["recall@10", "recall@100", "all-evidence@100", "MRR",
                          "recall@16000 source characters", "changed output cases",
                          "improved/regressed", "p95 query ms", "characters", "errors"],
            "safety_separate": ["forbidden evidence hits", "empty retrieval for unanswerable questions"],
            "not": "not AML score, answer accuracy, calibrated prediction or semantic faithfulness",
        },
        "selection_rule": "Select one candidate per direction using development evidence score; tie by recall100 then ID. Save choices before validation. Publish all100 validation results without retuning or replacing selections from validation results.",
        "source_ids_normalized": True, "gold_in_service_input": False,
        "full_histories": True, "shared_distractor_histories_possible": True,
        "prior_holdouts_reused": "LoCoMo labelled regression only",
        "answerer": {"status": "unavailable", "reason": "No approved model API configured"},
    }
    for name, value in [("data.json", text), ("manifest.json", json.dumps(manifest, indent=2))]:
        with (output / name).open("x") as f:
            f.write(value + "\n")
    print(json.dumps({k: manifest[k] for k in ("counts", "regression_queries", "prepared_sha256")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.workspace.resolve(), args.output.resolve())
