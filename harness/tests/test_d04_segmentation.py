from dataclasses import replace
from itertools import product

from harness.directions import d04_segmentation as d04
from harness.types import Document, Message


def _message(text, *, mid="m1"):
    return Message(
        id=mid, request_id="r1", session_id="s1", position=1,
        role="user", text=text, timestamp=None, ordinal=1,
    )


def _document(message):
    return Document(
        id="base", message_id=message.id, session_id=message.session_id,
        request_id=message.request_id, position=message.position, offset=0,
        text=message.text, role=message.role, timestamp=message.timestamp,
        ordinal=message.ordinal,
    )


def _apply(name, text):
    message = _message(text)
    document = _document(message)
    hook = next(exp.document_hook for exp in d04.experiments() if exp.name == name)
    return message, hook((message,), (document,))


def _texts(result):
    return [document.text for document in result]


def _assert_provenance(message, documents):
    assert all(message.text[doc.offset:doc.offset + len(doc.text)] == doc.text for doc in documents)
    assert all(doc.message_id == message.id for doc in documents)


def test_catalog_has_ten_single_hook_experiments():
    experiments = d04.experiments()
    assert [exp.id for exp in experiments] == [f"D04.{i:02d}" for i in range(1, 11)]
    assert {exp.direction for exp in experiments} == {"D04 structural segmentation"}
    assert len({exp.name for exp in experiments}) == 10
    assert len({exp.document_hook for exp in experiments}) == 10
    assert len({exp.mechanism for exp in experiments}) == 10
    assert all(exp.hypothesis and exp.limitation for exp in experiments)
    assert all(
        exp.document_hook
        and not exp.query_hook
        and not exp.retrieval_hook
        and not exp.output_hook
        for exp in experiments
    )


def test_sentence_units_split_sentences_but_not_decimal_or_abbreviation():
    text = "Dr. Lin saw v1.2. It passed. Ship?"
    message, result = _apply("Sentence units", text)

    assert _texts(result) == ["Dr. Lin saw v1.2.", "It passed.", "Ship?"]
    _assert_provenance(message, result)


def test_paragraph_units_split_on_blank_lines_only():
    text = "line one\nline two\n\nline three"
    message, result = _apply("Paragraph units", text)

    assert _texts(result) == ["line one\nline two", "line three"]
    _assert_provenance(message, result)


def test_heading_sections_keep_heading_with_section_body():
    text = "# One\nalpha\n## Two\nbeta"
    message, result = _apply("Heading sections", text)

    assert _texts(result) == ["# One\nalpha", "## Two\nbeta"]
    _assert_provenance(message, result)


def test_hierarchical_list_items_keep_nested_children():
    text = "- alpha\n  - child a\n  more detail\n- beta\n  - child b"
    message, result = _apply("Hierarchical list items", text)

    assert _texts(result) == ["- alpha\n  - child a\n  more detail", "- beta\n  - child b"]
    _assert_provenance(message, result)


def test_markdown_table_rows_split_records_and_leave_plain_pipes_unchanged():
    text = "| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |\nfooter"
    message, result = _apply("Markdown table rows", text)

    assert _texts(result) == [
        "| name | value |\n| --- | --- |\n| alpha | 1 |",
        "| beta | 2 |",
        "footer",
    ]
    _assert_provenance(message, result)

    plain = _message("a | b\nplain")
    assert d04.table_rows((plain,), (_document(plain),)) == [_document(plain)]


def test_transcript_turns_split_speaker_blocks():
    text = "Alice: hello\ncontinued\nBob: hi"
    message, result = _apply("Transcript turns", text)

    assert _texts(result) == ["Alice: hello\ncontinued", "Bob: hi"]
    _assert_provenance(message, result)


def test_repeated_field_records_split_on_repeated_key_only():
    text = "Name: Ada\nScore: 9\nName: Ben\nScore: 7"
    message, result = _apply("Repeated-field records", text)

    assert _texts(result) == ["Name: Ada\nScore: 9", "Name: Ben\nScore: 7"]
    _assert_provenance(message, result)


def test_atomic_fenced_blocks_keep_code_as_one_span():
    text = "Before\n```python\nprint('x')\n```\nAfter"
    message, result = _apply("Atomic fenced blocks", text)

    assert _texts(result) == ["Before", "```python\nprint('x')\n```", "After"]
    _assert_provenance(message, result)


def test_explicit_quotation_blocks_separate_quotes_from_commentary():
    text = "Intro\n> quote one\n> quote two\nOutro"
    message, result = _apply("Explicit quotation blocks", text)

    assert _texts(result) == ["Intro", "> quote one\n> quote two", "Outro"]
    _assert_provenance(message, result)


