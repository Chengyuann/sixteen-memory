import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sixteen_memory.app import MAX_BODY_BYTES, create_app
from sixteen_memory.memory import Memory, chunks, tokens
from sixteen_memory.providers import Models, ProviderError
from sixteen_memory.schema import AddRequest, SearchRequest, Settings

KEY = "test-only-not-a-real-secret-12345678"
HEADERS = {"Authorization": "Bearer " + KEY}


def settings(tmp_path, **kwargs):
    return Settings(database=str(tmp_path / "memory.sqlite3"), api_key=KEY, mode="lexical", **kwargs)


def payload(text="Alice lives in Paris.", **kwargs):
    return {
        "request_id": "write-1", "user_id": "scope-1", "session_id": "session-1",
        "messages": [{"role": "user", "content": text, "timestamp": 1704067200000}],
        **kwargs,
    }


def search(client, query="Alice", user_id="scope-1", top_k=100):
    return client.post("/v1/memories/search", json={
        "query": query, "user_id": user_id, "top_k": top_k,
    }, headers=HEADERS)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as result:
        yield result


def test_roundtrip_and_exact_contract(client):
    write = payload()
    response = client.post("/v1/memories/add", json=write, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "success": True, "request_id": "write-1", "user_id": "scope-1", "session_id": "session-1",
    }
    found = search(client).json()["data"]
    assert len(found) == 1
    assert found[0]["content"].endswith("Alice lives in Paris.")
    assert found[0]["created_at"] == "2024-01-01T00:00:00+00:00"
    assert found[0]["id"].startswith("mem_")
    assert search(client, user_id="scope-2").json() == {"data": []}


def test_auth_and_health(client):
    assert client.get("/health").status_code == 200
    for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}):
        assert client.post("/v1/memories/add", json=payload(), headers=header).status_code == 401
        assert client.post("/v1/memories/search", json={
            "query": "secret", "user_id": "scope-1", "top_k": 1,
        }, headers=header).status_code == 401


def test_idempotency_and_conflicting_retry(client):
    for _ in range(3):
        assert client.post("/v1/memories/add", json=payload(), headers=HEADERS).status_code == 200
    assert len(search(client).json()["data"]) == 1
    response = client.post("/v1/memories/add", json=payload("Alice lives in Rome."), headers=HEADERS)
    assert response.status_code == 409
    assert search(client).json()["data"][0]["content"].endswith("Alice lives in Paris.")
    assert client.post("/v1/memories/add", json=payload(
        "Bob lives in Rome.", user_id="scope-2",
    ), headers=HEADERS).status_code == 200
    assert search(client, query="Rome").json() == {"data": []}


