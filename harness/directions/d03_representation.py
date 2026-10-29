"""Source-local index representations; these hooks never change source spans."""

import re
from bisect import bisect_right
from dataclasses import replace
from datetime import date

from harness.types import Document, Experiment, Message


_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_LIST = re.compile(r"^([ \t]*)(?:[-+*]|\d+[.)])[ \t]+(\S.*)$")
_FIELD = re.compile(r"^[ \t]*([^\W\d_][\w ./-]{0,59})[ \t]*[:=][ \t]*(\S.*)$")
_SPEAKER = re.compile(
    r"^[ \t]*(?:\[\d{2}:\d{2}(?::\d{2})?\][ \t]*)?"
    r"(?P<name>(?i:user|assistant|system|tool|human|speaker[ \t]+\d+)"
    r"|[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2})"
    r":[ \t]+\S"
)
_NEGATION = re.compile(
    r"\b(?:not|no|never|neither|without|cannot)\b|\b\w+n['\u2019]t\b", re.I
)


def _lines(text):
    offset = 0
    for raw in text.splitlines(keepends=True):
        yield offset, offset + len(raw), raw.rstrip("\r\n")
        offset += len(raw)


def _source_lines(text):
    """Ignore fenced code when interpreting prose markers."""
    fence = None
    for start, end, line in _lines(text):
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if (marker and marker[1][0] == fence[0]
                    and len(marker[1]) >= fence[1] and not marker[2].strip()):
                fence = None
            yield start, end, ""
        elif marker:
            fence = (marker[1][0], len(marker[1]))
            yield start, end, ""
        else:
            yield start, end, line


def _enrich(document, cues):
    cues = list(dict.fromkeys(cue for cue in cues if cue))
    if not cues:
        return document
    annotation = "\n".join(cues)
    index_text = document.index_text or document.text
    context = "\n".join(part for part in (document.context, annotation) if part)
    return replace(document, index_text=index_text + "\n" + annotation, context=context)


def _valid_source(document, message):
    return (
        message is not None
        and document.offset >= 0
        and message.text[document.offset:document.offset + len(document.text)] == document.text
        and all(getattr(document, field) == getattr(message, field) for field in (
            "session_id", "request_id", "position", "role", "timestamp"
        ))
    )


def _annotate_local(messages, documents, prepare, describe):
    sources = {message.id: message for message in messages}
    prepared = {}
    result = []
    for document in documents:
        message = sources.get(document.message_id)
        if not _valid_source(document, message):
            result.append(document)
            continue
        if message.id not in prepared:
            prepared[message.id] = prepare(message.text)
        result.append(_enrich(document, describe(document, prepared[message.id])))
    return result


def _interval_index(intervals):
    return [start for start, _, _ in intervals], intervals


def _overlapping(document, index):
    starts, intervals = index
    i = max(0, bisect_right(starts, document.offset) - 1)
    end = document.offset + len(document.text)
    while i < len(intervals) and intervals[i][0] < end:
        if intervals[i][1] > document.offset:
            yield intervals[i][2]
        i += 1


def role_labels(messages: tuple[Message, ...], documents: tuple[Document, ...]):
    return _annotate_local(
        messages, documents, lambda text: None,
        lambda document, _: [f"message role: {document.role}"] if document.role else [],
    )


def _heading_paths(text):
    stack, points = [], []
    lines = list(_source_lines(text))
    for i, (start, _, line) in enumerate(lines):
        atx = re.match(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line)
        level, title = (len(atx[1]), atx[2]) if atx else (0, "")
        if not atx and line.strip() and i + 1 < len(lines) and not _LIST.match(line):
            underline = re.fullmatch(r"[ \t]{0,3}(={3,}|-{3,})[ \t]*", lines[i + 1][2])
            if underline:
                level, title = (1 if underline[1][0] == "=" else 2), line.strip()
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            points.append((start, "heading path: " + " / ".join(item[1] for item in stack)))
    return _interval_index([
        (start, points[i + 1][0] if i + 1 < len(points) else len(text), cue)
        for i, (start, cue) in enumerate(points)
    ])


def heading_paths(messages, documents):
    return _annotate_local(messages, documents, _heading_paths, _overlapping)


