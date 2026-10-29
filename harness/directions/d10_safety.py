"""Privacy and abstention experiments over extractive retrieval results."""

from __future__ import annotations

from dataclasses import replace
import re

from harness.types import Context, Experiment, Hit


_QUOTED_RE = re.compile(r'"([^"\n]+)"|[\u201c]([^\u201d\n]+)[\u201d]')
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_CARD_RE = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![ -]?\d)")
_SECRET_RE = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|secret|token|otp|pin)\b"
    r"""\s*(?:is|:|=)\s*(?:"(?P<double>[^"\n]+)"|'(?P<single>[^'\n]+)'"""
    r"|(?P<value>[^\s,;]+))",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(
    r"^\s*(?:if|suppose|imagine|hypothetically|for\s+example)\b"
    r"|\b(?:would|could|might|may)\s+(?:be|have|use)\b"
    r"|\b(?:do\s+not|don't|does\s+not|doesn't)\s+know\b"
    r"|\b(?:unknown|not\s+(?:known|recorded|available)|no\s+record\s+of)\b"
    r"|\b(?:placeholder|hypothetical|example\s+only)\b",
    re.IGNORECASE,
)
_CONFIDENTIAL_RE = re.compile(
    r"\b(?:confidential|private|do\s+not\s+share|internal\s+only|off\s+the\s+record"
    r"|keep\s+this\s+between\s+us)\b"
    r"|\u4fdd\u5bc6|\u4e0d\u8981\u5206\u4eab|\u4ec5\u9650\u672c\u4eba",
    re.IGNORECASE,
)
_CATEGORY_PATTERNS = {
    "contact": re.compile(
        r"\b(?:email|e-mail|phone|telephone|mobile|contact|home\s+address|mailing\s+address)\b",
        re.IGNORECASE,
    ),
    "credential": re.compile(
        r"\b(?:password|passcode|api[_ -]?key|secret|token|otp|pin|credential)\b",
        re.IGNORECASE,
    ),
    "financial": re.compile(
        r"\b(?:bank|account\s+number|routing\s+number|credit\s+card|debit\s+card"
        r"|salary|income|tax)\b",
        re.IGNORECASE,
    ),
    "health": re.compile(
        r"\b(?:health|medical|diagnosis|diagnosed|medication|therapy|illness|disability)\b",
        re.IGNORECASE,
    ),
    "identity": re.compile(
        r"\b(?:ssn|social\s+security|passport|national\s+id|driver'?s?\s+license"
        r"|identity\s+number)\b",
        re.IGNORECASE,
    ),
}
_CATEGORY_TERMS = frozenset(
    {
        "account",
        "address",
        "api",
        "bank",
        "card",
        "contact",
        "credential",
        "credit",
        "debit",
        "diagnosis",
        "diagnosed",
        "disability",
        "driver",
        "email",
        "financial",
        "health",
        "identity",
        "illness",
        "income",
        "key",
        "license",
        "mailing",
        "medical",
        "medication",
        "mobile",
        "national",
        "number",
        "otp",
        "passcode",
        "passport",
        "password",
        "phone",
        "pin",
        "routing",
        "salary",
        "secret",
        "security",
        "social",
        "ssn",
        "tax",
        "telephone",
        "therapy",
        "token",
    }
)
_QUERY_STOP = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "can",
        "did",
        "do",
        "does",
        "find",
        "for",
        "give",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "please",
        "s",
        "show",
        "tell",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
    }
)
_USER_ROLE_RE = re.compile(
    r"\b(?:i|me|my|user)\b.{0,24}\b(?:said|wrote|mentioned|told)\b"
    r"|\bwhat\s+did\s+i\s+say\b|\u6211\u8bf4|\u7528\u6237\u8bf4",
    re.IGNORECASE,
)
_ASSISTANT_ROLE_RE = re.compile(
    r"\b(?:you|assistant|bot)\b.{0,24}\b(?:said|wrote|mentioned|told)\b"
    r"|\bwhat\s+did\s+you\s+say\b|\u4f60\u8bf4|\u52a9\u624b\u8bf4",
    re.IGNORECASE,
)


def _tokens(text: str, ctx: Context) -> tuple[str, ...]:
    values = ctx.tokens(text)
    return tuple(token.casefold() for token in values if token.strip())


def _query_terms(query: str, ctx: Context) -> frozenset[str]:
    return frozenset(token for token in _tokens(query, ctx) if token not in _QUERY_STOP)


def _document_terms(hit: Hit, ctx: Context) -> frozenset[str]:
    return frozenset(_tokens(hit.document.text, ctx))


def _direct_ids(query: str, hits: list[Hit], ctx: Context) -> set[str]:
    terms = _query_terms(query, ctx)
    if not terms:
        return set()
    return {
        hit.document.id
        for hit in hits
        if terms.intersection(_document_terms(hit, ctx))
    }


