"""Independent structural segmenters that retain every non-whitespace source character."""

import re
from dataclasses import replace
from datetime import datetime, time

from harness.types import Document, Experiment, Message


_BULLET = re.compile(r"^([ \t]*)(?:[-+*]|\d+[.)])[ \t]+\S")
_KEY = re.compile(r"^[ \t]*([^\W\d_][\w ./-]{0,59})[ \t]*[:=][ \t]*\S")
_TURN = re.compile(
    r"^[ \t]*(?:\[\d{2}:\d{2}(?::\d{2})?\][ \t]*)?"
    r"(?:(?i:user|assistant|system|tool|human|speaker[ \t]+\d+)"
    r"|[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2}):[ \t]+\S"
)
_STAMP = r"(?:\d{4}-\d{2}-\d{2}[T ])?\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
_LOG = re.compile(r"^[ \t]*(?:\[(?P<bracket>" + _STAMP + r")\]|(?P<bare>" + _STAMP + r"))[ \t]+\S")


def _lines(text):
    result, offset = [], 0
    for raw in text.splitlines(keepends=True):
        result.append((offset, offset + len(raw), raw.rstrip("\r\n")))
        offset += len(raw)
    return result


def _partition(text, boundaries):
    if not boundaries:
        return []
    edges = sorted({0, len(text), *boundaries})
    spans = []
    for start, end in zip(edges, edges[1:]):
        if not 0 <= start <= end <= len(text):
            raise ValueError("Structural boundary outside the source message")
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
    return spans


def _fences(text):
    ranges, opened = [], None
    for start, end, line in _lines(text):
        match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", line)
        if not match:
            continue
        if opened is None:
            opened = (start, match[1][0], len(match[1]))
        elif match[1][0] == opened[1] and len(match[1]) >= opened[2] and not match[2].strip():
            ranges.append((opened[0], end))
            opened = None
    if opened is not None:
        ranges.append((opened[0], len(text)))
    return ranges


def _prose_lines(text):
    ranges, index = _fences(text), 0
    for start, end, line in _lines(text):
        while index < len(ranges) and start >= ranges[index][1]:
            index += 1
        blocked = index < len(ranges) and ranges[index][0] <= start < ranges[index][1]
        yield start, end, None if blocked else line


def _resegment(messages, documents, tag, splitter):
    sources = {message.id: message for message in messages}
    groups = {}
    for document in documents:
        groups.setdefault(document.message_id, []).append(document)
    changed = {}
    for message_id, existing in groups.items():
        message = sources.get(message_id)
        if message is None:
            continue
        spans = splitter(message.text)
        if not spans:
            continue
        template = min(existing, key=lambda document: document.offset)
        if len(existing) == len(spans) == 1 and (
            template.offset, template.text
        ) == (spans[0][0], message.text[spans[0][0]:spans[0][1]]):
            continue
        changed[message_id] = [
            replace(
                template,
                id=f"d04.{tag}:{len(message.id)}:{message.id}:{start}:{end}",
                message_id=message.id, session_id=message.session_id,
                request_id=message.request_id, position=message.position,
                offset=start, text=message.text[start:end], role=message.role,
                timestamp=message.timestamp,
                index_text=message.text[start:end],
            )
            for start, end in spans
        ]
    result, emitted = [], set()
    for document in documents:
        if document.message_id not in changed:
            result.append(document)
        elif document.message_id not in emitted:
            result.extend(changed[document.message_id])
            emitted.add(document.message_id)
    if not changed:
        return result
    return [replace(document, ordinal=i) for i, document in enumerate(result)]


def _sentence_spans(text):
    boundaries = []
    abbreviations = {
        "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "etc.", "vs.", "e.g.", "i.e.",
    }
    for match in re.finditer(r"""[.!?\u3002\uff01\uff1f]+["'\u201d\u2019)\]]*""", text):
        mark = match[0].rstrip("\"'\u201d\u2019)]")
        end = match.end()
        if mark[0] in ".!?" and end < len(text) and not text[end].isspace():
            continue
        if mark == ".":
            prefix = text[:match.end()]
            word = re.search(r"[\w.]+$", prefix)
            if word and (
                word[0].lower() in abbreviations
                or re.fullmatch(r"(?:[A-Za-z]\.)+", word[0])
                or re.fullmatch(r"\d+\.", prefix.rsplit("\n", 1)[-1].strip())
            ):
                continue
        if mark.startswith(".."):
            continue
        boundaries.append(end)
    return _partition(text, boundaries)


