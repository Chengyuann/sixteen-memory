import json
import subprocess
import sys

from harness.metrics import aggregate
from harness.run import latest_records, source_job, successful


def test_worker_payload_has_no_labels_categories_splits_or_other_queries():
    case = {"id": "private", "split": "validation", "dataset": "x",
            "batches": [{"request_id": "r", "session_id": "s",
                         "messages": [{"role": "user", "content": "text", "has_answer": True}]}],
            "queries": [{"id": "q", "query": "where", "answer": "secret",
                         "category": "x", "required": [{"request_id": "r", "position": 0}]}]}
    payload = source_job(case, ["D01.01"])
    text = json.dumps(payload)
    for forbidden in ("secret", "has_answer", "required", "category", "validation", "dataset"):
        assert forbidden not in text
    assert payload["queries"] == ["where"]


def test_partial_scores_are_invalid_not_rescaled():
    assert aggregate([{"status": "error", "id": "missing"}])["evidence_score"] is None


def test_fatal_and_partial_records_are_not_resumable_success():
    assert not successful({"fatal": "ValueError"}, ["D01.01"], {"q"})
    assert not successful({"rows": {"BASE": [{"id": "q", "status": "ok"}]}},
                          ["D01.01"], {"q"})
    record = {"rows": {k: [{"id": "q", "status": "ok"}] for k in ("BASE", "D01.01")}}
    assert successful(record, ["D01.01"], {"q"})
    assert not successful(record, ["D01.01"], {"q", "missing"})


def test_resume_summarizes_latest_attempt(tmp_path):
    file = tmp_path / "results.jsonl"
    records = [{"conversation": "a", "fatal": "old"}, {"conversation": "a", "rows": {}}]
    file.write_text("\n".join(json.dumps(r) for r in records))
    assert latest_records(file) == {"a": records[-1]}


def test_completed_spawned_run_resumes_byte_identically(tmp_path):
    data = tmp_path / "data.json"
    output = tmp_path / "run"
    data.write_text(json.dumps({"conversations": [{
        "id": "c", "dataset": "synthetic", "split": "development",
        "batches": [{"request_id": "r", "session_id": "s", "messages": [
            {"role": "user", "content": "Atlas is in Dublin."}]}],
        "queries": [{"id": "q", "query": "Where is Atlas?", "category": "lookup",
                     "required": [{"request_id": "r", "position": 0}]}],
    }]}))
    command = [sys.executable, "-m", "harness.run", "--data", str(data),
               "--output", str(output), "--split", "development",
               "--workers", "1", "--experiment", "D01.01"]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    original = {path.name: path.read_bytes() for path in output.iterdir()}
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    assert original == {path.name: path.read_bytes() for path in output.iterdir()}
