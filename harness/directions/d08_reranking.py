"""Ten independent, source-only candidate rerankers."""

from collections import Counter
from dataclasses import replace
from math import isfinite, log, sqrt
import re

from harness.types import Context, Experiment, Hit


def _query_tokens(query: str, ctx: Context) -> list[str]:
    return ctx.tokens(ctx.focused(query))


def _rank(hits: list[Hit], scores: list[float]) -> list[Hit]:
    # Stable ties retain the incoming baseline rank, without mixing score scales.
    return [
        replace(hit, score=float(score))
        for hit, score in sorted(zip(hits, scores), key=lambda item: item[1], reverse=True)
    ]


def _weight(term: str, ctx: Context) -> float:
    value = ctx.idf.get(term, 1.0)
    return value if isfinite(value) and value > 0 else 1.0


def unique_coverage(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = set(_query_tokens(query, ctx))
    if not wanted:
        return list(hits)
    return _rank(hits, [
        len(wanted.intersection(ctx.tokens(hit.document.text))) / len(wanted)
        for hit in hits
    ])


def _proximity(tokens: list[str], wanted: set[str]) -> float:
    matched = wanted.intersection(tokens)
    if len(matched) < 2:
        return 0.0
    counts: Counter[str] = Counter()
    left = 0
    width = len(tokens) + 1
    for right, token in enumerate(tokens):
        if token in matched:
            counts[token] += 1
        while len(counts) == len(matched):
            width = min(width, right - left + 1)
            departing = tokens[left]
            if departing in counts:
                counts[departing] -= 1
                if counts[departing] == 0:
                    del counts[departing]
            left += 1
    return (len(matched) - 1) / (width - 1)


def minimum_window(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = set(_query_tokens(query, ctx))
    if len(wanted) < 2:
        return list(hits)
    return _rank(hits, [_proximity(ctx.tokens(hit.document.text), wanted) for hit in hits])


def rare_term_balance(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = list(dict.fromkeys(_query_tokens(query, ctx)))
    if not wanted:
        return list(hits)
    weights = [_weight(term, ctx) for term in wanted]
    total_weight = sum(weights)
    scores = []
    for hit in hits:
        counts = Counter(ctx.tokens(hit.document.text))
        if any(not counts[term] for term in wanted):
            scores.append(0.0)
            continue
        # A weighted harmonic mean makes the weakest rare term the bottleneck.
        denominator = sum(
            weight * (counts[term] + 1) / counts[term]
            for term, weight in zip(wanted, weights)
        )
        scores.append(total_weight / denominator)
    return _rank(hits, scores)


def adjacent_pairs(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    tokens = _query_tokens(query, ctx)
    pairs = set(zip(tokens, tokens[1:]))
    if not pairs:
        return list(hits)
    scores = []
    for hit in hits:
        source = ctx.tokens(hit.document.text)
        observed = set(zip(source, source[1:]))
        scores.append(len(pairs.intersection(observed)) / len(pairs))
    return _rank(hits, scores)


def _subsequence_length(query: list[str], source: list[str]) -> int:
    row = [0] * (len(query) + 1)
    for token in source:
        diagonal = 0
        for index, expected in enumerate(query, 1):
            previous = row[index]
            row[index] = diagonal + 1 if token == expected else max(row[index], row[index - 1])
            diagonal = previous
    return row[-1]


def ordered_subsequence(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    tokens = _query_tokens(query, ctx)
    if len(tokens) < 2:
        return list(hits)
    return _rank(hits, [
        _subsequence_length(tokens, ctx.tokens(hit.document.text)) / len(tokens)
        for hit in hits
    ])


_ROLE_ATTRIBUTION = re.compile(
    r"\b(user|assistant|system|tool)\s+(?:said|wrote|mentioned|reported|recommended)\b"
    r"|\b(?:did|does)\s+(?:the\s+)?(user|assistant|system|tool)\s+"
    r"(?:say|write|mention|report|recommend)\b",
    re.IGNORECASE,
)


def attributed_role(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    roles = {first.lower() or second.lower() for first, second in _ROLE_ATTRIBUTION.findall(query)}
    if len(roles) != 1:
        return list(hits)
    role = next(iter(roles))
    wanted = set(_query_tokens(query, ctx)) - {role}
    if not wanted:
        return list(hits)
    return _rank(hits, [
        float(hit.document.role.lower() == role and
              bool(wanted.intersection(ctx.tokens(hit.document.text))))
        for hit in hits
    ])


_FACET_BOUNDARY = re.compile(r"\b(?:and|versus|vs)\b|[,;?\u3001\uff0c\uff1b\uff1f]", re.IGNORECASE)


def weakest_facet(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    facets = []
    for fragment in _FACET_BOUNDARY.split(query):
        facet = frozenset(_query_tokens(fragment, ctx))
        if facet and facet not in facets:
            facets.append(facet)
    if len(facets) < 2:
        return list(hits)
    scores = []
    for hit in hits:
        source = set(ctx.tokens(hit.document.text))
        scores.append(min(len(facet & source) / len(facet) for facet in facets))
    return _rank(hits, scores)


def lexical_cosine(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    query_counts = Counter(_query_tokens(query, ctx))
    if not query_counts:
        return list(hits)
    query_vector = {term: count * _weight(term, ctx) for term, count in query_counts.items()}
    query_norm = sqrt(sum(value * value for value in query_vector.values()))
    scores = []
    for hit in hits:
        vector = {
            term: count * _weight(term, ctx)
            for term, count in Counter(ctx.tokens(hit.document.text)).items()
        }
        norm = sqrt(sum(value * value for value in vector.values()))
        dot = sum(value * vector.get(term, 0.0) for term, value in query_vector.items())
        scores.append(dot / (query_norm * norm) if norm else 0.0)
    return _rank(hits, scores)


def background_likelihood(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    tokens = _query_tokens(query, ctx)
    if not tokens or not ctx.documents:
        return list(hits)
    background: Counter[str] = Counter()
    for document in ctx.documents:
        background.update(ctx.tokens(document.text))
    total = sum(background.values())
    if not total:
        return list(hits)
    vocabulary = len(set(background).union(tokens))
    prior = {term: (background[term] + 1) / (total + vocabulary) for term in dict.fromkeys(tokens)}
    mass = total / len(ctx.documents)
    scores = []
    for hit in hits:
        counts = Counter(ctx.tokens(hit.document.text))
        length = sum(counts.values())
        scores.append(sum(
            log((counts[term] + mass * prior[term]) / (length + mass))
            for term in tokens
        ) / len(tokens))
    return _rank(hits, scores)


_SENTENCE_BOUNDARY = re.compile(r"[.!?]+(?=\s|$)|[\u3002\uff01\uff1f]+|\r?\n")


def sentence_colocation(query: str, hits: list[Hit], ctx: Context) -> list[Hit]:
    wanted = set(_query_tokens(query, ctx))
    if not wanted:
        return list(hits)
    return _rank(hits, [
        max(
            (len(wanted.intersection(ctx.tokens(sentence))) / len(wanted)
             for sentence in _SENTENCE_BOUNDARY.split(hit.document.text)),
            default=0.0,
        )
        for hit in hits
    ])


def experiments() -> list[Experiment]:
    specifications = [
        (
            "Unique query coverage",
            "Distinct query terms improve ranking over repeated matches to one term.",
            "Rank by the fraction of distinct focused query tokens present in source text.",
            "Ignores word order, negation, term frequency and semantic paraphrases.",
            unique_coverage,
        ),
        (
            "Minimum term window",
            "Nearby matching terms are more likely to describe the same fact.",
            "Rank by matched-term density in the shortest token window covering all matched terms.",
            "A compact partial match can outrank a complete but dispersed match.",
            minimum_window,
        ),
        (
            "Rare term balance",
            "Balanced support for rare query terms resists repetition of only common terms.",
            "Rank by the IDF-weighted harmonic mean of tf/(tf+1); missing terms score zero.",
            "Requires every literal query token and can suppress useful partial evidence.",
            rare_term_balance,
        ),
        (
            "Adjacent query pairs",
            "Preserving adjacent query token pairs reduces bag-of-words false matches.",
            "Rank by the fraction of distinct query bigrams occurring in source token order.",
            "Different query pairs can occur in separate passages; paraphrases lose credit.",
            adjacent_pairs,
        ),
        (
            "Ordered query subsequence",
            "Query order helps even when modifiers separate matching source words.",
            "Rank by longest common subsequence length divided by focused query length.",
            "Allows arbitrarily distant matches; O(query tokens times source tokens) work.",
            ordered_subsequence,
        ),
        (
            "Explicit speaker attribution",
            "An explicitly requested speaker role should precede other matching speakers.",
            "Prefer lexically matching hits with the single role named in an English attribution.",
            "Narrow English grammar only; role metadata does not establish truth or authorship.",
            attributed_role,
        ),
        (
            "Weakest query facet",
            "Balanced coverage of separate query clauses improves multi-condition ranking.",
            "Split explicit conjunctions and punctuation; rank by minimum per-facet coverage.",
            "Conjunctions need not be independent facets; distributed evidence can be demoted.",
            weakest_facet,
        ),
        (
            "Source lexical cosine",
            "Length-normalized lexical focus reduces distracting long-document matches.",
            "Rank by TF-IDF cosine between focused query and the full original source text.",
            "Can penalize useful context and qualifiers; exact repetitions are scale invariant.",
            lexical_cosine,
        ),
        (
            "Corpus background likelihood",
            "Corpus-relative query likelihood separates informative matches from common wording.",
            "Use add-one corpus probabilities and Dirichlet mass equal to mean document length.",
            "Lexical repetition is not proof; chunk overlap biases background token counts.",
            background_likelihood,
        ),
        (
            "Single sentence support",
            "Terms in one sentence are more useful than terms spread across unrelated sentences.",
            "Rank by maximum unique query-token coverage in any one source sentence.",
            "Punctuation rules are approximate and do not resolve negation or clause meaning.",
            sentence_colocation,
        ),
    ]
    return [
        Experiment(
            id=f"D08.{index:02d}",
            direction="D08",
            name=name,
            hypothesis=hypothesis,
            mechanism=mechanism,
            limitation=limitation,
            retrieval_hook=hook,
        )
        for index, (name, hypothesis, mechanism, limitation, hook) in enumerate(specifications, 1)
    ]
