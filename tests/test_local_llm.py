from __future__ import annotations

import json

import httpx
import pytest

from app.ai_matching.confirmer import LocalMatchConfirmer
from app.ai_matching.embeddings import LocalEmbeddingClient
from app.local_llm import LlamaCppClient, LlamaCppServerError
from app.schema import Transaction


def _client(handler) -> LlamaCppClient:
    """LlamaCppClient wired to an in-memory transport — exercises the real request
    building and response parsing without a llama.cpp server running."""
    return LlamaCppClient(
        base_url="http://127.0.0.1:8080",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_complete_json_sends_schema_constrained_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _chat_response('{"ok": true}')

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    result = _client(handler).complete_json("hello", json_schema=schema)

    assert result == '{"ok": true}'
    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    # the grammar constraint is what keeps a 3B model's output parseable
    assert captured["body"]["response_format"]["json_schema"]["schema"] == schema
    # deterministic decoding, so the same statement reconciles identically twice
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]


def test_connection_error_raises_actionable_start_server_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LlamaCppServerError, match="llama-server -hf"):
        _client(handler).complete_json("hi", json_schema={"type": "object"})


def test_http_error_status_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model failed to load")

    with pytest.raises(LlamaCppServerError, match="500"):
        _client(handler).complete_json("hi", json_schema={"type": "object"})


def test_embed_returns_vectors_in_request_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # deliberately out of order: the OpenAI schema allows this, and
        # shortlist_candidates zips the result back against its input list
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    assert _client(handler).embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_embed_mean_pools_per_token_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # some pooling modes return one vector per token rather than one per input
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [[1.0, 3.0], [3.0, 5.0]]}]},
        )

    assert _client(handler).embed(["a"]) == [[2.0, 4.0]]


def test_embed_short_circuits_without_calling_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not have called the server")

    assert _client(handler).embed([]) == []


def test_embed_rejects_wrong_number_of_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(LlamaCppServerError, match="Expected 2 embeddings"):
        _client(handler).embed(["a", "b"])


def test_local_embedding_client_delegates_to_llama_client() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["input"] == ["a"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})

    assert LocalEmbeddingClient(_client(handler)).embed(["a"]) == [[0.5]]


def _txn(source: str) -> Transaction:
    from datetime import date
    from decimal import Decimal

    return Transaction(
        date=date(2024, 4, 1),
        amount=Decimal("1000"),
        description=f"{source} row",
        source=source,
        file_name=f"{source}.csv",
        raw_row={},
    )


def test_local_match_confirmer_parses_constrained_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # both rows must reach the model for it to judge the pair
        assert "bank row" in body["messages"][0]["content"]
        assert "ledger row" in body["messages"][0]["content"]
        return _chat_response(
            '{"is_match": true, "confidence": 0.82, "reasoning": "Same vendor and amount."}'
        )

    result = LocalMatchConfirmer(_client(handler)).confirm(_txn("bank"), _txn("ledger"))

    assert result.is_match is True
    assert result.confidence == 0.82
    assert result.reasoning == "Same vendor and amount."
