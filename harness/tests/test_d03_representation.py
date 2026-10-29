from dataclasses import replace

from harness.directions import d03_representation as d03
from harness.types import Document, Message


def _message(text, *, role="user", mid="m1"):
    return Message(
        id=mid, request_id="r1", session_id="s1", position=1,
        role=role, text=text, timestamp=1700000000, ordinal=1,
    )


def _document(message, text=None, offset=0, *, did="d1"):
    if text is None:
        text = message.text[offset:]
    return Document(
        id=did, message_id=message.id, session_id=message.session_id,
        request_id=message.request_id, position=message.position,
        offset=offset, text=text, role=message.role, timestamp=message.timestamp,
        ordinal=message.ordinal,
    )


def _apply(name, message, document):
    hook = next(exp.document_hook for exp in d03.experiments() if exp.name == name)
    return hook((message,), (document,))


def test_catalog_has_ten_single_hook_experiments():
    experiments = d03.experiments()
    assert [exp.id for exp in experiments] == [f"D03.{i:02d}" for i in range(1, 11)]
    assert {exp.direction for exp in experiments} == {"D03 source/index representations"}
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


def test_role_labels_change_only_index_fields_and_are_deterministic():
    message = _message("remember deploy window")
    document = _document(message)

    first = _apply("Message role labels", message, document)
    second = _apply("Message role labels", message, document)

    assert first == second
    assert first[0].text == document.text
    assert first[0].offset == 0
    assert message.text[first[0].offset:first[0].offset + len(first[0].text)] == first[0].text
    assert "message role: user" in first[0].index_text
    assert first[0].context == "message role: user"


def test_representation_preserves_existing_index_context_and_document_ordinal():
    message = _message("source fact")
    document = Document(
        id="d9", message_id=message.id, session_id=message.session_id,
        request_id=message.request_id, position=message.position, offset=0,
        text=message.text, role=message.role, timestamp=message.timestamp,
        ordinal=9, index_text="baseline index", context="previous question",
    )

    result = _apply("Message role labels", message, document)

    assert result[0].ordinal == 9
    assert result[0].index_text == "baseline index\nmessage role: user"
    assert result[0].context == "previous question\nmessage role: user"


def test_heading_path_inherits_only_current_heading_path():
    text = "# Travel\nHotel: Sol\n## Flights\nSeat 12A confirmed"
    message = _message(text)
    offset = text.index("Seat")
    document = _document(message, "Seat 12A confirmed", offset)

    result = _apply("Heading path inheritance", message, document)

    assert "heading path: Travel / Flights" in result[0].context
    assert "Hotel" not in result[0].context


def test_explicit_date_aliases_validate_dates_and_keep_bad_dates_out():
    text = "deadline 2026-10-29\nbad date 2026-02-31"
    message = _message(text)
    document = _document(message)

    result = _apply("Explicit date aliases", message, document)

    assert "date: October 29 2026" in result[0].context
    assert "February 31" not in result[0].context


def test_negation_cues_repeat_negative_source_clause():
    text = "Use the new endpoint. Do not send credentials."
    message = _message(text)
    offset = text.index("Do")
    document = _document(message, "Do not send credentials.", offset)

    result = _apply("Negation cue indexing", message, document)

    assert "negation cue: not" in result[0].context
    assert "negated text: Do not send credentials." in result[0].context


def test_identifier_word_indexing_splits_identifiers_without_synonyms():
    message = _message("Call uploadToBitsV2 after parse_action_blocks.")
    document = _document(message)

    result = _apply("Identifier word indexing", message, document)

    assert "upload To Bits V 2" in result[0].context
    assert "parse action blocks" in result[0].context
    assert "repository" not in result[0].context


def test_field_key_inheritance_applies_to_value_span_only():
    text = "Owner: Dana\nStatus: ready"
    message = _message(text)
    value = "ready"
    document = _document(message, value, text.index(value))

    result = _apply("Field key inheritance", message, document)

    assert result[0].context == "field key: Status"


def test_list_ancestor_inheritance_uses_enclosing_source_bullet():
    text = "- Project Alpha\n  - deadline moved\n- Project Beta\n  - unchanged"
    message = _message(text)
    item = "deadline moved"
    document = _document(message, item, text.index(item))

    result = _apply("List ancestor inheritance", message, document)

    assert result[0].context == "list ancestors: Project Alpha"
    assert "Project Beta" not in result[0].context


def test_inline_speaker_inheritance_stops_at_blank_line():
    text = "Alice: Ship it\nstill Alice\n\nloose line"
    message = _message(text)
    document = _document(message, "still Alice", text.index("still"))
    loose = _document(message, "loose line", text.index("loose"), did="d2")

    result = d03.inline_speakers((message,), (document, loose))

    assert result[0].context == "inline speaker: Alice"
    assert result[1].context == ""


def test_character_fragments_are_source_derived_for_partial_matching():
    message = _message("watermelon")
    document = _document(message)

    result = _apply("Character fragment indexing", message, document)

    assert "wat ate ter erm rme mel elo lon" in result[0].context


def test_local_acronym_expansion_does_not_import_future_or_other_message_defs():
    text = (
        "Discuss RAG now. Retrieval Augmented Generation (RAG) was selected. "
        "RAG details later."
    )
    message = _message(text)
    early = _document(message, "Discuss RAG now.", 0)
    late = _document(message, "RAG details later.", text.index("RAG details"), did="d2")
    other = _message("Retrieval Augmented Generation (RAG)", mid="m2")

    result = d03.explicit_acronyms((message, other), (early, late, _document(other)))

    assert result[0].context == ""
    assert "acronym RAG: Retrieval Augmented Generation" in result[1].context
    assert "acronym RAG" in result[2].context


