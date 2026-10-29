"""D07: independent, bounded joins between explicitly linked source documents.

Only original documents are returned. Association expansion skips marked private
messages, recognizable sensitive literals and oversized sources; it does not
remove directly retrieved baseline hits or claim to be a general privacy filter.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import re
from urllib.parse import urlsplit

from harness.types import Context, Document, Experiment, Hit


_DOCUMENT_LIMIT = 6000
_TEXT_LIMIT = 2400
_MESSAGE_LIMIT = 48000
_SEED_LIMIT = 12
_PIVOT_LIMIT = 6
_FANOUT = 8
_HOPS = 2
_ADDITION_LIMIT = 64
_OUTPUT_LIMIT = 300

_PRIVATE = re.compile(
    r"\b(?:confidential|private|do\s+not\s+share|internal\s+only|off\s+the\s+record)\b"
    r"|\b(?:password|passcode|api[_ -]?key|secret|access[_ -]?token|ssn|"
    r"passport|account\s+number|home\s+address|phone|email)\s*[:=]"
    r"|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|(?<!\w)\+\d[\d ()-]{7,}\d(?!\w)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_][\w]*(?:[-'][\w]+)*", re.UNICODE)
_NAME_STOP = frozenset(
    "note notes section entry question answer yes no see read hello thanks".split()
)
_UNIT_BREAK = re.compile(r"[.!?](?=\s|$)|[\u3002\uff01\uff1f]|\r?\n")


def _ordered(hits: list[Hit], limit: int) -> list[Hit]:
    best: dict[str, Hit] = {}
    for hit in hits:
        previous = best.get(hit.document.id)
        if previous is None or hit.score > previous.score:
            best[hit.document.id] = hit
    return sorted(
        best.values(), key=lambda h: (-h.score, h.document.ordinal, h.document.id)
    )[:limit]


def _contains(words: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    return bool(phrase) and any(
        words[start:start + len(phrase)] == phrase
        for start in range(len(words) - len(phrase) + 1)
    )


def _label(text: str) -> str:
    value = text.strip(" \t\r\n.,;:!?\"'`")
    return value if 1 < len(value) <= 96 and len(value.split()) <= 10 else ""


def _units(document: Document) -> list[str]:
    return [part.strip() for part in _UNIT_BREAK.split(document.text) if part.strip()][:32]


def _names(text: str, ctx: Context) -> list[str]:
    names: list[str] = []
    start = end = None
    count = 0
    for match in _WORD.finditer(text):
        word = match.group()
        eligible = (
            word[0].isupper() and len(word) > 1
            and word.casefold() not in _NAME_STOP and bool(ctx.focused(word))
        )
        if eligible and start is not None and text[end:match.start()].isspace() and count < 4:
            end, count = match.end(), count + 1
            continue
        if start is not None:
            names.append(text[start:end])
        start, end, count = (match.start(), match.end(), 1) if eligible else (None, None, 0)
    if start is not None:
        names.append(text[start:end])
    return list(dict.fromkeys(name for name in names if _label(name)))[:_PIVOT_LIMIT]


class _Corpus:
    """Per-call, source-text-only postings, with no state shared across indexes."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        blocked = {
            message.id for message in ctx.messages
            if len(message.text) > _MESSAGE_LIMIT or _PRIVATE.search(message.text)
        }
        documents = sorted(ctx.documents, key=lambda d: (d.ordinal, d.id))[:_DOCUMENT_LIMIT]
        self.documents = tuple(
            document for document in documents
            if document.message_id not in blocked
            and document.role in {"user", "assistant", "tool"}
            and len(document.text) <= _TEXT_LIMIT and not _PRIVATE.search(document.text)
        )
        self.by_id = {document.id: document for document in self.documents}
        self.words = {
            document.id: tuple(ctx.tokens(document.text)) for document in self.documents
        }
        self.postings: dict[str, list[Document]] = defaultdict(list)
        for document in self.documents:
            for word in sorted(set(self.words[document.id])):
                self.postings[word].append(document)

    def key(self, text: str) -> tuple[str, ...]:
        return tuple(self.ctx.tokens(text))

    def has(self, text: str, phrase: str) -> bool:
        return _contains(self.key(text), self.key(phrase))

    def lookup(self, phrase: str, *, rare: bool = False) -> list[Document]:
        words = self.key(phrase)
        if not words:
            return []
        anchor = min(set(words), key=lambda word: (len(self.postings.get(word, ())), word))
        matches: list[Document] = []
        for document in self.postings.get(anchor, ()):
            if _contains(self.words[document.id], words):
                matches.append(document)
                if len(matches) > _FANOUT:
                    return [] if rare else matches[:_FANOUT]
        return matches

    def seeds(self, query: str, hits: list[Hit]) -> list[Hit]:
        wanted = _query_terms(query, self.ctx)
        return [
            Hit(self.by_id[hit.document.id], hit.score)
            for hit in _ordered(hits, _OUTPUT_LIMIT)
            if hit.document.id in self.by_id
            and math.isfinite(hit.score) and hit.score > 0
            and wanted.intersection(self.words[hit.document.id])
        ][:_SEED_LIMIT]


