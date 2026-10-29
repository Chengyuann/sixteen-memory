"""Immutable inputs keep experiment mechanisms separate from scoring labels."""
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass(frozen=True)
class Message:
    id: str
    request_id: str
    session_id: str
    position: int
    role: str
    text: str
    timestamp: int | None
    ordinal: int


@dataclass(frozen=True)
class Document:
    id: str
    message_id: str
    session_id: str
    request_id: str
    position: int
    offset: int
    text: str
    role: str
    timestamp: int | None
    ordinal: int
    index_text: str = ""
    context: str = ""


@dataclass(frozen=True)
class Hit:
    document: Document
    score: float


class Context(Protocol):
    messages: tuple[Message, ...]
    documents: tuple[Document, ...]
    message_documents: dict[str, tuple[Document, ...]]
    session_documents: dict[str, tuple[Document, ...]]
    idf: dict[str, float]
    doc_tokens: dict[str, frozenset[str]]

    def tokens(self, text: str) -> list[str]: ...
    def focused(self, text: str) -> str: ...
    def lexical(self, text: str, limit: int = 300) -> list[Hit]: ...


@dataclass(frozen=True)
class Experiment:
    id: str
    direction: str
    name: str
    hypothesis: str
    mechanism: str
    limitation: str
    query_hook: Callable | None = field(default=None, repr=False)
    document_hook: Callable | None = field(default=None, repr=False)
    retrieval_hook: Callable | None = field(default=None, repr=False)
    output_hook: Callable | None = field(default=None, repr=False)

    def __post_init__(self):
        hooks = [self.query_hook, self.document_hook, self.retrieval_hook, self.output_hook]
        if sum(h is not None for h in hooks) != 1:
            raise ValueError(f"{self.id}: exactly one mechanism hook must be enabled")

    def description(self):
        return {k: getattr(self, k) for k in
                ("id", "direction", "name", "hypothesis", "mechanism", "limitation")}
