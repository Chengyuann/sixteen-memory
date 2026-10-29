import re

import pytest

from harness.directions.d08_reranking import experiments
from harness.engine import Index
from harness.types import Document, Hit, Message


class StubContext:
    def __init__(self, documents=(), idf=None):
        self.documents = tuple(documents)
        self.messages = ()
        self.message_documents = {}
        self.session_documents = {}
        self.idf = idf or {}
        self.doc_tokens = {
            document.id: frozenset(self.tokens(document.text))
            for document in self.documents
        }

    def tokens(self, text):
        return re.findall(r"\w+", text.lower(), re.UNICODE)

    def focused(self, text):
        return " ".join(
            token for token in self.tokens(text)
            if token not in {"the", "a", "an", "did", "does", "say", "said", "and", "or"}
        )

    def lexical(self, text, limit=300):
        wanted = set(self.tokens(text))
        return [
            Hit(document, float(len(wanted & set(self.tokens(document.text)))))
            for document in self.documents[:limit]
        ]


def document(identifier, text, role="assistant", position=0):
    return Document(
        id=identifier,
        message_id=f"m-{identifier}",
        session_id=f"s-{identifier}",
        request_id=f"r-{identifier}",
        position=position,
        offset=0,
        text=text,
        role=role,
        timestamp=position,
        ordinal=position,
        index_text=text,
        context="",
    )


def run(experiment_id, query, documents, *, idf=None):
    ctx = StubContext(documents, idf=idf)
    hits = [Hit(item, float(len(documents) - index)) for index, item in enumerate(documents)]
    experiment = {item.id: item for item in experiments()}[experiment_id]
    return experiment.retrieval_hook(query, hits, ctx)


def test_catalog_has_ten_distinct_single_hook_experiments():
    catalog = experiments()
    assert [item.id for item in catalog] == [f"D08.{index:02d}" for index in range(1, 11)]
    assert len({item.name for item in catalog}) == 10
    assert all(item.direction == "D08" and item.retrieval_hook for item in catalog)
    assert all(
        not item.query_hook and not item.document_hook and not item.output_hook
        for item in catalog
    )


@pytest.mark.parametrize(
    ("experiment_id", "query", "first", "second", "idf"),
    [
        ("D08.01", "red blue", "red red red", "red blue", None),
        ("D08.02", "red blue", "red filler filler blue", "red blue", None),
        ("D08.03", "common rare", "common common rare", "common rare rare",
         {"common": 1, "rare": 5}),
        ("D08.04", "red blue green", "red green blue", "red blue green", None),
        ("D08.05", "red blue green", "green red blue", "red x blue x green", None),
        ("D08.07", "red blue and green yellow", "red blue green", "red blue green yellow", None),
        ("D08.08", "red blue", "red blue filler filler filler", "red blue", {"red": 1, "blue": 1}),
        ("D08.09", "red rare", "red common common rare", "red rare", None),
        ("D08.10", "red blue", "red is here. blue is elsewhere.", "red and blue are here.", None),
    ],
)
def test_lexical_mechanisms_have_targeted_effect(
    experiment_id, query, first, second, idf
):
    documents = [document("first", first), document("second", second)]
    output = run(experiment_id, query, documents, idf=idf)
    assert [hit.document.id for hit in output] == ["second", "first"]


def test_explicit_role_reranker_only_acts_on_an_attributed_role():
    assistant = document("assistant", "release starts friday", role="assistant")
    user = document("user", "release starts friday", role="user")

    attributed = run("D08.06", "What did the user say about the release?", [assistant, user])
    ambiguous = run("D08.06", "Compare user and assistant release notes", [assistant, user])

    assert [hit.document.id for hit in attributed] == ["user", "assistant"]
    assert [hit.document.id for hit in ambiguous] == ["assistant", "user"]


def test_all_rerankers_are_deterministic_and_preserve_source_objects():
    documents = [
        document("one", "red blue. green follows.", role="assistant"),
        document("two", "green red blue", role="user", position=1),
        document("three", "blue red", role="tool", position=2),
    ]
    ctx = StubContext(documents, idf={"red": 3.0, "blue": 2.0, "green": 1.0})
    hits = [Hit(documents[0], 3.0), Hit(documents[1], 2.0), Hit(documents[2], 1.0)]

    for experiment in experiments():
        first = experiment.retrieval_hook("What did the user say? red blue and green", hits, ctx)
        second = experiment.retrieval_hook("What did the user say? red blue and green", hits, ctx)
        assert first == second
        assert {id(hit.document) for hit in first} == {id(item) for item in documents}
        assert sorted(hit.document.id for hit in first) == ["one", "three", "two"]


def test_minimum_window_counterexample_can_prefer_compact_partial_match():
    complete = document("complete", "red one two three four five blue green")
    partial = document("partial", "red blue")
    output = run("D08.02", "red blue green", [complete, partial])
    assert [hit.document.id for hit in output] == ["partial", "complete"]


def test_coverage_counterexample_does_not_understand_negation():
    denied = document("denied", "red is not blue")
    affirmed = document("affirmed", "red is blue")
    output = run("D08.01", "red blue", [denied, affirmed])
    assert output[0].score == output[1].score
    assert output[0].document is denied


