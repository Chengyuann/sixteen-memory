import json

import httpx
import numpy as np
import pytest

from sixteen_memory.providers import Models, ProviderError
from sixteen_memory.schema import Settings


def config(**kwargs):
    return Settings(
        database="unused", api_key="test-only-not-a-real-secret-12345678",
        embedding_api_key="fake-provider-key", dimensions=64, **kwargs,
    )


def test_provider_batches_and_restores_index_order():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path.endswith("/embeddings")
        assert body["model"] == "text-embedding-v4"
        assert request.headers["authorization"] == "Bearer fake-provider-key"
        return httpx.Response(200, json={
            "data": [{"index": index, "embedding": [1.0] * 64}
                     for index in reversed(range(len(body["input"])))],
        })

    model = Models(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        result = model.embed(["source"] * 23)
        assert [len(call["input"]) for call in calls] == [10, 10, 3]
        assert len(result) == 23
        assert np.linalg.norm(np.frombuffer(result[0], dtype="<f4")) == pytest.approx(1)
    finally:
        model.close()


@pytest.mark.parametrize("data", [
    [], [{"index": 0, "embedding": [1]}], [{"index": 2, "embedding": [1] * 64}],
    [{"index": 0, "embedding": [0] * 64}],
])
def test_invalid_vectors_fail_closed(data):
    model = Models(config(), httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": data}),
    )))
    try:
        with pytest.raises(ProviderError):
            model.embed(["source"])
    finally:
        model.close()


def test_fact_quotes_must_exist_in_source():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({"facts": [{
                "fact": "invented fact", "quote": "not present",
            }]})},
        }]})

    model = Models(config(enable_facts=True, llm_api_key="fake"),
                   httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(ProviderError, match="source-linked"):
            model.extract("Alice lives in Paris.")
    finally:
        model.close()


def test_good_fact_is_indexed_without_becoming_an_answer():
    content = json.dumps({"facts": [{"fact": "Alice residence: Paris", "quote": "lives in Paris"}]})
    model = Models(config(enable_facts=True, llm_api_key="fake"),
                   httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
                       200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
                   ))))
    try:
        assert model.extract("Alice lives in Paris.") == "Alice residence: Paris"
    finally:
        model.close()


def test_config_requires_real_configuration_not_silent_fallback():
    with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
        Settings(database="unused", api_key="x" * 32)
    with pytest.raises(ValueError, match="mandated"):
        config(embedding_model="some-other-model")
    with pytest.raises(ValueError, match="HTTPS"):
        config(embedding_base_url="http://example.invalid")
