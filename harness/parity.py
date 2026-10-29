"""Compare the local baseline against archived real Workers-runtime metrics."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from .engine import BASELINE_COMMIT, Index, from_batches, source_documents
from .metrics import evaluate


def check(workspace, output):
    directory = workspace / "outputs/v026-analysis-20261029"
    data = json.loads((directory / "study-data.json").read_text())
    references = {}
    hashes = {}
    for split in ("development", "heldout"):
        file = directory / f"{split}-fixed1200.json"
        hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        references.update({r["id"]: r for r in json.loads(file.read_text())["cases"]})
    differences = []
    for case in data["conversations"]:
        messages = from_batches(case["id"], case["batches"])
        index = Index(messages, source_documents(case["id"], messages))
        try:
            for question in case["queries"]:
                metrics = evaluate(index.apply(question["query"]), question)
                for field in ("recall10", "recall20", "recall100"):
                    expected = references[question["id"]][field]
                    if abs(expected - metrics[field]) > 1e-10:
                        differences.append({"id": question["id"], "field": field,
                                            "worker": expected, "local": metrics[field]})
        finally:
            index.close()
    result = {"baseline_commit": BASELINE_COMMIT, "cases": len(references),
              "status": "passed" if not differences else "failed",
              "differences": differences, "reference_sha256": hashes,
              "sqlite_version": sqlite3.sqlite_version,
              "scope": "Message recall at ranks10/20/100 on72 archived public cases; not byte-identical runtime or production deployment proof."}
    output.write_text(json.dumps(result, indent=2) + "\n")
    if differences:
        raise ValueError("Baseline parity failed")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    check(args.workspace, args.output)
