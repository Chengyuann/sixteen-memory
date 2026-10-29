import re

import pytest

from harness.directions import d09_evidence
from harness.directions.d09_evidence import experiments
from harness.engine import Index
from harness.types import Document, Hit, Message


class StubContext:
    def __init__(self, messages, documents):
        self.messages = tuple(messages)
        self.documents = tuple(documents)
        self.message_documents = {}
        for document in documents:
            self.message_documents.setdefault(document.message_id, []).append(document)
        self.message_documents = {
            key: tuple(value) for key, value in self.message_documents.items()
        }
        self.session_documents = {}
        for document in documents:
            self.session_documents.setdefault(document.session_id, []).append(document)
        self.session_documents = {
            key: tuple(value) for key, value in self.session_documents.items()
        }
        self.idf = {}
        self.doc_tokens = {
            document.id: frozenset(self.tokens(document.text))
            for document in documents
        }

    def tokens(self, text):
        return re.findall(r"\w+", text.lower(), re.UNICODE)

    def focused(self, text):
        return " ".join(
            token for token in self.tokens(text)
            if token not in {"the", "a", "an", "and", "or"}
        )

    def lexical(self, text, limit=300):
        return [Hit(document, 1.0) for document in self.documents[:limit]]


def message(identifier, text, *, timestamp=0, ordinal=0):
    return Message(
        id=identifier,
        request_id=f"r-{identifier}",
        session_id=f"s-{identifier}",
        position=ordinal,
        role="assistant",
        text=text,
        timestamp=timestamp,
        ordinal=ordinal,
    )


def document(
    identifier,
    source,
    *,
    message_id=None,
    session_id=None,
    start=0,
    end=None,
    timestamp=0,
    ordinal=0,
):
    end = len(source) if end is None else end
    message_id = message_id or f"m-{identifier}"
    session_id = session_id or f"s-{identifier}"
    text = source[start:end]
    return Document(
        id=identifier,
        message_id=message_id,
        session_id=session_id,
        request_id=f"r-{message_id}",
        position=ordinal,
        offset=start,
        text=text,
        role="assistant",
        timestamp=timestamp,
        ordinal=ordinal,
        index_text=text,
        context="",
    )


def context_for(documents, sources):
    messages = [
        message(
            message_id,
            text,
            timestamp=next(
                (item.timestamp for item in documents if item.message_id == message_id), 0
            ),
        )
        for message_id, text in sources.items()
    ]
    return StubContext(messages, documents)


def run(experiment_id, query, documents, sources=None):
    sources = sources or {item.message_id: item.text for item in documents}
    ctx = context_for(documents, sources)
    hits = [Hit(item, float(len(documents) - index)) for index, item in enumerate(documents)]
    experiment = {item.id: item for item in experiments()}[experiment_id]
    return experiment.output_hook(query, hits, ctx)


def assert_source_provenance(output, sources):
    for hit in output:
        document = hit.document
        source = sources[document.message_id]
        assert document.text == source[document.offset:document.offset + len(document.text)]
        assert document.index_text == document.text


def test_catalog_has_ten_distinct_single_hook_experiments():
    catalog = experiments()
    assert [item.id for item in catalog] == [f"D09.{index:02d}" for index in range(1, 11)]
    assert len({item.name for item in catalog}) == 10
    assert all(item.direction == "D09" and item.output_hook for item in catalog)
    assert all(
        not item.query_hook and not item.document_hook and not item.retrieval_hook
        for item in catalog
    )


def test_exact_duplicates_keep_first_ranked_hit():
    documents = [document("one", "same"), document("two", "same"), document("three", "other")]
    assert [hit.document.id for hit in run("D09.01", "", documents)] == ["one", "three"]


def test_overlap_subtraction_emits_only_unseen_source_coordinates():
    source = "0123456789"
    documents = [
        document("first", source, message_id="m", start=0, end=6),
        document("second", source, message_id="m", start=4, end=10),
    ]
    sources = {"m": source}

    output = run("D09.02", "", documents, sources)

    assert [(hit.document.offset, hit.document.text) for hit in output] == [
        (0, "012345"),
        (6, "6789"),
    ]
    assert_source_provenance(output, sources)


@pytest.mark.parametrize(
    ("experiment_id", "source", "expected_text", "expected_offset"),
    [
        (
            "D09.03",
            "Noise only. Red and blue are together. Tail.",
            "Red and blue are together.",
            12,
        ),
        (
            "D09.04",
            "Noise only.\n\nRed and blue are together.\n\nTail.",
            "Red and blue are together.",
            13,
        ),
        (
            "D09.05",
            "Noise. Red is here. Blue is next. Tail.",
            "Red is here. Blue is next.",
            7,
        ),
    ],
)
def test_extractive_mechanisms_choose_exact_original_spans(
    experiment_id, source, expected_text, expected_offset
):
    documents = [document("source", source, message_id="m")]
    sources = {"m": source}

    output = run(experiment_id, "red blue", documents, sources)

    assert output[0].document.text == expected_text
    assert output[0].document.offset == expected_offset
    assert output[0].document.id.startswith(f"{experiment_id}:")
    assert_source_provenance(output, sources)


