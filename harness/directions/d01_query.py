"""D01: deterministic query decomposition experiments."""

from __future__ import annotations

import re
from collections.abc import Iterable

from harness.types import Context, Experiment


_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*|\d+(?:[.:/-]\d+)*", re.UNICODE)
_QUESTION_WORDS = {
    "can",
    "could",
    "did",
    "do",
    "does",
    "how",
    "is",
    "may",
    "should",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "would",
}


def _queries(
    original: str, ctx: Context, variants: Iterable[str]
) -> list[tuple[str, float]]:
    """Preserve baseline weights; add each distinct alternative token bag once."""
    result = [(original, 2.0)]
    focused = ctx.focused(original)
    if focused and focused != " ".join(ctx.tokens(original)):
        result.append((focused, 2.0))
    seen = {frozenset(ctx.tokens(original)), frozenset(ctx.tokens(focused))}
    for variant in variants:
        cleaned = re.sub(r"\s+", " ", variant).strip(" \t\r\n,;:.?!")
        terms = frozenset(ctx.tokens(cleaned))
        if terms and terms not in seen:
            result.append((cleaned, 0.72))
            seen.add(terms)
    return result


def _clause_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    pieces = re.split(
        r"(?:[;；\n]+|(?<=[.!?。！？])\s+|\s+(?:and|but|while|whereas)\s+)",
        query,
        flags=re.IGNORECASE,
    )
    variants = pieces if len(pieces) > 1 else ()
    return _queries(
        query, ctx, (piece for piece in variants if len(_WORD_RE.findall(piece)) >= 2)
    )


def _enumeration_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    tail = query.rsplit(":", 1)[-1]
    if "," not in tail and "、" not in tail and ";" not in tail:
        return _queries(query, ctx, ())
    items = re.split(r"\s*(?:,|、|;|\band\b|\bor\b)\s*", tail, flags=re.IGNORECASE)
    return _queries(query, ctx, (item for item in items if _WORD_RE.search(item)))


def _quoted_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    quoted = re.findall(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’|\'([^\'\n]+)\'', query)
    return _queries(query, ctx, ("".join(groups) for groups in quoted))


def _parenthetical_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    contents = re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", query)
    main = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", query)
    variants = [main]
    variants.extend("".join(groups) for groups in contents)
    return _queries(query, ctx, variants)


def _entity_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*", query)
    entities: list[str] = []
    run: list[str] = []
    for token in tokens + [""]:
        entity_like = bool(
            token
            and (
                token.isupper()
                or any(char.isdigit() for char in token)
                or token[0].isupper()
            )
        )
        if entity_like:
            if not run and token.casefold() in _QUESTION_WORDS:
                continue
            run.append(token)
        elif run:
            entities.append(" ".join(run))
            run = []
    return _queries(query, ctx, entities)


def _relation_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    marker = (
        r"before|after|during|because\s+of|caused\s+by|owned\s+by|part\s+of|"
        r"used\s+for|works?\s+with|reported\s+to"
    )
    token = r"[A-Za-z0-9][\w'.-]*"
    relation = re.compile(
        rf"((?:{token}\s+){{0,3}}{token})\s+({marker})\s+"
        rf"({token}(?:\s+{token}){{0,3}})",
        re.IGNORECASE,
    )
    variants = (" ".join(match.groups()) for match in relation.finditer(query))
    return _queries(query, ctx, variants)


def _question_core_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    core = re.sub(
        r"^\s*(?:who|what|when|where|why|how|which)\b(?:\s+\w+){0,2}\s+",
        "",
        query,
        count=1,
        flags=re.IGNORECASE,
    )
    return _queries(query, ctx, (core,))


def _temporal_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    months = (
        r"January|February|March|April|May|June|July|August|September|October|"
        r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    )
    pattern = re.compile(
        rf"\b(?:{months})\.?\s+\d{{4}}\b|\b(?:19|20)\d{{2}}\b|"
        r"\bQ[1-4]\s*(?:19|20)?\d{2}\b|"
        r"\b(?:yesterday|today|tomorrow|last|next|previous|following)\s+"
        r"(?:day|week|month|quarter|year)\b",
        re.IGNORECASE,
    )
    return _queries(query, ctx, (match.group(0) for match in pattern.finditer(query)))


