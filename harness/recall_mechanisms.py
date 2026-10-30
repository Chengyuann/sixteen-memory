"""Source-only recall experiments; never applied to a live submission.

Fixed mechanisms, not parameter sweeps. R01/R02 repair the coarse T04 penalty.
R03/R04 can acquire evidence outside the old top100. Every output remains a
complete, unchanged original chunk with its original source coordinates.
"""
from collections import defaultdict
from dataclasses import replace
import math
import re

from .engine import Index, focused, tokens
from .next_mechanisms import PRIVATE, message
from .types import Experiment, Hit


def _reorder(rows, scores):
    return [
        Hit(rows[i].document, scores[i])
        for i in sorted(range(len(rows)), key=lambda i: (-scores[i], i))
    ]


def facet_diversity(query, hits, ctx):
    """Penalize a repeated message only if it supplies no new query facet."""
    rows = list(hits[:100])
    wanted = set(tokens(focused(query)))
    if not wanted:
        return rows
    seen, covered = set(), defaultdict(set)
    scores = [h.score for h in rows]
    for i, hit in enumerate(rows):
        d = hit.document
        present = wanted.intersection(tokens(d.text))
        if i >= 5 and d.message_id in seen and present <= covered[d.message_id]:
            scores[i] *= 0.75
        seen.add(d.message_id)
        covered[d.message_id].update(present)
    return _reorder(rows, scores)


def content_diversity(query, hits, ctx):
    """Only penalize near-duplicate chunks, not different facts in one message."""
    rows = list(hits[:100])
    wanted = set(tokens(focused(query)))
    scores = [h.score for h in rows]
    earlier = defaultdict(list)
    for i, hit in enumerate(rows):
        d = hit.document
        words = set(tokens(d.text))
        facets = words & wanted
        # Different numbers or polarity can encode a genuinely different fact.
        specifics = {w for w in words if any(c.isdigit() for c in w)}
        specifics.update(w for w in words if w in {"not", "no", "never", "without", "cannot"})
        duplicate = any(
            words and len(words & old) / len(words | old) >= 0.65
            and facets <= old_facets and specifics == old_specifics
            for old, old_facets, old_specifics in earlier[d.message_id]
        )
        if i >= 5 and duplicate:
            scores[i] *= 0.75
        earlier[d.message_id].append((words, facets, specifics))
    return _reorder(rows, scores)


def message_pool(query, hits, ctx):
    """Keep the top20, then give unseen source messages first access to the tail."""
    pool = list(hits[:600])
    if len(pool) <= 20:
        return pool
    selected = pool[:20]
    seen_messages = {h.document.message_id for h in selected}
    seen_ids = {h.document.id for h in selected}
    # At most20 previously unseen messages are acquired. Prefer the original
    # rank and do not scan outside the already lexical/context-matched pool.
    for hit in pool[20:]:
        if hit.document.message_id not in seen_messages:
            selected.append(hit)
            seen_messages.add(hit.document.message_id)
            seen_ids.add(hit.document.id)
            if len(selected) == 40:
                break
    selected.extend(h for h in pool if h.document.id not in seen_ids)
    selected = selected[:100]
    # Return monotone scores after intentional ordering, retaining the top20.
    ceiling = selected[19].score
    tail = []
    for h in selected[20:]:
        ceiling = min(h.score, ceiling * 0.995)
        tail.append(Hit(h.document, ceiling))
    return selected[:20] + tail


def _micro_documents(documents):
    """Two-scale acquisition units with original provenance; no generated text."""
    children, parents = [], {}
    for d in documents:
        # An auxiliary index is bounded; an oversized corpus uses baseline only.
        if len(children) + math.ceil(len(d.text) / 360) > 20_000:
            return (), {}
        for offset in range(0, len(d.text), 360):
            child = replace(
                d, id=f"{d.id}:micro:{offset}", offset=d.offset + offset,
                text=d.text[offset:offset + 360],
                index_text=d.text[offset:offset + 360], context="",
                ordinal=len(children),
            )
            children.append(child)
            parents[child.id] = d
    return tuple(children), parents