def _query_terms(query: str, ctx: Context) -> frozenset[str]:
    return frozenset(ctx.tokens(ctx.focused(query[:_TEXT_LIMIT]))[:24])


def _finish(hits: list[Hit], additions: list[Hit]) -> list[Hit]:
    baseline = {hit.document.id: hit for hit in _ordered(hits, _OUTPUT_LIMIT)}
    # Multiple routes to one document provide at most one support boost.
    for support in _ordered(additions, _ADDITION_LIMIT):
        previous = baseline.get(support.document.id)
        baseline[support.document.id] = Hit(
            support.document, support.score + (previous.score if previous else 0.0)
        )
    return _ordered(list(baseline.values()), _OUTPUT_LIMIT)


def entity_bridge(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    wanted = _query_terms(query, ctx)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        for name in _names(seed.document.text, ctx):
            if wanted.intersection(corpus.key(name)):
                continue
            for target in corpus.lookup(name, rare=True):
                if target.message_id != seed.document.message_id:
                    additions.append(Hit(target, seed.score * 0.85))
    return _finish(hits, additions)


@dataclass(frozen=True)
class _Relation:
    left: str
    right: str
    document: Document


_ALIAS = re.compile(
    r"^(.{1,96}?)\s+(?:is\s+)?"
    r"(?:also\s+known\s+as|also\s+called|abbreviated\s+as|aka)\s+(.{1,96})$",
    re.IGNORECASE,
)
_DEFINITION = re.compile(
    r"^(.{1,96}?)\s+(?:means|is\s+defined\s+as|refers\s+to)\s+(.{1,180})$",
    re.IGNORECASE,
)


def _relations(corpus: _Corpus, pattern: re.Pattern) -> list[_Relation]:
    result = []
    for document in corpus.documents:
        for unit in _units(document):
            match = pattern.fullmatch(unit)
            if match and _label(match[1]) and match[2].strip():
                result.append(_Relation(_label(match[1]), match[2].strip(), document))
    return result


def alias_chain(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    relations = [
        relation for relation in _relations(corpus, _ALIAS) if _label(relation.right)
    ]
    by_label: dict[tuple[str, ...], list[tuple[str, Document]]] = defaultdict(list)
    by_document: dict[str, list[_Relation]] = defaultdict(list)
    for relation in relations:
        by_label[corpus.key(relation.left)].append((relation.right, relation.document))
        by_label[corpus.key(relation.right)].append((relation.left, relation.document))
        by_document[relation.document.id].append(relation)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        frontier = [
            (corpus.key(label), seed.score)
            for relation in by_document[seed.document.id]
            for label in (relation.left, relation.right)
            if corpus.has(query[:_TEXT_LIMIT], label)
        ][:_PIVOT_LIMIT]
        visited = {key for key, _ in frontier}
        for depth in range(_HOPS):
            following = []
            for key, score in frontier:
                for alias, source in by_label[key][:_FANOUT]:
                    if depth == 0 and source.id != seed.document.id:
                        continue
                    next_key = corpus.key(alias)
                    if next_key in visited:
                        continue
                    visited.add(next_key)
                    additions.append(Hit(source, score * 0.85))
                    for target in corpus.lookup(alias):
                        if target.message_id != source.message_id:
                            additions.append(Hit(target, score * 0.8))
                    following.append((next_key, score * 0.8))
            frontier = following[:_FANOUT]
    return _finish(hits, additions)


def definition_chain(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    relations = _relations(corpus, _DEFINITION)
    by_document: dict[str, list[_Relation]] = defaultdict(list)
    by_first: dict[str, list[_Relation]] = defaultdict(list)
    for relation in relations:
        words = corpus.key(relation.left)
        if words:
            by_document[relation.document.id].append(relation)
            by_first[words[0]].append(relation)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        frontier = [
            (relation, seed.score) for relation in by_document[seed.document.id]
            if corpus.has(query[:_TEXT_LIMIT], relation.left)
        ][:_PIVOT_LIMIT]
        visited = {relation for relation, _ in frontier}
        for _ in range(_HOPS):
            following = []
            for relation, score in frontier:
                words = corpus.key(relation.right)
                for word in dict.fromkeys(words):
                    for target in by_first.get(word, ())[:_FANOUT]:
                        if target in visited:
                            continue
                        if _contains(words, corpus.key(target.left)):
                            visited.add(target)
                            additions.append(Hit(target.document, score * 0.85))
                            following.append((target, score * 0.85))
            frontier = following[:_FANOUT]
    return _finish(hits, additions)


_REFERENCE = re.compile(
    r"\b(?:see|refer\s+to|details\s+in|continued\s+in)\s+"
    r"(note|section|entry)\s+([A-Z0-9][A-Z0-9_.-]{0,48})",
    re.IGNORECASE,
)
_ANCHOR = re.compile(
    r"^\s*(?:#{1,6}\s+)?(note|section|entry)\s+"
    r"([A-Z0-9][A-Z0-9_.-]{0,48})\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+([^\n]{1,96})$", re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]\n]{1,96})\]\]")


