"""Temporal retrieval experiments using only dates and source metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
import re

from harness.types import Context, Document, Experiment, Hit


_DATE_TOKEN = (
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\d{4}\u5e74\d{1,2}\u6708\d{1,2}\u65e5?)"
)
_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"
)
_CJK_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\u5e74(?P<month>\d{1,2})\u6708"
    r"(?P<day>\d{1,2})(?!\d)\u65e5?"
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_DATE_RE = re.compile(
    r"\b(?P<month>"
    + "|".join(_MONTHS)
    + r")\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*(?:to|through|\u81f3|\u5230|[\u2013\u2014]| - )"
    rf"\s*(?P<end>{_DATE_TOKEN})",
    re.IGNORECASE,
)
_CUTOFF_RE = re.compile(
    r"(?:\b(?P<english>as\s+of|before|by|through|up\s+to)\s*"
    r"|\u622a\u81f3\s*)$",
    re.IGNORECASE,
)
_HISTORY_RE = re.compile(
    r"\b(?:history|historical|formerly|previously|previous|earlier|oldest|at\s+first"
    r"|before|yesterday|last\s+(?:year|month|week|time)|used\s+to)\b"
    r"|\u5386\u53f2|\u4e4b\u524d|\u6700\u65e9|\u539f\u6765",
    re.IGNORECASE,
)
_CURRENT_RE = re.compile(
    r"\b(?:current|currently|latest|now|updated|changed|most\s+recent)\b"
    r"|\u5f53\u524d|\u6700\u65b0|\u73b0\u5728|\u66f4\u65b0|\u6539\u4e3a",
    re.IGNORECASE,
)
_UPDATE_RE = re.compile(
    r"\b(?:updated|changed|revised|now|currently|no\s+longer|replaced|superseded)\b"
    r"|\u66f4\u65b0|\u6539\u4e3a|\u73b0\u4e3a|\u4e0d\u518d|\u66ff\u6362",
    re.IGNORECASE,
)
_CHANGE_RE = re.compile(
    r"\b(?:changed\s+from|updated\s+from|replaced\s+by|no\s+longer|now|currently)\b"
    r"|\u4ece.+?(?:\u6539\u4e3a|\u53d8\u4e3a)|\u73b0\u4e3a|\u4e0d\u518d",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|disabled|false|cannot|can't|isn't|wasn't|no\s+longer)\b"
    r"|\u4e0d\u518d|\u4e0d\u662f|\u6ca1\u6709|\u672a|\u7981\u7528|\u5173\u95ed",
    re.IGNORECASE,
)
_TOPIC_STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "by",
    "current",
    "currently",
    "did",
    "do",
    "does",
    "for",
    "from",
    "history",
    "historical",
    "in",
    "is",
    "latest",
    "me",
    "most",
    "no",
    "not",
    "never",
    "without",
    "disabled",
    "false",
    "cannot",
    "now",
    "of",
    "on",
    "or",
    "recent",
    "the",
    "to",
    "was",
    "what",
    "when",
    "how",
    "has",
    "had",
    "have",
    "it",
    "its",
    "my",
    "s",
    "changed",
    "updated",
    "status",
    "with",
}


def _make_date(year: str, month: str | int, day: str) -> date | None:
    try:
        month_number = _MONTHS.get(str(month).casefold(), month)
        return date(int(year), int(month_number), int(day))
    except (TypeError, ValueError):
        return None


def _date_mentions(text: str) -> tuple[tuple[int, int, date], ...]:
    found: list[tuple[int, int, date]] = []
    for pattern in (_NUMERIC_DATE_RE, _CJK_DATE_RE, _MONTH_DATE_RE):
        for match in pattern.finditer(text):
            value = _make_date(match["year"], match["month"], match["day"])
            if value is not None:
                found.append((match.start(), match.end(), value))
    return tuple(sorted(found))


def _explicit_dates(text: str) -> tuple[date, ...]:
    return tuple(dict.fromkeys(value for _, _, value in _date_mentions(text)))


def _current_intent(query: str) -> bool:
    # An explicit past date or history request takes precedence over "latest".
    return bool(
        _CURRENT_RE.search(query)
        and not _HISTORY_RE.search(query)
        and not re.search(r"(?<!\d)\d{4}(?!\d)", query)
    )


def _timestamp_date(document: Document) -> date | None:
    if document.timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(document.timestamp / 1000, UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _latest_timestamp(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    if not _current_intent(query):
        return list(hits)
    indexed = list(enumerate(hits))
    indexed.sort(
        key=lambda pair: (
            pair[1].document.timestamp is None,
            -pair[1].document.timestamp if pair[1].document.timestamp is not None else 0,
            pair[0],
        )
    )
    return [hit for _, hit in indexed]


def _historical_timestamp(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    if not _HISTORY_RE.search(query):
        return list(hits)
    indexed = list(enumerate(hits))
    indexed.sort(
        key=lambda pair: (
            pair[1].document.timestamp is None,
            pair[1].document.timestamp if pair[1].document.timestamp is not None else 0,
            pair[0],
        )
    )
    return [hit for _, hit in indexed]


def _as_of_cutoff(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    mentions = _date_mentions(query)
    if len(mentions) != 1:
        return list(hits)
    start, end, cutoff = mentions[0]
    marker = _CUTOFF_RE.search(query[:start])
    suffix_before = query[end:].lstrip().startswith("\u4e4b\u524d")
    if marker is None and not suffix_before:
        return list(hits)
    exclusive = suffix_before or bool(
        marker and (marker["english"] or "").casefold() == "before"
    )

    def key(pair: tuple[int, Hit]) -> tuple[int, int, int]:
        index, hit = pair
        source_date = _timestamp_date(hit.document)
        if source_date is None:
            return (1, 0, index)
        if source_date < cutoff or (source_date == cutoff and not exclusive):
            return (0, -source_date.toordinal(), index)
        return (2, source_date.toordinal(), index)

    return [hit for _, hit in sorted(enumerate(hits), key=key)]


def _body_date_match(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    query_dates = frozenset(_explicit_dates(query))
    if not query_dates:
        return list(hits)
    return [
        hit
        for _, hit in sorted(
            enumerate(hits),
            key=lambda pair: (
                not bool(query_dates.intersection(_explicit_dates(pair[1].document.text))),
                pair[0],
            ),
        )
    ]


def _date_aliases(query: str, ctx: Context) -> list[tuple[str, float]]:
    output = [(query, 2.0)]
    focused = ctx.focused(query)
    if focused and focused != query and focused != " ".join(ctx.tokens(query)):
        output.append((focused, 2.0))
    mentions = _date_mentions(query)
    if len(mentions) != 1:
        return output
    start, end, value = mentions[0]
    month = tuple(_MONTHS)[value.month - 1].title()
    aliases = (
        value.isoformat(),
        f"{value.year:04d}/{value.month:02d}/{value.day:02d}",
        f"{month} {value.day}, {value.year}",
        f"{value.year}\u5e74{value.month}\u6708{value.day}\u65e5",
    )
    known = {text for text, _ in output}
    for alias in aliases:
        text = query[:start] + alias + query[end:]
        if text not in known:
            output.append((text, 1.0))
            known.add(text)
    return output


def _date_ranges(text: str) -> tuple[tuple[date, date], ...]:
    ranges: list[tuple[date, date]] = []
    for match in _RANGE_RE.finditer(text):
        starts = _explicit_dates(match["start"])
        ends = _explicit_dates(match["end"])
        if starts and ends and starts[0] <= ends[0]:
            ranges.append((starts[0], ends[0]))
    return tuple(ranges)


def _validity_interval(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    dates = _explicit_dates(query)
    if len(dates) != 1:
        return list(hits)
    target = dates[-1]
    return [
        hit
        for _, hit in sorted(
            enumerate(hits),
            key=lambda pair: (
                not any(
                    start <= target <= end
                    for start, end in _date_ranges(pair[1].document.text)
                ),
                pair[0],
            ),
        )
    ]


def _update_cue(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    if not _current_intent(query):
        return list(hits)
    query_terms = _topic_tokens(query, ctx)

    def supports_update(hit: Hit) -> bool:
        return any(
            _UPDATE_RE.search(hit.document.text[start:end])
            and query_terms.intersection(_topic_tokens(hit.document.text[start:end], ctx))
            for start, end in _sentence_spans(hit.document.text)
        )

    return [
        hit
        for _, hit in sorted(
            enumerate(hits),
            key=lambda pair: (
                not supports_update(pair[1]),
                pair[0],
            ),
        )
    ]


def _topic_tokens(text: str, ctx: Context) -> frozenset[str]:
    tokens = ctx.tokens(text)
    return frozenset(
        token.casefold()
        for token in tokens
        if token.casefold() not in _TOPIC_STOP and len(token.strip()) > 1
    )


def _negation_counterparts(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    query_terms = _topic_tokens(query, ctx)
    if not hits or not query_terms:
        return list(hits)
    candidates = list(hits)
    known = {hit.document.id for hit in candidates}
    for hit in ctx.lexical(query, limit=300):
        if hit.document.id not in known:
            candidates.append(hit)
            known.add(hit.document.id)

    output: list[Hit] = []
    emitted: set[str] = set()
    terms = {hit.document.id: _topic_tokens(hit.document.text, ctx) for hit in candidates}
    polarity = {
        hit.document.id: bool(_NEGATION_RE.search(hit.document.text)) for hit in candidates
    }
    for anchor in hits:
        if anchor.document.id not in emitted:
            output.append(anchor)
            emitted.add(anchor.document.id)
        anchor_negative = polarity[anchor.document.id]
        anchor_terms = terms[anchor.document.id]
        if not query_terms.intersection(anchor_terms):
            continue
        ranked: list[tuple[int, int, Hit]] = []
        for index, candidate in enumerate(candidates):
            if candidate.document.id in emitted:
                continue
            if polarity[candidate.document.id] == anchor_negative:
                continue
            if not query_terms.intersection(anchor_terms, terms[candidate.document.id]):
                continue
            overlap = len(anchor_terms.intersection(terms[candidate.document.id]))
            if overlap >= 2:
                ranked.append((-overlap, index, candidate))
        if ranked:
            counterpart = min(ranked)[2]
            output.append(counterpart)
            emitted.add(counterpart.document.id)

    output.extend(hit for hit in hits if hit.document.id not in emitted)
    return output


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


def _change_clause(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    if not _current_intent(query):
        return list(hits)
    query_terms = _topic_tokens(query, ctx)
    output: list[Hit] = []
    for hit in hits:
        source = hit.document
        selected = [
            (start, end)
            for start, end in _sentence_spans(source.text)
            if _CHANGE_RE.search(source.text[start:end])
            and query_terms.intersection(_topic_tokens(source.text[start:end], ctx))
        ]
        if not selected:
            output.append(hit)
            continue
        for start, end in selected:
            text = source.text[start:end]
            document = replace(
                source,
                id=f"{source.id}#d06-change-{start}",
                offset=source.offset + start,
                text=text,
                index_text=text,
            )
            output.append(Hit(document=document, score=hit.score))
    return output


def _nearest_source_date(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    del ctx
    dates = _explicit_dates(query)
    if len(dates) != 1:
        return list(hits)
    target = dates[-1]

    def key(pair: tuple[int, Hit]) -> tuple[int, int, int]:
        index, hit = pair
        source_date = _timestamp_date(hit.document)
        if source_date is None:
            return (1, 0, index)
        return (0, abs((source_date - target).days), index)

    return [hit for _, hit in sorted(enumerate(hits), key=key)]


def experiments() -> list[Experiment]:
    """Return ten independent temporal mechanisms."""

    return [
        Experiment(
            id="D06.01",
            direction="D06 temporal/update retrieval",
            name="Latest source timestamp first",
            hypothesis=(
                "Recent source records are better first candidates for current-state queries."
            ),
            mechanism="For undated current-state questions, rank newer source records first.",
            limitation="All old sources remain; a record timestamp is not necessarily a fact date.",
            retrieval_hook=_latest_timestamp,
        ),
        Experiment(
            id="D06.02",
            direction="D06 temporal/update retrieval",
            name="Historical intent chronology",
            hypothesis="Explicit history questions benefit from earliest evidence first.",
            mechanism="For historical wording only, order dated hits from oldest to newest.",
            limitation="Implicit historical intent is left unchanged.",
            retrieval_hook=_historical_timestamp,
        ),
        Experiment(
            id="D06.03",
            direction="D06 temporal/update retrieval",
            name="As-of source cutoff",
            hypothesis="An explicit as-of date should prioritize evidence recorded by that date.",
            mechanism="Prioritize sources recorded by the cutoff, exclusive for explicit before.",
            limitation="Uses UTC calendar days of millisecond timestamps, not event times.",
            retrieval_hook=_as_of_cutoff,
        ),
        Experiment(
            id="D06.04",
            direction="D06 temporal/update retrieval",
            name="Exact body date alignment",
            hypothesis="A date written in both query and source is strong temporal evidence.",
            mechanism="Promote documents containing the same complete calendar date as the query.",
            limitation="Equivalent relative dates and paraphrased dates are not interpreted.",
            retrieval_hook=_body_date_match,
        ),
        Experiment(
            id="D06.05",
            direction="D06 temporal/update retrieval",
            name="Calendar spelling query aliases",
            hypothesis="Equivalent explicit date spellings recover otherwise missing lexical hits.",
            mechanism="Keep original and focused queries and add four equivalent date spellings.",
            limitation="No aliases for incomplete, invalid, multiple, or relative dates.",
            query_hook=_date_aliases,
        ),
        Experiment(
            id="D06.06",
            direction="D06 temporal/update retrieval",
            name="Stated validity interval",
            hypothesis="A source-stated date range can answer point-in-time retrieval directly.",
            mechanism="Promote documents with a closed written date range covering the query date.",
            limitation="Open-ended or implicit ranges are not inferred.",
            retrieval_hook=_validity_interval,
        ),
        Experiment(
            id="D06.07",
            direction="D06 temporal/update retrieval",
            name="Update language promotion",
            hypothesis=(
                "Current-state questions benefit from sources that explicitly describe a change."
            ),
            mechanism=(
                "For current wording, promote source text with update or replacement language."
            ),
            limitation="Update language without a date does not prove it is the newest state.",
            retrieval_hook=_update_cue,
        ),
        Experiment(
            id="D06.08",
            direction="D06 temporal/update retrieval",
            name="Negation counterpart retrieval",
            hypothesis="Showing both polarities reduces errors when stored statements conflict.",
            mechanism="Add one lexical source with opposite negation and strong topic overlap.",
            limitation="Lexical polarity cannot resolve which source is correct or newer.",
            retrieval_hook=_negation_counterparts,
        ),
        Experiment(
            id="D06.09",
            direction="D06 temporal/update retrieval",
            name="Extractive change clause",
            hypothesis=(
                "The exact clause describing a change is safer than an unrelated full chunk."
            ),
            mechanism="For current queries, emit all query-linked original change sentences.",
            limitation="Only an explicit same-sentence change is extracted; no pronoun resolution.",
            output_hook=_change_clause,
        ),
        Experiment(
            id="D06.10",
            direction="D06 temporal/update retrieval",
            name="Nearest recorded date",
            hypothesis="Queries naming a date benefit from records captured closest to that date.",
            mechanism=(
                "Order timestamped sources by absolute distance from the explicit query date."
            ),
            limitation="Temporal proximity alone does not establish topical relevance.",
            retrieval_hook=_nearest_source_date,
        ),
    ]