def micro_parent(query, hits, ctx):
    """Fuse short-window matches, but return the complete original parent chunk."""
    if not hits or not tokens(query):
        return list(hits[:100])
    children, parents = _micro_documents(ctx.documents)
    if not children:
        return list(hits[:100])
    index = Index(ctx.messages, children)
    try:
        micro_lists = [index.lexical(query)]
        focus = focused(query)
        if focus and focus != " ".join(tokens(query)):
            micro_lists.append(index.lexical(focus))
        micro_scores = {}
        for rows in micro_lists:
            parent_rank = {}
            for hit in rows:
                parent = parents[hit.document.id]
                if parent.id not in parent_rank:
                    parent_rank[parent.id] = len(parent_rank)
            for identifier, rank in parent_rank.items():
                micro_scores[identifier] = micro_scores.get(identifier, 0) + (
                    1 / len(micro_lists) / (61 + rank)
                )
        originals = {d.id: d for d in ctx.documents}
        scores = {h.document.id: h.score for h in hits[:600]}
        for identifier, score in micro_scores.items():
            scores[identifier] = scores.get(identifier, 0) + 0.5 * score
        return [
            Hit(originals[identifier], score)
            for identifier, score in sorted(scores.items(), key=lambda p: (-p[1], p[0]))
        ][:100]
    finally:
        index.close()


def question_neighbor(query, hits, ctx):
    """Acquire a short answer's immediate source question in the same session."""
    rows = list(hits[:300])
    if not rows:
        return rows
    wanted = set(tokens(focused(query)))
    messages = list(ctx.messages)
    positions = {m.id: i for i, m in enumerate(messages)}
    best = {h.document.id: h for h in rows}
    additions = 0
    for hit in rows[:12]:
        d = hit.document
        m = messages[positions[d.message_id]]
        if len(m.text) > 500 or not (wanted & set(tokens(m.text))):
            continue
        i = positions[m.id]
        if i == 0:
            continue
        previous = messages[i - 1]
        if (previous.session_id != m.session_id or len(previous.text) > 1500
                or "?" not in previous.text or PRIVATE.search(previous.text)):
            continue
        # Preserve explicit speaker labels; never infer a speaker from opaque IDs.
        if re.match(r"^\s*(?:system|developer)\s*:", previous.text, re.I):
            continue
        for candidate in ctx.message_documents[previous.id]:
            score = hit.score * 0.98
            existing = best.get(candidate.id)
            if existing is None or score > existing.score:
                best[candidate.id] = Hit(candidate, score)
                additions += 1
            if additions >= 8:
                break
        if additions >= 8:
            break
    return sorted(best.values(), key=lambda h: (-h.score, h.document.id))[:100]


def load():
    specs = [
        ("T04", "Frozen v0.2.8 message penalty", message,
         "Retain the frozen comparison rather than silently changing its behavior."),
        ("R01", "Query-facet preserving diversity", facet_diversity,
         "Do not demote a long-message chunk that supplies a previously missing query term."),
        ("R02", "Source-content redundancy penalty", content_diversity,
         "Different facts from one message are not duplicates; demote only near-identical content."),
        ("R03", "Bounded distinct-message candidate pool", message_pool,
         "Protect top20 while recovering up to20 unrepresented messages beyond the old top100."),
        ("R04", "Micro-window to original-parent retrieval", micro_parent,
         "Short-window BM25 can find facts diluted inside a long original chunk."),
        ("R05", "Session-local short-answer context", question_neighbor,
         "A short relevant reply may need the immediate earlier source question across Add batches."),
    ]
    return [
        Experiment(identifier, "recall-study", name, hypothesis, hypothesis,
                   "Source-only lexical prototype; not an answer-quality claim. "
                   "Acquisition variants may trade source coverage for breadth; "
                   "R04 rebuilds an auxiliary local index and is not deployment-ready.",
                   retrieval_hook=hook)
        for identifier, name, hook, hypothesis in specs
    ]
