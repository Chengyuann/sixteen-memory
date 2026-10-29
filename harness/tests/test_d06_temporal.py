from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import re

from harness.directions.d06_temporal import experiments
from harness.types import Document, Hit, Message


def _millis(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


def _doc(
    identifier: str,
    text: str,
    *,
    timestamp: int | None = None,
    offset: int = 0,
    message_id: str | None = None,
) -> Document:
    return Document(
        id=identifier,
        message_id=message_id or f"m-{identifier}",
        session_id="session",
        request_id=f"request-{identifier}",
        position=0,
        offset=offset,
        text=text,
        role="user",
        timestamp=timestamp,
        ordinal=0,
        index_text=text,
        context="source context",
    )


def _hits(*documents: Document) -> list[Hit]:
    return [
        Hit(document=document, score=1.0 - index / 10)
        for index, document in enumerate(documents)
    ]


class FakeContext:
    def __init__(self, documents: tuple[Document, ...], lexical_hits: list[Hit] | None = None):
        self.documents = documents
        self.messages = tuple(
            Message(
                id=document.message_id,
                request_id=document.request_id,
                session_id=document.session_id,
                position=document.position,
                role=document.role,
                text=" " * document.offset + document.text,
                timestamp=document.timestamp,
                ordinal=document.ordinal,
            )
            for document in documents
        )
        self.message_documents = {
            document.message_id: (document,) for document in documents
        }
        self.session_documents = {"session": documents}
        self.idf = {}
        self.doc_tokens = {}
        self._lexical_hits = list(lexical_hits or [])

    def tokens(self, text: str) -> list[str]:
        return re.findall(r"[^\W_]+", text.casefold())

    def focused(self, text: str) -> str:
        return text

    def lexical(self, text: str, limit: int = 300) -> list[Hit]:
        del text
        return self._lexical_hits[:limit]


def _experiment(number: int):
    return experiments()[number - 1]


def _run(number: int, query: str, hits: list[Hit], ctx: FakeContext) -> list[Hit]:
    experiment = _experiment(number)
    hook = experiment.retrieval_hook or experiment.output_hook
    assert hook is not None
    return hook(query, list(hits), ctx)


def test_catalog_has_ten_distinct_single_hook_mechanisms():
    catalog = experiments()

    assert [experiment.id for experiment in catalog] == [
        f"D06.{number:02d}" for number in range(1, 11)
    ]
    assert len({experiment.name for experiment in catalog}) == 10
    assert len({experiment.mechanism for experiment in catalog}) == 10
    assert len({
        experiment.query_hook or experiment.retrieval_hook or experiment.output_hook
        for experiment in catalog
    }) == 10
    for experiment in catalog:
        hooks = (
            experiment.query_hook,
            experiment.document_hook,
            experiment.retrieval_hook,
            experiment.output_hook,
        )
        assert sum(hook is not None for hook in hooks) == 1


def test_timestamp_ordering_preserves_old_and_undated_evidence():
    old = _doc("old", "The old plan.", timestamp=_millis("2024-01-01"))
    new = _doc("new", "The new plan.", timestamp=_millis("2026-01-01"))
    undated = _doc("undated", "A plan without a source date.")
    hits = _hits(old, undated, new)
    ctx = FakeContext((old, new, undated))

    latest = _run(1, "current plan", hits, ctx)
    historical = _run(2, "historical plan", hits, ctx)
    no_history_intent = _run(2, "plan", hits, ctx)

    assert [hit.document.id for hit in latest] == ["new", "old", "undated"]
    assert [hit.document.id for hit in historical] == ["old", "new", "undated"]
    assert [hit.document.id for hit in no_history_intent] == ["old", "undated", "new"]
    assert {hit.document.id for hit in latest} == {"old", "new", "undated"}
    assert _run(1, "latest historical plan", hits, ctx) == hits
    assert _run(1, "latest plan before last year", hits, ctx) == hits
    assert _run(1, "latest plan as of 2024-01-01", hits, ctx) == hits


def test_as_of_cutoff_prioritizes_without_hiding_later_sources():
    before = _doc("before", "Recorded before cutoff.", timestamp=_millis("2025-05-01"))
    after = _doc("after", "Recorded after cutoff.", timestamp=_millis("2025-07-01"))
    unknown = _doc("unknown", "No timestamp.")
    hits = _hits(after, unknown, before)
    ctx = FakeContext((before, after, unknown))

    result = _run(3, "status as of 2025-06-01", hits, ctx)

    assert [hit.document.id for hit in result] == ["before", "unknown", "after"]
    assert _run(3, "status on an unspecified date", hits, ctx) == hits


def test_written_date_and_interval_use_only_explicit_source_text():
    exact = _doc("exact", "The review happened on 2025-06-01.")
    same_year = _doc("year", "The 2025 annual review used another date.")
    interval = _doc("interval", "The policy applied 2025-01-01 to 2025-12-31.")
    unrelated = _doc("other", "The 2024 review.")
    hits = _hits(unrelated, interval, same_year, exact)
    ctx = FakeContext((exact, same_year, interval, unrelated))

    assert _run(4, "What happened on 2025-06-01?", hits, ctx)[0].document.id == "exact"
    assert _run(6, "Policy on 2025-06-01", hits, ctx)[0].document.id == "interval"
    assert _run(6, "Policy without a date", hits, ctx) == hits


def test_date_spelling_aliases_keep_query_and_do_not_invent_relative_dates():
    ctx = FakeContext(())
    hook = _experiment(5).query_hook
    assert hook is not None

    variants = hook("Policy on June 1, 2025", ctx)

    assert variants[0] == ("Policy on June 1, 2025", 2.0)
    assert ("Policy on 2025-06-01", 1.0) in variants
    assert ("Policy on 2025/06/01", 1.0) in variants
    assert len({text for text, _ in variants}) == len(variants)
    assert hook("Policy yesterday", ctx) == [("Policy yesterday", 2.0)]


def test_date_alias_hook_without_dates_preserves_baseline_query_lists():
    class FocusContext(FakeContext):
        def focused(self, text: str) -> str:
            return " ".join(token for token in self.tokens(text) if token != "what")

    ctx = FocusContext(())
    hook = _experiment(5).query_hook
    assert hook is not None
    assert hook("Alpha, beta!", ctx) == [("Alpha, beta!", 2.0)]
    assert hook("What alpha beta?", ctx) == [
        ("What alpha beta?", 2.0), ("alpha beta", 2.0)
    ]


def test_update_cue_promotes_change_evidence_but_keeps_history():
    history = _doc("history", "The color was red.", timestamp=_millis("2025-01-01"))
    update = _doc(
        "update",
        "The color changed from red to blue.",
        timestamp=_millis("2025-02-01"),
    )
    hits = _hits(history, update)
    ctx = FakeContext((history, update))

    current = _run(7, "What is the current color?", hits, ctx)

    assert [hit.document.id for hit in current] == ["update", "history"]
    assert _run(7, "What color was recorded?", hits, ctx) == hits


def test_negation_counterpart_is_source_backed_and_deterministic():
    positive = _doc("positive", "The launch is enabled for alpha users.")
    negative = _doc("negative", "The launch is not enabled for alpha users.")
    unrelated = _doc("unrelated", "The office is not open on Sunday.")
    hits = _hits(positive)
    lexical = _hits(unrelated, negative)
    ctx = FakeContext((positive, negative, unrelated), lexical)

    first = _run(8, "launch enabled alpha users", hits, ctx)
    second = _run(8, "launch enabled alpha users", hits, ctx)

    assert [hit.document.id for hit in first] == ["positive", "negative"]
    assert first == second
    assert first[1].document is negative
    assert all(hit.document.text in {document.text for document in ctx.documents} for hit in first)


def test_change_clause_is_an_exact_subspan_with_corrected_offset():
    text = "Background remains useful. The color changed from red to blue. Old notes follow."
    source = _doc("change", text, offset=40)
    hits = _hits(source)
    ctx = FakeContext((source,))

    result = _run(9, "What is the current color?", hits, ctx)
    derived = result[0].document
    expected = "The color changed from red to blue."

    assert derived.text == expected
    assert derived.offset == source.offset + text.index(expected)
    assert derived.message_id == source.message_id
    assert derived.session_id == source.session_id
    assert derived.request_id == source.request_id
    assert derived.role == source.role
    assert derived.timestamp == source.timestamp
    assert derived.context == source.context
    relative = derived.offset - source.offset
    assert text[relative:relative + len(expected)] == expected
    assert _run(9, "What color was mentioned?", hits, ctx) == hits


def test_nearest_source_date_uses_recorded_dates_and_leaves_undated_last():
    far = _doc("far", "Far record.", timestamp=_millis("2025-01-01"))
    near = _doc("near", "Near record.", timestamp=_millis("2025-06-02"))
    undated = _doc("undated", "No timestamp.")
    hits = _hits(far, undated, near)
    ctx = FakeContext((far, near, undated))

    result = _run(10, "status around 2025-06-01", hits, ctx)

    assert [hit.document.id for hit in result] == ["near", "far", "undated"]
    assert _run(10, "status without a date", hits, ctx) == hits


def test_timestamp_zero_is_known_and_cutoff_before_is_exclusive():
    epoch = _doc("epoch", "Epoch record.", timestamp=0)
    next_day = _doc("next", "Next day record.", timestamp=86_400_000)
    unknown = _doc("unknown", "Undated record.")
    hits = _hits(unknown, next_day, epoch)
    ctx = FakeContext((epoch, next_day, unknown))

    inclusive = _run(3, "status as of 1970-01-02", hits, ctx)
    exclusive = _run(3, "status before 1970-01-02", hits, ctx)

    assert [h.document.id for h in inclusive] == ["next", "epoch", "unknown"]
    assert [h.document.id for h in exclusive] == ["epoch", "unknown", "next"]
    assert _run(10, "status on 1970-01-01", hits, ctx)[0].document.id == "epoch"


def test_ambiguous_invalid_relative_dates_and_reversed_ranges_are_not_resolved():
    wrong = _doc("wrong", "Effective 2025-12-31 to 2025-01-01.")
    valid = _doc("valid", "Effective 2025-01-01 through 2025-12-31.")
    hits = _hits(wrong, valid)
    ctx = FakeContext((wrong, valid))
    for number in (3, 4, 6, 10):
        for query in ("policy tomorrow", "policy on 2025-02-30", "policy in 2025"):
            assert _run(number, query, hits, ctx) == hits
    for number in (3, 6, 10):
        assert _run(number, "between 2025-01-01 and 2025-12-31", hits, ctx) == hits
    assert _run(6, "policy on 2025-06-01", hits, ctx)[0].document.id == "valid"
    assert _run(3, "compare 2025-06-01 before starting work", hits, ctx) == hits
    hook = _experiment(5).query_hook
    assert hook is not None
    assert hook("policy on 2025-02-30", ctx) == [("policy on 2025-02-30", 2.0)]


def test_body_date_can_disagree_with_record_date_without_being_removed():
    historical = _doc(
        "history",
        "The launch was on February 29, 2024.",
        timestamp=_millis("2026-05-01"),
    )
    recorded = _doc("recorded", "A lunch note.", timestamp=_millis("2024-02-29"))
    hits = _hits(recorded, historical)
    ctx = FakeContext((historical, recorded))

    assert _run(4, "launch on 2024-02-29", hits, ctx)[0].document is historical
    assert _run(10, "launch on 2024-02-29", hits, ctx)[0].document is recorded
    assert {h.document.id for h in _run(4, "launch on 2024-02-29", hits, ctx)} == {
        "history", "recorded"
    }
    assert _run(4, "launch on 2024\u5e742\u670829\u65e5", hits, ctx)[0].document is historical


def test_update_text_must_match_topic_and_all_supported_changes_survive():
    unrelated = _doc("unrelated", "The color is red. The budget is now larger.")
    changes = _doc(
        "changes",
        "\u524d\u8a00\u3002 The color changed from red to blue. "
        "The color is now green. The version is 2.5.",
        offset=17,
    )
    hits = _hits(unrelated, changes)
    ctx = FakeContext((unrelated, changes))

    assert _run(7, "current color", hits, ctx)[0].document is changes
    trimmed = _run(9, "current color", hits, ctx)
    assert trimmed[0].document is unrelated
    assert [h.document.text for h in trimmed[1:]] == [
        "The color changed from red to blue.",
        "The color is now green.",
    ]
    for hit in trimmed[1:]:
        relative = hit.document.offset - changes.offset
        assert changes.text[relative:relative + len(hit.document.text)] == hit.document.text
        assert hit.document.position == changes.position
        assert hit.document.ordinal == changes.ordinal
    assert _run(9, "historical color changes", hits, ctx) == hits


def test_opposite_negation_needs_shared_query_topic_and_keeps_sources():
    anchor = _doc("anchor", "The launch is enabled for alpha users.")
    related = _doc("related", "The launch is not enabled for alpha users.")
    other = _doc("other", "The office is not enabled for alpha users.")
    ctx = FakeContext((anchor, related, other), _hits(other, related))
    hits = _hits(anchor)

    assert [h.document.id for h in _run(8, "launch", hits, ctx)] == ["anchor", "related"]
    assert _run(8, "unrelated topic", hits, ctx) == hits
    assert _run(8, "launch", [], ctx) == []
    same_polarity = replace(related, text="The launch is enabled for beta users.")
    no_counterpart = FakeContext((anchor, same_polarity), _hits(same_polarity))
    assert _run(8, "launch", hits, no_counterpart) == hits


def test_all_temporal_hooks_are_repeatable_immutable_and_source_exact():
    old = _doc("old", "Color was red on June 1, 2025.", timestamp=0, offset=9)
    new = _doc(
        "new", "Note. Color changed from red to blue. Color is not red.",
        timestamp=_millis("2025-06-03"), offset=7,
    )
    hits = _hits(old, new)
    ctx = FakeContext((old, new), _hits(new, old))
    original = tuple(hits)
    queries = (
        "current color", "historical color", "color as of 2025-06-01",
        "color on 2025-06-01", "color June 1, 2025", "color on 2025-06-01",
        "current color", "color red", "current color", "color on 2025-06-01",
    )
    source_by_id = {m.id: m for m in ctx.messages}
    for experiment, query in zip(experiments(), queries):
        if experiment.query_hook:
            assert experiment.query_hook(query, ctx) == experiment.query_hook(query, ctx)
            continue
        hook = experiment.retrieval_hook or experiment.output_hook
        result = hook(query, hits, ctx)
        assert result == hook(query, hits, ctx)
        assert tuple(hits) == original
        assert len({h.document.id for h in result}) == len(result)
        for hit in result:
            document = hit.document
            message = source_by_id[document.message_id]
            end = document.offset + len(document.text)
            assert message.text[document.offset:end] == document.text
            assert (document.request_id, document.session_id, document.position,
                    document.role, document.timestamp, document.ordinal) == (
                message.request_id, message.session_id, message.position,
                message.role, message.timestamp, message.ordinal,
            )
            assert document.context == "source context"