def test_rare_term_balance_counterexample_zeroes_a_missing_literal():
    partial = document("partial", "red blue")
    empty = document("empty", "unrelated")
    output = run("D08.03", "red blue green", [partial, empty], idf={"green": 10})
    assert [hit.score for hit in output] == [0.0, 0.0]


def test_adjacent_pairs_counterexample_does_not_require_a_whole_phrase():
    scattered = document("scattered", "red blue; unrelated; blue green")
    phrase = document("phrase", "red blue green")
    output = run("D08.04", "red blue green", [scattered, phrase])
    assert [hit.score for hit in output] == [1.0, 1.0]
    assert output[0].document is scattered


def test_subsequence_counterexample_ignores_arbitrarily_long_gaps():
    distant = document("distant", "red " + "filler " * 100 + "blue green")
    compact = document("compact", "red blue green")
    output = run("D08.05", "red blue green", [distant, compact])
    assert [hit.score for hit in output] == [1.0, 1.0]
    assert output[0].document is distant


def test_role_counterexamples_do_not_infer_speakers_from_names_or_topics():
    assistant = document("assistant", "release details", role="assistant")
    user = document("user", "release details", role="user")
    for query in (
        "What did Alice say about the release?",
        "What did the user and assistant say about the release?",
        "What did the user say? What did the assistant say?",
    ):
        output = run("D08.06", query, [assistant, user])
        assert [hit.document.id for hit in output] == ["assistant", "user"]


def test_facets_counterexample_cannot_combine_distributed_evidence():
    documents = [document("first", "red blue"), document("second", "green yellow")]
    output = run("D08.07", "red blue and green yellow", documents)
    assert [hit.score for hit in output] == [0.0, 0.0]


def test_cosine_counterexample_is_invariant_to_whole_text_repetition():
    repeated = document("repeated", "red blue red blue")
    compact = document("compact", "red blue")
    output = run("D08.08", "red blue", [repeated, compact])
    assert output[0].score == pytest.approx(output[1].score)
    assert output[0].document is repeated


def test_background_likelihood_counterexample_rewards_repetition_not_truth():
    statement = document("statement", "red filler filler filler")
    repetition = document("repetition", "red red red filler")
    output = run("D08.09", "red", [statement, repetition])
    assert output[0].document is repetition


def test_sentence_colocation_counterexample_misses_clause_negation():
    denied = document("denied", "red is not blue.")
    affirmed = document("affirmed", "red is blue.")
    output = run("D08.10", "red blue", [denied, affirmed])
    assert [hit.score for hit in output] == [1.0, 1.0]
    assert output[0].document is denied


@pytest.mark.parametrize("experiment", experiments(), ids=lambda item: item.id)
def test_empty_inputs_and_repeatable_ties(experiment):
    ctx = StubContext()
    assert experiment.retrieval_hook("red blue and green", [], ctx) == []
    documents = [document("z", "red blue green"), document("a", "red blue green")]
    hits = [Hit(documents[0], 1.0), Hit(documents[1], 100.0)]
    ctx = StubContext(documents)
    output = experiment.retrieval_hook("red blue and green", hits, ctx)
    assert [hit.document.id for hit in output] == ["z", "a"]
    assert hits == [Hit(documents[0], 1.0), Hit(documents[1], 100.0)]


@pytest.mark.parametrize(
    ("experiment_id", "query"),
    [
        ("D08.01", "the and"),
        ("D08.02", "single"),
        ("D08.03", "the"),
        ("D08.04", "single"),
        ("D08.05", "single"),
        ("D08.06", "user assistant topic"),
        ("D08.07", "single facet"),
        ("D08.08", "the"),
        ("D08.09", "the"),
        ("D08.10", "the"),
    ],
)
def test_counterexamples_leave_input_order_unchanged(experiment_id, query):
    documents = [document("first", "alpha"), document("second", "beta")]
    assert [hit.document.id for hit in run(experiment_id, query, documents)] == [
        "first",
        "second",
    ]


def test_types_expose_no_scoring_labels_to_rerankers():
    context = StubContext()
    assert not hasattr(context, "gold")
    assert not hasattr(context, "category")
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


def test_real_index_runs_every_reranker_through_engine_validation():
    messages = (
        Message("m1", "r1", "s1", 0, "user", "Red blue plan?", 1, 0),
        Message("m2", "r1", "s1", 1, "assistant", "Red and blue launch. Green follows.", 2, 1),
        Message("m3", "r2", "s2", 0, "assistant", "Green red blue.", 3, 2),
    )
    documents = tuple(
        Document(
            f"d{index}",
            item.id,
            item.session_id,
            item.request_id,
            item.position,
            0,
            item.text,
            item.role,
            item.timestamp,
            item.ordinal,
            item.text,
            "",
        )
        for index, item in enumerate(messages)
    )
    index = Index(messages, documents)
    try:
        baseline = index.baseline("red blue green")
        for experiment in experiments():
            assert index.apply("red blue green", experiment, baseline)
    finally:
        index.close()