def test_persistence_and_config_lock(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        client.post("/v1/memories/add", json=payload(), headers=HEADERS)
        before = search(client).json()
    with TestClient(create_app(config)) as client:
        assert search(client).json() == before
    with pytest.raises(ValueError, match="configuration changed"):
        create_app(replace(config, chunk_chars=1000))


def test_previous_versions_not_overwritten(client):
    client.post("/v1/memories/add", json=payload(), headers=HEADERS)
    newer = payload("Alice moved to Berlin.", request_id="write-2")
    newer["messages"][0]["timestamp"] += 86400000
    client.post("/v1/memories/add", json=newer, headers=HEADERS)
    found = search(client).json()["data"]
    assert len(found) == 2
    assert {item["created_at"] for item in found} == {
        "2024-01-01T00:00:00+00:00", "2024-01-02T00:00:00+00:00",
    }
    assert len(search(client, top_k=1).json()["data"]) == 1


def test_chunks_preserve_all_characters_and_order():
    original = " a b\n" * 2500
    pieces = list(chunks(original, 1200))
    rebuilt = [""] * len(original)
    for start, content in pieces:
        rebuilt[start:start + len(content)] = content
    assert "".join(rebuilt) == original
    assert pieces[-1][0] + len(pieces[-1][1]) == len(original)
    assert len(pieces) > 1
    assert tokens("ABC\u5317\u4eac 2026") == ["abc", "\u5317", "\u4eac", "2026"]


def test_missing_timestamp_not_fabricated(client):
    write = payload(messages=[{"role": "assistant", "content": "Alice prefers green tea."}])
    client.post("/v1/memories/add", json=write, headers=HEADERS)
    item = search(client).json()["data"][0]
    assert "created_at" not in item
    assert '"source_time": null' in item["content"]


def test_invalid_input_never_echoes_memory(client):
    invalid = payload(messages=[{"role": "system", "content": "private-value-123"}])
    response = client.post("/v1/memories/add", json=invalid, headers=HEADERS)
    assert response.status_code == 422
    assert "private-value-123" not in response.text
    for top_k in (0, 101, "2"):
        assert search(client, top_k=top_k).status_code == 422
    assert search(client, query="   ").status_code == 422
    response = client.post("/v1/memories/add", content=b"x" * (MAX_BODY_BYTES + 1), headers=HEADERS)
    assert response.status_code == 413


def test_capacity_is_atomic(tmp_path):
    with TestClient(create_app(settings(tmp_path, max_chunks_per_user=1))) as client:
        response = client.post("/v1/memories/add", json=payload("Alice " * 1000), headers=HEADERS)
        assert response.status_code == 422
        assert search(client).json() == {"data": []}
        assert client.post("/v1/memories/add", json=payload(), headers=HEADERS).status_code == 200


class BrokenModels:
    def extract(self, text):
        return ""

    def embed(self, texts):
        raise ProviderError("sensitive upstream body")

    def close(self):
        pass


def test_failed_model_does_not_ack_or_partially_store(tmp_path):
    app = create_app(settings(tmp_path), BrokenModels())
    with TestClient(app) as client:
        response = client.post("/v1/memories/add", json=payload(), headers=HEADERS)
        assert response.status_code == 503
        assert "sensitive" not in response.text
        assert app.state.memory.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert app.state.memory.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_concurrent_idempotent_add(tmp_path):
    config = settings(tmp_path)
    memory = Memory(config, Models(config))
    try:
        request = AddRequest.model_validate(payload())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: memory.add(request), range(8)))
        assert memory.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
        assert memory.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    finally:
        memory.models.close()
        memory.close()


def test_expired_data_not_searched_and_physically_purged(tmp_path):
    config = settings(tmp_path)
    memory = Memory(config, Models(config))
    try:
        memory.add(AddRequest.model_validate(payload()))
        memory.db.execute("UPDATE memories SET expires_at=?", (time.time() - 1,))
        memory.db.execute("UPDATE requests SET expires_at=?", (time.time() - 1,))
        assert memory.search(SearchRequest(query="Alice", user_id="scope-1", top_k=10)) == []
        memory.purge()
        assert memory.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert memory.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    finally:
        memory.models.close()
        memory.close()


def test_single_punctuation_memory_is_safe(client):
    client.post("/v1/memories/add", json=payload("...!!!"), headers=HEADERS)
    assert search(client).json() == {"data": []}


class FakeVectors:
    """Test-only deterministic vectors; never a competition embedding backend."""
    def extract(self, text):
        return ""

    def embed(self, texts):
        output = []
        for text in texts:
            vector = np.zeros(64, dtype="<f4")
            vector[0 if "Paris" in text or "France" in text else 1] = 1
            output.append(vector.tobytes())
        return output

    def close(self):
        pass


def test_hybrid_path_with_injected_test_vectors(tmp_path):
    config = replace(settings(tmp_path), mode="hybrid", dimensions=64, embedding_api_key="test")
    with TestClient(create_app(config, FakeVectors())) as client:
        client.post("/v1/memories/add", json=payload(), headers=HEADERS)
        client.post("/v1/memories/add", json=payload("Bob lives in Tokyo.", request_id="write-2"),
                    headers=HEADERS)
        found = search(client, query="capital of France").json()["data"]
        assert "Paris" in found[0]["content"]
        assert len(found) == 2
        assert search(client, query="France", user_id="different").json() == {"data": []}


def test_multiple_choice_options_are_used_only_for_retrieval(client):
    client.post("/v1/memories/add", json=payload(), headers=HEADERS)
    response = client.post("/v1/memories/search", headers=HEADERS, json={
        "query": "Which city?", "options": ["Paris", "Rome"],
        "user_id": "scope-1", "top_k": 100,
    })
    assert response.status_code == 200
    assert response.json()["data"][0]["content"].endswith("Alice lives in Paris.")