def test_timestamped_log_events_split_valid_events_and_ignore_invalid_times():
    text = "10:00:00 boot\n detail\n10:01:05 ready"
    message, result = _apply("Timestamped log events", text)

    assert _texts(result) == ["10:00:00 boot\n detail", "10:01:05 ready"]
    _assert_provenance(message, result)

    bad = _message("25:00:00 bad\n26:00:00 also bad")
    assert d04.log_events((bad,), (_document(bad),)) == [_document(bad)]


def test_unmatched_structure_preserves_original_document():
    text = "one topic only"
    message = _message(text)
    document = _document(message)

    assert d04.paragraphs((message,), (document,)) == [document]


def test_segmentation_is_deterministic_and_stable_for_chinese_offsets():
    text = "第一句。第二句。"
    message = _message(text)
    document = _document(message)

    first = d04.sentences((message,), (document,))
    second = d04.sentences((message,), (document,))

    assert first == second
    assert _texts(first) == ["第一句。", "第二句。"]
    assert [doc.offset for doc in first] == [0, 4]
    _assert_provenance(message, first)


def test_every_segmenter_is_deterministic_source_local_and_covers_source():
    text = (
        "\U0001f642\u539f\u6587\u3002 More facts.\r\n\r\n"
        "# Title\r\nalpha\r\n## Next\r\n"
        "- root\r\n  - child\r\n- sibling\r\n"
        "| name | count |\r\n| --- | --- |\r\n| a | 1 |\r\n| b | 2 |\r\n"
        "Alice: hello\r\nBob: goodbye\r\n"
        "Name: a\r\nValue: 1\r\nName: b\r\nValue: 2\r\n"
        "```text\r\n# literal\r\n```\r\n> quote\r\nplain\r\n"
        "10:00:00 boot\r\n detail\r\n10:01:00 done"
    )
    message = _message(text)
    document = _document(message)
    other = _message("# FOREIGN_CANARY\nFOREIGN_CANARY", mid="foreign")
    for experiment in d04.experiments():
        hook = experiment.document_hook
        result = hook((message,), (document,))
        assert result == hook((message,), (document,)), experiment.id
        assert result == hook((other, message), (document,)), experiment.id
        assert len(result) > 1, experiment.id
        assert len({doc.id for doc in result}) == len(result)
        _assert_provenance(message, result)
        cursor = 0
        for doc in result:
            assert doc.offset >= cursor
            assert not text[cursor:doc.offset].strip()
            assert "FOREIGN_CANARY" not in doc.text + doc.context + doc.index_text
            assert doc.index_text == doc.text
            assert (
                doc.request_id, doc.session_id, doc.position, doc.role, doc.timestamp
            ) == (
                message.request_id, message.session_id, message.position,
                message.role, message.timestamp,
            )
            cursor = doc.offset + len(doc.text)
        assert not text[cursor:].strip()
        assert document == _document(message)


def test_every_segmenter_has_a_nonmatching_input():
    cases = [
        (d04.sentences, "words without sentence terminals"),
        (d04.paragraphs, "line one\nline two"),
        (d04.heading_sections, "```text\n# first\n# second\n```"),
        (d04.list_items, "hyphen-within-word"),
        (d04.table_rows, "one | two\nthree | four"),
        (d04.dialogue_turns, "Alice: just one turn"),
        (d04.field_records, "Name: Ada\nScore: 9"),
        (d04.fenced_blocks, "inline `code` and prose"),
        (d04.quote_blocks, "text > other text"),
        (d04.log_events, "2026-10-01 event\n2026-10-02 event"),
    ]
    for hook, text in cases:
        message = _message(text)
        document = _document(message)
        assert hook((message,), (document,)) == [document], hook.__name__


def test_single_fenced_structure_merges_baseline_chunks_without_losing_context():
    from harness.engine import source_documents, validate_documents

    message = _message("```text\n" + "item\n" * 300 + "```")
    original = source_documents("ordinary-case", (message,))
    documents = tuple(replace(doc, context="previous question") for doc in original)
    assert len(documents) > 1
    result = d04.fenced_blocks((message,), documents)
    assert len(result) == 1
    assert result[0].text == message.text
    assert result[0].context == "previous question"
    validate_documents((message,), result)


