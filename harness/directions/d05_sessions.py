"""D05: bounded session and parent-child retrieval mechanisms."""

from __future__ import annotations

from collections import defaultdict
from math import sqrt

from harness.types import Context, Document, Experiment, Hit, Message


_SEED_LIMIT = 160
_OUTPUT_LIMIT = 300
_SESSION_SCAN_LIMIT = 48
_SESSION_RESULT_LIMIT = 18
_FAMILY_LIMIT = 40


def _ranked(hits: list[Hit], limit: int = _OUTPUT_LIMIT) -> list[Hit]:
    best: dict[str, Hit] = {}
    for hit in hits:
        current = best.get(hit.document.id)
        if current is None or hit.score > current.score:
            best[hit.document.id] = hit
    return sorted(
        best.values(),
        key=lambda hit: (-hit.score, hit.document.ordinal, hit.document.id),
    )[:limit]


def _seeds(hits: list[Hit]) -> list[Hit]:
    return _ranked(hits, _SEED_LIMIT)


def _query_terms(query: str, ctx: Context) -> frozenset[str]:
    return frozenset(ctx.tokens(ctx.focused(query) or query))


def _doc_terms(document: Document, ctx: Context) -> frozenset[str]:
    cached = ctx.doc_tokens.get(document.id)
    return cached if cached is not None else frozenset(ctx.tokens(document.text))


def _session_hits(hits: list[Hit]) -> dict[str, list[Hit]]:
    grouped: dict[str, list[Hit]] = defaultdict(list)
    for hit in hits:
        grouped[hit.document.session_id].append(hit)
    return grouped


def _boost_by_session(
    seeds: list[Hit],
    session_scores: dict[str, float],
    weight: float = 0.25,
) -> list[Hit]:
    scale = max(session_scores.values(), default=1.0) or 1.0
    amplitude = max((max(hit.score, 0.0) for hit in seeds), default=0.0)
    return _ranked(
        [
            Hit(hit.document, hit.score + weight * amplitude * session_scores.get(hit.document.session_id, 0.0) / scale)
            for hit in seeds
        ]
    )