def sentences(messages: tuple[Message, ...], documents: tuple[Document, ...]):
    return _resegment(messages, documents, "01", _sentence_spans)


def _paragraph_spans(text):
    return _partition(text, [match.end() for match in re.finditer(r"\r?\n[ \t]*\r?\n", text)])


def paragraphs(messages, documents):
    return _resegment(messages, documents, "02", _paragraph_spans)


def _section_spans(text):
    lines, boundaries = list(_prose_lines(text)), []
    for i, (start, _, line) in enumerate(lines):
        if line is None:
            continue
        atx = re.match(r"^[ \t]{0,3}#{1,6}[ \t]+\S", line)
        setext = (
            line.strip() and not _BULLET.match(line) and i + 1 < len(lines)
            and lines[i + 1][2] is not None
            and re.fullmatch(r"[ \t]{0,3}(?:={3,}|-{3,})[ \t]*", lines[i + 1][2])
        )
        if atx or setext:
            boundaries.append(start)
    return _partition(text, boundaries)


def heading_sections(messages, documents):
    return _resegment(messages, documents, "03", _section_spans)


def _list_spans(text):
    boundaries, root_indent = [], None
    for start, _, line in _prose_lines(text):
        if line is None:
            root_indent = None
            continue
        match = _BULLET.match(line)
        indent = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
        if match:
            if root_indent is None or indent <= root_indent:
                boundaries.append(start)
                root_indent = indent
        elif line.strip() and root_indent is not None and indent <= root_indent:
            boundaries.append(start)
            root_indent = None
    return _partition(text, boundaries)


def list_items(messages, documents):
    return _resegment(messages, documents, "04", _list_spans)


def _cells(line):
    if line is None or "|" not in line:
        return []
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", value)]
    return cells if len(cells) >= 2 else []


def _table_spans(text):
    lines, boundaries, i = list(_prose_lines(text)), [], 0
    while i + 2 < len(lines):
        header, separator = _cells(lines[i][2]), _cells(lines[i + 1][2])
        if not (
            header and len(header) == len(separator)
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
            and len(_cells(lines[i + 2][2])) == len(header)
        ):
            i += 1
            continue
        boundaries.append(lines[i][0])
        j = i + 3
        while j < len(lines) and len(_cells(lines[j][2])) == len(header):
            boundaries.append(lines[j][0])
            j += 1
        boundaries.append(lines[j][0] if j < len(lines) else len(text))
        i = j
    return _partition(text, boundaries)


def table_rows(messages, documents):
    return _resegment(messages, documents, "05", _table_spans)


def _dialogue_spans(text):
    turns = [
        start
        for start, _, line in _prose_lines(text)
        if line is not None and _TURN.match(line)
    ]
    return _partition(text, turns if len(turns) >= 2 else [])


def dialogue_turns(messages, documents):
    return _resegment(messages, documents, "06", _dialogue_spans)


def _record_spans(text):
    boundaries, seen, cuts = [], set(), []
    begin, root_indent = None, 0

    def finish(end):
        if cuts:
            boundaries.extend([begin, *cuts, end])

    for start, _, line in _prose_lines(text):
        if line is not None and not line.strip():
            continue
        match = _KEY.match(line) if line is not None else None
        indent = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip()) if line else 0
        if match:
            key = " ".join(match[1].casefold().split())
            if begin is None:
                begin, root_indent = start, indent
            if key in seen:
                cuts.append(start)
                seen.clear()
            seen.add(key)
        elif begin is not None and line is not None and indent > root_indent:
            continue
        else:
            finish(start)
            begin, seen, cuts = None, set(), []
    finish(len(text))
    return _partition(text, boundaries)


def field_records(messages, documents):
    return _resegment(messages, documents, "07", _record_spans)


def _code_spans(text):
    return _partition(text, [edge for pair in _fences(text) for edge in pair])


def fenced_blocks(messages, documents):
    return _resegment(messages, documents, "08", _code_spans)


def _quote_spans(text):
    lines, boundaries, i = list(_prose_lines(text)), [], 0
    while i < len(lines):
        if lines[i][2] is None or not re.match(r"^[ \t]{0,3}>", lines[i][2]):
            i += 1
            continue
        boundaries.append(lines[i][0])
        j = i + 1
        while j < len(lines):
            if lines[j][2] is None:
                break
            if re.match(r"^[ \t]{0,3}>", lines[j][2]):
                j += 1
            elif (not lines[j][2].strip() and j + 1 < len(lines)
                  and lines[j + 1][2] is not None
                  and re.match(r"^[ \t]{0,3}>", lines[j + 1][2])):
                j += 1
            else:
                break
        boundaries.append(lines[j][0] if j < len(lines) else len(text))
        i = j
    return _partition(text, boundaries)


