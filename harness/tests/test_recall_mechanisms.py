from dataclasses import replace

import pytest

from harness.engine import Index, evidence_signature, from_batches, source_documents
from harness.recall_mechanisms import (
    content_diversity, facet_diversity, load, message_pool, micro_parent, question_neighbor,
)
from harness.types import Hit


def make_index(texts):
    messages = from_batches("test", [{
        "session_id": "s", "request_id": "r",
        "messages": [{"role": "user", "content": text} for text in texts],
    }])
    return Index(messages, source_documents("test", messages))


def positioned_rows():
    idx = make_index(["alpha beta gamma"] * 8)
    rows = [Hit(d, 1 - i * .01) for i, d in enumerate(idx.documents)]
    # Represent two chunks from one source message for pure ranking tests.
    rows[5] = Hit(replace(rows[0].document, id="second", offset=20,
                          text="alpha beta extra"), rows[5].score)
    return idx, rows


def test_facet_repair_protects_new_terms_in_the_same_long_message():
    idx, rows = positioned_rows()
    try:
        rows[0] = Hit(replace(rows[0].document, text="alpha"), rows[0].score)
        output = facet_diversity("alpha beta", rows, idx._context)
        second = next(h for h in output if h.document.id == "second")
        assert second.score == rows[5].score
        old = facet_diversity("alpha", rows, idx._context)
        assert next(h for h in old if h.document.id == "second").score < rows[5].score
    finally:
        idx.close()


def test_content_repair_does_not_penalize_different_number_or_negation():
    idx, rows = positioned_rows()
    try:
        text = "alpha beta gamma delta epsilon zeta theta kappa lambda payment 40"
        rows[0] = Hit(replace(rows[0].document, text=text), 1)
        for changed in [text.replace("40", "50"), "not " + text]:
            rows[5] = Hit(replace(rows[5].document, text=changed), .95)
            output = content_diversity("alpha payment", rows, idx._context)
            assert next(h for h in output if h.document.id == "second").score == .95
        rows[5] = Hit(replace(rows[5].document, text=text), .95)
        output = content_diversity("alpha payment", rows, idx._context)
        assert next(h for h in output if h.document.id == "second").score == .95 * .75
    finally:
        idx.close()


def test_diversity_never_mixes_identical_text_from_distinct_messages():
    idx = make_index(["alpha beta"] * 8)
    try:
        rows = [Hit(d, 1 - i * .01) for i, d in enumerate(idx.documents)]
        assert content_diversity("alpha", rows, idx._context) == rows
    finally:
        idx.close()


def test_message_pool_acquires_beyond100_and_protects_top20():
    idx = make_index(["alpha " * 25000, "alpha unique distant record"])
    try:
        first = [d for d in idx.documents if d.message_id == "r:0"]
        assert len(first) > 100
        rows = [Hit(d, 1 / (61 + i)) for i, d in enumerate(first)]
        rows.append(Hit(idx.documents[-1], 1 / 1000))
        output = message_pool("alpha", rows, idx._context)
        assert output[:20] == rows[:20]
        assert any(h.document.message_id == "r:1" for h in output)
        assert len(output) == 100
        assert all(a.score >= b.score for a, b in zip(output, output[1:]))
    finally:
        idx.close()


def test_micro_parent_returns_complete_original_chunks():
    idx = make_index([
        "weather gardening music reading " * 30 + "violet quartz laboratory",
        "violet quartz " * 60,
        "laboratory " * 50,
    ])
    try:
        rows = micro_parent("violet quartz laboratory", idx.baseline("violet quartz laboratory"), idx._context)
        assert rows
        assert all(h.document in idx.documents for h in rows)
        assert evidence_signature(rows) == evidence_signature(
            micro_parent("violet quartz laboratory", idx.baseline("violet quartz laboratory"), idx._context)
        )
    finally:
        idx.close()


def test_question_neighbor_can_cross_batch_but_not_session_boundary():
    messages = from_batches("test", [
        {"session_id": "same", "request_id": "a",
         "messages": [{"role": "user", "content": "What is the rental deposit?"}]},
        {"session_id": "same", "request_id": "b",
         "messages": [{"role": "assistant", "content": "Deposit is forty coins."}]},
        {"session_id": "other", "request_id": "c",
         "messages": [{"role": "assistant", "content": "Forty coins elsewhere."}]},
    ])
    idx = Index(messages, source_documents("test", messages))
    try:
        answer = idx.documents[1]
        output = question_neighbor("deposit", [Hit(answer, 1)], idx._context)
        assert {h.document.message_id for h in output} == {"a:0", "b:0"}
        other = question_neighbor("coins", [Hit(idx.documents[2], 1)], idx._context)
        assert len(other) == 1
    finally:
        idx.close()


def test_private_question_is_not_acquired():
    idx = make_index(["What is the private password?", "The password is forty."])
    try:
        output = question_neighbor("password", [Hit(idx.documents[1], 1)], idx._context)
        assert len(output) == 1
    finally:
        idx.close()


@pytest.mark.parametrize("experiment", load(), ids=lambda e: e.id)
def test_experiments_are_deterministic_extractive_and_bounded(experiment):
    idx = make_index(["What is the Atlas venue?", "Atlas venue in Dublin.",
                      "Orion project budget ten.", "Atlas costs twenty."])
    try:
        first = idx.apply("Atlas venue budget", experiment)
        second = idx.apply("Atlas venue budget", experiment)
        assert evidence_signature(first) == evidence_signature(second)
        assert len(first) <= 100
        assert len({(h.document.id, h.document.offset) for h in first}) == len(first)
        assert all(h.document in idx.documents for h in first)
        assert all(a.score >= b.score for a, b in zip(first, first[1:]))
    finally:
        idx.close()


@pytest.mark.parametrize("hook", [facet_diversity, content_diversity, message_pool,
                                  micro_parent, question_neighbor])
def test_empty_corpus_is_safe(hook):
    idx = make_index([])
    try:
        assert hook("anything", [], idx._context) == []
    finally:
        idx.close()