def _aggregate_support(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Favor sessions supported by several strong, independent lexical hits."""
    seeds = _seeds(hits)
    scores: dict[str, float] = {}
    for session_id, session_hits in _session_hits(seeds).items():
        scores[session_id] = sum(max(hit.score, 0.0) / (rank + 1) for rank, hit in enumerate(session_hits[:8]))
    scale = max((max(hit.score, 0.0) for hit in seeds), default=1.0) or 1.0
    return _ranked([
        Hit(hit.document, scores[hit.document.session_id] + hit.score / scale * 0.01)
        for hit in seeds
    ])


def _idf_coverage(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Favor sessions whose source documents jointly cover more query concepts."""
    seeds = _seeds(hits)
    query_terms = _query_terms(query, ctx)
    scores: dict[str, float] = {}
    for session_id in _session_hits(seeds):
        covered: set[str] = set()
        for document in ctx.session_documents.get(session_id, ())[:_SESSION_SCAN_LIMIT]:
            covered.update(query_terms.intersection(_doc_terms(document, ctx)))
        scores[session_id] = sum(ctx.idf.get(term, 1.0) for term in covered)
    return _boost_by_session(seeds, scores, 0.4)


def _message_support(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Prefer corroboration spread across source messages over repeated chunks."""
    seeds = _seeds(hits)
    scores: dict[str, float] = {}
    for session_id, session_hits in _session_hits(seeds).items():
        best_by_message: dict[str, float] = {}
        for hit in session_hits:
            best_by_message[hit.document.message_id] = max(
                best_by_message.get(hit.document.message_id, 0.0),
                max(hit.score, 0.0),
            )
        scores[session_id] = sum(sorted(best_by_message.values(), reverse=True)[:8])
    return _boost_by_session(seeds, scores, 0.35)


def _session_reacquisition(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Use top session evidence to acquire missed matching chunks in those sessions."""
    seeds = _seeds(hits)
    terms = _query_terms(query, ctx)
    total_weight = sum(ctx.idf.get(term, 1.0) for term in sorted(terms)) or 1.0
    grouped = _session_hits(seeds)
    ordered_sessions = sorted(
        grouped,
        key=lambda session_id: (
            -sum(max(hit.score, 0.0) for hit in grouped[session_id][:6]),
            session_id,
        ),
    )[:6]
    additions: list[Hit] = []
    for session_id in ordered_sessions:
        parent_score = max(max(hit.score, 0.0) for hit in grouped[session_id])
        candidates: list[Hit] = []
        for document in ctx.session_documents.get(session_id, ())[:_SESSION_SCAN_LIMIT]:
            overlap = terms.intersection(_doc_terms(document, ctx))
            if overlap:
                score = sum(ctx.idf.get(term, 1.0) for term in sorted(overlap)) / total_weight
                candidates.append(Hit(document, parent_score * (0.5 + 0.4 * score)))
        additions.extend(_ranked(candidates, _SESSION_RESULT_LIMIT))
    return _ranked(seeds + additions)


def _marginal_coverage(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Promote representatives from sessions that add missing query concepts."""
    seeds = _seeds(hits)
    grouped = _session_hits(seeds)
    uncovered = set(_query_terms(query, ctx))
    coverage = {
        session_id: set().union(*(
            uncovered.intersection(_doc_terms(hit.document, ctx)) for hit in session_hits
        ))
        for session_id, session_hits in grouped.items()
    }
    remaining = set(grouped)
    result: list[Hit] = []
    while remaining and uncovered:
        session_id = min(remaining, key=lambda key: (
            -sum(ctx.idf.get(term, 1.0) for term in sorted(coverage[key] & uncovered)),
            -grouped[key][0].score, key,
        ))
        gain = coverage[session_id] & uncovered
        if not gain:
            break
        representative = max(grouped[session_id], key=lambda hit: (
            len(gain.intersection(_doc_terms(hit.document, ctx))), hit.score,
            -hit.document.ordinal,
        ))
        result.append(representative)
        uncovered.difference_update(_doc_terms(representative.document, ctx))
        remaining.remove(session_id)
    promoted = {hit.document.id for hit in result}
    result.extend(hit for hit in seeds if hit.document.id not in promoted)
    return result[:_OUTPUT_LIMIT]


def _session_mmr(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Select session representatives using relevance minus source-token redundancy."""
    grouped = _session_hits(_seeds(hits))
    profiles: dict[str, frozenset[str]] = {}
    relevance: dict[str, float] = {}
    for session_id, session_hits in grouped.items():
        profile: set[str] = set()
        for hit in session_hits[:8]:
            profile.update(_doc_terms(hit.document, ctx))
        profiles[session_id] = frozenset(profile)
        relevance[session_id] = sum(max(hit.score, 0.0) / (rank + 1) for rank, hit in enumerate(session_hits[:8]))

    scale = max(relevance.values(), default=1.0) or 1.0
    remaining = set(grouped)
    selected: list[str] = []
    redundancy = dict.fromkeys(remaining, 0.0)
    while remaining:
        chosen = min(remaining, key=lambda key: (
            -(relevance[key] / scale - 0.45 * redundancy[key]), key,
        ))
        selected.append(chosen)
        remaining.remove(chosen)
        for session_id in remaining:
            intersection = len(profiles[session_id].intersection(profiles[chosen]))
            denominator = sqrt(max(len(profiles[session_id]), 1) * max(len(profiles[chosen]), 1))
            redundancy[session_id] = max(redundancy[session_id], intersection / denominator)

    result: list[Hit] = []
    for depth in range(max((len(grouped[session_id]) for session_id in selected), default=0)):
        for session_id in selected:
            if depth < len(grouped[session_id]):
                result.append(grouped[session_id][depth])
    return result[:_OUTPUT_LIMIT]


def _message_siblings(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Acquire complementary query-bearing chunks from the same source message."""
    seeds = _seeds(hits)
    terms = _query_terms(query, ctx)
    additions: list[Hit] = []
    visited: set[str] = set()
    for hit in seeds[:16]:
        if hit.document.message_id in visited:
            continue
        visited.add(hit.document.message_id)
        missing = terms.difference(_doc_terms(hit.document, ctx))
        for document in ctx.message_documents.get(hit.document.message_id, ())[:_FAMILY_LIMIT]:
            if missing.intersection(_doc_terms(document, ctx)):
                additions.append(Hit(document, max(hit.score, 0.0) * 0.9))
    return _ranked(seeds + additions)


def _messages_by_request(ctx: Context) -> dict[tuple[str, str], tuple[Message, ...]]:
    grouped: dict[tuple[str, str], list[Message]] = defaultdict(list)
    for message in ctx.messages:
        grouped[message.session_id, message.request_id].append(message)
    return {
        request_id: tuple(sorted(messages, key=lambda message: (message.position, message.ordinal, message.id)))
        for request_id, messages in grouped.items()
    }


def _message_hits(message: Message, score: float, ctx: Context) -> list[Hit]:
    return [Hit(document, score) for document in ctx.message_documents.get(message.id, ())[:4]]


def _turn_links(ctx: Context) -> tuple[dict[str, Message], dict[str, Message]]:
    parents: dict[str, Message] = {}
    children: dict[str, Message] = {}
    for messages in _messages_by_request(ctx).values():
        parent = None
        for message in messages:
            if message.role == "user":
                parent = message
            elif message.role == "assistant" and parent is not None:
                parents[message.id] = parent
                children.setdefault(parent.id, message)
    return parents, children


def _prompt_parent(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Attach the user prompt that is the parent of a matched assistant message."""
    seeds = _seeds(hits)
    parents, _ = _turn_links(ctx)
    additions: list[Hit] = []
    for hit in seeds[:24]:
        if hit.document.role != "assistant":
            continue
        parent = parents.get(hit.document.message_id)
        if parent is not None:
            additions.extend(_message_hits(parent, max(hit.score, 0.0) * 0.9, ctx))
    return _ranked(seeds + additions)


def _response_child(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Attach the assistant response that is the child of a matched user prompt."""
    seeds = _seeds(hits)
    _, children = _turn_links(ctx)
    additions: list[Hit] = []
    for hit in seeds[:24]:
        if hit.document.role != "user":
            continue
        child = children.get(hit.document.message_id)
        if child is not None:
            additions.extend(_message_hits(child, max(hit.score, 0.0) * 0.9, ctx))
    return _ranked(seeds + additions)


def _session_intersection(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    """Intersect independent lexical result sessions for rare query concepts."""
    seeds = _seeds(hits)
    terms = sorted(_query_terms(query, ctx), key=lambda term: (-ctx.idf.get(term, 1.0), term))[:4]
    if len(terms) < 2:
        return seeds
    lists = [ctx.lexical(term, limit=100)[:100] for term in terms]
    common = set.intersection(*({hit.document.session_id for hit in rows} for rows in lists))
    scale = max((max(hit.score, 0.0) for hit in seeds), default=1.0)
    additions = [
        Hit(hit.document, scale * 0.95 / (1.0 + 0.05 * rank))
        for rows in lists for rank, hit in enumerate(rows)
        if hit.document.session_id in common
    ]
    return _ranked(seeds + additions)


def experiments() -> list[Experiment]:
    return [
        Experiment(
            "D05.01",
            "Hierarchical/session retrieval",
            "Session aggregate support",
            "Several independent hits in one session can be stronger evidence than one isolated hit.",
            "Add a bounded reciprocal-rank session support score to each baseline hit.",
            "A long session with repeated wording can still accumulate excess support.",
            retrieval_hook=_aggregate_support,
        ),
        Experiment(
            "D05.02",
            "Hierarchical/session retrieval",
            "Session query coverage",
            "Joint coverage of distinct query concepts identifies useful multi-turn sessions.",
            "Measure IDF-weighted query-term coverage across bounded documents in candidate sessions.",
            "Lexical coverage does not resolve synonyms or implicit references.",
            retrieval_hook=_idf_coverage,
        ),
        Experiment(
            "D05.03",
            "Hierarchical/session retrieval",
            "Independent message support",
            "Evidence from separate source messages is more useful than duplicate chunks.",
            "Aggregate only the best hit from each source message before boosting its session.",
            "Repeated facts in genuinely distinct messages may still dominate.",
            retrieval_hook=_message_support,
        ),
        Experiment(
            "D05.04",
            "Hierarchical/session retrieval",
            "Top-session reacquisition",
            "A strong session can contain relevant chunks missed by the initial lexical top set.",
            "Rescore bounded documents from the six strongest candidate sessions by query-term IDF overlap.",
            "Only lexically overlapping chunks in already discovered sessions can be acquired.",
            retrieval_hook=_session_reacquisition,
        ),
        Experiment(
            "D05.05",
            "Hierarchical/session retrieval",
            "Session marginal coverage",
            "Sessions covering different query concepts provide more complete evidence.",
            "Greedily promote session representatives with the largest uncovered IDF-weighted query coverage.",
            "Coverage relies on exact tokens and can promote a merely incidental mention.",
            retrieval_hook=_marginal_coverage,
        ),
        Experiment(
            "D05.06",
            "Hierarchical/session retrieval",
            "Session MMR",
            "Token-diverse sessions provide broader evidence than near-duplicate conversations.",
            "Order session representatives by aggregate relevance minus source-token redundancy.",
            "Lexically different sessions can describe the same event and receive too much diversity credit.",
            retrieval_hook=_session_mmr,
        ),
        Experiment(
            "D05.07",
            "Hierarchical/session retrieval",
            "Complementary message siblings",
            "A matched chunk's parent message can contain missing query concepts in other chunks.",
            "Acquire bounded sibling chunks only when they contain query tokens missing from the seed.",
            "Lexically implicit continuation and chunks beyond the per-message limit are missed.",
            retrieval_hook=_message_siblings,
        ),
        Experiment(
            "D05.08",
            "Hierarchical/session retrieval",
            "Prompt parent",
            "A matched answer is easier to interpret with its exact source prompt.",
            "For assistant hits, add the nearest preceding user message in the same request.",
            "Multi-user-message requests may have a more distant logical parent.",
            retrieval_hook=_prompt_parent,
        ),
        Experiment(
            "D05.09",
            "Hierarchical/session retrieval",
            "Response child",
            "A matched prompt should retrieve its exact assistant response.",
            "For user hits, add the first following assistant message in the same request.",
            "Tool messages or multi-answer requests may break the simple user-to-assistant hierarchy.",
            retrieval_hook=_response_child,
        ),
        Experiment(
            "D05.10",
            "Hierarchical/session retrieval",
            "Split-query session intersection",
            "Separate query concepts can identify a session even when they occur in different turns.",
            "Intersect session sets from up to four rare-term lexical lookups and acquire their matching chunks.",
            "A rare term with no literal match prevents acquisition; each lookup is capped at 100 hits.",
            retrieval_hook=_session_intersection,
        ),
    ]
