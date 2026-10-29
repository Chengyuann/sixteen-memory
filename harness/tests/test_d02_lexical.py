from __future__ import annotations

from itertools import combinations
from types import SimpleNamespace

import pytest

from harness.directions.d02_lexical import _queries, experiments
from harness.engine import Index, evidence_signature, focused, from_batches, source_documents, tokens


class SourceContext:
    messages = ()
    documents = ()
    message_documents = {}
    session_documents = {}
    idf = {}
    doc_tokens = {}
    tokens = staticmethod(tokens)
    focused = staticmethod(focused)

    def lexical(self, text: str, limit: int = 300) -> list:
        raise AssertionError("Lexical aliases must not replace retrieval")


@pytest.fixture
def hooks():
    return {experiment.id: experiment.query_hook for experiment in experiments()}


@pytest.fixture
def make_index():
    indexes = []

    def create(texts):
        messages = from_batches("synthetic-d02", [{
            "request_id": "r", "session_id": "s",
            "messages": [{"role": "user", "content": text} for text in texts],
        }])
        index = Index(messages, source_documents("synthetic-d02", messages))
        indexes.append(index)
        return index

    yield create
    for index in indexes:
        index.close()


def baseline_queries(query):
    result = [(query, 2.0)]
    focus = focused(query)
    if focus and focus != " ".join(tokens(query)):
        result.append((focus, 2.0))
    return result


def texts(result):
    return [text for text, _ in result]


def test_catalog_has_ten_distinct_single_hook_experiments():
    catalog = experiments()
    assert [experiment.id for experiment in catalog] == [
        f"D02.{number:02d}" for number in range(1, 11)
    ]
    assert len({experiment.name for experiment in catalog}) == 10
    assert len({experiment.mechanism for experiment in catalog}) == 10
    assert len({experiment.query_hook for experiment in catalog}) == 10
    for experiment in catalog:
        assert experiment.query_hook is not None
        assert experiment.document_hook is None
        assert experiment.retrieval_hook is None
        assert experiment.output_hook is None
        assert experiment.hypothesis
        assert experiment.limitation
    assert "cardinal" in catalog[2].name.lower()
    assert "source-defined abbreviation" in catalog[8].name.lower()


@pytest.mark.parametrize("query", [
    "Atlas", "COBALT?!", "plain\t words.", "Where is Atlas?",
    "echo echo", "the echo echo", "the", "", "?!", "你好，世界",
])
def test_helper_matches_engine_baseline_exactly(make_index, monkeypatch, query):
    index = make_index(["Atlas", "cobalt", "plain words", "echo", "the", "你好世界"])
    observed = []
    lexical = index.lexical

    def capture(text, limit=300):
        observed.append((text, limit))
        return lexical(text, limit)

    monkeypatch.setattr(index, "lexical", capture)
    before = index.baseline(query)
    result = _queries(query, index, ())
    assert result == baseline_queries(query)
    assert [text for text, _ in observed] == [text for text, _ in result if text]
    assert index.baseline(query, result) == before


@pytest.mark.parametrize(("query", "variants", "extra"), [
    ("Alpha, BETA!", ["alpha beta", "BETA alpha", "alpha alpha beta", "!!!"], []),
    ("Find Alpha and beta?", ["find alpha and BETA", "BETA alpha", "alpha beta beta"], []),
    ("alpha beta", ["Gamma Delta", "delta gamma", "GAMMA DELTA!", "gamma gamma delta"],
     [("Gamma Delta", 0.76)]),
    ("你好 世界", ["世 界 你 好", "你你好世界"], []),
])
def test_alternatives_are_unique_fts_token_bags(query, variants, extra):
    assert _queries(query, SourceContext(), iter(variants)) == baseline_queries(query) + extra


_TARGETS = [
    ("D02.01", "Ｆｕｌｌｗｉｄｔｈ ①", "Fullwidth 1"),
    ("D02.02", "Straße API", "strasse api"),
    ("D02.03", "fourteen records", "14 records"),
    ("D02.04", "memory‑store", "memorystore"),
    ("D02.05", "Ada’s notebook", "Ada notebook"),
    ("D02.06", "U.S.A. and N L P", "USA and NLP"),
    ("D02.07", "HTTPServer_v2/api", "HTTP Server v 2 api"),
    ("D02.08", "1,024 and 2_000 records", "1024 and 2000 records"),
    ("D02.09", "NASA budget", "National Aeronautics and Space Administration budget"),
    ("D02.10", "R&D grew 5%", "R and D grew 5 percent"),
]
_DEFINITION = "The National Aeronautics and Space Administration (NASA) launched Artemis."


