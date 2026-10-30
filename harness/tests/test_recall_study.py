import json
from pathlib import Path
import subprocess
import sys

import pytest

from harness.engine import from_batches, source_documents
from harness.next_run import source_job
from harness.recall_prepare import source_anchors
from harness.recall_run import anchor_fraction
from harness.types import Hit


def example():
    return {
        "messages": [
            {"role": "system", "content": "Use the provided sources."},
            {"role": "user", "content": "The original rental deposit is forty copper coins."},
            {"role": "assistant", "content": "I have noted the rental terms."},
            {"role": "user", "content": "What was the original rental deposit?"},
        ],
        "rubrics": ['The answer must state "forty copper coins" and include "not in source at all".'],
    }


def test_only_verbatim_prior_source_quotes_make_anchors():
    row = example()
    row["rubrics"] = ['Mention "original rental deposit is forty copper coins".',
                      'Do not invent "this phrase is absent from all the prior messages".']
    anchors = source_anchors(row)
    assert len(anchors) == 1
    assert anchors[0]["occurrences"] == [{
        "request_id": "source", "position": 1, "start": 4, "end": 49,
    }]
    assert "rubrics" not in anchors[0]


def test_phrase_present_in_current_question_is_excluded():
    row = example()
    row["rubrics"] = ['Quote "What was the original rental deposit?"']
    row["messages"][1]["content"] = row["messages"][-1]["content"]
    assert source_anchors(row) == []


def test_source_only_payload_contains_no_rubric_gold_or_span_annotations():
    case = {
        "id": "labelled-case", "dataset": "hidden-dataset", "split": "validation",
        "batches": [{"request_id": "r", "session_id": "s", "messages": [{
            "role": "user", "content": "Original source.", "gold": "LEAK",
        }]}],
        "queries": [{"id": "hidden-question-id", "category": "hidden-class",
                     "query": "Original?", "required": [{"request_id": "r", "position": 0}],
                     "anchors": [{"id": "LEAK", "occurrences": []}], "answer": "LEAK"}],
    }
    payload = source_job(case, ["R01"])
    text = json.dumps(payload)
    assert all(term not in text for term in [
        "LEAK", "hidden-question-id", "hidden-class", "hidden-dataset",
        "validation", "required", "anchors", "answer",
    ])
    assert set(payload) == {"case_id", "batches", "queries", "experiments"}


def test_anchor_metric_does_not_count_a_hit_elsewhere_in_same_message():
    messages = from_batches("t", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": "a" * 1040 + "target literal answer" + "z" * 1040}],
    }])
    documents = source_documents("t", messages, size=600)
    anchor = [{"occurrences": [{"request_id": "r", "position": 0, "start": 1040, "end": 1061}]}]
    assert anchor_fraction([Hit(documents[0], 1)], anchor) == 0
    assert anchor_fraction([Hit(d, 1) for d in documents], anchor) == 1


def test_anchor_metric_merges_adjacent_spans_but_not_gaps():
    from dataclasses import replace

    messages = from_batches("t", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": "abcdefghij"}],
    }])
    d = source_documents("t", messages)[0]
    anchor = [{"occurrences": [{"request_id": "r", "position": 0, "start": 2, "end": 8}]}]
    first = Hit(replace(d, text="abcde"), 1)
    second = Hit(replace(d, id="second", text="fghij", offset=5), .9)
    gap = Hit(replace(d, id="gap", text="ghij", offset=6), .8)
    assert anchor_fraction([first, second], anchor) == 1
    assert anchor_fraction([first, gap], anchor) == 0
    assert anchor_fraction([first], []) is None


def test_real_spawn_run_resume_and_validation_gate(tmp_path):
    root = Path(__file__).resolve().parents[2]
    data = tmp_path / "data.json"
    cases = []
    for split in ["development", "validation"]:
        cases.append({
            "id": split, "dataset": "synthetic-only", "split": split,
            "batches": [{"request_id": "r", "session_id": "s", "messages": [{
                "role": "user", "content": "Atlas laboratory costs forty coins.",
            }]}],
            "queries": [{"id": split, "query": "Atlas laboratory costs?", "category": "synthetic",
                         "required": [{"request_id": "r", "position": 0}],
                         "anchors": [{"id": "test", "occurrences": [{
                             "request_id": "r", "position": 0, "start": 0, "end": 16,
                         }]}]}],
        })
    data.write_text(json.dumps({"schema": 1, "conversations": cases}))
    output = tmp_path / "result"
    command = [
        sys.executable, "-m", "harness.recall_run", "--data", str(data), "--output",
        str(output), "--workers", "1", "--experiment", "T04", "--experiment", "R01",
        "--split", "development",
    ]
    subprocess.run(command, cwd=root, check=True, capture_output=True, timeout=40)
    files = ["run-development.json", "results-development.jsonl", "summary-development.json"]
    before = {name: (output / name).read_bytes() for name in files}
    subprocess.run(command, cwd=root, check=True, capture_output=True, timeout=40)
    assert before == {name: (output / name).read_bytes() for name in files}
    summary = json.loads(before["summary-development.json"])
    assert not summary["fatals"]
    assert {r["id"] for r in summary["table"]} == {"BASE", "T04", "R01"}
    assert all(r["anchor100"] == 1 for r in summary["table"])
    invalid = subprocess.run(command[:-1] + ["validation"], cwd=root, check=False,
                             capture_output=True, timeout=40)
    assert invalid.returncode != 0
    assert not (output / "results-validation.jsonl").exists()


@pytest.mark.parametrize("bad", ["single", "large"])
def test_unsuitable_clbench_tasks_are_not_relabelled(bad):
    row = example()
    if bad == "single":
        row["messages"] = row["messages"][:2]
    else:
        row["messages"][-1]["content"] = "x" * 32001
    assert source_anchors(row) == []
