"""Freeze development-only direction selections before opening validation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def select(directory, preserve=None):
    if (directory / "results-validation.jsonl").exists():
        raise ValueError("Validation already inspected; cannot create a prevalidation selection")
    summary_path = directory / "summary-development.json"
    summary = json.loads(summary_path.read_text())
    if summary["fatals"] or len(summary["table"]) != 101:
        raise ValueError("Incomplete development evaluation")
    baseline = next(r for r in summary["table"] if r["id"] == "BASE")
    selected = []
    original = json.loads(preserve.read_text()) if preserve else None
    original_ids = {r["direction"]: r["id"] for r in original["selected"]} if original else {}
    for direction in range(1, 11):
        rows = [r for r in summary["table"] if r["id"].startswith(f"D{direction:02}.")
                and not r["errors"] and r["ok"] == baseline["ok"]]
        if not rows:
            selected.append({"direction": f"D{direction:02}", "id": None, "reason": "no valid run"})
            continue
        rows.sort(key=lambda r: (-r["evidence_score"], -r["recall100"], r["id"]))
        best = next((r for r in rows if r["id"] == original_ids.get(f"D{direction:02}")), None) if original else rows[0]
        if best is None:
            raise ValueError("Original nominee no longer has a complete valid run")
        selected.append({"direction": f"D{direction:02}", "id": best["id"],
                         "development_score": best["evidence_score"],
                         "delta": best["evidence_score"] - baseline["evidence_score"],
                         "source_coverage100": best["source_coverage100"],
                         "triggered": best["triggered"]})
    result = {"selected_at": datetime.now(timezone.utc).isoformat(), "selected": selected,
              "development_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
              "baseline_score": baseline["evidence_score"],
              "criterion": "Per-direction evidence_score, then recall100, then stable ID; error-free complete runs only.",
              "scope": "Exploratory nominees, not release decisions. Validation is not used to replace nominees.",
              "additional_release_gates": "Positive validation direction, no increased privacy hits, no material source coverage loss, activation evidence, performance, actual Worker and approved answerer validation."}
    if original:
        result["original_selection_sha256"] = hashlib.sha256(preserve.read_bytes()).hexdigest()
        result["original_selected_at"] = original["selected_at"]
        result["validation_previously_seen"] = True
        result["criterion"] = "Preserve exactly the original ten prevalidation nominees after integrity-only runner repairs; no algorithm changes or validation-based reselection."
    with (directory / "selection-before-validation.json").open("x") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preserve", type=Path, help="Original nominee receipt for an integrity-only rerun")
    args = parser.parse_args()
    select(args.output, args.preserve)