@pytest.mark.parametrize(("experiment_id", "query", "expected"), _TARGETS)
def test_each_mechanism_adds_a_distinct_target(hooks, make_index, experiment_id, query, expected):
    index = make_index([_DEFINITION])
    result = hooks[experiment_id](query, index)
    base = baseline_queries(query)
    assert result[:len(base)] == base
    assert expected in texts(result[len(base):])
    bags = [frozenset(tokens(text)) for text, _ in result]
    assert len(bags) == len(set(bags))
    assert all(weight == 0.76 for _, weight in result[len(base):])


@pytest.mark.parametrize(("experiment_id", "query", "target"), _TARGETS)
def test_each_experiment_changes_engine_evidence_order(make_index, experiment_id, query, target):
    words = list(dict.fromkeys(tokens(query)))
    corpus = [target, " ".join(words), *words,
              *(" ".join(pair) for pair in combinations(words, 2)), _DEFINITION]
    index = make_index(corpus)
    experiment = next(item for item in experiments() if item.id == experiment_id)
    documents, messages = index.documents, index.messages
    before = index.apply(query)
    after = index.apply(query, experiment)
    assert before and after
    assert evidence_signature(after) != evidence_signature(before)
    assert after == index.apply(query, experiment)
    assert before == index.apply(query)
    assert index.documents == documents
    assert index.messages == messages
    assert all(hit.document == index.by_id[hit.document.id] for hit in after)
    for hit in after:
        doc = hit.document
        source = index.by_message[doc.message_id].text
        assert source[doc.offset:doc.offset + len(doc.text)] == doc.text


@pytest.mark.parametrize("query", ["Plain token?!", "Where is Atlas?", "", "?!", "the the", "你好"])
def test_no_applicable_form_returns_exact_baseline(hooks, make_index, query):
    index = make_index(["plain token", "Atlas", "the", "你好"])
    for experiment in experiments():
        assert hooks[experiment.id](query, index) == baseline_queries(query)
        assert index.apply(query, experiment) == index.apply(query)


def test_hyphen_mechanism_adds_only_the_distinct_joined_form(hooks):
    query = "memory‑store"
    assert hooks["D02.04"](query, SourceContext()) == [(query, 2.0), ("memorystore", 0.76)]


@pytest.mark.parametrize(("experiment_id", "query"), [
    ("D02.01", "Plain text"),
    ("D02.02", "PLAIN TEXT"),
    ("D02.07", "api/server_name"),
    ("D02.10", "red&blue and"),
])
def test_token_equivalent_normalizations_do_not_reweight(hooks, make_index, experiment_id, query):
    index = make_index(["plain text", "api server name", "red blue and"])
    experiment = next(item for item in experiments() if item.id == experiment_id)
    assert hooks[experiment_id](query, index) == baseline_queries(query)
    assert index.apply(query, experiment) == index.apply(query)


def test_straightening_apostrophe_does_not_add_an_extra_list(hooks):
    query = "Ada’s"
    assert hooks["D02.05"](query, SourceContext()) == [(query, 2.0), ("Ada", 0.76)]


@pytest.mark.parametrize(("query", "alias"), [
    ("zero records", "0 records"), ("0 records", "zero records"),
    ("TWENTY-FOUR records", "24 records"), ("24 records", "twenty four records"),
    ("ninety nine records", "99 records"), ("99 records", "ninety nine records"),
    ("fourteen records.", "14 records."), ("14 records.", "fourteen records."),
])
def test_cardinal_aliases_are_bidirectional(hooks, query, alias):
    assert hooks["D02.03"](query, SourceContext()) == baseline_queries(query) + [(alias, 0.76)]


@pytest.mark.parametrize("number", [
    "one hundred", "one hundred and five", "one thousand twenty four", "100", "2026",
    "1,024", "2_000", "3.14", "14th", "014", "-14", "+14", "v14", "X-14",
    "14-20", "14/20", "14:20", "fourteenfold",
])
def test_cardinals_do_not_partially_rewrite_other_numeric_forms(hooks, number):
    query = f"{number} records"
    assert hooks["D02.03"](query, SourceContext()) == baseline_queries(query)


@pytest.mark.parametrize(("query", "source"), [
    ("fourteen", "14"), ("14", "fourteen"), ("twenty-four", "24"),
])
def test_cardinal_alias_recovers_previously_absent_evidence(make_index, query, source):
    index = make_index([source, "unrelated"])
    experiment = experiments()[2]
    assert index.apply(query) == []
    assert [hit.document.text for hit in index.apply(query, experiment)] == [source]


