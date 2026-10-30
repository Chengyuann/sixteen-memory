"""A fresh context-disjoint CL-bench source-anchor diagnostic and old regressions.

CL-bench has no supplied retrieval-span labels. We use only literal phrases
quoted by a rubric AND occurring verbatim in prior source messages, excluding
the current query. This is a narrow retrieval diagnostic, NOT CL-bench accuracy.
Rubrics, quote text and annotations never enter retrieval worker payloads.
"""
import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

QUOTE = re.compile(r'"([^"\n]{20,240})"|“([^”\n]{20,240})”|`([^`\n]{20,240})`')
SEED = "sixteen-context-recall-20261030-v1"


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def source_anchors(row):
    messages = row["messages"]
    if len(messages) < 4 or messages[-1]["role"] != "user":
        return []
    query = messages[-1]["content"]
    if not 1 <= len(query) <= 32000:
        return []
    prior = messages[:-1]
    found = {}
    for rubric in row["rubrics"]:
        for match in QUOTE.finditer(rubric):
            quote = next(part for part in match.groups() if part is not None)
            if len(re.findall(r"\w+", quote)) < 4 or quote in query:
                continue
            occurrences = []
            for position, message in enumerate(prior):
                start = message["content"].find(quote)
                while start >= 0:
                    occurrences.append({
                        "request_id": "source", "position": position,
                        "start": start, "end": start + len(quote),
                    })
                    start = message["content"].find(quote, start + 1)
            if occurrences:
                # Stable ID hashes the phrase; the actual quote is not exported.
                found[sha(quote)] = {"id": sha(quote), "occurrences": occurrences}
    return [found[key] for key in sorted(found)]


def prepare(workspace, output):
    output.mkdir(parents=True, exist_ok=True)
    source = workspace / "work/public-benchmarks/CL-bench/CL-bench.jsonl"
    # Iterate physical lines. str.splitlines() incorrectly splits embedded U+2028.
    with source.open() as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    grouped = defaultdict(dict)
    for row in rows:
        anchors = source_anchors(row)
        if anchors:
            meta = row["metadata"]
            grouped[meta["context_category"]].setdefault(meta["context_id"], []).append((row, anchors))
    split_by_context = {}
    for category, groups in sorted(grouped.items()):
        ids = sorted(groups, key=lambda i: sha(SEED + ":" + i))
        for i, context_id in enumerate(ids):
            split_by_context[context_id] = "development" if i % 2 == 0 else "validation"
    conversations, selection = [], []
    for category, groups in sorted(grouped.items()):
        for context_id, tasks in sorted(groups.items()):
            split = split_by_context[context_id]
            for row, anchors in sorted(tasks, key=lambda p: p[0]["metadata"]["task_id"]):
                task_id = row["metadata"]["task_id"]
                required = sorted({
                    (o["request_id"], o["position"]) for a in anchors for o in a["occurrences"]
                })
                # System text is retained as untrusted source, not as a live instruction.
                messages = [{
                    "role": "assistant" if m["role"] == "assistant" else "user",
                    "content": m["content"],
                } for m in row["messages"][:-1]]
                conversations.append({
                    "id": task_id, "dataset": "clbench-literal-source-diagnostic",
                    "split": split,
                    "batches": [{"request_id": "source", "session_id": "history", "messages": messages}],
                    "queries": [{
                        "id": task_id, "query": row["messages"][-1]["content"], "category": category,
                        "required": [{"request_id": r, "position": p} for r, p in required],
                        "anchors": anchors,
                    }],
                })
                selection.append({
                    "id": task_id, "context_id": context_id, "category": category,
                    "split": split, "anchors": len(anchors),
                })
    previous = workspace / "outputs/triple-smoke-20261029/fresh/data.json"
    prior = json.loads(previous.read_text())
    for c in prior["conversations"]:
        conversations.append({
            **c, "split": "regression",
            "dataset": c["dataset"] + "-previously-used",
        })
    data = json.dumps({"schema": 1, "conversations": conversations},
                      ensure_ascii=False, separators=(",", ":")) + "\n"
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": "Public Tencent-Hunyuan CL-bench JSONL",
        "source_sha256": sha(source.read_bytes()), "prior_data_sha256": sha(previous.read_bytes()),
        "prepared_sha256": sha(data), "seed": SEED,
        "split_unit": "context_id, stratified by source context category",
        "selection": selection, "contexts": len(split_by_context),
        "counts": dict(Counter(c["split"] for c in conversations)),
        "questions": dict(Counter(c["split"] for c in conversations for _ in c["queries"])),
        "fresh_diagnostic": "Literal rubric-quoted phrases found in prior source; "
                            "not complete relevance labels or official CL-bench accuracy.",
        "rubrics_sent_to_retrieval": False, "answer_labels_sent_to_retrieval": False,
        "full_histories": True, "new_validation_seen_before_selection": False,
        "regression": "All prior LongMemEval/LoCoMo questions are previously used, not fresh holdout.",
        "primary": "Macro literal source-anchor coverage within first16000 source characters.",
        "selection_rule": "Freeze one nominee using development only: no errors, no macro "
                          "anchor@100 decrease, positive anchor@16000 versus BASE and T04; "
                          "tie by anchor@100 then ID. No nominee if no candidate qualifies.",
        "secondary": ["anchor@100", "anchor@20", "source_coverage100", "p95_ms"],
        "deployment_rule": "No automatic promotion or official submission; regression and "
                           "runtime/source-provenance checks required.",
        "model_calls": 0, "official_evaluations": 0,
    }
    dev = {r["context_id"] for r in selection if r["split"] == "development"}
    val = {r["context_id"] for r in selection if r["split"] == "validation"}
    assert not dev & val
    for name, text in [("data.json", data), ("manifest.json", json.dumps(manifest, indent=2) + "\n")]:
        with (output / name).open("x") as handle:
            handle.write(text)
    print(json.dumps({k: manifest[k] for k in ("counts", "questions", "contexts", "prepared_sha256")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.workspace.resolve(), args.output.resolve())
