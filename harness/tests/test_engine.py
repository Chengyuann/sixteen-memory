from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from types import MappingProxyType

import pytest

from harness.engine import ANAPHORIC, OVERVIEW, RULE, Index, from_batches, source_documents, tokens
from harness.metrics import evaluate
from harness.types import Experiment, Hit


def fixture():
    batches = [{"request_id": "r", "session_id": "s", "messages": [
        {"role": "user", "content": "Where is Project Atlas?"},
        {"role": "assistant", "content": "It is in Dublin."},
        {"role": "user", "content": "Private unrelated note."},
    ]}]
    messages = from_batches("c", batches)
    return messages, source_documents("c", messages)


def test_baseline_retrieves_anaphoric_answer_and_original_substrings():
    messages, documents = fixture()
    index = Index(messages, documents)
    hits = index.apply("Project Atlas")
    assert [h.document.message_id for h in hits][:2] == ["r:0", "r:1"]
    assert all(messages[int(h.document.message_id.split(":")[1])].text[
               h.document.offset:h.document.offset + len(h.document.text)] == h.document.text
               for h in hits)
    index.close()


def test_output_provenance_is_checked_before_top100():
    messages, documents = fixture()
    index = Index(messages, documents)
    broken = replace(documents[0], text="fabricated")
    experiment = Experiment("D01.01", "d", "n", "h", "m", "l",
                            output_hook=lambda q, hits, ctx: [Hit(broken, 1), *hits] * 60)
    with pytest.raises(ValueError, match="Non-extractive"):
        index.apply("Atlas", experiment)
    index.close()


def test_message_turn_recall_and_source_coverage_are_separate():
    messages, documents = fixture()
    short = replace(documents[1], id="short", text="It", index_text="It")
    metrics = evaluate([Hit(short, 1)], {
        "required": [{"request_id": "r", "position": 1, "source": messages[1].text}],
    })
    assert metrics["recall100"] == 1
    assert metrics["source_coverage100"] < .2


@pytest.mark.parametrize("hook_name", ["query_hook", "retrieval_hook", "output_hook"])
def test_hooks_receive_read_only_context_without_index_state(hook_name):
    messages, documents = fixture()
    index = Index(messages, documents)
    try:
        expected = index.apply("Atlas")
        observed = []

        def inspect_context(ctx):
            observed.append(ctx)
            assert ctx is not index
            assert is_dataclass(ctx) and ctx.__dataclass_params__.frozen
            for name in ("connection", "cache", "_cache", "index", "_index",
                         "by_id", "by_message", "close", "apply", "baseline", "__dict__"):
                assert not hasattr(ctx, name)
            for item in fields(ctx):
                with pytest.raises(FrozenInstanceError):
                    setattr(ctx, item.name, None)
            for name in ("messages", "documents"):
                sequence = getattr(ctx, name)
                assert isinstance(sequence, tuple)
                with pytest.raises(TypeError):
                    sequence[0] = None
                with pytest.raises(FrozenInstanceError):
                    sequence[0].text = "tampered"
            for name in ("message_documents", "session_documents", "idf", "doc_tokens"):
                mapping = getattr(ctx, name)
                assert isinstance(mapping, MappingProxyType)
                key = next(iter(mapping))
                with pytest.raises(TypeError):
                    mapping[key] = None
                with pytest.raises(TypeError):
                    del mapping[key]
            for name in ("message_documents", "session_documents"):
                sequence = next(iter(getattr(ctx, name).values()))
                assert isinstance(sequence, tuple)
                with pytest.raises(TypeError):
                    sequence[0] = None
            token_set = ctx.doc_tokens[documents[0].id]
            assert isinstance(token_set, frozenset)
            with pytest.raises(AttributeError):
                token_set.add("tampered")
            assert ctx.lexical.__self__ is ctx
            original_hits = ctx.lexical("Atlas", limit=2)
            returned_hits = ctx.lexical("Atlas", limit=2)
            returned_hits.clear()
            assert ctx.lexical("Atlas", limit=2) == original_hits
            with pytest.raises(FrozenInstanceError):
                original_hits[0].score = 0
            with pytest.raises(ValueError, match="Bounded lexical"):
                ctx.lexical("Atlas", limit=1001)
            terms = ctx.tokens("Atlas")
            terms.clear()
            assert ctx.tokens("Atlas") == ["atlas"]
            assert ctx.focused("Where is Atlas?") == "atlas"

        def query_hook(query, ctx):
            inspect_context(ctx)
            return [(query, 2)]

        def hits_hook(query, hits, ctx):
            inspect_context(ctx)
            return hits

        hook = query_hook if hook_name == "query_hook" else hits_hook
        experiment = Experiment("D01.01", "d", "n", "h", "m", "l", **{hook_name: hook})
        assert index.apply("Atlas", experiment) == expected
        assert len(observed) == 1
        assert index.apply("Atlas") == expected
        assert index.connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 3
    finally:
        index.close()


@pytest.mark.parametrize("different_ids", [False, True])
def test_output_deduplicates_source_spans_before_top100(different_ids):
    messages = from_batches("c", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": f"Atlas {i}."} for i in range(101)],
    }])
    documents = source_documents("c", messages)
    index = Index(messages, documents)
    try:
        first = Hit(documents[0], 1)
        copies = [
            Hit(replace(documents[0], id=f"clone-{i}") if different_ids else documents[0], 10)
            for i in range(105)
        ]
        remaining = [Hit(d, 1) for d in documents[1:]]
        experiment = Experiment(
            "D09.01", "d", "n", "h", "m", "l",
            output_hook=lambda q, hits, ctx: [first, *copies, *remaining],
        )
        result = index.apply("Atlas", experiment)
        assert result == [first, *remaining[:99]]
        assert index.apply("Atlas", baseline_hits=[first, *copies, *remaining]) == result
    finally:
        index.close()


