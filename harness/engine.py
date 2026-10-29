"""Local v0.2.5 lexical engine and single-hook experiment execution.

The scorer and its labels are deliberately absent from this module.
Hooks are trusted Python plugins: the read-only facade prevents accidental
mutation, not malicious reflection or arbitrary code execution.
"""
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType

from .types import Document, Hit, Message

BASELINE_COMMIT = "a3520ccb7843156e2dd51e92e29caf0f6dafafa4"
STOPWORDS = frozenset(
    "a an the is are was were be been being do does did have has had can could would should "
    "will shall may might must i me my mine we us our ours you your yours he him his she her "
    "hers it its they them their theirs what which who whom whose when where why how that "
    "this these those am as at by for from in into of on onto to with and or but if then than "
    "so because about please tell give describe explain find know remember recall".split()
)
OVERVIEW = re.compile(r"\b(?:summari[sz]e|summary|overview|overall|trajectory|trends?)\b"
                      r"|总结|概述|综述|总览|整体|变化趋势", re.IGNORECASE | re.ASCII)
RULE = re.compile(r"\b(?:rules?|polic(?:y|ies)|procedures?|workflows?|instructions?|protocols?"
                  r"|algorithms?|steps?|calculate|compute|simulate|simulation|formula|formulas)\b"
                  r"|规则|政策|流程|步骤|指令|算法|计算|模拟|公式", re.IGNORECASE | re.ASCII)
ANAPHORIC = re.compile(
    r"^(?:yes|no|it|they|this|that|these|those|there|thanks|depends|varies|about|for|we|i)\b",
    re.IGNORECASE | re.ASCII,
)


@lru_cache(maxsize=16384)
def _tokens(text):
    text = re.sub(r"([\u3400-\u9fff])", r" \1 ", text.lower())
    # Match JS \p{L}/\p{N} using the runtime's Unicode categories, not \w.
    return tuple("".join(c if unicodedata.category(c)[0] in "LN" else " "
                         for c in text).split())


def tokens(text):
    return list(_tokens(text))


def focused(text):
    return " ".join(t for t in _tokens(text) if t not in STOPWORDS)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def from_batches(case_id, batches):
    messages = []
    for batch in batches:
        for position, row in enumerate(batch["messages"]):
            messages.append(Message(
                id=f'{batch["request_id"]}:{position}', request_id=batch["request_id"],
                session_id=batch["session_id"], position=position, role=row["role"],
                text=row["content"], timestamp=row.get("timestamp", batch.get("timestamp")),
                ordinal=len(messages),
            ))
    if len({m.id for m in messages}) != len(messages):
        raise ValueError(f"Duplicate source message identity: {case_id}")
    return tuple(messages)


