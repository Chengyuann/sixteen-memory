"""D02: deterministic lexical-normalization experiments."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from harness.types import Context, Experiment


_DASHES = "\u058a\u05be\u1400\u1806\u2010\u2011\u2012\u2013\u2014\u2015\u2e17\u2e1a\u2e3a\u2e3b"
_APOSTROPHES = "\u02bc\u055a\u07f4\u07f5\u2018\u2019\u201b\uff07"
_SMALL_NUMBERS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_CARDINALS = {
    **dict(enumerate(_SMALL_NUMBERS)),
    **{
        tens * 10 + units: word + (" " + _SMALL_NUMBERS[units] if units else "")
        for tens, word in enumerate(_TENS, 2)
        for units in range(10)
    },
}
_WORD_NUMBERS = {word: str(number) for number, word in _CARDINALS.items()}
_NUMBER_ATOM = "|".join(
    [*_SMALL_NUMBERS, *_TENS, "hundred", "thousand", "million", "billion", "trillion"]
)
_CARDINAL_RE = re.compile(
    rf"\b(?:{_NUMBER_ATOM})(?:[ -]+(?:and[ -]+)?(?:{_NUMBER_ATOM}))*\b|"
    r"(?<![\w.,/:-])[+-]?[0-9]+(?:[.,:/-][0-9]+)*(?:st|nd|rd|th)?(?![\w-])",
    re.IGNORECASE,
)
_LONG_SHORT_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]*(?:[ \t]+[A-Za-z][A-Za-z0-9]*){1,11})"
    r"[ \t]*\(([A-Z]{2,10})\)"
)
_SHORT_LONG_RE = re.compile(
    r"\b([A-Z]{2,10})[ \t]*\("
    r"([A-Za-z][A-Za-z0-9]*(?:[ \t]+[A-Za-z][A-Za-z0-9]*){1,11})\)"
)
_INITIALISM_CONNECTORS = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to"}


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
        cleaned = re.sub(r"\s+", " ", variant).strip()
        terms = frozenset(ctx.tokens(cleaned))
        if terms and terms not in seen:
            result.append((cleaned, 0.76))
            seen.add(terms)
    return result


def _nfkc_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    return _queries(query, ctx, (unicodedata.normalize("NFKC", query),))


def _casefold_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    return _queries(query, ctx, (query.casefold(),))


def _cardinal_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    def alias(match: re.Match[str]) -> str:
        value = match.group(0)
        if re.fullmatch(r"0|[1-9][0-9]?", value):
            return _CARDINALS[int(value)]
        return _WORD_NUMBERS.get(re.sub(r"[ -]+", " ", value.lower()), value)

    return _queries(query, ctx, (_CARDINAL_RE.sub(alias, query),))


def _hyphen_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    canonical = query.translate(str.maketrans({char: "-" for char in _DASHES}))
    spaced = re.sub(r"(?<=\w)-(?=\w)", " ", canonical)
    joined = re.sub(r"(?<=\w)-(?=\w)", "", canonical)
    return _queries(query, ctx, (canonical, spaced, joined))


def _possessive_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    straight = query.translate(str.maketrans({char: "'" for char in _APOSTROPHES}))
    unpossessed = re.sub(r"\b([\w-]+)'s\b", r"\1", straight, flags=re.IGNORECASE)
    return _queries(query, ctx, (straight, unpossessed))


def _acronym_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    def undot(match: re.Match[str]) -> str:
        return match.group(0).replace(".", "")

    compact = re.sub(r"\b(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", undot, query)
    compact = re.sub(
        r"\b(?:[A-Z]\s+){1,}[A-Z]\b",
        lambda match: re.sub(r"\s+", "", match.group(0)),
        compact,
    )
    return _queries(query, ctx, (compact,))


def _identifier_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", query)
    split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", split)
    split = re.sub(r"[_/.\\]+", " ", split)
    split = re.sub(r"(?<=\D)(?=\d)|(?<=\d)(?=\D)", " ", split)
    return _queries(query, ctx, (split,))


def _numeric_grouping_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    normalized = re.sub(r"(?<=\d)[,_](?=\d{3}(?:\D|$))", "", query)
    return _queries(query, ctx, (normalized,))


def _source_abbreviation_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    requested = set(ctx.tokens(query))
    definitions: dict[str, dict[tuple[str, ...], str]] = {}

    def record(short: str, long: str, *, trim_prefix: bool = False) -> None:
        key = short.lower()
        if key not in requested:
            return
        words = long.split()
        matches = []
        # Only a name before parentheses may include preceding sentence words.
        for start in range(len(words)) if trim_prefix else (0,):
            suffix = words[start:]
            initials = "".join(
                word[0] for word in suffix if word.lower() not in _INITIALISM_CONNECTORS
            )
            if initials.lower() == key or "".join(word[0] for word in suffix).lower() == key:
                matches.append(" ".join(suffix))
        if not matches:
            return
        long = min(matches, key=lambda value: (len(value.split()), value))
        normalized = tuple(ctx.tokens(long))
        choices = definitions.setdefault(key, {})
        choices[normalized] = min(long, choices.get(normalized, long))

    for message in ctx.messages:
        for match in _LONG_SHORT_RE.finditer(message.text):
            record(match.group(2), match.group(1), trim_prefix=True)
        for match in _SHORT_LONG_RE.finditer(message.text):
            record(match.group(1), match.group(2))
    aliases = {
        short: next(iter(choices.values()))
        for short, choices in definitions.items()
        if len(choices) == 1
    }
    expanded = re.sub(
        r"\b[A-Za-z]{2,10}\b",
        lambda match: aliases.get(match.group(0).lower(), match.group(0)),
        query,
    )
    return _queries(query, ctx, (expanded,))


def _symbol_word_hook(query: str, ctx: Context) -> list[tuple[str, float]]:
    replacements = {"&": " and ", "+": " plus ", "@": " at ", "%": " percent "}
    words = query
    for symbol, replacement in replacements.items():
        words = words.replace(symbol, replacement)
    return _queries(query, ctx, (words,))


def experiments() -> list[Experiment]:
    """Return exactly ten independent lexical-normalization experiments."""
    specs = [
        (
            "D02.01",
            "Unicode compatibility form",
            "Compatibility-equivalent characters should share lexical matches.",
            "Add an NFKC-normalized query for width, ligature, and compatibility forms.",
            "NFKC can erase distinctions that are meaningful in specialized notation.",
            _nfkc_hook,
        ),
        (
            "D02.02",
            "Unicode case folding",
            "Multi-character case folds can match spellings outside baseline lowercasing.",
            "Add Unicode casefold aliases, such as sharp-s to ss, beyond simple lowercasing.",
            "Case can distinguish identifiers and proper names.",
            _casefold_hook,
        ),
        (
            "D02.03",
            "Cardinal word-number aliases",
            "Explicit number words can recover numeric evidence and vice versa.",
            "Exchange standalone English cardinal words and decimal integers from 0 to 99.",
            "Larger numbers are not expanded; number words can have nonnumeric meanings.",
            _cardinal_hook,
        ),
        (
            "D02.04",
            "Hyphen topology aliases",
            "Hyphenated, spaced, and joined forms often describe the same lexical item.",
            "Canonicalize dash characters and add spaced and joined word forms.",
            "Changing a hyphen can merge terms whose punctuation is meaningful.",
            _hyphen_hook,
        ),
        (
            "D02.05",
            "Possessive aliases",
            "Possessive punctuation differences should not block a noun match.",
            "Canonicalize apostrophes and add a form without English possessive suffixes.",
            "The pattern cannot reliably distinguish every possessive from a contraction.",
            _possessive_hook,
        ),
        (
            "D02.06",
            "Acronym punctuation",
            "Dotted and letter-spaced acronyms should match compact acronym forms.",
            "Compact repeated single-letter acronym forms without a dictionary.",
            "Ordinary isolated capital letters can be mistaken for an acronym.",
            _acronym_hook,
        ),
        (
            "D02.07",
            "Identifier segmentation",
            "Splitting identifier boundaries exposes words hidden in code-like names.",
            "Split camel case, acronym transitions, separators, and letter-digit boundaries.",
            "Opaque hashes and product identifiers may become noisy fragments.",
            _identifier_hook,
        ),
        (
            "D02.08",
            "Numeric grouping marks",
            "Grouped and ungrouped digit strings should retrieve the same numeric evidence.",
            "Remove comma or underscore grouping marks before three-digit groups.",
            "Locale-specific decimal commas can be misread as grouping marks.",
            _numeric_grouping_hook,
        ),
        (
            "D02.09",
            "Source-defined abbreviation expansion",
            "An explicitly defined abbreviation can recover evidence using its full name.",
            "Expand initialisms from source-only long name (ABC) or ABC (long name) definitions.",
            "Only parenthetical Latin initialisms are parsed; conflicting definitions are skipped.",
            _source_abbreviation_hook,
        ),
        (
            "D02.10",
            "Symbol word aliases",
            "Textual names for common symbols can match prose that omits the glyph.",
            "Replace ampersand, plus, at, and percent symbols with generic word aliases.",
            "Symbols can carry domain-specific meanings not captured by these words.",
            _symbol_word_hook,
        ),
    ]
    return [
        Experiment(
            id=experiment_id,
            direction="D02 lexical normalization",
            name=name,
            hypothesis=hypothesis,
            mechanism=mechanism,
            limitation=limitation,
            query_hook=hook,
        )
        for experiment_id, name, hypothesis, mechanism, limitation, hook in specs
    ]
