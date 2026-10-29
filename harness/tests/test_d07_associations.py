from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
import subprocess
import sys

import pytest

from harness.directions import d07_associations as d07
from harness.engine import Index, validate_documents
from harness.types import Document, Hit, Message


@contextmanager
def source_index(texts, *, prefix="", roles=None, requests=None, sessions=None, size=None):
    messages, documents = [], []
    for number, text in enumerate(texts):
        message = Message(
            id=f"m{number}",
            request_id=requests[number] if requests else f"r{number}",
            session_id=sessions[number] if sessions else f"s{number}",
            position=number,
            role=roles[number] if roles else "assistant",
            text=prefix + text,
            timestamp=number,
            ordinal=number,
        )
        messages.append(message)
        width = size or max(1, len(text))
        for offset in range(0, len(text), width):
            content = text[offset:offset + width]
            documents.append(Document(
                id=f"d{number}-{offset}",
                message_id=message.id,
                session_id=message.session_id,
                request_id=message.request_id,
                position=message.position,
                offset=len(prefix) + offset,
                text=content,
                role=message.role,
                timestamp=message.timestamp,
                ordinal=len(documents),
                index_text=content,
            ))
    index = Index(messages, documents)
    try:
        yield index
    finally:
        index.close()


def run(index, number, query, seeds=(0,)):
    hits = [Hit(index.documents[position], 1.0 - rank * 0.1) for rank, position in enumerate(seeds)]
    return index.apply(query, d07.experiments()[number - 1], hits)


def source_ids(hits):
    return {hit.document.message_id for hit in hits}


EXAMPLES = [
    (1, "Cinder", ["Cinder is maintained by Mae Lin.", "Mae Lin works in Cedar Hall.",
                    "Lea Winn works in Alder Hall."], {}, (0,), {"m0", "m1"}),
    (2, "Night Cart", ["Night Cart is also known as Moon Wagon.",
                       "Moon Wagon is also called Silver Trolley.",
                       "Silver Trolley carries ceramic trays."], {}, (0,), {"m0", "m1", "m2"}),
    (3, "signal book", ["signal book means relay register.",
                        "relay register refers to dispatch ledger.",
                        "dispatch ledger is defined as a written shift record.",
                        "an unrelated entry mentions a ledger."], {}, (0,), {"m0", "m1", "m2"}),
    (4, "Cinder", ["Cinder details: see note N-17.", "Note N-17: refer to section B-4.",
                   "Section B-4: the workshop opens at noon."], {}, (0,), {"m0", "m1", "m2"}),
    (5, "cinder", ["cinder.maintainer: mae lin", "mae lin.office: south annex",
                   "south annex.desk: row seven", "other.office: north annex"],
     {}, (0,), {"m0", "m1", "m2"}),
    (6, "Cinder", ["Cinder components:\n- Lumen relay\n- copper switch",
                   "Lumen relay accepts a ceramic housing.", "copper switch requires a rubber cap.",
                   "Other components:\n- brass dial"], {}, (0,), {"m0", "m1", "m2"}),
    (7, "Cinder accessibility", ["Which facility hosts Cinder?", "Cedar Hall.",
                                "Cedar Hall has ramp accessibility.", "Cedar Hall hosts pottery."],
     {"roles": ["user", "assistant", "assistant", "assistant"],
      "requests": ["r-pair", "r-pair", "r-detail", "r-other"],
      "sessions": ["s-pair", "s-pair", "s-detail", "s-other"]}, (0,), {"m0", "m1", "m2"}),
    (8, "Cinder", ["Cinder notes: https://docs.example.invalid/workshop",
                   "Inventory at https://docs.example.invalid/workshop lists three stools.",
                   "Another item: https://docs.example.invalid/workshop-old"],
     {}, (0,), {"m0", "m1"}),
    (9, "Cinder", ['Cinder cites "keep the copper switch enabled".',
                   "Workshop memo: keep the copper switch enabled until morning.",
                   "Workshop memo: keep the copper lever enabled until morning."],
     {}, (0,), {"m0", "m1"}),
    (10, "Cinder Marrow", ["Cinder connects to Bellwick.", "Marrow connects to Frostmere.",
                          "Bellwick and Frostmere share an overnight connection.",
                          "Bellwick has another route.", "Frostmere has a separate route."],
     {}, (0, 1), {"m0", "m1", "m2"}),
]