def test_invalid_source_provenance_is_left_unchanged():
    message = _message("source text")
    wrong = Document(
        id="bad", message_id=message.id, session_id=message.session_id,
        request_id=message.request_id, position=message.position, offset=0,
        text="not source", role=message.role, timestamp=message.timestamp,
        ordinal=message.ordinal,
    )

    result = _apply("Message role labels", message, wrong)

    assert result == [wrong]


def test_every_representation_is_deterministic_immutable_and_source_local():
    text = (
        "\u8ba1\u5212\U0001f642\n# Project\n## Schedule\n"
        "Date: 2026-10-29\n- Delivery\n  - uploadToBitsV2 is not ready.\n"
        "Alice: Retrieval Augmented Generation (RAG) is used.\n"
        "RAG supports watermelon lookup."
    )
    message = _message(text)
    other = _message(
        "# FOREIGN_CANARY\nOwner: FOREIGN_CANARY\n- FOREIGN_CANARY\n"
        "Foreign Canary (FC)\nAlice: FOREIGN_CANARY",
        mid="other-source",
    )
    documents = tuple(
        replace(
            _document(message, text[offset:offset + 35], offset, did=f"d{offset}"),
            ordinal=i + 15,
        )
        for i, offset in enumerate(range(0, len(text), 25))
    )
    for experiment in d03.experiments():
        hook = experiment.document_hook
        first = hook((message,), documents)
        assert first == hook((message,), documents), experiment.id
        assert first == hook((other, message), documents), experiment.id
        assert first != list(documents), experiment.id
        assert len(first) == len(documents)
        for before, after in zip(documents, first):
            assert replace(after, index_text=before.index_text, context=before.context) == before
            assert text[after.offset:after.offset + len(after.text)] == after.text
            assert "FOREIGN_CANARY" not in after.index_text + after.context
            assert before.index_text == before.context == ""


def test_every_representation_has_a_conservative_no_match_case():
    cases = [
        (d03.role_labels, _message("body", role="")),
        (d03.heading_paths, _message("Title\nplain body")),
        (d03.explicit_dates, _message("Tomorrow or 10/11/12 or 2026-02-31")),
        (d03.negation_cues, _message("Tie a knot at noon.")),
        (d03.identifier_words, _message("plain words")),
        (d03.field_keys, _message("Key:\n  multiline value")),
        (d03.list_ancestors, _message("- parent\n- same level")),
        (d03.inline_speakers, _message("unlabeled dialogue")),
        (d03.character_fragments, _message("a cat")),
        (d03.explicit_acronyms, _message("Unrelated Words (RAG). RAG")),
    ]
    for hook, message in cases:
        document = _document(message)
        assert hook((message,), (document,)) == [document], hook.__name__


def test_structural_representations_ignore_fenced_markers():
    message = _message(
        "```text\n# Not a heading\nOwner: hidden\nAlice: hidden\n"
        "- Parent\n  - child\n```"
    )
    document = _document(message)
    for hook in (d03.heading_paths, d03.field_keys, d03.inline_speakers, d03.list_ancestors):
        assert hook((message,), (document,)) == [document]


def test_acronym_definitions_are_local_ordered_and_do_not_bridge_punctuation():
    text = (
        "Remote Access Gateway (RAG). RAG first. "
        "Retrieval Augmented Generation (RAG). RAG second."
    )
    message = _message(text)
    first = _document(message, "RAG first", text.index("RAG first"))
    second = _document(message, "RAG second", text.index("RAG second"), did="d2")
    result = d03.explicit_acronyms((message,), (first, second))
    assert result[0].context == "acronym RAG: Remote Access Gateway"
    assert result[1].context == "acronym RAG: Retrieval Augmented Generation"

    undefined = _message("RAG is used.", mid="undefined")
    plain = _document(undefined, did="plain")
    assert d03.explicit_acronyms((message, undefined), (plain,)) == [plain]
    broken = _message("Remote/Access/Gateway (RAG)")
    assert d03.explicit_acronyms((broken,), (_document(broken),)) == [_document(broken)]


def test_written_date_alias_validates_leap_days_without_timestamp_inference():
    message = _message("February 29, 2024; February 29, 2025")
    result = d03.explicit_dates((message,), (_document(message),))
    assert result[0].context == "date: 2024-02-29"


def test_representation_enables_real_lexical_match_for_clipped_field_value():
    from harness.engine import Index, source_documents, validate_documents

    message = _message("prefix " * 200 + "\nEndpoint: ready")
    baseline = source_documents("ordinary-case", (message,))
    labeled = d03.role_labels((message,), baseline)
    assert len(baseline) > 1
    assert all("message role: user" in document.context for document in labeled)
    validate_documents((message,), labeled)

    clipped = _document(message, "ready", message.text.index("ready"))
    enriched = d03.field_keys((message,), (clipped,))
    before, after = Index((message,), (clipped,)), Index((message,), enriched)
    try:
        assert before.lexical("Endpoint") == []
        assert [hit.document.text for hit in after.lexical("Endpoint")] == ["ready"]
    finally:
        before.close()
        after.close()
