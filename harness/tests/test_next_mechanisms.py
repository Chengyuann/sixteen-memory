import pytest
from harness.engine import Index, from_batches, source_documents, evidence_signature
from harness.next_mechanisms import entities, load, rerank


def fixture():
    m = from_batches(
        "synthetic",
        [
            {
                "request_id": "r",
                "session_id": "s",
                "messages": [
                    {"role": "user", "content": "What Atlas project venue?", "timestamp": 100},
                    {"role": "assistant", "content": "Atlas venue in Dublin.", "timestamp": 200},
                    {
                        "role": "user",
                        "content": "Atlas private password not for sharing.",
                        "timestamp": 300,
                    },
                ],
            },
            {
                "request_id": "q",
                "session_id": "t",
                "messages": [
                    {"role": "user", "content": "Atlas project Paris venue.", "timestamp": 400}
                ],
            },
        ],
    )
    return Index(m, source_documents("synthetic", m))


def test_pronouns_are_not_entities():
    assert entities("Which grocery store did I spend the most money at?") == []
    assert entities("Where is Alice in Project Atlas?") == ["Alice", "Project Atlas"]


@pytest.mark.parametrize("mode", ["session", "parent", "message", "entity", "current"])
def test_reranking_preserves_full_source_set(mode):
    idx = fixture()
    try:
        b = idx.apply("Atlas project venue current")
        r = rerank("Atlas project venue current", b, mode)
        assert set(evidence_signature(b)) == set(evidence_signature(r))
        assert evidence_signature(r) == evidence_signature(
            rerank("Atlas project venue current", b, mode)
        )
    finally:
        idx.close()


@pytest.mark.parametrize("e", load(), ids=lambda e: e.id)
def test_all_hooks_valid_deterministic(e):
    idx = fixture()
    try:
        a = idx.apply("Where is Atlas and Dublin venue currently?", e)
        b = idx.apply("Where is Atlas and Dublin venue currently?", e)
        assert evidence_signature(a) == evidence_signature(b)
    finally:
        idx.close()