def test_catalog_has_ten_distinct_single_hook_experiments():
    experiments = d07.experiments()
    assert [experiment.id for experiment in experiments] == [
        f"D07.{number:02d}" for number in range(1, 11)
    ]
    assert len({experiment.retrieval_hook for experiment in experiments}) == 10
    assert len({experiment.name for experiment in experiments}) == 10
    assert all(experiment.direction == "D07" for experiment in experiments)
    for experiment in experiments:
        assert experiment.retrieval_hook
        assert not (experiment.query_hook or experiment.document_hook or experiment.output_hook)
        assert experiment.hypothesis and experiment.mechanism and experiment.limitation


@pytest.mark.parametrize("number,query,texts,options,seeds,expected", EXAMPLES)
def test_every_mechanism_acquires_only_its_linked_source(
    number, query, texts, options, seeds, expected
):
    with source_index(texts, **options) as index:
        original = tuple(index.documents)
        output = run(index, number, query, seeds)
        assert source_ids(output) == expected
        assert expected - {index.documents[seed].message_id for seed in seeds}
        assert tuple(index.documents) == original
        assert all(hit.document is index.by_id[hit.document.id] for hit in output)
        validate_documents(index.messages, [hit.document for hit in output])
        assert output == run(index, number, query, seeds)


@pytest.mark.parametrize("number,query,texts,options,seeds,expected", EXAMPLES)
def test_nonzero_unicode_offsets_survive_every_join(
    number, query, texts, options, seeds, expected
):
    prefix = "\u5907\u6ce8\U0001f9ed\uff1a\n"
    with source_index(texts, prefix=prefix, **options) as index:
        output = run(index, number, query, seeds)
        assert source_ids(output) == expected
        for hit in output:
            document = hit.document
            source = index.by_message[document.message_id]
            assert document.offset == len(prefix)
            assert source.text[document.offset:document.offset + len(document.text)] == document.text
            assert (
                document.session_id, document.request_id, document.position,
                document.role, document.timestamp,
            ) == (
                source.session_id, source.request_id, source.position, source.role, source.timestamp,
            )


@pytest.mark.parametrize(
    "number,query,texts,seeds",
    [
        (1, "cinder", ["cinder uses mae lin.", "mae lin works in a studio."], (0,)),
        (2, "Night Cart", ["Night Cart resembles Moon Wagon.", "Moon Wagon carries trays."], (0,)),
        (3, "relay register", ["signal book means relay register.", "signal book has a shelf."], (0,)),
        (4, "Cinder", ["Cinder: see note missing.", "An unlabeled nearby note."], (0,)),
        (5, "cinder", ["cinder.maintainer: mae lin", "mae linson.office: south annex"], (0,)),
        (6, "Cinder", ["Cinder is mentioned.\nOther components:\n- copper switch",
                       "copper switch requires a cap."], (0,)),
        (7, "Cinder", ["Which facility hosts Cinder?", "Cedar Hall."], (0,)),
        (8, "Cinder", ["Cinder: https://example.invalid/a?key=opaque",
                       "Reference: https://example.invalid/a?key=opaque"], (0,)),
        (9, "Cinder", ['Cinder cites "copper switch".', "The copper switch is enabled."], (0,)),
        (10, "Cinder Marrow", ["Cinder connects to Bellwick.",
                              "Bellwick and Frostmere share a route."], (0,)),
    ],
)
def test_each_mechanism_has_a_nonmatching_counterexample(number, query, texts, seeds):
    with source_index(texts) as index:
        assert source_ids(run(index, number, query, seeds)) == {"m0"}


