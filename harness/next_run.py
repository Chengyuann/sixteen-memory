"""Source-only spawned retrieval workers; labels stay in the parent evaluator."""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time
import traceback

from .next_mechanisms import load
from .engine import Index, from_batches, source_documents
from .metrics import aggregate, evaluate
from .types import Document, Hit


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def source_job(conversation, experiment_ids):
    """The only payload allowed into a spawned retrieval process."""
    return {
        "case_id": "source-" + sha(conversation["id"])[:16],
        "batches": [{
            "request_id": b["request_id"], "session_id": b["session_id"],
            **({"timestamp": b["timestamp"]} if "timestamp" in b else {}),
            "messages": [{key: row[key] for key in ("role", "content", "timestamp") if key in row}
                         for row in b["messages"]],
        } for b in conversation["batches"]],
        "queries": [q["query"] for q in conversation["queries"]],
        "experiments": list(experiment_ids),
    }


def encode_hits(hits):
    return [[h.document.message_id, h.document.offset, len(h.document.text),
             sha(h.document.text), h.score] for h in hits]


def retrieve_job(job):
    """No split, category, evidence annotations or answers exist here."""
    experiments = {e.id: e for e in load() if e.id in job["experiments"]}
    messages = from_batches(job["case_id"], job["batches"])
    documents = source_documents(job["case_id"], messages)
    results = {}
    for identifier in ["BASE", *job["experiments"]]:
        experiment = experiments.get(identifier)
        started = time.perf_counter()
        index = None
        try:
            representation = (experiment.document_hook(messages, documents)
                              if experiment and experiment.document_hook else documents)
            index = Index(messages, tuple(representation))
            build_ms = (time.perf_counter() - started) * 1000
            rows = []
            for query in job["queries"]:
                started = time.perf_counter()
                try:
                    hits = index.apply(query, None if experiment and experiment.document_hook else experiment)
                    rows.append({"status": "ok", "hits": encode_hits(hits),
                                 "search_ms": (time.perf_counter() - started) * 1000,
                                 "build_ms": build_ms})
                except Exception as error:
                    rows.append({"status": "error", "error_type": type(error).__name__,
                                 "error": str(error)[:300], "build_ms": build_ms,
                                 "search_ms": (time.perf_counter() - started) * 1000})
            results[identifier] = rows
        except Exception as error:
            results[identifier] = [{"status": "error", "error_type": type(error).__name__,
                                    "error": str(error)[:300], "search_ms": 0,
                                    "build_ms": (time.perf_counter() - started) * 1000}
                                   for _ in job["queries"]]
        finally:
            if index is not None:
                index.close()
    return results


def decode_hits(encoded, sources):
    hits = []
    for i, (message_id, offset, length, checksum, score) in enumerate(encoded):
        m = sources[message_id]
        if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length <= 0:
            raise ValueError("Invalid source interval")
        text = m.text[offset:offset + length]
        if len(text) != length or sha(text) != checksum:
            raise ValueError("Returned source interval/checksum mismatch")
        hits.append(Hit(Document(f"result-{i}", m.id, m.session_id, m.request_id, m.position,
                                 offset, text, m.role, m.timestamp, m.ordinal, text), score))
    return hits


def score_job(conversation, results):
    """Only the parent evaluator can access annotations."""
    sources = {m.id: m for m in from_batches(conversation["id"], conversation["batches"])}
    expected = len(conversation["queries"])
    output = {}
    for identifier, observations in results.items():
        if len(observations) != expected:
            raise ValueError("Missing query observation")
        rows = []
        for position, (q, observation) in enumerate(zip(conversation["queries"], observations)):
            identity = {"id": q["id"], "dataset": conversation["dataset"],
                        "category": q["category"], "split": conversation["split"]}
            if observation["status"] != "ok":
                rows.append({**identity, **observation, "answerable": bool(q.get("required")),
                             "characters": 0, "forbidden_hits": 0, "abstain_correct": None,
                             "triggered": False})
                continue
            hits = decode_hits(observation["hits"], sources)
            question = {**q, "required": [
                {**r, "source": sources[f'{r["request_id"]}:{r["position"]}'].text}
                for r in q.get("required", [])
            ]}
            base = results["BASE"][position]
            signature = [h[:4] for h in observation["hits"]]
            baseline_signature = [h[:4] for h in base.get("hits", [])]
            rows.append({**identity, **evaluate(hits, question), "status": "ok",
                         "search_ms": observation["search_ms"], "build_ms": observation["build_ms"],
                         "triggered": signature != baseline_signature,
                         "evidence_signature_sha256": sha(json.dumps(signature))})
        output[identifier] = rows
    return output


def successful(record, experiment_ids, expected_ids):
    if record.get("fatal") or set(record.get("rows", {})) != {"BASE", *experiment_ids}:
        return False
    return all(
        {r["id"] for r in rows} == expected_ids and len(rows) == len(expected_ids)
        and all(r["status"] == "ok" for r in rows)
        for rows in record["rows"].values())


def latest_records(result_path):
    latest = {}
    if result_path.exists():
        for line in result_path.read_text().splitlines():
            record = json.loads(line)
            latest[record["conversation"]] = record
    return latest


