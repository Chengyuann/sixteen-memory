from __future__ import annotations

from dataclasses import replace
import re

from harness.directions.d10_safety import experiments
from harness.types import Document, Hit, Message


def _doc(
    identifier: str,
    text: str,
    *,
    message_id: str | None = None,
    request_id: str | None = None,
    role: str = "user",
    offset: int = 0,
) -> Document:
    return Document(
        id=identifier,
        message_id=message_id or f"m-{identifier}",
        session_id="session",
        request_id=request_id or f"request-{identifier}",
        position=0,
        offset=offset,
        text=text,
        role=role,
        timestamp=1000,
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
    def __init__(self, documents: tuple[Document, ...]):
        self.documents = documents
        self.message_documents = {}
        for document in documents:
            self.message_documents.setdefault(document.message_id, tuple())
            self.message_documents[document.message_id] += (document,)
        messages = []
        for parts in self.message_documents.values():
            first = parts[0]
            text = ""
            for part in sorted(parts, key=lambda d: d.offset):
                assert part.offset >= len(text), "Synthetic chunks must not overlap"
                text += " " * (part.offset - len(text)) + part.text
            messages.append(Message(
                first.message_id, first.request_id, first.session_id, first.position,
                first.role, text, first.timestamp, first.ordinal,
            ))
        self.messages = tuple(messages)
        self.session_documents = {"session": documents}
        self.idf = {}
        self.doc_tokens = {}

    def tokens(self, text: str) -> list[str]:
        return re.findall(r"[^\W_]+", text.casefold())

    def focused(self, text: str) -> str:
        return text

    def lexical(self, text: str, limit: int = 300) -> list[Hit]:
        del text, limit
        return []


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
        f"D10.{number:02d}" for number in range(1, 11)
    ]
    assert len({experiment.name for experiment in catalog}) == 10
    assert len({experiment.mechanism for experiment in catalog}) == 10
    assert len({
        experiment.retrieval_hook or experiment.output_hook for experiment in catalog
    }) == 10
    for experiment in catalog:
        hooks = (
            experiment.query_hook,
            experiment.document_hook,
            experiment.retrieval_hook,
            experiment.output_hook,
        )
        assert sum(hook is not None for hook in hooks) == 1


def test_direct_evidence_abstains_without_query_support():
    alice = _doc("alice", "Alice prefers email.")
    bob = _doc("bob", "Bob prefers phone calls.")
    hits = _hits(alice, bob)
    ctx = FakeContext((alice, bob))

    assert [hit.document.id for hit in _run(1, "Alice preference", hits, ctx)] == ["alice"]
    assert _run(1, "unmentioned subject", hits, ctx) == []


def test_quoted_evidence_requires_the_exact_source_phrase():
    exact = _doc("exact", "The note says blue launch plan.")
    paraphrase = _doc("paraphrase", "The launch plan is blue.")
    hits = _hits(paraphrase, exact)
    ctx = FakeContext((exact, paraphrase))

    result = _run(2, 'Find "blue launch plan"', hits, ctx)

    assert [hit.document.id for hit in result] == ["exact"]
    assert _run(2, "Find the launch plan", hits, ctx) == hits


def test_same_message_gate_keeps_local_context_only():
    direct = _doc("direct", "Alice project code.", message_id="m1", request_id="r1")
    local_context = _doc(
        "local", "The deadline is Friday.", message_id="m1", request_id="r1",
        offset=len(direct.text) + 1,
    )
    other = _doc("other", "Another deadline is Monday.", message_id="m2")
    hits = _hits(other, local_context, direct)
    ctx = FakeContext((direct, local_context, other))

    result = _run(3, "Alice project", hits, ctx)

    assert [hit.document.id for hit in result] == ["local", "direct"]
    assert _run(3, "unmentioned subject", hits, ctx) == []


def test_role_intent_keeps_only_the_requested_speaker():
    user = _doc("user", "I selected blue.", role="user")
    assistant = _doc("assistant", "Blue is recorded.", role="assistant")
    hits = _hits(assistant, user)
    ctx = FakeContext((user, assistant))

    result = _run(4, "What did I say about blue?", hits, ctx)

    assert [hit.document.id for hit in result] == ["user"]
    assert _run(4, "What was said about blue?", hits, ctx) == hits


def test_sensitive_category_intent_does_not_cross_categories():
    phone = _doc("phone", "Alice phone is +1 415 555 0123.")
    health = _doc("health", "Alice medical diagnosis is recorded.")
    ordinary = _doc("ordinary", "Alice likes blue.")
    hits = _hits(health, ordinary, phone)
    ctx = FakeContext((phone, health, ordinary))

    result = _run(5, "What is Alice's phone?", hits, ctx)

    assert [hit.document.id for hit in result] == ["ordinary", "phone"]
    assert [hit.document.id for hit in _run(5, "What does Alice like?", hits, ctx)] == [
        "ordinary"
    ]


