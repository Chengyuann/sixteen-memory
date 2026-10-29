"""Integrity checks only; these tests never execute experiment algorithms."""

import json
import os
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from harness.diagnostics import (
    CASES_PER_CATEGORY,
    CATEGORIES,
    SYNTHETIC_NOT_BENCHMARK,
    _case,
    _validate_cases,
    _validate_reference,
    cases,
)


def _assert_nonempty_text(value):
    if isinstance(value, dict):
        for item in value.values():
            _assert_nonempty_text(item)
    elif isinstance(value, list):
        for item in value:
            _assert_nonempty_text(item)
    elif isinstance(value, str):
        assert value.strip()


def test_diagnostic_corpus_is_large_balanced_and_synthetic():
    corpus = cases()
    counts = Counter(query["category"] for case in corpus for query in case["queries"])
    assert SYNTHETIC_NOT_BENCHMARK is True
    assert len(corpus) == 120
    assert set(counts) == set(CATEGORIES)
    assert all(count == CASES_PER_CATEGORY for count in counts.values())
    assert all(case["dataset"] == "synthetic" for case in corpus)
    assert all(case["split"] == "diagnostic" for case in corpus)
    assert json.loads(json.dumps(corpus)) == corpus


def test_ids_are_unique_opaque_and_fields_are_not_empty():
    corpus = cases()
    case_ids = [case["id"] for case in corpus]
    query_ids = [query["id"] for case in corpus for query in case["queries"]]
    request_ids = [batch["request_id"] for case in corpus for batch in case["batches"]]
    assert len(case_ids) == len(set(case_ids))
    assert len(query_ids) == len(set(query_ids))
    assert len(request_ids) == len(set(request_ids))
    _assert_nonempty_text(corpus)
    session_sets = []
    for case in corpus:
        assert set(case) == {"id", "dataset", "split", "batches", "queries"}
        assert case["batches"] and case["queries"]
        sessions = {batch["session_id"] for batch in case["batches"]}
        assert all(sessions.isdisjoint(previous) for previous in session_sets)
        session_sets.append(sessions)
        for batch in case["batches"]:
            assert batch["messages"]
            assert re.fullmatch(r"[0-9a-f]{24}", batch["request_id"])
            assert re.fullmatch(r"[0-9a-f]{24}", batch["session_id"])


def test_all_references_resolve_to_exact_message_positions():
    for case in cases():
        messages_by_request = {
            batch["request_id"]: batch["messages"] for batch in case["batches"]
        }
        for query in case["queries"]:
            assert bool(query["required"]) is not query.get("abstain", False)
            reference_sets = []
            for field in ("required", "forbidden"):
                references = query.get(field, [])
                pairs = set()
                for reference in references:
                    assert set(reference) == {"request_id", "position"}
                    request_id, position = reference["request_id"], reference["position"]
                    assert request_id in messages_by_request
                    assert type(position) is int
                    assert 0 <= position < len(messages_by_request[request_id])
                    assert messages_by_request[request_id][position]["content"].strip()
                    pairs.add((request_id, position))
                assert len(pairs) == len(references)
                reference_sets.append(pairs)
            assert reference_sets[0].isdisjoint(reference_sets[1])


def test_cases_are_reproducible_deeply_fresh_and_leave_global_rng_unchanged():
    original_state = random.getstate()
    try:
        random.seed(1919)
        before = random.getstate()
        first = cases()
        assert before == random.getstate()
        random.seed(8181)
        before = random.getstate()
        second = cases()
        assert before == random.getstate()
    finally:
        random.setstate(original_state)

    baseline = json.dumps(second, sort_keys=True, separators=(",", ":"))
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == baseline
    first[0]["batches"][0]["messages"][0]["content"] = "caller mutation"
    first[0]["queries"][0]["required"][0]["position"] = 9999
    assert json.dumps(second, sort_keys=True, separators=(",", ":")) == baseline
    assert json.dumps(cases(), sort_keys=True, separators=(",", ":")) == baseline


def test_reproducibility_across_process_hash_seeds():
    command = (
        "import hashlib, json; from harness.diagnostics import cases; "
        "print(hashlib.sha256(json.dumps(cases(), sort_keys=True, "
        "separators=(',', ':')).encode()).hexdigest())"
    )
    digests = [
        subprocess.check_output(
            [sys.executable, "-B", "-c", command],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            timeout=15,
        ).strip()
        for seed in ("1", "789")
    ]
    assert digests[0] == digests[1]


