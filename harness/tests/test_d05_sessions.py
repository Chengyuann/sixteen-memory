from __future__ import annotations

import re
from collections import defaultdict

from harness.directions.d05_sessions import experiments
from harness.types import Document, Hit, Message


class FakeContext:
    def __init__(self, messages: list[Message], documents: list[Document]):
        self.messages = tuple(messages)
        self.documents = tuple(documents)
        by_message: dict[str, list[Document]] = defaultdict(list)
        by_session: dict[str, list[Document]] = defaultdict(list)
        for document in documents:
            by_message[document.message_id].append(document)
            by_session[document.session_id].append(document)
        self.message_documents = {key: tuple(value) for key, value in by_message.items()}
        self.session_documents = {key: tuple(value) for key, value in by_session.items()}
        self.doc_tokens = {document.id: frozenset(self.tokens(document.text)) for document in documents}
        frequencies: dict[str, int] = defaultdict(int)
        for terms in self.doc_tokens.values():
            for term in terms:
                frequencies[term] += 1
        self.idf = {term: 1.0 + len(documents) / count for term, count in frequencies.items()}

    def tokens(self, text: str) -> list[str]:
        return re.findall(r"[\w]+", text.casefold())

    def focused(self, text: str) -> str:
        return text

    def lexical(self, text: str, limit: int = 300) -> list[Hit]:
        terms = set(self.tokens(text))
        hits = [
            Hit(document, float(len(terms.intersection(self.doc_tokens[document.id]))))
            for document in self.documents
            if terms.intersection(self.doc_tokens[document.id])
        ]
        return sorted(hits, key=lambda hit: (-hit.score, hit.document.ordinal, hit.document.id))[:limit]


def _message(
    identifier: str,
    request: str,
    session: str,
    position: int,
    role: str,
    text: str,
    ordinal: int,
) -> Message:
    return Message(identifier, request, session, position, role, text, ordinal, ordinal)


def _document(message: Message, identifier: str | None = None) -> Document:
    return Document(
        identifier or f"d-{message.id}",
        message.id,
        message.session_id,
        message.request_id,
        message.position,
        0,
        message.text,
        message.role,
        message.timestamp,
        message.ordinal,
        message.text,
        "",
    )


def _fixture() -> tuple[FakeContext, dict[str, Document]]:
    messages = [
        _message("m1", "r1", "s1", 1, "user", "Where is the Atlas launch plan?", 1),
        _message("m2", "r1", "s1", 2, "assistant", "The Atlas launch is planned for Lisbon.", 2),
        _message("m3", "r2", "s1", 3, "assistant", "Atlas owners approved the launch checklist.", 3),
        _message("m4", "r3", "s2", 1, "assistant", "Atlas is briefly mentioned here.", 4),
        _message("m5", "r4", "s3", 1, "user", "Discuss the Boreal budget.", 5),
        _message("m6", "r4", "s3", 2, "assistant", "Boreal has a separate budget answer.", 6),
        _message("m7", "r-secret", "s1", 4, "assistant", "Password: nearby-but-unrelated.", 7),
    ]
    documents = [_document(message) for message in messages]
    return FakeContext(messages, documents), {document.message_id: document for document in documents}


def _hook(identifier: str):
    return next(experiment.retrieval_hook for experiment in experiments() if experiment.id == identifier)


def test_catalog_has_ten_distinct_single_hook_mechanisms():
    catalog = experiments()
    assert [experiment.id for experiment in catalog] == [f"D05.{index:02d}" for index in range(1, 11)]
    assert len({experiment.name for experiment in catalog}) == 10
    assert all(experiment.retrieval_hook is not None for experiment in catalog)


def test_session_aggregation_rewards_multiple_independent_hits():
    ctx, docs = _fixture()
    hits = [
        Hit(docs["m4"], 5.0),
        Hit(docs["m2"], 4.0),
        Hit(docs["m3"], 4.0),
    ]
    result = _hook("D05.01")("Atlas launch", hits, ctx)
    assert result[0].document.session_id == "s1"


def test_marginal_coverage_promotes_a_session_with_a_missing_query_concept():
    ctx, docs = _fixture()
    hits = [
        Hit(docs["m2"], 9.0),
        Hit(docs["m3"], 8.0),
        Hit(docs["m1"], 7.0),
        Hit(docs["m4"], 6.0),
        Hit(docs["m6"], 5.0),
    ]
    result = _hook("D05.05")("Atlas launch Boreal", hits, ctx)
    assert [hit.document.session_id for hit in result[:2]] == ["s1", "s3"]


def test_parent_child_retrieval_uses_exact_request_not_nearby_sensitive_text():
    ctx, docs = _fixture()
    parent_result = _hook("D05.08")("Lisbon", [Hit(docs["m2"], 7.0)], ctx)
    child_result = _hook("D05.09")("Atlas plan", [Hit(docs["m1"], 7.0)], ctx)
    paired_result = _hook("D05.10")("Atlas plan", [Hit(docs["m2"], 7.0)], ctx)

    assert docs["m1"].id in {hit.document.id for hit in parent_result}
    assert docs["m2"].id in {hit.document.id for hit in child_result}
    assert {docs["m1"].id, docs["m2"].id}.issubset(
        {hit.document.id for hit in paired_result}
    )
    assert docs["m7"].id not in {hit.document.id for hit in parent_result + child_result + paired_result}


def test_every_hook_is_deterministic_bounded_and_preserves_original_provenance():
    ctx, docs = _fixture()
    hits = [Hit(docs["m2"], 6.0), Hit(docs["m4"], 5.0), Hit(docs["m6"], 4.0)]
    messages = {message.id: message for message in ctx.messages}

    for experiment in experiments():
        first = experiment.retrieval_hook("Atlas launch", hits, ctx)
        second = experiment.retrieval_hook("Atlas launch", hits, ctx)
        assert first == second
        assert len(first) <= 300
        for hit in first:
            source = messages[hit.document.message_id].text
            start = hit.document.offset
            assert source[start : start + len(hit.document.text)] == hit.document.text
            assert hit.document in ctx.documents