@pytest.mark.parametrize("failure", ["text", "provenance", "score"])
def test_invalid_output_after_top100_is_checked_even_when_source_is_duplicate(failure):
    messages = from_batches("c", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": f"Atlas {i}."} for i in range(101)],
    }])
    documents = source_documents("c", messages)
    index = Index(messages, documents)
    try:
        if failure == "text":
            invalid = Hit(replace(documents[0], text="fabricated"), 1)
            error = "Non-extractive"
        elif failure == "provenance":
            invalid = Hit(replace(documents[0], id="clone", session_id="other"), 1)
            error = "Changed provenance"
        else:
            invalid = Hit(replace(documents[0], id="clone"), float("nan"))
            error = "Non-finite"
        experiment = Experiment(
            "D09.01", "d", "n", "h", "m", "l",
            output_hook=lambda q, hits, ctx: [*[Hit(d, 1) for d in documents], invalid],
        )
        with pytest.raises(ValueError, match=error):
            index.apply("Atlas", experiment)
    finally:
        index.close()


def test_source_identity_preserves_distinct_messages_offsets_and_lengths():
    messages = from_batches("c", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": "Atlas Atlas"},
                     {"role": "assistant", "content": "Atlas Atlas"}],
    }])
    documents = source_documents("c", messages)
    distinct = [
        documents[0], documents[1],
        replace(documents[0], id="first-word", text="Atlas"),
        replace(documents[0], id="second-word", offset=6, text="Atlas"),
    ]
    hits = [Hit(d, 1) for d in distinct]
    index = Index(messages, documents)
    try:
        assert index.apply("Atlas", baseline_hits=hits) == hits
    finally:
        index.close()


def test_index_still_rejects_duplicate_document_ids():
    messages, documents = fixture()
    with pytest.raises(ValueError, match="Duplicate document identity"):
        Index(messages, (*documents, documents[0]))


@pytest.mark.parametrize(("pattern", "text", "expected"), [
    (OVERVIEW, "summary\u4e2d\u6587", True),
    (OVERVIEW, "\u4e2d\u6587SUMMARY", True),
    (OVERVIEW, "summary\u00e9", True),
    (OVERVIEW, "summary_word", False),
    (OVERVIEW, "xsummary", False),
    (OVERVIEW, "x\u603b\u7ed3x", True),
    (RULE, "rules\u4e2d\u6587", True),
    (RULE, "\u4e2d\u6587RULES", True),
    (RULE, "rules\u00e9", True),
    (RULE, "rules_word", False),
    (RULE, "xrules", False),
    (RULE, "x\u89c4\u5219x", True),
    (ANAPHORIC, "It\u4e2d\u6587", True),
    (ANAPHORIC, "YES\u4e2d\u6587", True),
    (ANAPHORIC, "it\u00e9", True),
    (ANAPHORIC, "it_word", False),
    (ANAPHORIC, "item", False),
    (ANAPHORIC, "\u4e2d\u6587it", False),
])
def test_english_boundaries_match_js_with_cjk_literals_intact(pattern, text, expected):
    assert bool(pattern.search(text)) is expected


def test_unicode_overview_and_rule_intent_reach_baseline_retrieval():
    messages, documents = fixture()
    index = Index(messages, documents)
    try:
        assert index.apply("absent") == []
        assert {h.document.id for h in index.apply("summary\u4e2d\u6587")} == {
            d.id for d in documents
        }
        assert documents[2].id not in {h.document.id for h in index.apply("Atlas")}
        assert documents[2].id in {h.document.id for h in index.apply("Atlas rules\u4e2d\u6587")}
    finally:
        index.close()


def test_anaphoric_answer_followed_by_cjk_keeps_question_context():
    messages = from_batches("c", [{
        "request_id": "r", "session_id": "s",
        "messages": [{"role": "user", "content": "Where is Project Atlas?"},
                     {"role": "assistant", "content": "It\u4e2d\u6587"}],
    }])
    documents = source_documents("c", messages)
    assert documents[1].context == messages[0].text
    index = Index(messages, documents)
    try:
        assert documents[1].id in {h.document.id for h in index.apply("Atlas")}
    finally:
        index.close()


@pytest.mark.parametrize(("text", "expected"), [
    ("A_\u00b2\u24b6\u2163e\u0301\u4e2d", ["a", "\u00b2", "\u2173e", "\u4e2d"]),
    ("\u01c5\u02b0\u05d0\u0663\u2163\u00b2", ["\u01c6\u02b0\u05d0\u0663\u2173\u00b2"]),
    ("a\u0301b\u0903c\u20ddd_e\u200cf", ["a", "b", "c", "d", "e", "f"]),
    ("\u4e2d\u6587\U00010400\U00020000", ["\u4e2d", "\u6587", "\U00010428\U00020000"]),
    ("_\u0301\u0903\u20dd\u200c\U0001f600", []),
])
def test_tokens_use_unicode_letter_and_number_categories(text, expected):
    assert tokens(text) == expected