def test_sentence_extraction_uses_unicode_code_point_offsets():
    source = "\u524d\u7f00\U0001f642\u3002Red blue."
    documents = [document("source", source, message_id="m")]
    output = run("D09.03", "red blue", documents, {"m": source})
    assert output[0].document.offset == len("\u524d\u7f00\U0001f642\u3002")
    assert output[0].document.text == "Red blue."


def test_independent_support_keeps_distinct_messages_for_each_term():
    documents = [
        document("a1", "red red", message_id="a"),
        document("a2", "red red", message_id="a"),
        document("b1", "red", message_id="b"),
        document("c1", "red", message_id="c"),
        document("d1", "blue", message_id="d"),
    ]
    output = run("D09.06", "red blue", documents)
    assert [hit.document.id for hit in output] == ["a1", "b1", "d1"]


def test_lexical_diversity_advances_a_distinct_source():
    documents = [
        document("red-one", "red red"),
        document("red-two", "red red"),
        document("blue", "blue"),
    ]
    output = run("D09.07", "", documents)
    assert [hit.document.id for hit in output] == ["red-one", "blue", "red-two"]


def test_chronological_presentation_places_unknown_time_last():
    documents = [
        document("late", "late", timestamp=30),
        document("unknown", "unknown", timestamp=None),
        document("early", "early", timestamp=10),
    ]
    output = run("D09.08", "", documents)
    assert [hit.document.id for hit in output] == ["early", "late", "unknown"]


def test_budgeted_coverage_prefers_complementary_evidence(monkeypatch):
    monkeypatch.setattr(d09_evidence, "CHARACTER_BUDGET", 12)
    documents = [
        document("red-long", "red xxxxxx"),
        document("red-short", "red"),
        document("blue", "blue"),
        document("green", "green"),
    ]
    output = run("D09.09", "red blue green", documents)
    assert [hit.document.id for hit in output] == ["red-short", "blue", "green"]
    assert sum(len(hit.document.text) for hit in output) <= 12


def test_source_blocks_restore_offsets_inside_first_seen_message_order():
    source_a = "aaa bbb"
    source_b = "ccc"
    documents = [
        document("a-late", source_a, message_id="a", start=4),
        document("b", source_b, message_id="b"),
        document("a-early", source_a, message_id="a", start=0, end=3),
    ]
    output = run("D09.10", "", documents, {"a": source_a, "b": source_b})
    assert [hit.document.id for hit in output] == ["a-early", "a-late", "b"]


def test_all_organizers_are_deterministic_and_preserve_original_sources():
    sources = {
        "a": "Noise. Red is here. Blue is next.",
        "b": "Red and blue are together.",
        "c": "Red and blue are together.",
    }
    documents = [
        document("a-wide", sources["a"], message_id="a", session_id="one"),
        document("a-overlap", sources["a"], message_id="a", session_id="one", start=7),
        document("b", sources["b"], message_id="b", session_id="two", timestamp=1),
        document("c", sources["c"], message_id="c", session_id="three", timestamp=None),
    ]
    ctx = context_for(documents, sources)
    hits = [Hit(item, float(4 - index)) for index, item in enumerate(documents)]

    for experiment in experiments():
        first = experiment.output_hook("red blue", hits, ctx)
        second = experiment.output_hook("red blue", hits, ctx)
        assert first == second
        assert_source_provenance(first, sources)
        assert all(hit.document.message_id in sources for hit in first)


def test_counterexamples_document_expected_information_loss(monkeypatch):
    duplicate_events = [
        document("old", "status changed", timestamp=1),
        document("new", "status changed", timestamp=2),
    ]
    assert len(run("D09.01", "", duplicate_events)) == 1

    monkeypatch.setattr(d09_evidence, "CHARACTER_BUDGET", 5)
    oversized = [document("large", "red blue")]
    assert run("D09.09", "red blue", oversized) == []

    no_sentence_match = [document("none", "unrelated text")]
    output = run("D09.03", "red", no_sentence_match)
    assert output[0].document is no_sentence_match[0]


def test_overlap_counterexample_can_split_a_word_but_never_merge_messages():
    source = "prefixsuffix"
    documents = [
        document("prefix", source, message_id="a", end=6),
        document("whole", source, message_id="a"),
        document("other", source, message_id="b"),
    ]
    output = run("D09.02", "", documents, {"a": source, "b": source})
    assert [hit.document.text for hit in output] == ["prefix", "suffix", source]
    assert_source_provenance(output, {"a": source, "b": source})


def test_sentence_counterexample_can_drop_a_following_qualifier():
    source = "Red blue release is ready. Except on weekends."
    output = run("D09.03", "red blue", [document("one", source)])
    assert output[0].document.text == "Red blue release is ready."
    assert "Except" not in output[0].document.text