@pytest.mark.parametrize("definition", [
    "Natural Language Processing (NLP) is used here.",
    "The team uses Natural Language Processing (NLP).",
    "NLP (Natural Language Processing) is used here.",
])
def test_abbreviation_uses_explicit_source_definition(hooks, make_index, definition):
    index = make_index([definition])
    assert hooks["D02.09"]("NLP report", index) == [
        ("NLP report", 2.0), ("Natural Language Processing report", 0.76),
    ]
    assert hooks["D02.09"]("nlp report", index)[-1] == (
        "Natural Language Processing report", 0.76,
    )


def test_abbreviation_recovers_full_name_evidence_without_abbreviation(make_index):
    definition = "Natural Language Processing (NLP) is the full name."
    evidence = "Natural Language Processing uses token sequences."
    index = make_index([definition, evidence, "unrelated"])
    assert [hit.document.text for hit in index.apply("NLP")] == [definition]
    after = index.apply("NLP", experiments()[8])
    assert evidence in [hit.document.text for hit in after]


def test_abbreviation_has_no_builtin_dictionary_or_query_defined_expansion(hooks, make_index):
    index = make_index(["NLP report", "Natural Language Processing uses tokens."])
    for query in ("NLP", "Natural Language Processing (NLP)"):
        assert hooks["D02.09"](query, index) == baseline_queries(query)


def test_abbreviation_skips_conflicting_source_definitions(hooks, make_index):
    sources = [
        "Natural Language Processing (NLP).",
        "NLP (Nonlinear Programming).",
        "NLP (National Logistics Program).",
    ]
    index = make_index(sources)
    assert hooks["D02.09"]("NLP report", index) == [("NLP report", 2.0)]
    reverse = make_index(list(reversed(sources)))
    assert hooks["D02.09"]("NLP report", reverse) == [("NLP report", 2.0)]


def test_repeated_definitions_do_not_add_weight_or_depend_on_source_order(hooks, make_index):
    definitions = [
        "Natural Language Processing (NLP).",
        "NATURAL LANGUAGE PROCESSING (NLP).",
        "Natural Language Processing (NLP).",
    ]
    first = hooks["D02.09"]("NLP", make_index(definitions))
    second = hooks["D02.09"]("NLP", make_index(list(reversed(definitions))))
    assert first == second
    assert len(first) == 2
    assert first[-1][1] == 0.76


@pytest.mark.parametrize("definition", [
    "Natural Language Processing (XYZ).",
    "A passing mention of NLP with no definition.",
    "Natural Language Processing (nlp).",
    "NLP means Natural Language Processing.",
    "NLP (Not Natural Language Processing).",
])
def test_abbreviation_limits_do_not_infer_undeclared_aliases(hooks, make_index, definition):
    index = make_index([definition])
    assert hooks["D02.09"]("NLP XYZ", index) == [("NLP XYZ", 2.0)]


def test_abbreviation_does_not_match_inside_an_identifier(hooks, make_index):
    index = make_index(["Natural Language Processing (NLP)."])
    query = "NLP_v2 report"
    assert hooks["D02.09"](query, index) == baseline_queries(query)


def test_abbreviation_reads_original_messages_only(hooks):
    context = SourceContext()
    context.documents = (SimpleNamespace(
        index_text="Natural Language Processing (NLP).",
        text="Natural Language Processing (NLP).",
        context="Natural Language Processing (NLP).",
    ),)
    assert hooks["D02.09"]("NLP", context) == [("NLP", 2.0)]
    context.messages = (SimpleNamespace(text="Natural Language Processing (NLP)."),)
    assert hooks["D02.09"]("NLP", context) == [
        ("NLP", 2.0), ("Natural Language Processing", 0.76),
    ]


def test_abbreviations_expand_together_in_one_list_without_chaining(hooks, make_index):
    index = make_index([
        "Natural Language Processing (NLP).",
        "Application Programming Interface (API).",
    ])
    assert hooks["D02.09"]("NLP API", index) == [
        ("NLP API", 2.0),
        ("Natural Language Processing Application Programming Interface", 0.76),
    ]


def test_all_hooks_are_deterministic_and_keep_original(hooks):
    query = "Málaga’s HTTPServer_v2 costs 1,024€"
    context = SourceContext()
    for hook in hooks.values():
        first = hook(query, context)
        assert first == hook(query, context)
        assert first[0] == (query, 2.0)
        bags = [frozenset(tokens(text)) for text, _ in first]
        assert len(bags) == len(set(bags))


def test_numeric_limitation_treats_decimal_comma_as_grouping(hooks):
    query = "value 1,234"
    assert hooks["D02.08"](query, SourceContext()) == [(query, 2.0), ("value 1234", 0.76)]