def _date_cues(document, _):
    values = []
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", document.text):
        try:
            value = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        values.append(f"date: {_MONTHS[value.month - 1]} {value.day} {value.year}")
    pattern = r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"
    for match in re.finditer(pattern, document.text, re.I):
        month = next(i + 1 for i, name in enumerate(_MONTHS) if name.lower() == match[1].lower())
        try:
            value = date(int(match[3]), month, int(match[2]))
        except ValueError:
            continue
        values.append(f"date: {value.isoformat()}")
    return values


def explicit_dates(messages, documents):
    return _annotate_local(messages, documents, lambda text: None, _date_cues)


def _negation_cues(document, _):
    cues = []
    for clause in re.split(r"(?<=[.!?;\u3002\uff01\uff1f])\s*|\n+", document.text):
        markers = list(dict.fromkeys(match[0].lower() for match in _NEGATION.finditer(clause)))
        if markers:
            cues.append(f"negation cue: {' '.join(markers)}\nnegated text: {clause.strip()}")
    return cues


def negation_cues(messages, documents):
    return _annotate_local(messages, documents, lambda text: None, _negation_cues)


def _identifier_cues(document, _):
    aliases = []
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]*(?:[_./-][A-Za-z0-9]+)*\b", document.text):
        word = match[0]
        split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", word)
        split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", split)
        split = re.sub(r"([A-Za-z])([0-9])|([0-9])([A-Za-z])", r"\1\3 \2\4", split)
        split = re.sub(r"[_./-]+", " ", split)
        split = " ".join(split.split())
        if split != word:
            aliases.append("identifier words: " + split)
    return aliases


def identifier_words(messages, documents):
    return _annotate_local(messages, documents, lambda text: None, _identifier_cues)


def _field_keys(text):
    intervals = []
    for start, end, line in _source_lines(text):
        match = _FIELD.match(line)
        if match:
            intervals.append((start + match.start(2), end, "field key: " + match[1].strip()))
    return _interval_index(intervals)


def field_keys(messages, documents):
    return _annotate_local(messages, documents, _field_keys, _overlapping)


def _list_parents(text):
    stack, intervals = [], []
    for start, end, line in _source_lines(text):
        match = _LIST.match(line)
        indent = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
        if match:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                intervals.append((start, end, "list ancestors: " + " / ".join(x[1] for x in stack)))
            stack.append((indent, match[2].strip()))
        elif line.strip():
            if stack and indent > stack[-1][0]:
                intervals.append((start, end, "list ancestors: " + " / ".join(x[1] for x in stack)))
            else:
                stack.clear()
    return _interval_index(intervals)


def list_ancestors(messages, documents):
    return _annotate_local(messages, documents, _list_parents, _overlapping)


def _speaker_labels(text):
    speaker, intervals = "", []
    for start, end, line in _source_lines(text):
        match = _SPEAKER.match(line)
        if match:
            speaker = match["name"]
        elif not line.strip():
            speaker = ""
        if speaker:
            intervals.append((start, end, "inline speaker: " + speaker))
    return _interval_index(intervals)


def inline_speakers(messages, documents):
    return _annotate_local(messages, documents, _speaker_labels, _overlapping)


def _character_cues(document, _):
    fragments = {}
    for match in re.finditer(r"[^\W_]+", document.text, re.UNICODE):
        word = match[0].lower()
        if len(word) >= 4:
            for i in range(len(word) - 2):
                fragments.setdefault(word[i:i + 3], None)
    return ["character fragments: " + " ".join(fragments)] if fragments else []


def character_fragments(messages, documents):
    return _annotate_local(messages, documents, lambda text: None, _character_cues)


def _acronym_definitions(text):
    definitions = {}
    pattern = r"((?:[A-Za-z]+[ \t]+){1,19}[A-Za-z]+)[ \t]+\(([A-Z]{2,10})\)"
    for match in re.finditer(pattern, text):
        prefix, acronym = match[1], match[2]
        # A definition must be an explicit, contiguous long-form phrase.
        words = list(re.finditer(r"[A-Za-z]+", prefix))
        for count in range(len(acronym), min(len(words), 2 * len(acronym)) + 1):
            selected = words[-count:]
            significant = [
                word[0]
                for word in selected
                if word[0].lower() not in {"and", "of", "the", "for"}
            ]
            if "".join(word[0].upper() for word in significant) == acronym:
                expansion = prefix[selected[0].start():selected[-1].end()]
                definitions.setdefault(acronym, []).append((match.end(), expansion))
                break
    return definitions


