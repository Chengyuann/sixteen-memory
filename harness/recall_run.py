"""Bounded offline recall study with source-only spawned workers and frozen runs."""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
import json
import multiprocessing
import os
from pathlib import Path
from statistics import mean
import time

from .engine import Index, from_batches, source_documents
from .metrics import aggregate
from .next_run import (
    decode_hits, encode_hits, latest_records, score_job, sha, source_job, successful,
)
from .recall_mechanisms import load


def retrieve_job(job):
    """Only source messages and original query are visible in this process."""
    experiments = {e.id: e for e in load()}
    messages = from_batches(job["case_id"], job["batches"])
    documents = source_documents(job["case_id"], messages)
    output = {}
    for identifier in ["BASE", *job["experiments"]]:
        started, index = time.perf_counter(), None
        try:
            index = Index(messages, documents)
            build_ms = (time.perf_counter() - started) * 1000
            rows = []
            for query in job["queries"]:
                started = time.perf_counter()
                try:
                    hits = index.apply(query, experiments.get(identifier))
                    rows.append({
                        "status": "ok", "hits": encode_hits(hits),
                        "search_ms": (time.perf_counter() - started) * 1000,
                        "build_ms": build_ms,
                    })
                except Exception as error:
                    rows.append({
                        "status": "error", "error_type": type(error).__name__,
                        "build_ms": build_ms, "search_ms": 0,
                    })
            output[identifier] = rows
        except Exception as error:
            output[identifier] = [{
                "status": "error", "error_type": type(error).__name__,
                "build_ms": 0, "search_ms": 0,
            } for _ in job["queries"]]
        finally:
            if index is not None:
                index.close()
    return output


def anchor_fraction(hits, anchors):
    """Fraction of literal anchors fully present at any labelled source position."""
    intervals = defaultdict(list)
    for hit in hits:
        d = hit.document
        intervals[(d.request_id, d.position)].append((d.offset, d.offset + len(d.text)))
    merged = {}
    for source, spans in intervals.items():
        joined = []
        for left, right in sorted(spans):
            if joined and left <= joined[-1][1]:
                joined[-1] = (joined[-1][0], max(joined[-1][1], right))
            else:
                joined.append((left, right))
        merged[source] = joined
    found = 0
    for anchor in anchors:
        found += any(
            left <= occurrence["start"] and right >= occurrence["end"]
            for occurrence in anchor["occurrences"]
            for left, right in merged.get((occurrence["request_id"], occurrence["position"]), [])
        )
    return found / len(anchors) if anchors else None


def score_case(case, observations):
    # score_job checks all returned source intervals against the untouched source.
    results = score_job(case, observations)
    sources = {m.id: m for m in from_batches(case["id"], case["batches"])}
    for identifier, rows in results.items():
        for row, query, observation in zip(rows, case["queries"], observations[identifier]):
            anchors = query.get("anchors", [])
            row["anchors"] = len(anchors)
            if row["status"] != "ok" or not anchors:
                continue
            hits = decode_hits(observation["hits"], sources)
            used, limited = 0, []
            for hit in hits:
                if used + len(hit.document.text) > 16000:
                    break
                limited.append(hit)
                used += len(hit.document.text)
            row.update(
                anchor20=anchor_fraction(hits[:20], anchors),
                anchor100=anchor_fraction(hits[:100], anchors),
                anchor16k=anchor_fraction(limited, anchors),
            )
    return results


def summarize(rows):
    result = aggregate(rows)
    labelled = [r for r in rows if r.get("anchors") and r["status"] == "ok"]
    strata = defaultdict(list)
    for row in labelled:
        strata[(row["dataset"], row["category"])].append(row)
    for field in ("anchor20", "anchor100", "anchor16k"):
        result[field] = (
            mean(mean(row[field] for row in group) for group in strata.values())
            if strata and not result["errors"] else None
        )
    result["anchor_questions"] = len(labelled)
    return result