def test_sensitive_subject_binding_excludes_another_person():
    alice = _doc("alice", "Alice phone is +1 415 555 0123.")
    bob = _doc("bob", "Bob phone is +1 212 555 0199.")
    ordinary = _doc("ordinary", "The directory was updated.")
    hits = _hits(bob, ordinary, alice)
    ctx = FakeContext((alice, bob, ordinary))

    result = _run(6, "What is Alice's phone?", hits, ctx)

    assert [hit.document.id for hit in result] == ["ordinary", "alice"]
    assert [hit.document.id for hit in _run(6, "What is the phone?", hits, ctx)] == [
        "ordinary"
    ]


def test_exact_sensitive_value_does_not_return_other_records():
    alice = _doc("alice", "Alice email is alice@example.com.")
    bob = _doc("bob", "Bob email is bob@example.com.")
    hits = _hits(bob, alice)
    ctx = FakeContext((alice, bob))

    result = _run(7, "Find alice@example.com", hits, ctx)

    assert [hit.document.id for hit in result] == ["alice"]
    assert _run(7, "Find Alice email", hits, ctx) == hits


def test_privacy_marker_blocks_marked_source_and_documents_its_counterexample():
    private = _doc("private", "Confidential: Alice selected blue.")
    ordinary = _doc("ordinary", "Alice selected green.")
    quoted_history = _doc("history", "The policy defines the word confidential.")
    hits = _hits(private, quoted_history, ordinary)
    ctx = FakeContext((private, quoted_history, ordinary))

    result = _run(8, "Alice selection", hits, ctx)

    assert [hit.document.id for hit in result] == ["ordinary"]
    assert quoted_history not in [hit.document for hit in result]


def test_minimum_sentence_is_exact_and_preserves_provenance():
    text = "Alice likes blue. Alice phone is +1 415 555 0123. She likes tea."
    source = _doc("source", text, offset=70)
    hits = _hits(source)
    ctx = FakeContext((source,))

    first = _run(9, "What is Alice's phone?", hits, ctx)
    second = _run(9, "What is Alice's phone?", hits, ctx)
    derived = first[0].document
    expected = "Alice phone is +1 415 555 0123."

    assert first == second
    assert derived.text == expected
    assert derived.offset == source.offset + text.index(expected)
    assert derived.message_id == source.message_id
    assert derived.session_id == source.session_id
    assert derived.request_id == source.request_id
    assert derived.role == source.role
    assert derived.timestamp == source.timestamp
    assert derived.context == source.context
    relative = derived.offset - source.offset
    assert source.text[relative : relative + len(derived.text)] == derived.text


def test_unasserted_evidence_abstains_without_complete_query_support():
    bob = _doc("bob", "Bob bank account number is 12345678.")
    ordinary = _doc("ordinary", "Alice opened a project.")
    alice = _doc("alice", "Alice bank account number is 87654321.")
    hits = _hits(bob, ordinary)
    ctx = FakeContext((bob, ordinary, alice))

    assert _run(10, "What is Alice's bank account number?", hits, ctx) == []

    supported = _run(10, "What is Alice's bank account number?", _hits(bob, alice), ctx)
    assert [hit.document.id for hit in supported] == ["alice"]


def test_index_context_and_source_identifiers_are_not_direct_evidence():
    ordinary = _doc("Alice-secret-answer", "Bob likes green.")
    indexed = replace(ordinary, index_text="Alice", context="Alice has a phone")
    hits = _hits(indexed)
    ctx = FakeContext((indexed,))

    assert _run(1, "Alice", hits, ctx) == []
    assert _run(3, "Alice", hits, ctx) == []
    assert _run(1, "what is it", hits, ctx) == []


def test_multiple_quotes_and_missing_speaker_do_not_gain_unearned_support():
    one = _doc("one", "red plan", role="user")
    both = _doc("both", "red plan with blue notes", role="assistant")
    hits = _hits(one, both)
    ctx = FakeContext((one, both))

    assert _run(2, 'find "red plan" and "blue notes"', hits, ctx) == [hits[1]]
    assert _run(2, 'find "missing words"', hits, ctx) == []
    assert _run(4, "What did you say?", hits, ctx) == [hits[1]]
    assert _run(4, "What did you say and what did I say?", hits, ctx) == hits
    assert _run(4, "What did I say?", [hits[1]], ctx) == []