def _acronym_cues(document, definitions):
    cues = []
    for match in re.finditer(r"\b[A-Z]{2,10}\b", document.text):
        entries = definitions.get(match[0], ())
        # Do not import a definition from a later part of the message.
        usable = [entry for entry in entries if entry[0] <= document.offset + match.end() + 1]
        if usable:
            cues.append(f"acronym {match[0]}: {usable[-1][1]}")
    return cues


def explicit_acronyms(messages, documents):
    return _annotate_local(messages, documents, _acronym_definitions, _acronym_cues)


def experiments() -> list[Experiment]:
    specs = [
        (
            "Message role labels", role_labels,
            "Indexing source roles improves retrieval of speaker-qualified requests.",
            "Append the original message role to index text and context without indexing IDs.",
            "Roles do not identify a real person; role-neutral queries may gain "
            "irrelevant matches.",
        ),
        (
            "Heading path inheritance", heading_paths,
            "Local heading paths recover chunks whose topic appears only in a section title.",
            "Inherit the active Markdown heading ancestry at each source span.",
            "Only explicit Markdown headings are recognized; large sections can supply "
            "noisy context.",
        ),
        (
            "Explicit date aliases", explicit_dates,
            "Equivalent numeric and written dates match more consistently after index "
            "normalization.",
            "Add validated ISO or English month-name aliases for explicit dates in the span.",
            "Ambiguous numeric dates, relative dates, other languages and implicit "
            "timestamps stay unchanged.",
        ),
        (
            "Negation cue indexing", negation_cues,
            "Repeating marked negative clauses increases recall for negative-constraint requests.",
            "Index literal English negative markers and their original clause with "
            "descriptive labels.",
            "Markers do not establish semantic scope; quotations and double negatives "
            "remain ambiguous.",
        ),
        (
            "Identifier word indexing", identifier_words,
            "Splitting source identifiers lets ordinary word queries find technical facts.",
            "Append lexical parts of camel case, acronym case, digit transitions and "
            "delimited identifiers.",
            "Splits can be semantically wrong and do not supply synonyms or domain knowledge.",
        ),
        (
            "Field key inheritance", field_keys,
            "Clipped values become retrievable when their explicit field key is indexed with them.",
            "Attach a same-line key only to spans intersecting that key's value.",
            "Only simple colon or equals fields are recognized; multiline and nested "
            "values are not parsed.",
        ),
        (
            "List ancestor inheritance", list_ancestors,
            "Nested list facts become retrievable by their otherwise missing parent labels.",
            "Attach original enclosing bullet labels using indentation within the same message.",
            "Indentation is heuristic; malformed lists and distant prose can obscure "
            "parent boundaries.",
        ),
        (
            "Inline speaker inheritance", inline_speakers,
            "Chunks within explicit transcript turns match speaker-qualified requests more often.",
            "Attach the nearest explicit speaker header within the current nonblank "
            "transcript block.",
            "Capitalized field keys can resemble names; blank lines end attribution "
            "conservatively.",
        ),
        (
            "Character fragment indexing", character_fragments,
            "Character trigrams recover lexical partial matches absent from whole-token indexing.",
            "Append deduplicated source-word trigrams without changing source text or boundaries.",
            "Shared fragments can increase false matches; short words and semantic "
            "paraphrases gain nothing.",
        ),
        (
            "Local acronym expansion", explicit_acronyms,
            "An explicit earlier long form makes later acronym-only chunks findable by "
            "expansion words.",
            "Reuse same-message parenthesized definitions whose long-form initials match "
            "the acronym.",
            "Only explicit Latin initialisms are supported; definitions in other "
            "messages are not imported.",
        ),
    ]
    return [
        Experiment(
            id=f"D03.{i:02d}", direction="D03 source/index representations",
            name=name, hypothesis=hypothesis,
            mechanism=mechanism, limitation=limitation, document_hook=hook,
        )
        for i, (name, hook, hypothesis, mechanism, limitation) in enumerate(specs, 1)
    ]
