from __future__ import annotations

from itertools import combinations

import pytest

from harness.directions.d01_query import _queries, experiments
from harness.engine import Index, evidence_signature, focused, from_batches, source_documents, tokens


class SourceContext:
    messages = ()
    documents = ()
    message_documents = {}
    session_documents = {}
    idf = {"archive": 1.0, "cobalt": 8.0, "migration": 4.0, "notes": 2.0}
    doc_tokens = {}
    tokens = staticmethod(tokens)
    focused = staticmethod(focused)

    def lexical(self, text: str, limit: int = 300) -> list:
        raise AssertionError("Query decomposition must not replace retrieval")


@pytest.fixture
def hooks():
    return {experiment.id: experiment.query_hook for experiment in experiments()}


@pytest.fixture
def make_index():
    indexes = []

    def create(texts):
        messages = from_batches("synthetic-d01", [{
            "request_id": "r", "session_id": "s",
            "messages": [{"role": "user", "content": text} for text in texts],
        }])
        index = Index(messages, source_documents("synthetic-d01", messages))
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
        f"D01.{number:02d}" for number in range(1, 11)
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
     [("Gamma Delta", 0.72)]),
    ("你好 世界", ["世 界 你 好", "你你好世界"], []),
])
def test_alternatives_are_unique_fts_token_bags(query, variants, extra):
    assert _queries(query, SourceContext(), iter(variants)) == baseline_queries(query) + extra


_TARGETS = [
    ("D01.01", "Alpha changed; beta stayed stable", "beta stayed stable"),
    ("D01.02", "Check formats: JSON, YAML, and TOML", "YAML"),
    ("D01.03", 'Find the phrase "bounded context" in notes', "bounded context"),
    ("D01.04", "Where was Orion (the 2019 prototype) tested?", "the 2019 prototype"),
    ("D01.05", "How did New York University use GPT-4?", "New York University"),
    ("D01.06",
     "Compare archived audit notes and describe service changed after database migration yesterday",
     "and describe service changed after database migration yesterday"),
    ("D01.07", "Where did Alice store the launch notes after testing?",
     "store the launch notes after testing"),
    ("D01.08", "Compare 2021 with March 2023", "March 2023"),
    ("D01.09", "SQLite rather than Redis", "Redis"),
    ("D01.10", "archive cobalt migration notes", "cobalt"),
]


@pytest.mark.parametrize(("experiment_id", "query", "expected"), _TARGETS)
def test_each_mechanism_adds_a_distinct_target(hooks, experiment_id, query, expected):
    result = hooks[experiment_id](query, SourceContext())
    base = baseline_queries(query)
    assert result[:len(base)] == base
    assert expected in texts(result[len(base):])
    bags = [frozenset(tokens(text)) for text, _ in result]
    assert len(bags) == len(set(bags))
    assert all(weight == 0.72 for _, weight in result[len(base):])


@pytest.mark.parametrize(("experiment_id", "query", "target"), _TARGETS)
def test_each_experiment_changes_engine_evidence_order(make_index, experiment_id, query, target):
    # Competing complete, isolated, and paired terms expose list-fusion rank changes.
    words = list(dict.fromkeys(tokens(query)))
    corpus = [target, " ".join(words), *words,
              *(" ".join(pair) for pair in combinations(words, 2))]
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


@pytest.mark.parametrize(("experiment_id", "query"), [
    ("D01.01", "Plain words?!"),
    ("D01.02", "plain words?!"),
    ("D01.03", "Where is Atlas?"),
    ("D01.04", "Plain words?!"),
    ("D01.05", "ATLAS?!"),
    ("D01.06", "plain words?!"),
    ("D01.07", "plain words?!"),
    ("D01.08", "plain words?!"),
    ("D01.09", "plain words?!"),
    ("D01.10", "COBALT?!"),
    ("D01.03", '"ALPHA beta!"'),
    ("D01.04", "(Where is Atlas?)"),
    ("D01.06", "What details show service changed after database migration?"),
    ("D01.07", "Where did the launch happen after testing?"),
    ("D01.08", "2023"),
    ("D01.10", "echo echo"),
])
def test_no_new_terms_returns_exact_baseline(hooks, make_index, experiment_id, query):
    index = make_index(["plain words", "Atlas", "cobalt", "alpha beta",
                        "launch testing", "database migration", "2023", "echo"])
    experiment = next(item for item in experiments() if item.id == experiment_id)
    assert hooks[experiment_id](query, index) == baseline_queries(query)
    assert index.apply(query, experiment) == index.apply(query)


def test_named_entity_islands_remain_separate(hooks):
    query = "How did New York University use GPT-4?"
    assert hooks["D01.05"](query, SourceContext()) == baseline_queries(query) + [
        ("New York University", 0.72), ("GPT-4", 0.72),
    ]


def test_all_hooks_are_deterministic_and_keep_original(hooks):
    query = 'Why did Project Atlas choose "cold-store" after March 2023?'
    context = SourceContext()
    for hook in hooks.values():
        first = hook(query, context)
        assert first == hook(query, context)
        assert first[0] == (query, 2.0)
        bags = [frozenset(tokens(text)) for text, _ in first]
        assert len(bags) == len(set(bags))


def test_clause_limitation_splits_conjunction_inside_one_description(hooks):
    query = "product research and development roadmap"
    assert hooks["D01.01"](query, SourceContext()) == baseline_queries(query) + [
        ("product research", 0.72), ("development roadmap", 0.72),
    ]


def test_rare_terms_use_source_idf_and_stable_query_position(hooks):
    context = SourceContext()
    query = "archive cobalt migration notes"
    assert texts(hooks["D01.10"](query, context)[1:]) == [
        "cobalt", "migration", "notes", "archive",
    ]
    context.idf = {}
    assert texts(hooks["D01.10"](query, context)[1:]) == [
        "archive", "cobalt", "migration", "notes",
    ]


@pytest.mark.parametrize("query", ["", "?!", "the the", "a"])
def test_empty_or_stopword_only_queries_are_safe(hooks, make_index, query):
    index = make_index(["alpha beta"])
    for experiment in experiments():
        assert hooks[experiment.id](query, index) == baseline_queries(query)
        assert index.apply(query, experiment) == index.apply(query)