def test_every_sensitive_category_requires_intent_even_in_mixed_chunks():
    sources = (
        _doc("contact", "Alice email is alice@example.com."),
        _doc("credential", "Alice password is ExampleOnly"),
        _doc("financial", "Alice salary is 100."),
        _doc("health", "Alice diagnosis is recorded."),
        _doc("identity", "Alice passport is AB0000000."),
    )
    ctx = FakeContext(sources)
    for source, term in zip(sources, ("email", "password", "salary", "diagnosis", "passport")):
        hits = _hits(source)
        assert _run(5, f"Alice {term}", hits, ctx) == hits
        assert _run(5, "Alice color", hits, ctx) == []
    mixed = _doc("mixed", "Alice email is alice@example.com. Alice diagnosis is recorded.")
    assert _run(5, "Alice email", _hits(mixed), FakeContext((mixed,))) == []


def test_dates_and_identity_numbers_are_not_accidentally_phone_numbers():
    dated = _doc("date", "Meeting on 2025-06-01.")
    identity = _doc("identity", "Alice SSN is 123-45-6789.")
    card = _doc("card", "Alice credit card is 4111 1111 1111 1111.")
    ctx = FakeContext((dated, identity, card))

    assert _run(5, "meeting", _hits(dated), ctx) == _hits(dated)
    assert _run(5, "Alice SSN", _hits(identity), ctx) == _hits(identity)
    assert _run(5, "Alice credit card", _hits(card), ctx) == _hits(card)
    assert _run(5, "Alice phone", _hits(identity, card), ctx) == []


def test_sensitive_literal_exactness_preserves_secret_case_and_all_values():
    upper = _doc("upper", 'token: "CaseSensitive"')
    lower = _doc("lower", 'token: "casesensitive"')
    phone = _doc("phone", "Contact +1 (415) 555-0123.")
    one = _doc("one", "alice@example.com")
    both = _doc("both", "alice@example.com and bob@example.com")
    ctx = FakeContext((upper, lower, phone, one, both))

    assert _run(7, 'token: "CaseSensitive"', _hits(lower, upper), ctx)[0].document is upper
    assert _run(7, "find +14155550123", _hits(phone), ctx) == _hits(phone)
    query = "find alice@example.com and bob@example.com"
    assert _run(7, query, _hits(one, both), ctx)[0].document is both
    assert _run(7, 'token: "missing"', _hits(upper, lower), ctx) == []


def test_subject_binding_does_not_use_category_words_or_another_chunk():
    source = _doc("source", "Bob bank account number is 12345678.")
    source = replace(source, index_text="Alice", context="Alice")
    ctx = FakeContext((source,))

    assert _run(6, "Alice bank account number", _hits(source), ctx) == []
    assert _run(6, "bank account number", _hits(source), ctx) == []
    assert _run(6, "Bob bank account number", _hits(source), ctx) == _hits(source)


def test_minimum_sentence_keeps_email_intact_and_omits_unrequested_private_text():
    source = _doc(
        "source",
        "\u5f00\u5934\u3002 Alice email is alice@example.com. Alice favorite color is green.",
        offset=11,
    )
    ctx = FakeContext((source,))

    email = _run(9, "Alice email", _hits(source), ctx)[0].document
    color = _run(9, "Alice favorite color", _hits(source), ctx)[0].document

    assert email.text == "Alice email is alice@example.com."
    assert color.text == "Alice favorite color is green."
    assert "example.com" not in color.text
    assert _run(9, "Alice medication", _hits(source), ctx) == []
    for result in (email, color):
        start = result.offset - source.offset
        assert source.text[start:start + len(result.text)] == result.text


def test_questions_hypotheses_and_unknowns_are_not_assertions_but_negatives_are():
    sources = tuple(_doc(str(index), text) for index, text in enumerate((
        "What is Alice phone?",
        "If Alice phone were listed, it could be a placeholder.",
        "Alice phone is unknown.",
        "Alice phone might be +1 415 555 0123.",
        "Alice phone is not available.",
        "Alice does not have a phone.",
        "Alice phone is +1 415 555 0123.",
    )))
    ctx = FakeContext(sources)

    result = _run(10, "Alice phone", _hits(*sources), ctx)

    assert [hit.document.id for hit in result] == ["5", "6"]
    assert _run(10, "Alice phone", _hits(*sources[:5]), ctx) == []
    assert _run(10, "what is it", _hits(*sources), ctx) == []


def test_all_safety_hooks_are_repeatable_immutable_and_source_exact():
    sources = (
        _doc("plain", "Alice likes green.", offset=9),
        _doc("phone", "Note. Alice phone is +1 415 555 0123. Alice likes tea.", offset=4),
        _doc("hypothesis", "If Alice phone were recorded, it might be a placeholder."),
    )
    ctx = FakeContext(sources)
    hits = _hits(*sources)
    original = tuple(hits)
    query = 'What did I say about Alice "phone"?'
    source_by_id = {m.id: m for m in ctx.messages}
    for experiment in experiments():
        hook = experiment.retrieval_hook or experiment.output_hook
        for query in (query, "Alice phone", "unmentioned topic", ""):
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