def reference_chain(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    anchors: dict[tuple[str, str], list[Document]] = defaultdict(list)
    links: dict[str, list[tuple[str, str]]] = {}
    for document in corpus.documents:
        keys = [(m[1].casefold(), m[2].casefold()) for m in _ANCHOR.finditer(document.text)]
        keys.extend(("heading", m[1].strip().casefold()) for m in _HEADING.finditer(document.text))
        for key in dict.fromkeys(keys):
            anchors[key].append(document)
        links[document.id] = [
            (m[1].casefold(), m[2].rstrip(".").casefold())
            for m in _REFERENCE.finditer(document.text)
        ]
        links[document.id].extend(
            ("heading", m[1].strip().casefold()) for m in _WIKILINK.finditer(document.text)
        )
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        frontier, visited = [seed], {seed.document.message_id}
        for _ in range(_HOPS):
            following = []
            for source in frontier:
                for key in links[source.document.id][:_PIVOT_LIMIT]:
                    targets = anchors.get(key, ())
                    if len({target.message_id for target in targets}) != 1:
                        continue
                    for target in targets[:_FANOUT]:
                        if target.message_id not in visited:
                            addition = Hit(target, source.score * 0.85)
                            additions.append(addition)
                            following.append(addition)
                    visited.update(target.message_id for target in targets)
            frontier = following[:_FANOUT]
    return _finish(hits, additions)


_ATTRIBUTE = re.compile(
    r"^[ \t]*([^.\n|:=]{1,96})[.|][ \t]*([\w -]{1,48})[ \t]*[:=|][ \t]*([^\n]{1,96})$",
    re.MULTILINE,
)


def attribute_chain(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    by_subject: dict[tuple[str, ...], list[_Relation]] = defaultdict(list)
    by_document: dict[str, list[_Relation]] = defaultdict(list)
    for document in corpus.documents:
        for match in list(_ATTRIBUTE.finditer(document.text))[:_FANOUT]:
            subject, value = _label(match[1]), _label(match[3])
            if subject and value:
                relation = _Relation(subject, value, document)
                by_subject[corpus.key(subject)].append(relation)
                by_document[document.id].append(relation)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        frontier = [
            (relation, seed.score) for relation in by_document[seed.document.id]
            if corpus.has(query[:_TEXT_LIMIT], relation.left)
        ][:_PIVOT_LIMIT]
        visited = {relation for relation, _ in frontier}
        for _ in range(_HOPS):
            following = []
            for source, score in frontier:
                for target in by_subject.get(corpus.key(source.right), ())[:_FANOUT]:
                    if target not in visited:
                        visited.add(target)
                        additions.append(Hit(target.document, score * 0.85))
                        following.append((target, score * 0.85))
            frontier = following[:_FANOUT]
    return _finish(hits, additions)


_BULLET = re.compile(r"^[ \t]{0,3}(?:[-*+]|\d+[.)])[ \t]+(.{1,96})$")


def list_member_bridge(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    wanted = _query_terms(query, ctx)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        active, count = False, 0
        for line in seed.document.text.splitlines()[:48]:
            match = _BULLET.fullmatch(line)
            if match:
                if active and count < _FANOUT:
                    member = _label(match[1])
                    for target in corpus.lookup(member):
                        if target.message_id != seed.document.message_id:
                            additions.append(Hit(target, seed.score * 0.85))
                    count += 1
            elif line.strip():
                active = (
                    line.rstrip().endswith(":") or line.lstrip().startswith("#")
                ) and bool(wanted.intersection(corpus.key(line)))
                count = 0
    return _finish(hits, additions)


def qa_entity_bridge(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    wanted = _query_terms(query, ctx)
    grouped = defaultdict(list)
    for message in ctx.messages:
        grouped[message.session_id, message.request_id].append(message)
    responses = {}
    for messages in grouped.values():
        parent = None
        for message in sorted(messages, key=lambda m: (m.position, m.ordinal, m.id)):
            if message.role == "user":
                parent = message
            elif message.role == "assistant" and parent is not None:
                responses.setdefault(parent.id, message.id)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        if seed.document.role != "user" or not re.search(r"[?\uff1f]", seed.document.text):
            continue
        response_id = responses.get(seed.document.message_id)
        for response in ctx.message_documents.get(response_id, ())[:4]:
            if response.id not in corpus.by_id:
                continue
            missing = wanted.difference(corpus.words[seed.document.id], corpus.words[response.id])
            if not missing:
                continue
            for name in _names(response.text, ctx):
                if set(corpus.key(name)).intersection(corpus.words[seed.document.id]):
                    continue
                for target in corpus.lookup(name, rare=True):
                    if (target.session_id, target.request_id) == (
                        response.session_id, response.request_id
                    ):
                        continue
                    if missing.intersection(corpus.words[target.id]):
                        additions.append(Hit(response, seed.score * 0.9))
                        additions.append(Hit(target, seed.score * 0.85))
    return _finish(hits, additions)


_URL = re.compile(r"https?://[^\s<>()\[\]\"']+", re.IGNORECASE)


def _resources(document: Document) -> list[tuple[str, str, str, str]]:
    result = []
    for match in list(_URL.finditer(document.text))[:_PIVOT_LIMIT]:
        try:
            url = urlsplit(match.group().rstrip(".,;!"))
            if url.hostname and not (url.username or url.password or url.query):
                result.append((url.scheme.lower(), url.netloc.lower(), url.path or "/", url.fragment))
        except ValueError:
            continue
    return list(dict.fromkeys(result))


def resource_bridge(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    by_resource = defaultdict(list)
    resources = {}
    for document in corpus.documents:
        resources[document.id] = _resources(document)
        for resource in resources[document.id]:
            by_resource[resource].append(document)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        for resource in resources[seed.document.id]:
            for target in by_resource[resource][:_FANOUT]:
                if target.message_id != seed.document.message_id:
                    additions.append(Hit(target, seed.score * 0.85))
    return _finish(hits, additions)


_QUOTE = re.compile(r'"([^"\n]{12,180})"|\u201c([^\u201d\n]{12,180})\u201d|^>[ \t]+([^\n]{12,180})$',
                    re.MULTILINE)


def quotation_bridge(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    additions: list[Hit] = []
    for seed in corpus.seeds(query, hits):
        for match in list(_QUOTE.finditer(seed.document.text))[:_PIVOT_LIMIT]:
            quote = next(part for part in match.groups() if part is not None).strip()
            if not 4 <= len(corpus.key(quote)) <= 24:
                continue
            for target in corpus.lookup(quote, rare=True):
                if target.message_id != seed.document.message_id and quote in target.text:
                    additions.append(Hit(target, seed.score * 0.85))
    return _finish(hits, additions)


def converging_entity_paths(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    corpus = _Corpus(ctx)
    wanted = _query_terms(query, ctx)
    support = defaultdict(list)
    for seed in corpus.seeds(query, hits):
        coverage = wanted.intersection(corpus.words[seed.document.id])
        for name in _names(seed.document.text, ctx):
            key = corpus.key(name)
            if wanted.intersection(key):
                continue
            for target in corpus.lookup(name, rare=True):
                if target.message_id != seed.document.message_id:
                    support[target.id].append((seed, key, coverage))
    additions: list[Hit] = []
    for identifier, paths in support.items():
        best = 0.0
        for index, (left, left_key, left_coverage) in enumerate(paths):
            for right, right_key, right_coverage in paths[index + 1:]:
                if (
                    left.document.message_id != right.document.message_id
                    and left_key != right_key
                    and left_coverage - right_coverage and right_coverage - left_coverage
                ):
                    best = max(best, min(left.score, right.score) * 0.9)
        if best:
            additions.append(Hit(corpus.by_id[identifier], best))
    return _finish(hits, additions)


def experiments() -> list[Experiment]:
    specifications = [
        (
            "Source entity bridge",
            "A name shared with a query-matched source can connect otherwise unmatched evidence.",
            "Join through up to six non-query capitalized names; reject high-fanout pivots.",
            "Capitalization misses lowercase names and cannot distinguish homonyms.",
            entity_bridge,
        ),
        (
            "Explicit alias chain",
            "Source-stated aliases can connect a query name to differently named records.",
            "Walk at most two bidirectional explicit alias edges and acquire literal alias mentions.",
            "Only explicit English alias syntax is recognized; conflicting aliases remain ambiguous.",
            alias_chain,
        ),
        (
            "Definition dependency chain",
            "A definition can refer to another locally defined term needed to interpret it.",
            "Follow defined terms occurring in definition bodies for at most two directed steps.",
            "Only explicit means, defined-as and refers-to clauses are supported, not inferred meaning.",
            definition_chain,
        ),
        (
            "Source reference chain",
            "Explicit note and heading references connect evidence across source messages.",
            "Follow two source-written references to unique textual anchors, with cycle checks.",
            "Unresolved or ambiguous anchors and references beyond the hop limit are skipped.",
            reference_chain,
        ),
        (
            "Structured attribute chain",
            "A field value that names another record can connect subjects to indirect properties.",
            "Join subject.field:value or pipe-delimited records by exact value-to-subject keys.",
            "Unstructured prose, nested values and more than two successive joins are unsupported.",
            attribute_chain,
        ),
        (
            "List membership bridge",
            "A query-matched group heading can lead to separately described member items.",
            "Join up to eight short bullet or numbered members to exact source mentions.",
            "Only local heading-list structure is used; nested and descriptive items can be missed.",
            list_member_bridge,
        ),
        (
            "Indirect question entity bridge",
            "An entity supplied in a response can connect a question to a missing query facet.",
            "Use a request-local Q&A only as a bridge to an external entity-and-facet source.",
            "Requires an explicit question, a named response entity and a missing lexical facet.",
            qa_entity_bridge,
        ),
        (
            "Shared resource bridge",
            "Separate source mentions of the same resource can supply complementary facts.",
            "Join literal HTTP resource identities locally without dereferencing any address.",
            "URLs with credentials or query strings are excluded; different paths stay separate.",
            resource_bridge,
        ),
        (
            "Exact quotation bridge",
            "A quoted passage can connect a matched discussion to its fuller source context.",
            "Join bounded quotations of four to twenty-four tokens to exact original substrings.",
            "Literal overlap does not prove authorship; paraphrases and short quotations are missed.",
            quotation_bridge,
        ),
        (
            "Converging entity paths",
            "Two independent entity paths can isolate evidence linking separate query facets.",
            "Require distinct names and source messages with complementary query coverage at a join.",
            "Needs two explicit facets and shared names; a single valid path is intentionally ignored.",
            converging_entity_paths,
        ),
    ]
    return [
        Experiment(
            id=f"D07.{number:02d}",
            direction="D07",
            name=name,
            hypothesis=hypothesis,
            mechanism=mechanism,
            limitation=limitation + (
                " Expansion caps sources at 6000, text at 2400 characters, additions at 64, "
                "and skips marked private or recognizable sensitive messages."
            ),
            retrieval_hook=hook,
        )
        for number, (name, hypothesis, mechanism, limitation, hook) in enumerate(specifications, 1)
    ]
