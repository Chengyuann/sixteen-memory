"""Independent source-only follow-up mechanisms, fixed before new validation."""

import math
import re
from collections import Counter, defaultdict
from .engine import focused, tokens
from .types import Experiment, Hit

PRONOUNS = set(
    "i me my mine we us our ours you your yours he him his she her hers it its they them their theirs who what where when why how which can could would should do does did is are was were has have had a an the this that these those yes no please".split()
)
PRIVATE = re.compile(
    r"\b(?:private|confidential|password|passcode|ssn|api[_ -]?key|do not share)\b", re.I
)
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*")


def entities(query):
    result, run = [], []
    for match in list(WORD.finditer(query)) + [None]:
        word = match.group() if match else ""
        eligible = (
            len(word) > 1
            and word.lower() not in PRONOUNS
            and (word[0].isupper() or any(c.isdigit() for c in word))
        )
        if eligible:
            run.append(word)
        elif run:
            result.append(" ".join(run))
            run = []
    return result[:4]


def query_base(query, ctx):
    result = [(query, 2.0)]
    f = ctx.focused(query)
    if f and f != " ".join(ctx.tokens(query)):
        result.append((f, 2.0))
    return result


def entity_queries(query, ctx):
    result = query_base(query, ctx)
    seen = {frozenset(ctx.tokens(x)) for x, _ in result}
    for phrase in entities(query):
        words = frozenset(ctx.tokens(phrase))
        if words not in seen and any(words <= ctx.doc_tokens[d.id] for d in ctx.documents):
            result.append((phrase, 0.5))
            seen.add(words)
    return result


def clauses(query, ctx):
    result = query_base(query, ctx)
    seen = {frozenset(ctx.tokens(x)) for x, _ in result}
    pieces = re.split(r"[;；\n]+|\s+(?:and|but|while|whereas|versus)\s+", query, flags=re.I)
    if len(pieces) < 2:
        return result
    for piece in pieces[:4]:
        words = frozenset(ctx.tokens(ctx.focused(piece)))
        if len(words) >= 2 and words not in seen:
            result.append((piece, 0.5))
            seen.add(words)
    return result


def rerank(query, hits, mode):
    """Never adds or drops baseline top100; score ties retain baseline position."""
    rows = list(hits[:100])
    if not rows:
        return rows
    q = set(tokens(focused(query)))
    words = [set(tokens(h.document.text)) for h in rows]
    scores = [h.score for h in rows]
    if mode == "session":
        if len(q) < 2:
            return rows
        df = Counter(t for w in words for t in w)
        idf = {t: math.log(1 + (len(rows) + 1) / (df.get(t, 0) + 1)) for t in q}
        cover = defaultdict(set)
        for h, w in zip(rows, words):
            cover[h.document.session_id].update(q & w)
        total = sum(idf.values())
        for i, h in enumerate(rows):
            if q & words[i]:
                scores[i] *= 1 + 0.2 * sum(idf[t] for t in cover[h.document.session_id]) / total
    elif mode == "parent":
        positions = defaultdict(list)
        for i, h in enumerate(rows):
            if h.document.role == "user":
                positions[(h.document.request_id, h.document.session_id)].append(i)
        for i, h in enumerate(rows[:24]):
            d = h.document
            if d.role != "assistant":
                continue
            candidates = [
                j
                for j in positions[d.request_id, d.session_id]
                if rows[j].document.position < d.position
            ]
            if not candidates:
                continue
            parent = max(candidates, key=lambda j: rows[j].document.position)
            pd = rows[parent].document
            if "?" not in pd.text or PRIVATE.search(pd.text) or not (q & words[parent]):
                continue
            scores[parent] = max(scores[parent], h.score * 0.98)
    elif mode == "message":
        seen = Counter()
        for i, h in enumerate(rows):
            key = h.document.message_id
            if i >= 5 and seen[key]:
                scores[i] *= 0.75
            seen[key] += 1
    elif mode == "entity":
        phrases = [set(tokens(p)) for p in entities(query)]
        if not phrases:
            return rows
        for i, w in enumerate(words):
            scores[i] *= 1 + 0.2 * sum(p <= w for p in phrases) / len(phrases)
    elif mode == "current":
        if not re.search(
            r"\b(?:current|currently|latest|now|most recent)\b|当前|最新|现在", query, re.I
        ):
            return rows
        if re.search(
            r"\b(?:before|previous|historical|formerly)\b|\b(?:19|20)\d{2}\b", query, re.I
        ):
            return rows
        dates = [h.document.timestamp for h in rows if h.document.timestamp is not None]
        if len(set(dates)) < 2:
            return rows
        lo, hi = min(dates), max(dates)
        for i, h in enumerate(rows):
            if h.document.timestamp is not None and q & words[i]:
                scores[i] *= 1 + 0.15 * (h.document.timestamp - lo) / (hi - lo)
    else:
        raise ValueError(mode)
    order = sorted(range(len(rows)), key=lambda i: (-scores[i], i))
    return [Hit(rows[i].document, scores[i]) for i in order]


def session(query, hits, ctx):
    return rerank(query, hits, "session")


def parent(query, hits, ctx):
    return rerank(query, hits, "parent")


def message(query, hits, ctx):
    return rerank(query, hits, "message")


def entity(query, hits, ctx):
    return rerank(query, hits, "entity")


def current(query, hits, ctx):
    return rerank(query, hits, "current")


def load():
    specs = [
        (
            "T01",
            "Validated entity query",
            "Exclude pronouns and require source-supported entity anchors.",
            entity_queries,
            "query",
        ),
        (
            "T02",
            "Bounded session coverage",
            "Use joint source query coverage without replacing baseline top100.",
            session,
            "retrieval",
        ),
        (
            "T03",
            "In-set prompt parent",
            "Promote directly query-linked source prompts already within top100.",
            parent,
            "retrieval",
        ),
        (
            "T04",
            "Repeated-message penalty",
            "Protect top5 and diversify remaining message evidence without dropping it.",
            message,
            "retrieval",
        ),
        (
            "T05",
            "Exact entity support",
            "Boost meaningful named-entity support without adding evidence.",
            entity,
            "retrieval",
        ),
        (
            "T06",
            "Current-state recency",
            "Apply mild recency only for explicit undated current-state queries.",
            current,
            "retrieval",
        ),
        (
            "T07",
            "Clause query union",
            "Retrieve distinct multi-clause concepts alongside original and focused queries.",
            clauses,
            "query",
        ),
    ]
    return [
        Experiment(
            i,
            i,
            name,
            hyp,
            hyp,
            "Lexical heuristic; not semantic answer quality or calibrated privacy.",
            **{kind + "_hook": fn},
        )
        for i, name, hyp, fn, kind in specs
    ]