def quote_blocks(messages, documents):
    return _resegment(messages, documents, "09", _quote_spans)


def _log_spans(text):
    boundaries = []
    for start, _, line in _prose_lines(text):
        match = _LOG.match(line) if line is not None else None
        if match:
            stamp = (match["bracket"] or match["bare"]).replace("Z", "+00:00").replace(",", ".")
            try:
                (datetime if re.match(r"\d{4}-", stamp) else time).fromisoformat(stamp)
            except ValueError:
                continue
            boundaries.append(start)
    return _partition(text, boundaries if len(boundaries) >= 2 else [])


def log_events(messages, documents):
    return _resegment(messages, documents, "10", _log_spans)


def experiments() -> list[Experiment]:
    specs = [
        (
            "Sentence units", sentences,
            "Punctuation-delimited facts reduce unrelated terms within retrieval candidates.",
            "Partition at sentence terminals with conservative English abbreviation and "
            "decimal handling.",
            "Heuristic punctuation cannot reliably parse all abbreviations, code or "
            "unpunctuated speech.",
        ),
        (
            "Paragraph units", paragraphs,
            "Blank-line paragraphs retain local context while separating unrelated discussion.",
            "Partition at blank lines, preserving single-newline continuations.",
            "A paragraph may mix topics; unformatted messages remain at baseline segmentation.",
        ),
        (
            "Heading sections", heading_sections,
            "Keeping each explicit heading with its section preserves topic-to-fact locality.",
            "Partition on ATX and setext headings outside fenced code.",
            "Nested headings start new sections; long sections are not subdivided by size.",
        ),
        (
            "Hierarchical list items", list_items,
            "Keeping nested bullets with their root item preserves dependencies in "
            "enumerated facts.",
            "Partition root-level list items and surrounding prose, retaining nested children.",
            "Malformed indentation and lazy unindented continuations can create false boundaries.",
        ),
        (
            "Markdown table rows", table_rows,
            "Row units isolate structured records that baseline chunks can mix together.",
            "Detect header-divider tables; keep the header with the first row and split "
            "later rows.",
            "Later rows do not inherit headers; multiline cells and irregular column "
            "counts are unsupported.",
        ),
        (
            "Transcript turns", dialogue_turns,
            "Explicit speaker turns keep attribution attached to each utterance.",
            "Partition messages with at least two line-start speaker headers outside code.",
            "Capitalized field keys may resemble speakers; unlabeled dialogue stays unchanged.",
        ),
        (
            "Repeated-field records", field_records,
            "Record boundaries prevent fields from adjacent entities sharing a candidate.",
            "Start a new record when a field key repeats within a contiguous key-value block.",
            "Repeated keys inside one record can over-split; unique-key and nested "
            "records are ambiguous.",
        ),
        (
            "Atomic fenced blocks", fenced_blocks,
            "Keeping fenced code intact preserves syntax and separates code from prose "
            "retrieval noise.",
            "Partition on matching Markdown code fences, retaining delimiters and "
            "complete code bodies.",
            "Indented code is not recognized; an unclosed fence consumes the remaining message.",
        ),
        (
            "Explicit quotation blocks", quote_blocks,
            "Separating marked quotations reduces mixing of quoted claims with "
            "surrounding commentary.",
            "Partition greater-than-prefixed quote blocks outside code fences from "
            "surrounding prose.",
            "Unmarked lazy continuation and natural-language quotation marks are not resolved.",
        ),
        (
            "Timestamped log events", log_events,
            "Log events keep a timestamped event with its multiline details while "
            "separating neighbors.",
            "Partition at validated line-start ISO date-time or clock-time log prefixes.",
            "Dates without times and nonstandard timestamps stay unchanged; clock order "
            "is not inferred.",
        ),
    ]
    return [
        Experiment(
            id=f"D04.{i:02d}", direction="D04 structural segmentation",
            name=name, hypothesis=hypothesis,
            mechanism=mechanism, limitation=limitation, document_hook=hook,
        )
        for i, (name, hook, hypothesis, mechanism, limitation) in enumerate(specs, 1)
    ]