@pytest.mark.parametrize("number,query,texts,options,seeds,expected", EXAMPLES)
def test_private_sources_are_never_added_or_used_as_bridges(
    number, query, texts, options, seeds, expected
):
    private = [text if position in seeds else "Private: " + text
               for position, text in enumerate(texts)]
    with source_index(private, **options) as index:
        assert source_ids(run(index, number, query, seeds)) == {
            index.documents[seed].message_id for seed in seeds
        }


@pytest.mark.parametrize(
    "sensitive",
    [
        "Password: local-only",
        "api_key=local-only",
        "Email: person@example.invalid",
        "Call +1 (312) 555-0142.",
        "Record 123-45-6789.",
        "Confidential personnel note.",
        "Do not share this text.",
    ],
)
def test_expansion_does_not_carry_sensitive_neighbors(sensitive):
    with source_index([
        "Cinder is maintained by Mae Lin.",
        f"Mae Lin works in Cedar Hall. {sensitive}",
        "Mae Lin writes public workshop notes.",
    ]) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0", "m2"}


def test_privacy_marker_outside_the_indexed_span_blocks_expansion():
    with source_index(
        ["Cinder mentions Mae Lin.", "Mae Lin works in Cedar Hall."], prefix="Private: "
    ) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0"}


def test_private_baseline_hits_are_preserved_but_cannot_expand():
    with source_index(["Private: Cinder mentions Mae Lin.", "Mae Lin works in Cedar Hall."]) as index:
        seed = Hit(index.documents[0], 1.0)
        for experiment in d07.experiments():
            assert index.apply("Cinder", experiment, [seed]) == [seed]


def test_sensitive_text_in_a_different_chunk_blocks_the_whole_source_message():
    texts = [
        "Cinder mentions Mae Lin.",
        "Mae Lin works in Cedar Hall." + " " * 100 + "Password: local-only",
    ]
    with source_index(texts, size=40) as index:
        assert len(index.message_documents["m1"]) > 1
        assert "Password" not in index.message_documents["m1"][0].text
        assert source_ids(run(index, 1, "Cinder")) == {"m0"}


def test_reference_depth_cycles_collisions_and_opaque_ids():
    with source_index([
        "Cinder: see note N-17.", "Note N-17: see note B-4.",
        "Note B-4: see note C-9.", "Note C-9: see note N-17.",
    ]) as index:
        assert source_ids(run(index, 4, "Cinder")) == {"m0", "m1", "m2"}
    with source_index([
        "Cinder: see note N-17.", "Note N-17: first copy.", "Note N-17: conflicting copy.",
    ]) as index:
        assert source_ids(run(index, 4, "Cinder")) == {"m0"}
    with source_index(["Cinder: see note m1.", "No textual anchor exists here."]) as index:
        assert source_ids(run(index, 4, "Cinder")) == {"m0"}
    with source_index(["Cinder: [[Workshop Map]].", "# Workshop Map\nSee note N-17.",
                       "Note N-17: [[Workshop Map]]."]) as index:
        assert source_ids(run(index, 4, "Cinder")) == {"m0", "m1", "m2"}


@pytest.mark.parametrize(
    "number,texts,query",
    [
        (2, ["Cinder is also called Marrow.", "Marrow is also called Cinder.",
             "Marrow is also called Sorrel.", "Sorrel is also called Hazel.",
             "Hazel carries trays."], "Cinder"),
        (3, ["cinder means marrow.", "marrow means sorrel.", "sorrel means hazel.",
             "hazel means cinder."], "cinder"),
        (5, ["cinder.next: marrow", "marrow.next: sorrel", "sorrel.next: hazel",
             "hazel.next: cinder"], "cinder"),
    ],
)
def test_relation_walks_are_bounded_and_cycle_safe(number, texts, query):
    with source_index(texts) as index:
        result = run(index, number, query)
        assert len(result) == len({hit.document.id for hit in result})
        assert result == run(index, number, query)
        assert "m4" not in source_ids(result)
        if number != 2:
            assert "m3" not in source_ids(result)