def test_resegmentation_emits_each_message_once_and_uses_dense_ordinals():
    from harness.engine import source_documents, validate_documents

    first, second = _message("First. Second."), _message("Third. Fourth.", mid="other")
    original = source_documents("ordinary-case", (first, second), size=8)
    assert len(original) > 2
    documents = tuple(replace(doc, context="source context") for doc in original)
    result = d04.sentences((first, second), documents)
    assert _texts(result) == ["First.", "Second.", "Third.", "Fourth."]
    assert [doc.ordinal for doc in result] == [0, 1, 2, 3]
    assert all(doc.context == "source context" for doc in result)
    validate_documents((first, second), result)


def test_structural_boundaries_cover_adversarial_combinations():
    pieces = (
        "", " \t\n", "\U0001f642\u4e2d\u6587\u3002", "Mr. Smith. Yes?",
        "## title", "- entry", "> quoted", "```", "~~~text",
        "Alice: hi", "Name: x", "10:01:00 ready", "| a | b |", "| --- | --- |",
    )
    for left, right in product(pieces, repeat=2):
        text = left + "\r\n\r\n" + right
        if not text.strip():
            continue
        message = _message(text)
        document = _document(message)
        for experiment in d04.experiments():
            result = experiment.document_hook((message,), (document,))
            _assert_provenance(message, result)
            assert len({doc.id for doc in result}) == len(result)
            assert "".join(char for doc in result for char in doc.text if not char.isspace()) == (
                "".join(char for char in text if not char.isspace())
            )


def test_unclosed_fence_and_shorter_inner_fence_keep_code_atomic():
    text = "Intro\n````text\n```\nbody\n````\nOutro"
    message, result = _apply("Atomic fenced blocks", text)
    assert _texts(result) == ["Intro", "````text\n```\nbody\n````", "Outro"]
    _assert_provenance(message, result)
    _, unclosed = _apply("Atomic fenced blocks", "Intro\n~~~text\nbody\nOutro")
    assert _texts(unclosed) == ["Intro", "~~~text\nbody\nOutro"]


def test_quote_segmentation_ignores_code_markers_and_keeps_adjacent_code_intact():
    text = "```text\n> literal code\n```\n> real quote\n```text\n> more code\n```"
    message, result = _apply("Explicit quotation blocks", text)
    assert _texts(result) == [
        "```text\n> literal code\n```",
        "> real quote",
        "```text\n> more code\n```",
    ]
    _assert_provenance(message, result)
    plain = _message("Intro\n```text\n> not a quote\n```\nOutro")
    assert d04.quote_blocks((plain,), (_document(plain),)) == [_document(plain)]


def test_known_syntactic_ambiguities_are_not_semantic_inference():
    _, abbreviations = _apply("Sentence units", "Words etc. Next topic.")
    assert len(abbreviations) == 1
    _, lazy_list = _apply("Hierarchical list items", "- one\nunindented continuation\n- two")
    assert _texts(lazy_list) == ["- one", "unindented continuation", "- two"]
    _, fields_as_names = _apply("Transcript turns", "Owner: Dana\nStatus: ready")
    assert _texts(fields_as_names) == ["Owner: Dana", "Status: ready"]
    _, repeated_field = _apply("Repeated-field records", "Tag: a\nTag: b")
    assert _texts(repeated_field) == ["Tag: a", "Tag: b"]
    _, lazy_quote = _apply("Explicit quotation blocks", "> quote\nunmarked continuation")
    assert _texts(lazy_quote) == ["> quote", "unmarked continuation"]


def test_setext_sections_and_escaped_table_pipes_preserve_offsets():
    _, sections = _apply("Heading sections", "Title\n=====\nbody\nNext\n----\nmore")
    assert _texts(sections) == ["Title\n=====\nbody", "Next\n----\nmore"]
    text = "a | b\n--- | ---\none\\|two | 1\nother | 2"
    message, rows = _apply("Markdown table rows", text)
    assert _texts(rows) == ["a | b\n--- | ---\none\\|two | 1", "other | 2"]
    _assert_provenance(message, rows)


def test_log_events_accept_iso_timezones_without_sorting_or_inference():
    text = (
        "[2026-10-29T10:05:00Z] first\n continuation\n"
        "2026-10-29T10:00:00+08:00 second"
    )
    message, result = _apply("Timestamped log events", text)
    assert _texts(result) == [
        "[2026-10-29T10:05:00Z] first\n continuation",
        "2026-10-29T10:00:00+08:00 second",
    ]
    _assert_provenance(message, result)


def test_empty_inputs_and_unrepresented_messages_emit_no_documents():
    message = _message("A. B.")
    for experiment in d04.experiments():
        assert experiment.document_hook((), ()) == []
        assert experiment.document_hook((message,), ()) == []