def _card_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for match in _CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        # A Luhn check avoids labelling every long identifier as a card number.
        checksum = 0
        for index, value in enumerate(reversed(digits)):
            digit = int(value)
            if index % 2:
                digit *= 2
                digit = digit - 9 if digit > 9 else digit
            checksum += digit
        if checksum % 10 == 0:
            spans.append((*match.span(), digits))
    return spans


def _phone_values(text: str) -> frozenset[str]:
    cards = _card_spans(text)
    phones = set()
    for match in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if not 10 <= len(digits) <= 15:
            continue
        if any(start < match.end() and match.start() < end for start, end, _ in cards):
            continue
        phones.add(digits)
    return frozenset(phones)


def _categories(text: str) -> frozenset[str]:
    categories = {
        category for category, pattern in _CATEGORY_PATTERNS.items() if pattern.search(text)
    }
    if _EMAIL_RE.search(text) or _phone_values(text):
        categories.add("contact")
    if _SSN_RE.search(text):
        categories.add("identity")
    if _card_spans(text):
        categories.add("financial")
    if _SECRET_RE.search(text):
        categories.add("credential")
    return frozenset(categories)


def _sensitive_literals(text: str) -> frozenset[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    values.update(("email", match.group().casefold()) for match in _EMAIL_RE.finditer(text))
    values.update(("phone", value) for value in _phone_values(text))
    values.update(("identity", match.group()) for match in _SSN_RE.finditer(text))
    values.update(
        ("financial", value) for _, _, value in _card_spans(text)
    )
    values.update(
        ("credential", match["double"] or match["single"] or match["value"])
        for match in _SECRET_RE.finditer(text)
    )
    return frozenset(values)


def _subject_terms(query: str, ctx: Context) -> frozenset[str]:
    return frozenset(
        token
        for token in _query_terms(query, ctx)
        if token not in _CATEGORY_TERMS and len(token) > 1
    )


def _direct_evidence(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    direct = _direct_ids(query, hits, ctx)
    return [hit for hit in hits if hit.document.id in direct]


def _quoted_evidence(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    quoted = tuple(
        next(group for group in match.groups() if group is not None).casefold()
        for match in _QUOTED_RE.finditer(query)
    )
    if not quoted:
        return list(hits)
    return [
        hit
        for hit in hits
        if all(value in hit.document.text.casefold() for value in quoted)
    ]


def _same_message_context(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    direct = _direct_ids(query, hits, ctx)
    if not direct:
        return []
    messages = {
        hit.document.message_id for hit in hits if hit.document.id in direct
    }
    return [
        hit
        for hit in hits
        if hit.document.id in direct or hit.document.message_id in messages
    ]


def _role_intent(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    wants_user = bool(_USER_ROLE_RE.search(query))
    wants_assistant = bool(_ASSISTANT_ROLE_RE.search(query))
    if wants_user == wants_assistant:
        return list(hits)
    role = "user" if wants_user else "assistant"
    return [hit for hit in hits if hit.document.role.casefold() == role]


def _category_intent(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    requested = _categories(query)
    output: list[Hit] = []
    for hit in hits:
        source_categories = _categories(hit.document.text)
        if source_categories.issubset(requested):
            output.append(hit)
    return output


def _subject_binding(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    subjects = _subject_terms(query, ctx)
    output: list[Hit] = []
    for hit in hits:
        if not _categories(hit.document.text):
            output.append(hit)
            continue
        if subjects and subjects.issubset(_document_terms(hit, ctx)):
            output.append(hit)
    return output


def _exact_sensitive_value(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    requested = _sensitive_literals(query)
    if not requested:
        return list(hits)
    return [
        hit
        for hit in hits
        if requested.issubset(_sensitive_literals(hit.document.text))
    ]


def _privacy_marker(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del query, ctx
    return [hit for hit in hits if not _CONFIDENTIAL_RE.search(hit.document.text)]


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    boundaries = re.finditer(r"(?:[.!?]+(?=\s|$)|[\u3002\uff01\uff1f]+|\n+)", text)
    for boundary in boundaries:
        end = boundary.end()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
        start = boundary.end()
    end = len(text.rstrip())
    while start < end and text[start].isspace():
        start += 1
    if start < end:
        spans.append((start, end))
    return tuple(spans)


def _least_disclosure(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    query_terms = _query_terms(query, ctx)
    output: list[Hit] = []
    for hit in hits:
        source = hit.document
        if not _categories(source.text):
            output.append(hit)
            continue
        candidates: list[tuple[int, int]] = []
        for start, end in _sentence_spans(source.text):
            text = source.text[start:end]
            sentence_terms = frozenset(_tokens(text, ctx))
            if query_terms and query_terms.issubset(sentence_terms):
                candidates.append((start, end))
        if not candidates:
            continue
        start, end = min(candidates, key=lambda span: (span[1] - span[0], span[0]))
        text = source.text[start:end]
        document = replace(
            source,
            id=f"{source.id}#d10-min-{start}",
            offset=source.offset + start,
            text=text,
            index_text=text,
        )
        output.append(Hit(document=document, score=hit.score))
    return output


def _asserted_evidence(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    query_terms = _query_terms(query, ctx)
    supported: list[Hit] = []
    for hit in hits:
        source = hit.document
        for start, end in _sentence_spans(source.text):
            text = source.text[start:end]
            if text.endswith(("?", "\uff1f")) or _UNSUPPORTED_RE.search(text):
                continue
            if not query_terms or not query_terms.issubset(_tokens(text, ctx)):
                continue
            if start == 0 and end == len(source.text):
                supported.append(hit)
            else:
                supported.append(
                    Hit(
                        replace(
                            source,
                            id=f"{source.id}#d10-asserted-{start}-{end}",
                            text=text,
                            offset=source.offset + start,
                            index_text=text,
                        ),
                        hit.score,
                    )
                )
    return supported


def experiments() -> list[Experiment]:
    """Return ten independent privacy and abstention mechanisms."""

    return [
        Experiment(
            id="D10.01",
            direction="D10 privacy/abstention",
            name="Direct lexical evidence only",
            hypothesis="Returning only directly matched sources reduces unsupported disclosure.",
            mechanism=(
                "Keep hits sharing an informative query token; return no hits without support."
            ),
            limitation="Synonyms and indirect references can cause false abstention.",
            retrieval_hook=_direct_evidence,
        ),
        Experiment(
            id="D10.02",
            direction="D10 privacy/abstention",
            name="Quoted evidence match",
            hypothesis=(
                "Quoted requests should disclose only sources containing the exact quoted text."
            ),
            mechanism=(
                "When the query contains quotes, require every quoted span in the source text."
            ),
            limitation="Paraphrases and normalized formatting do not count as exact support.",
            retrieval_hook=_quoted_evidence,
        ),
        Experiment(
            id="D10.03",
            direction="D10 privacy/abstention",
            name="Same-message context gate",
            hypothesis=(
                "Context from the directly matched message is safer than unrelated expansion."
            ),
            mechanism=(
                "Keep direct hits plus chunks sharing their source message; otherwise abstain."
            ),
            limitation="Relevant context recorded in another message is excluded.",
            retrieval_hook=_same_message_context,
        ),
        Experiment(
            id="D10.04",
            direction="D10 privacy/abstention",
            name="Requested speaker only",
            hypothesis="Explicit speaker questions should not expose another speaker's text.",
            mechanism="When the query names user or assistant speech, keep only that source role.",
            limitation="Ambiguous or unstated speaker intent leaves baseline results unchanged.",
            retrieval_hook=_role_intent,
        ),
        Experiment(
            id="D10.05",
            direction="D10 privacy/abstention",
            name="Sensitive category intent",
            hypothesis=(
                "Sensitive sources should appear only when the query requests that category."
            ),
            mechanism="Require query intent for every sensitive category in each returned source.",
            limitation=(
                "English category keywords and numeric heuristics can miss or over-mark data."
            ),
            retrieval_hook=_category_intent,
        ),
        Experiment(
            id="D10.06",
            direction="D10 privacy/abstention",
            name="Sensitive subject binding",
            hypothesis=(
                "A named subject should prevent disclosure of another subject's private data."
            ),
            mechanism=(
                "Require all informative non-category query terms inside each sensitive source."
            ),
            limitation="Descriptive query words and aliases can cause false abstention.",
            retrieval_hook=_subject_binding,
        ),
        Experiment(
            id="D10.07",
            direction="D10 privacy/abstention",
            name="Exact sensitive value match",
            hypothesis=(
                "A query containing a sensitive literal should not retrieve different values."
            ),
            mechanism="Require every supplied sensitive literal to occur in the original source.",
            limitation=(
                "Only recognized literals are gated; unquoted credential punctuation is literal."
            ),
            retrieval_hook=_exact_sensitive_value,
        ),
        Experiment(
            id="D10.08",
            direction="D10 privacy/abstention",
            name="Source privacy marker",
            hypothesis="Explicit non-disclosure wording should block that source from retrieval.",
            mechanism="Remove source text marked private, confidential, or not for sharing.",
            limitation="A quoted or historical discussion of privacy wording can be over-filtered.",
            retrieval_hook=_privacy_marker,
        ),
        Experiment(
            id="D10.09",
            direction="D10 privacy/abstention",
            name="Minimum supported sentence",
            hypothesis=(
                "A matched sentence discloses less than a full chunk containing private data."
            ),
            mechanism="Emit the shortest original sentence covering every informative query term.",
            limitation=(
                "Paraphrases cause abstention; original context metadata is retained unchanged."
            ),
            output_hook=_least_disclosure,
        ),
        Experiment(
            id="D10.10",
            direction="D10 privacy/abstention",
            name="Asserted evidence or abstention",
            hypothesis=(
                "Questions, hypotheticals, and stated unknowns should not be treated as facts."
            ),
            mechanism="Emit exact query-supporting assertions; return nothing if none are present.",
            limitation="Lexical assertion rules miss indirect evidence and can reject modal facts.",
            output_hook=_asserted_evidence,
        ),
    ]