@pytest.mark.parametrize(
    "number,texts,query",
    [
        (3, [
            "signal book means relay register and duty register.",
            "relay register means shift ledger. duty register means station ledger.",
            "shift ledger means a paper log.",
            "station ledger means a site log.",
        ], "signal book"),
        (5, [
            "cinder.maintainer: mae lin",
            "mae lin.office: south annex\nmae lin.workshop: west hall",
            "south annex.desk: row seven",
            "west hall.bench: row three",
        ], "cinder"),
    ],
)
def test_distinct_relations_in_one_source_can_each_continue(number, texts, query):
    with source_index(texts) as index:
        assert source_ids(run(index, number, query)) == {"m0", "m1", "m2", "m3"}


def test_qa_bridge_requires_same_request_and_adds_no_direct_response_only():
    texts = ["Which facility hosts Cinder?", "Cedar Hall.", "Cedar Hall has ramp accessibility."]
    with source_index(texts, roles=["user", "assistant", "assistant"]) as index:
        assert source_ids(run(index, 7, "Cinder accessibility")) == {"m0"}
    with source_index(
        texts, roles=["user", "assistant", "assistant"],
        requests=["shared", "shared", "detail"], sessions=["one", "two", "three"],
    ) as index:
        assert source_ids(run(index, 7, "Cinder accessibility")) == {"m0"}
    with source_index(
        texts, roles=["user", "assistant", "assistant"],
        requests=["pair", "pair", "detail"], sessions=["one", "one", "two"],
    ) as index:
        assert source_ids(run(index, 7, "Cinder")) == {"m0"}
        assert source_ids(run(index, 7, "Cinder accessibility")) == {"m0", "m1", "m2"}


def test_resource_parser_does_not_merge_paths_fragments_or_credentials():
    with source_index([
        "Cinder: https://DOCS.example.invalid/map#entry",
        "Route: https://docs.example.invalid/map#entry",
        "Route: https://docs.example.invalid/map#other",
        "Route: https://docs.example.invalid/mapping#entry",
        "Route: https://user:pass@docs.example.invalid/map#entry",
    ]) as index:
        assert source_ids(run(index, 8, "Cinder")) == {"m0", "m1"}


def test_quotation_requires_exact_surface_not_only_normalized_tokens():
    with source_index([
        'Cinder cites "keep the copper switch enabled".',
        "KEEP THE COPPER SWITCH ENABLED.",
        "keep the copper switch-enabled",
        "keep the copper switch enabled until noon.",
    ]) as index:
        assert source_ids(run(index, 9, "Cinder")) == {"m0", "m3"}


def test_two_paths_need_distinct_sources_entities_and_query_facets():
    with source_index([
        "Cinder connects to Bellwick.",
        "Cinder also connects to Frostmere.",
        "Bellwick and Frostmere share a route.",
    ]) as index:
        assert source_ids(run(index, 10, "Cinder Marrow", (0, 1))) == {"m0", "m1"}
    with source_index([
        "Cinder connects to Bellwick.", "Marrow connects to Bellwick.", "Bellwick has a route.",
    ]) as index:
        assert source_ids(run(index, 10, "Cinder Marrow", (0, 1))) == {"m0", "m1"}


def test_baseline_index_can_acquire_an_unmatched_linked_document():
    with source_index(["Cinder is maintained by Mae Lin.", "Mae Lin works in Cedar Hall."]) as index:
        baseline = index.baseline("Cinder")
        assert source_ids(baseline) == {"m0"}
        assert source_ids(index.apply("Cinder", d07.experiments()[0])) == {"m0", "m1"}