def _contrast_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    parts = re.split(
        r"\s+(?:vs\.?|versus|rather\s+than|instead\s+of|but\s+not)\s+",
        query,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    return _queries(query, ctx, parts if len(parts) == 2 else ())


def _rare_term_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for position, token in enumerate(ctx.tokens(query)):
        key = token.casefold()
        if key in seen or len(key) < 2:
            continue
        seen.add(key)
        scored.append((ctx.idf.get(key, 0.0), -position, token))
    scored.sort(reverse=True)
    return _queries(query, ctx, (token for _, _, token in scored[:4]))


def experiments() -> list[Experiment]:
    """Return exactly ten independent query-decomposition experiments."""
    specs = [
        (
            "D01.01",
            "Punctuation and conjunction clauses",
            "Independent clauses retrieve evidence that a long compound query can dilute.",
            "Split on sentence punctuation, semicolons, newlines, and major conjunctions.",
            "Conjunctions inside a single concept can be split incorrectly.",
            _clause_hook,
        ),
        (
            "D01.02",
            "Enumeration items",
            "Searching list members separately reduces competition among enumerated terms.",
            "Detect comma, semicolon, or ideographic-comma lists and emit each item.",
            "Ordinary comma-separated prose can look like an enumeration.",
            _enumeration_hook,
        ),
        (
            "D01.03",
            "Quoted phrase islands",
            "Quoted text often names the exact phrase that should match memory.",
            "Emit spans inside straight or typographic quotation marks.",
            "Quotations used for emphasis may not be retrieval entities.",
            _quoted_hook,
        ),
        (
            "D01.04",
            "Parenthetical dual view",
            "Main text and parenthetical qualifiers may match different source wording.",
            "Emit the query without bracketed text and each bracketed span separately.",
            "Nested or unmatched brackets are intentionally not parsed.",
            _parenthetical_hook,
        ),
        (
            "D01.05",
            "Named-entity islands",
            "Capitalized, acronym, and alphanumeric spans provide high-precision anchors.",
            "Extract consecutive entity-like Latin tokens without an external entity model.",
            "Sentence-initial capitalization can create false entities.",
            _entity_hook,
        ),
        (
            "D01.06",
            "Relation-centered propositions",
            "Subject-relation-object fragments preserve relational intent in shorter queries.",
            "Extract bounded token windows around common temporal and semantic relations.",
            "The fixed relation vocabulary misses implicit and uncommon relations.",
            _relation_hook,
        ),
        (
            "D01.07",
            "Interrogative core",
            "Removing a leading question scaffold makes content terms dominate lexical search.",
            "Strip a bounded leading wh-question phrase while retaining the original query.",
            "Complex inversion can leave an incomplete or ungrammatical core.",
            _question_core_hook,
        ),
        (
            "D01.08",
            "Temporal anchors",
            "Dates and relative periods can independently recover time-specific evidence.",
            "Extract years, month-year forms, quarters, and simple relative periods.",
            "A time expression alone can retrieve unrelated events from the same period.",
            _temporal_hook,
        ),
        (
            "D01.09",
            "Contrast branches",
            "Searching both sides of an explicit contrast can recover evidence for either choice.",
            "Split once on versus, rather than, instead of, or but not.",
            "The two branches may depend on shared context lost by the split.",
            _contrast_hook,
        ),
        (
            "D01.10",
            "Corpus-rare term islands",
            "High-IDF query terms can recover precise evidence hidden by common words.",
            "Emit up to four unique query tokens ordered by corpus IDF and source position.",
            "Rare misspellings and identifiers can receive misleading priority.",
            _rare_term_hook,
        ),
    ]
    return [
        Experiment(
            id=experiment_id,
            direction="D01 query decomposition",
            name=name,
            hypothesis=hypothesis,
            mechanism=mechanism,
            limitation=limitation,
            query_hook=hook,
        )
        for experiment_id, name, hypothesis, mechanism, limitation, hook in specs
    ]