def main(data_path, output, split, selected, workers):
    if not 1 <= workers <= 8:
        raise ValueError("Bounded worker count required")
    known = {e.id: e for e in load()}
    selected = sorted(selected or known)
    if set(selected) - set(known):
        raise ValueError("Unknown experiment")
    output.mkdir(parents=True, exist_ok=True)
    raw = data_path.read_bytes()
    cases = [c for c in json.loads(raw)["conversations"] if c["split"] == split]
    if not cases:
        raise ValueError("Empty split")
    expected = {c["id"]: {q["id"] for q in c["queries"]} for c in cases}
    query_ids = [q["id"] for c in cases for q in c["queries"]]
    if len(expected) != len(cases) or len(query_ids) != len(set(query_ids)):
        raise ValueError("Duplicate case or question")
    here = Path(__file__).parent
    files = [here / n for n in [
        "engine.py", "types.py", "metrics.py", "next_run.py", "next_mechanisms.py",
        "recall_run.py", "recall_prepare.py", "recall_mechanisms.py",
    ]]
    code_sha = sha(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files))
    manifest = {
        "created_at": datetime.now(UTC).isoformat(), "split": split,
        "data_sha256": sha(raw), "code_sha256": code_sha, "experiments": selected,
        "expected_query_ids": sorted(query_ids), "questions": len(query_ids),
        "source_only_worker": True, "worker_start_method": "spawn",
        "index_isolation": "fresh per experiment", "workers": workers,
        "baseline": "v0.2.5/a3520ccb", "comparison": "T04/v0.2.8",
        "model_calls": 0, "official_evaluations": 0,
    }
    if split == "validation":
        receipt_file = output / "selection-before-validation.json"
        receipt = json.loads(receipt_file.read_text())
        if receipt["development_sha256"] != sha((output / "summary-development.json").read_bytes()):
            raise ValueError("Development selection hash mismatch")
        if receipt["data_sha256"] != manifest["data_sha256"] or receipt["code_sha256"] != code_sha:
            raise ValueError("Code/data changed since selection")
        if selected != sorted(["T04", *receipt["selected"]]):
            raise ValueError("Validation must use only preselected nominee and frozen T04")
        manifest["selection_sha256"] = sha(receipt_file.read_bytes())
    run_path, result_path = output / f"run-{split}.json", output / f"results-{split}.jsonl"
    if run_path.exists():
        previous = json.loads(run_path.read_text())
        for field in ["split", "data_sha256", "code_sha256", "experiments",
                      "expected_query_ids", "selection_sha256"]:
            if previous.get(field) != manifest.get(field):
                raise ValueError(f"Incompatible resume: {field}")
        manifest = previous
    else:
        run_path.write_text(json.dumps(manifest, indent=2) + "\n")
    latest = latest_records(result_path)
    completed = {key for key, value in latest.items() if key in expected
                 and successful(value, selected, expected[key])}
    pending = [c for c in cases if c["id"] not in completed]
    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = {pool.submit(retrieve_job, source_job(c, selected)): c for c in pending}
        with result_path.open("a") as handle:
            for future in as_completed(futures):
                case = futures[future]
                try:
                    record = {"conversation": case["id"], "split": split,
                              "rows": score_case(case, future.result())}
                except Exception as error:
                    record = {"conversation": case["id"], "split": split,
                              "fatal": type(error).__name__}
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                latest[case["id"]] = record
                if successful(record, selected, expected[case["id"]]):
                    completed.add(case["id"])
                print(json.dumps({"split": split, "complete": len(completed),
                                  "total": len(cases), "fatal": record.get("fatal")}), flush=True)
    fatals, all_rows = [], defaultdict(list)
    for case in cases:
        record = latest.get(case["id"], {})
        if not successful(record, selected, expected[case["id"]]):
            fatals.append(case["id"])
        for identifier in ["BASE", *selected]:
            all_rows[identifier].extend(record.get("rows", {}).get(identifier, []))
    table = [{"id": identifier, **summarize(rows)} for identifier, rows in all_rows.items()]
    summary = {"manifest": manifest, "fatals": fatals, "table": table}
    if fatals:
        # Never report a score from a denominator missing failed cases.
        for row in table:
            for field in ("evidence_score", "recall10", "recall20", "recall100",
                          "anchor20", "anchor100", "anchor16k"):
                row[field] = None
    summary_path = output / f"summary-{split}.json"
    text = json.dumps(summary, indent=2) + "\n"
    if pending or not summary_path.exists():
        summary_path.write_text(text)
    print(json.dumps({"summary": str(summary_path), "fatals": fatals}), flush=True)
    if fatals:
        raise SystemExit("Incomplete study; results invalid")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", required=True, choices=["development", "validation", "regression"])
    parser.add_argument("--experiment", action="append")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    main(args.data, args.output, args.split, args.experiment, args.workers)