def test_hard_cases_and_counterexamples_have_real_evidence_structure():
    evidence_counts = Counter()
    temporal = Counter()
    abstention = Counter()
    negative_answers = 0
    for case in cases():
        batch_map = {batch["request_id"]: batch for batch in case["batches"]}
        query = case["queries"][0]
        references = query["required"]
        category = query["category"]
        evidence_counts[len(references)] += 1
        if category == "cross_session_association":
            sessions = {batch_map[ref["request_id"]]["session_id"] for ref in references}
            assert len(sessions) == 2
        if category == "multi_evidence":
            assert len(references) == 3
        if category == "coreference":
            assert {ref["position"] for ref in references} == {0, 1}
        if category == "structured_tables_lists":
            content = batch_map[references[0]["request_id"]]["messages"][0]["content"]
            assert "\n" in content
        if category == "timestamp_history_update":
            timestamps = [batch["timestamp"] for batch in case["batches"]]
            required_time = batch_map[references[0]["request_id"]]["timestamp"]
            temporal["historical" if required_time == min(timestamps) else "current"] += 1
            temporal["out_of_order"] += timestamps != sorted(timestamps)
            for batch in case["batches"]:
                assert type(batch["timestamp"]) is int
                assert batch["messages"][0]["timestamp"] == batch["timestamp"]
            temporal["timestamp_only"] += all(
                batch["messages"][0]["content"].startswith("Venue record")
                for batch in case["batches"]
            )
        if category == "negation":
            source = batch_map[references[0]["request_id"]]["messages"][0]["content"]
            negative_answers += " not " in source
        if category == "abstention":
            abstention[query.get("abstain", False)] += 1
        if category == "privacy_minimal_disclosure":
            assert len(references) == 1 and query["forbidden"]

    assert evidence_counts == {0: 8, 1: 64, 2: 36, 3: 12}
    assert temporal == {
        "historical": 6,
        "current": 6,
        "out_of_order": 6,
        "timestamp_only": 4,
    }
    assert negative_answers == 3
    assert abstention == {True: 8, False: 4}


@pytest.mark.parametrize(
    "reference",
    [
        {"request_id": "missing", "position": 0},
        {"request_id": "r", "position": -1},
        {"request_id": "r", "position": 2},
        {"request_id": "r", "position": True},
        {"request_id": "r", "position": "0"},
        {"request_id": "r", "position": 0, "answer": "unexpected"},
    ],
)
def test_reference_validator_rejects_invalid_coordinates(reference):
    with pytest.raises(ValueError):
        _validate_reference(reference, {"r": 2}, "test")


@pytest.mark.parametrize("field", ["required", "forbidden"])
def test_validator_rejects_duplicate_references(field):
    corpus = cases()
    refs = corpus[0]["queries"][0][field]
    refs.append(dict(refs[0]))
    with pytest.raises(ValueError, match=f"duplicate {field} reference"):
        _validate_cases(corpus)


@pytest.mark.parametrize("mode", ["overlap", "foreign_case"])
def test_validator_rejects_overlapping_and_cross_case_references(mode):
    corpus = cases()
    query = corpus[0]["queries"][0]
    if mode == "overlap":
        query["forbidden"].append(dict(query["required"][0]))
        expected = "overlap"
    else:
        query["required"][0] = dict(corpus[1]["queries"][0]["required"][0])
        expected = "unknown request"
    with pytest.raises(ValueError, match=expected):
        _validate_cases(corpus)


@pytest.mark.parametrize(
    ("query", "answer", "error"),
    [
        ("Is the venue COBALT HALL?", "Cobalt Hall", "answer appears in query"),
        ("Where is the meeting?", "Quartz Lab", "answer missing"),
    ],
)
def test_case_builder_rejects_answer_leakage_and_unsupported_answers(query, answer, error):
    batches = [{
        "request_id": "r",
        "session_id": "s",
        "messages": [{"role": "user", "content": "The meeting is in Cobalt Hall."}],
    }]
    with pytest.raises(ValueError, match=error):
        _case(
            1,
            "lexical_reformulation",
            batches,
            query,
            [{"request_id": "r", "position": 0}],
            answers=(answer,),
        )