def source_documents(case_id, messages, size=1200):
    docs = []
    previous_by_request = {}
    for m in messages:
        previous = previous_by_request.get(m.request_id)
        cue = re.sub(r"^[^:\n]{1,50}:\s*", "", m.text)
        context = ""
        if previous and ANAPHORIC.search(cue) and "?" in previous.text:
            context = previous.text.encode("utf-16-le")[-2400:].decode("utf-16-le", errors="replace")
        for offset in range(0, len(m.text), size - min(160, size // 5)):
            text = m.text[offset:offset + size]
            identifier = "mem_" + digest(json.dumps(
                [case_id, m.request_id, m.position, offset], ensure_ascii=False, separators=(",", ":")))
            docs.append(Document(identifier, m.id, m.session_id, m.request_id, m.position,
                                 offset, text, m.role, m.timestamp, len(docs), text, context))
            if offset + size >= len(m.text):
                break
        previous_by_request[m.request_id] = m
    return tuple(docs)


def validate_documents(messages, documents, *, unique_ids=True):
    sources = {m.id: m for m in messages}
    seen = set()
    for d in documents:
        if unique_ids and d.id in seen:
            raise ValueError("Duplicate document identity")
        seen.add(d.id)
        m = sources.get(d.message_id)
        if m is None or not d.text or d.offset < 0 or m.text[d.offset:d.offset + len(d.text)] != d.text:
            raise ValueError(f"Non-extractive document: {d.id}")
        if (d.request_id, d.session_id, d.position, d.role, d.timestamp) != (
                m.request_id, m.session_id, m.position, m.role, m.timestamp):
            raise ValueError(f"Changed provenance: {d.id}")
    if len(documents) > 20000:
        raise ValueError("Experiment exceeds offline document limit")


@dataclass(frozen=True, slots=True)
class Context:
    """Read-only source data and retrieval access for trusted experiment hooks."""

    messages: tuple[Message, ...]
    documents: tuple[Document, ...]
    message_documents: Mapping[str, tuple[Document, ...]]
    session_documents: Mapping[str, tuple[Document, ...]]
    idf: Mapping[str, float]
    doc_tokens: Mapping[str, frozenset[str]]
    _lexical: Callable[[str, int], list[Hit]] = field(repr=False, compare=False)

    tokens = staticmethod(tokens)
    focused = staticmethod(focused)

    def lexical(self, text, limit=300):
        return self._lexical(text, limit)


class Index:
    def __init__(self, messages, documents):
        self.messages, self.documents = tuple(messages), tuple(documents)
        validate_documents(self.messages, self.documents)
        self.by_id = {d.id: d for d in self.documents}
        self.by_message = {m.id: m for m in self.messages}
        grouped = defaultdict(list)
        sessions = defaultdict(list)
        for d in self.documents:
            grouped[d.message_id].append(d)
            sessions[d.session_id].append(d)
        self.message_documents = {k: tuple(v) for k, v in grouped.items()}
        self.session_documents = {k: tuple(v) for k, v in sessions.items()}
        self.doc_tokens = {d.id: frozenset(_tokens(d.index_text or d.text)) for d in self.documents}
        df = Counter(t for values in self.doc_tokens.values() for t in values)
        self.idf = {t: math.log(1 + (len(self.documents) + 1) / (n + 1)) for t, n in df.items()}
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript("""
            CREATE TABLE chunks(rowid INTEGER PRIMARY KEY,id TEXT,timestamp INTEGER,ordinal INTEGER);
            CREATE VIRTUAL TABLE search_index USING fts5(source,facts,context,tokenize='porter unicode61');
        """)
        self.connection.executemany("INSERT INTO chunks VALUES (?,?,?,?)",
                                   [(i + 1, d.id, d.timestamp, i + 1) for i, d in enumerate(documents)])
        self.connection.executemany("INSERT INTO search_index(rowid,source,facts,context) VALUES (?,?,?,?)",
                                   [(i + 1, " ".join(_tokens(d.index_text or d.text)), "",
                                     " ".join(_tokens(d.context))) for i, d in enumerate(documents)])
        self.connection.commit()
        self._cache = {}
        self._context = Context(
            self.messages, self.documents,
            MappingProxyType(self.message_documents),
            MappingProxyType(self.session_documents),
            MappingProxyType(self.idf), MappingProxyType(self.doc_tokens),
            self.lexical,
        )

    tokens = staticmethod(tokens)
    focused = staticmethod(focused)

    def lexical(self, text, limit=300):
        if not 1 <= limit <= 1000:
            raise ValueError("Bounded lexical limit required")
        key = (text, limit)
        if key not in self._cache:
            words = list(dict.fromkeys(_tokens(text)))
            if not words:
                self._cache[key] = ()
            else:
                match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
                rows = self.connection.execute("""
                    SELECT c.id,bm25(search_index,1,1,0.25) FROM search_index
                    JOIN chunks c ON c.rowid=search_index.rowid
                    WHERE search_index MATCH ?
                    ORDER BY bm25(search_index,1,1,0.25),c.timestamp DESC,c.rowid DESC LIMIT ?
                """, (match, limit)).fetchall()
                self._cache[key] = tuple(Hit(self.by_id[i], -score) for i, score in rows)
        return list(self._cache[key])

    def baseline(self, query, query_lists=None):
        if query_lists is None:
            query_lists = [(query, 2)]
            focus = focused(query)
            if focus and focus != " ".join(_tokens(query)):
                query_lists.append((focus, 2))
        lists = [(self.lexical(text), weight) for text, weight in query_lists if text and weight > 0]
        lists = [(rows, weight) for rows, weight in lists if rows]
        total_weight = sum(w for _, w in lists)
        ranked = {}
        for rows, weight in lists:
            for rank, hit in enumerate(rows):
                ranked[hit.document.id] = ranked.get(hit.document.id, 0) + weight / total_weight / (61 + rank)
        if OVERVIEW.search(query):
            timeline = sorted(self.documents, key=lambda d: (
                -1 if d.timestamp is None else d.timestamp, d.ordinal))
            count = min(100, len(timeline))
            for i in range(count):
                index = len(timeline) - 1 if count == 1 else math.floor(i * (len(timeline) - 1) / (count - 1) + .5)
                identifier = timeline[index].id
                ranked[identifier] = ranked.get(identifier, 0) + 1 / (61 + i)
        if RULE.search(query):
            anchors = sorted(ranked.items(), key=lambda p: -p[1])[:4]
            for identifier, score in anchors:
                anchor = self.by_id[identifier]
                related = sorted(self.session_documents[anchor.session_id],
                                 key=lambda d: (abs(d.ordinal - anchor.ordinal), d.ordinal))[:24]
                for i, doc in enumerate(related):
                    ranked[doc.id] = max(ranked.get(doc.id, 0), score * .95 / (1 + i * .05))
        return [Hit(self.by_id[i], score) for i, score in sorted(ranked.items(), key=lambda p: (-p[1], p[0]))]

    def apply(self, query, experiment=None, baseline_hits=None):
        if experiment and experiment.query_hook:
            query_lists = experiment.query_hook(query, self._context)
            hits = self.baseline(query, query_lists)
        else:
            hits = list(baseline_hits) if baseline_hits is not None else self.baseline(query)
        if experiment and experiment.retrieval_hook:
            hits = experiment.retrieval_hook(query, hits, self._context)
        if experiment and experiment.output_hook:
            hits = experiment.output_hook(query, hits, self._context)
        hits = list(hits)
        for h in hits:
            if not math.isfinite(h.score):
                raise ValueError("Non-finite score")
        # Index IDs must be unique; output identity is the original source span.
        validate_documents(self.messages, [h.document for h in hits], unique_ids=False)
        seen = set()
        unique = []
        for h in hits:
            identity = (h.document.message_id, h.document.offset, h.document.text)
            if identity not in seen:
                seen.add(identity)
                unique.append(h)
        return unique[:100]

    def close(self):
        self.connection.close()
        self._cache.clear()


def evidence_signature(hits):
    return tuple((h.document.message_id, h.document.offset, h.document.text) for h in hits[:100])