def test_paragraph_counterexample_cannot_split_a_single_paragraph():
    source = "Noise. Red blue. More unrelated text."
    item = document("one", source)
    assert run("D09.04", "red blue", [item])[0].document is item


def test_covering_passage_counterexample_retains_distant_intervening_text():
    source = "Red is first. " + "Unrelated sentence. " * 20 + "Blue is last."
    item = document("one", source)
    assert run("D09.05", "red blue", [item])[0].document is item


def test_independent_support_counterexample_loses_third_message_details():
    documents = [
        document("one", "red day one"),
        document("two", "red day two"),
        document("three", "red day three with a new exception"),
    ]
    output = run("D09.06", "red", documents)
    assert [hit.document.id for hit in output] == ["one", "two"]


def test_diversity_counterexample_promotes_an_unrelated_distractor():
    documents = [
        document("one", "red red"),
        document("two", "red red"),
        document("distractor", "unrelated"),
    ]
    output = run("D09.07", "red", documents)
    assert [hit.document.id for hit in output[:2]] == ["one", "distractor"]


def test_chronology_counterexample_can_displace_top_ranked_recent_evidence():
    documents = [
        document("recent", "red", timestamp=100),
        document("old", "unrelated", timestamp=1),
    ]
    output = run("D09.08", "red", documents)
    assert output[0].document.id == "old"


def test_message_blocks_counterexample_advances_low_ranked_fragments():
    source = "unrelated red"
    documents = [
        document("match", source, message_id="a", start=10),
        document("strong", "red", message_id="b"),
        document("weak", source, message_id="a", end=9),
    ]
    output = run("D09.10", "red", documents, {"a": source, "b": "red"})
    assert [hit.document.id for hit in output] == ["weak", "match", "strong"]


def test_overlap_subtraction_keeps_disjoint_residuals_with_composed_offsets():
    source = "XXabcdefghijklYY"
    documents = [
        document("middle", source, message_id="m", start=6, end=10),
        document("container", source, message_id="m", start=2, end=14),
        document("covered", source, message_id="m", start=3, end=5),
    ]
    output = run("D09.02", "", documents, {"m": source})
    assert [(hit.document.offset, hit.document.text) for hit in output] == [
        (6, "efgh"), (2, "abcd"), (10, "ijkl")
    ]
    assert len({hit.document.id for hit in output}) == 3
    assert_source_provenance(output, {"m": source})


@pytest.mark.parametrize("experiment_id", ["D09.03", "D09.04", "D09.05"])
def test_extraction_composes_existing_offset_and_clears_old_index_context(experiment_id):
    from dataclasses import replace

    source = "\U0001f642Prefix. Noise.\r\n\r\nRed blue.\r\n\r\nTail."
    original = document("original", source, message_id="m", start=9)
    original = replace(original, index_text="external index", context="old context")
    output = run(experiment_id, "red blue", [original], {"m": source})
    result = output[0].document
    assert result.offset == source.index("Red blue.")
    assert result.text == "Red blue."
    assert result.index_text == result.text and result.context == ""
    assert original.index_text == "external index" and original.context == "old context"
    for field in ("message_id", "session_id", "request_id", "position", "role",
                  "timestamp", "ordinal"):
        assert getattr(result, field) == getattr(original, field)


@pytest.mark.parametrize("experiment", experiments(), ids=lambda item: item.id)
def test_empty_input_returns_no_invented_evidence(experiment):
    assert experiment.output_hook("red blue", [], StubContext([], [])) == []


def test_types_expose_no_answers_or_benchmark_metadata():
    context = StubContext([], [])
    assert not hasattr(context, "gold")
    assert not hasattr(context, "answers")
    assert not hasattr(context, "benchmark_id")
    assert Message.__dataclass_fields__.keys() == {
        "id",
        "request_id",
        "session_id",
        "position",
        "role",
        "text",
        "timestamp",
        "ordinal",
    }


def test_real_index_accepts_every_organizer_and_extracted_document():
    messages = (
        Message("m1", "r1", "s1", 0, "assistant", "Noise. Red here. Blue next.", 1, 0),
        Message("m2", "r2", "s2", 0, "assistant", "Red and blue together.", 2, 1),
    )
    documents = (
        Document("d1", "m1", "s1", "r1", 0, 0, messages[0].text, "assistant", 1, 0,
                 messages[0].text, ""),
        Document("d2", "m1", "s1", "r1", 0, 7, messages[0].text[7:], "assistant", 1, 1,
                 messages[0].text[7:], ""),
        Document("d3", "m2", "s2", "r2", 0, 0, messages[1].text, "assistant", 2, 2,
                 messages[1].text, ""),
    )
    index = Index(messages, documents)
    try:
        baseline = [Hit(documents[0], 3.0), Hit(documents[1], 2.0), Hit(documents[2], 1.0)]
        for experiment in experiments():
            output = index.apply("red blue", experiment, baseline)
            assert all(
                index.by_message[hit.document.message_id].text[
                    hit.document.offset:hit.document.offset + len(hit.document.text)
                ] == hit.document.text
                for hit in output
            )
    finally:
        index.close()