def main(data_path, output, splits, workers, experiment_ids):
    output.mkdir(parents=True, exist_ok=True)
    data_text = data_path.read_text()
    data = json.loads(data_text)
    experiments = load()
    known = {e.id for e in experiments}
    selected = sorted(experiment_ids or known)
    if set(selected) - known:
        raise ValueError("Unknown experiments")
    conversations = [c for c in data["conversations"] if c["split"] in splits]
    if not conversations:
        raise ValueError("No conversations for split")
    expected = {c["id"]: {q["id"] for q in c["queries"]} for c in conversations}
    all_ids = [q["id"] for c in conversations for q in c["queries"]]
    if len(set(all_ids)) != len(all_ids) or len(expected) != len(conversations):
        raise ValueError("Duplicate case/query identity")
    result_path = output / ("results-" + "-".join(sorted(splits)) + ".jsonl")
    latest = latest_records(result_path)
    completed = {key for key, record in latest.items() if key in expected
                 and successful(record, selected, expected[key])}
    pending = [c for c in conversations if c["id"] not in completed]
    code_files = [Path(__file__).parent / name for name in
                  ("engine.py", "metrics.py", "types.py", "run.py", "catalog.py", "next_run.py", "next_mechanisms.py")]
    code_files += sorted((Path(__file__).parent / "directions").glob("*.py"))
    code_hash = sha(b"".join(str(p.relative_to(Path(__file__).parent)).encode()
                            + b"\0" + p.read_bytes() for p in code_files))
    manifest = {
        "data_sha256": sha(data_text), "splits": sorted(splits), "experiments": selected,
        "workers": workers, "conversations": len(conversations), "pending_at_start": len(pending),
        "expected_queries": len(all_ids), "expected_query_ids": sorted(all_ids),
        "baseline": "v0.2.5/a3520ccb7843156e2dd51e92e29caf0f6dafafa4",
        "official_evaluations": 0, "model_calls": 0, "code_sha256": code_hash,
        "worker_start_method": "spawn", "worker_payload": "source_messages_and_queries_only",
        "index_isolation": "one_index_per_experiment", "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if "validation" in splits:
        receipt_path = output / "selection-before-validation.json"
        receipt = json.loads(receipt_path.read_text())
        if sha((output / "summary-development.json").read_bytes()) != receipt["development_sha256"]:
            raise ValueError("Selection receipt development hash mismatch")
        manifest["selection_sha256"] = sha(receipt_path.read_bytes())
    run_path = output / ("run-" + "-".join(sorted(splits)) + ".json")
    if run_path.exists():
        previous = json.loads(run_path.read_text())
        for field in ("data_sha256", "splits", "experiments", "code_sha256",
                      "expected_query_ids", "selection_sha256"):
            if previous.get(field) != manifest.get(field):
                raise ValueError(f"Incompatible resume: {field}")
        # A no-op resume must not invalidate a selection bound to this manifest.
        manifest = previous
    else:
        run_path.write_text(json.dumps(manifest, indent=2) + "\n")
    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=multiprocessing.get_context("spawn")) as executor:
        futures = {executor.submit(retrieve_job, source_job(c, selected)): c for c in pending}
        with result_path.open("a") as handle:
            for future in as_completed(futures):
                c = futures[future]
                try:
                    record = {"conversation": c["id"], "split": c["split"],
                              "rows": score_job(c, future.result())}
                except Exception as error:
                    record = {"conversation": c["id"], "split": c["split"],
                              "fatal": type(error).__name__, "detail": str(error)[:500],
                              "trace": traceback.format_exc(limit=5)}
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                latest[c["id"]] = record
                if successful(record, selected, expected[c["id"]]):
                    completed.add(c["id"])
                print(json.dumps({"complete": len(completed), "total": len(conversations),
                                  "conversation": c["id"], "fatal": record.get("fatal")}), flush=True)
    all_rows = defaultdict(list)
    fatals = []
    for c in conversations:
        record = latest.get(c["id"], {"conversation": c["id"], "fatal": "missing"})
        if not successful(record, selected, expected[c["id"]]):
            fatals.append({"conversation": c["id"], "fatal": record.get("fatal", "experiment_error")})
        for identifier in ["BASE", *selected]:
            present = {r["id"]: r for r in record.get("rows", {}).get(identifier, [])}
            for q in c["queries"]:
                all_rows[identifier].append(present.get(q["id"], {
                    "id": q["id"], "status": "error", "error_type": "MissingObservation",
                    "dataset": c["dataset"], "category": q["category"], "split": c["split"]}))
    baseline_by_id = {r["id"]: r for r in all_rows["BASE"] if r["status"] == "ok"}
    descriptions = {"BASE": {"id": "BASE", "direction": "BASE", "name": "v0.2.5"}}
    descriptions.update({e.id: e.description() for e in experiments})
    table = []
    for identifier in ["BASE", *selected]:
        rows = all_rows[identifier]
        summary = aggregate(rows)
        deltas = [r["recall20"] - baseline_by_id[r["id"]]["recall20"] for r in rows
                  if r["status"] == "ok" and r["id"] in baseline_by_id and r["answerable"]]
        table.append({**summary, **descriptions[identifier],
                      "improved": sum(d > 1e-12 for d in deltas),
                      "regressed": sum(d < -1e-12 for d in deltas),
                      "unchanged": sum(abs(d) <= 1e-12 for d in deltas)})
    summary_path = output / ("summary-" + "-".join(sorted(splits)) + ".json")
    summary_path.write_text(json.dumps({"manifest": manifest, "fatals": fatals, "table": table},
                                       ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"summary": str(summary_path), "rows": len(table), "fatals": len(fatals)}))
    if fatals:
        raise SystemExit("Incomplete results are invalid; resume failures or preserve this run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", action="append", required=True)
    parser.add_argument("--workers", type=int, default=max(1, min(8, os.cpu_count() or 1)))
    parser.add_argument("--experiment", action="append")
    args = parser.parse_args()
    main(args.data, args.output, set(args.split), args.workers, args.experiment)
