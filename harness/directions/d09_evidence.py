"""Independent extractive evidence selection and presentation experiments."""

from collections import Counter
from dataclasses import replace
import re

from harness.types import Context, Experiment, Hit


CHARACTER_BUDGET = 4096


def _wanted(query: str, ctx: Context) -> set[str]:
    return set(ctx.tokens(ctx.focused(query)))


def _extract(hit: Hit, start: int, end: int, mechanism: str) -> Hit:
    document = hit.document
    if start == 0 and end == len(document.text):
        return hit
    if not 0 <= start < end <= len(document.text):
        raise ValueError("Extraction must be a nonempty source interval")
    absolute_start = document.offset + start
    absolute_end = document.offset + end
    text = document.text[start:end]
    return replace(hit, document=replace(
        document,
        id=f"{mechanism}:{len(document.id)}:{document.id}:{absolute_start}:{absolute_end}",
        offset=absolute_start,
        text=text,
        index_text=text,
        context="",
    ))


def exact_duplicates(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    seen = set()
    result = []
    for hit in hits:
        if hit.document.text not in seen:
            seen.add(hit.document.text)
            result.append(hit)
    return result


def _add_interval(intervals: list[tuple[int, int]], start: int, end: int) -> list[tuple[int, int]]:
    merged = []
    for left, right in sorted([*intervals, (start, end)]):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def subtract_source_overlap(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    occupied: dict[str, list[tuple[int, int]]] = {}
    result = []
    for hit in hits:
        document = hit.document
        start, end = document.offset, document.offset + len(document.text)
        if start == end:
            continue
        intervals = occupied.get(document.message_id, [])
        cursor = start
        for left, right in intervals:
            if right <= cursor:
                continue
            if left >= end:
                break
            if left > cursor:
                result.append(_extract(hit, cursor - start, left - start, "D09.02"))
            cursor = max(cursor, right)
            if cursor >= end:
                break
        if cursor < end:
            result.append(_extract(hit, cursor - start, end - start, "D09.02"))
        occupied[document.message_id] = _add_interval(intervals, start, end)
    return result


_SENTENCE_END = re.compile(
    r"""[.!?]+["')\]]*(?=\s|$)|[\u3002\uff01\uff1f]+["'\u201d\u2019]*|\r?\n"""
)
_PARAGRAPH_END = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _spans(text: str, boundary: re.Pattern[str]) -> list[tuple[int, int]]:
    result = []
    start = 0
    for match in boundary.finditer(text):
        span = _trimmed_span(text, start, match.end())
        if span:
            result.append(span)
        start = match.end()
    tail = _trimmed_span(text, start, len(text))
    if tail:
        result.append(tail)
    return result


def _best_unit(
    query: str, hits: list[Hit], ctx: Context, boundary: re.Pattern[str], mechanism: str
) -> list[Hit]:
    wanted = _wanted(query, ctx)
    if not wanted:
        return list(hits)
    result = []
    for hit in hits:
        best = None
        best_coverage = 0
        for start, end in _spans(hit.document.text, boundary):
            coverage = len(wanted.intersection(ctx.tokens(hit.document.text[start:end])))
            if coverage > best_coverage:
                best, best_coverage = (start, end), coverage
        result.append(_extract(hit, *best, mechanism) if best else hit)
    return result


def best_sentence(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    return _best_unit(query, hits, ctx, _SENTENCE_END, "D09.03")


def best_paragraph(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    return _best_unit(query, hits, ctx, _PARAGRAPH_END, "D09.04")


def covering_passage(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = _wanted(query, ctx)
    if not wanted:
        return list(hits)
    result = []
    for hit in hits:
        spans = _spans(hit.document.text, _SENTENCE_END)
        support = [
            wanted.intersection(ctx.tokens(hit.document.text[start:end]))
            for start, end in spans
        ]
        target = set().union(*support)
        if not target:
            result.append(hit)
            continue
        counts: Counter[str] = Counter()
        left = 0
        best = None
        for right, terms in enumerate(support):
            counts.update(terms)
            while len(counts) == len(target):
                start, end = spans[left][0], spans[right][1]
                if best is None or end - start < best[1] - best[0]:
                    best = start, end
                for term in support[left]:
                    counts[term] -= 1
                    if counts[term] == 0:
                        del counts[term]
                left += 1
        result.append(_extract(hit, *best, "D09.05") if best else hit)
    return result


def independent_support(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = _wanted(query, ctx)
    if not wanted:
        return list(hits)
    sources: dict[str, set[str]] = {term: set() for term in sorted(wanted)}
    result = []
    for hit in hits:
        terms = wanted.intersection(ctx.tokens(hit.document.text))
        new_support = [
            term for term in sorted(terms)
            if hit.document.message_id not in sources[term] and len(sources[term]) < 2
        ]
        if new_support:
            result.append(hit)
            for term in new_support:
                sources[term].add(hit.document.message_id)
    return result or list(hits)


def lexical_diversity(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    if len(hits) < 2:
        return list(hits)
    token_sets = [set(ctx.tokens(hit.document.text)) for hit in hits]
    remaining = list(range(len(hits)))
    maximum_similarity = [0.0] * len(hits)
    result = []
    while remaining:
        best = max(
            remaining,
            key=lambda index: (len(hits) - index) / len(hits) - 0.5 * maximum_similarity[index],
        )
        result.append(hits[best])
        remaining.remove(best)
        for index in remaining:
            union = token_sets[index] | token_sets[best]
            similarity = len(token_sets[index] & token_sets[best]) / len(union) if union else 1.0
            maximum_similarity[index] = max(maximum_similarity[index], similarity)
    return result


def chronological(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    return sorted(hits, key=lambda hit: (
        hit.document.timestamp is None,
        hit.document.timestamp if hit.document.timestamp is not None else 0,
        hit.document.ordinal,
        hit.document.offset,
    ))


def _ranked_budget(hits: list[Hit]) -> list[Hit]:
    remaining = CHARACTER_BUDGET
    result = []
    for hit in hits:
        size = len(hit.document.text)
        if 0 < size <= remaining:
            result.append(hit)
            remaining -= size
    return result


def budgeted_coverage(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = _wanted(query, ctx)
    support = [wanted.intersection(ctx.tokens(hit.document.text)) for hit in hits]
    if not any(support):
        return _ranked_budget(hits)
    remaining = list(range(len(hits)))
    covered: set[str] = set()
    budget = CHARACTER_BUDGET
    result = []
    while remaining:
        eligible = [
            index for index in remaining
            if 0 < len(hits[index].document.text) <= budget and support[index] - covered
        ]
        if not eligible:
            break
        best = max(eligible, key=lambda index: (
            len(support[index] - covered) / len(hits[index].document.text),
            len(support[index] - covered),
        ))
        result.append(hits[best])
        covered.update(support[best])
        budget -= len(hits[best].document.text)
        remaining.remove(best)
    return result


def source_blocks(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    messages: dict[str, list[Hit]] = {}
    for hit in hits:
        messages.setdefault(hit.document.message_id, []).append(hit)
    return [
        hit
        for block in messages.values()
        for hit in sorted(block, key=lambda item: item.document.offset)
    ]


def experiments() -> list[Experiment]:
    specifications = [
        (
            "Exact text deduplication",
            "Removing repeated text leaves more room for distinct evidence.",
            "Keep the first incoming hit for each exactly equal Python text string.",
            "Identical statements at different times lose their separate event provenance.",
            exact_duplicates,
        ),
        (
            "Source overlap subtraction",
            "Removing already presented source coordinates reduces overlapping chunk waste.",
            "Subtract earlier intervals within each source message and emit exact residual spans.",
            "Residuals may start mid-word and lose context; distinct messages never coalesce.",
            subtract_source_overlap,
        ),
        (
            "Best supporting sentence",
            "A sentence with maximal query coverage removes distracting surrounding text.",
            "Extract the earliest sentence with maximal distinct query-token coverage per hit.",
            "May omit adjacent qualifiers; punctuation segmentation is approximate.",
            best_sentence,
        ),
        (
            "Best supporting paragraph",
            "A source paragraph retains nearby explanations while removing unrelated paragraphs.",
            "Extract the earliest blank-line-delimited paragraph with maximal query coverage.",
            "No compression without paragraph boundaries; other paragraphs may add needed context.",
            best_paragraph,
        ),
        (
            "Shortest covering passage",
            "A continuous passage can retain split support without the whole document.",
            "Extract the shortest sentence-aligned span covering all query terms matched in a hit.",
            "A distant match forces a long passage; absent query terms remain absent.",
            covering_passage,
        ),
        (
            "Independent message support",
            "Keeping distinct messages for each query term reduces repeated-chunk evidence.",
            "Keep the first two distinct message sources for each query token in incoming order.",
            "Different messages can repeat a claim; a third message with new details may be lost.",
            independent_support,
        ),
        (
            "Lexical evidence diversity",
            "Lexically distinct evidence reduces repetition near the top of a ranked list.",
            "Maximize normalized input rank minus half maximum selected Jaccard similarity.",
            "Lexical novelty can promote distractors; pairwise comparisons take quadratic time.",
            lexical_diversity,
        ),
        (
            "Chronological presentation",
            "Source-time order makes changing statements easier to interpret.",
            "Order known timestamps ascending, then unknown timestamps, with source-order ties.",
            "Old low-ranked evidence can displace useful recent evidence before top-k truncation.",
            chronological,
        ),
        (
            "Budgeted query coverage",
            "New query terms per character can fit complementary evidence into a fixed budget.",
            "Select whole hits by new token coverage per character within 4096 code points.",
            "Skips oversized hits and repeated corroboration; lexical coverage is not sufficiency.",
            budgeted_coverage,
        ),
        (
            "Source message blocks",
            "Restoring source fragment order improves reading of evidence from the same message.",
            "Group by opaque message ID in first-hit order, then sort each block by source offset.",
            "Grouping can move low-ranked fragments ahead of stronger hits from another message.",
            source_blocks,
        ),
    ]
    return [
        Experiment(
            id=f"D09.{index:02d}",
            direction="D09",
            name=name,
            hypothesis=hypothesis,
            mechanism=mechanism,
            limitation=limitation,
            output_hook=hook,
        )
        for index, (name, hypothesis, mechanism, limitation, hook) in enumerate(specifications, 1)
    ]