def test_links_ignore_index_annotations_and_do_not_cross_index_instances():
    with source_index(["Cinder is maintained nearby.", "Mae Lin works in Cedar Hall."]) as index:
        documents = list(index.documents)
        documents[0] = replace(documents[0], index_text="Cinder Mae Lin", context="Mae Lin")
        annotated = Index(index.messages, documents)
        try:
            assert source_ids(run(annotated, 1, "Cinder")) == {"m0"}
        finally:
            annotated.close()
    with source_index(["Cinder is maintained by Mae Lin.", "Mae Lin works in Cedar Hall."]) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0", "m1"}
    with source_index(["Cinder is maintained nearby.", "Mae Lin works in Cedar Hall."]) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0"}


def test_many_mentions_oversized_text_and_duplicate_hits_are_bounded():
    with source_index(
        ["Cinder uses Mae Lin."] + [f"Mae Lin has task {number}." for number in range(20)]
    ) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0"}
    with source_index(["Cinder uses Mae Lin.", "Mae Lin " + "x" * d07._TEXT_LIMIT]) as index:
        assert source_ids(run(index, 1, "Cinder")) == {"m0"}
    with source_index([f"Cinder mentions Copper{number}." for number in range(350)]) as index:
        hits = [Hit(document, 1.0) for document in index.documents] * 2
        for experiment in d07.experiments():
            result = experiment.retrieval_hook("Cinder", hits, index)
            assert len(result) <= d07._OUTPUT_LIMIT
            assert len({hit.document.id for hit in result}) == len(result)
            assert all(hit.document in index.documents for hit in result)


def test_explicit_corpus_seed_and_addition_caps(monkeypatch):
    texts = [
        "Cinder is also called Moon Wagon.",
        "Moon Wagon carries trays.",
        "Moon Wagon carries cups.",
        "Moon Wagon carries bowls.",
        "Cinder is also called Stone Cart.",
        "Stone Cart carries plates.",
    ]
    with source_index(texts) as index:
        monkeypatch.setattr(d07, "_DOCUMENT_LIMIT", 3)
        assert source_ids(run(index, 2, "Cinder")) == {"m0", "m1", "m2"}
        monkeypatch.setattr(d07, "_DOCUMENT_LIMIT", 6000)
        monkeypatch.setattr(d07, "_SEED_LIMIT", 1)
        assert "m5" not in source_ids(run(index, 2, "Cinder", (0, 4)))
        monkeypatch.setattr(d07, "_ADDITION_LIMIT", 2)
        result = run(index, 2, "Cinder")
        assert len(source_ids(result) - {"m0"}) <= 2


def test_empty_unrelated_and_nonpositive_seeds_do_not_start_a_walk():
    with source_index(["Cinder uses Mae Lin.", "Mae Lin works in Cedar Hall."]) as index:
        for experiment in d07.experiments():
            assert index.apply("Cinder", experiment, []) == []
            assert source_ids(index.apply("unmatched", experiment, [Hit(index.documents[0], 1)])) == {
                "m0"
            }
            assert source_ids(index.apply("the and", experiment, [Hit(index.documents[0], 1)])) == {
                "m0"
            }
            assert index.apply("Cinder", experiment, [Hit(index.documents[0], 0)]) == [
                Hit(index.documents[0], 0)
            ]


def test_determinism_across_python_hash_seeds():
    script = """
import json
from harness.tests.test_d07_associations import EXAMPLES, run, source_index
output = []
for number, query, texts, options, seeds, _ in EXAMPLES:
    with source_index(texts, **options) as index:
        output.append([(h.document.id, h.score) for h in run(index, number, query, seeds)])
print(json.dumps(output))
"""
    results = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": value, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
        )
        for value in ("7", "83")
    ]
    assert json.loads(results[0]) == json.loads(results[1])
