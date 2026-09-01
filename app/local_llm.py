from __future__ import annotations

import os
from typing import Any

import httpx

# `llama-server`'s default bind address. Everything runs against a locally hosted
# model, so no API key is involved anywhere in the pipeline.
DEFAULT_SERVER_URL = "http://127.0.0.1:8080"

# Embeddings run as a second llama-server process on the next port: llama-server
# hosts one model per process, and a dedicated embedding model gives much better
# retrieval vectors than pooling a chat model's hidden states. See README.md.
DEFAULT_EMBEDDING_SERVER_URL = "http://127.0.0.1:8081"

# A 3B model on CPU is markedly slower than a hosted API — a statement-extraction
# call can legitimately run well past a minute, so the default timeout is generous.
DEFAULT_TIMEOUT_SECONDS = 300.0

_START_SERVER_HINT = (
    "Could not reach the llama.cpp server at {url}. Start it with:\n"
    "  llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M --port 8080 -c 8192\n"
    "See README.md for the full offline setup."
)


class LlamaCppServerError(RuntimeError):
    """Raised when the local llama.cpp server is unreachable or returns an error.

    Stage 2 already treats an exception from the embedding/confirmer clients as
    "leave this row for the CA to review" (see app/ai_matching/matcher.py), so a
    server that isn't running degrades the report rather than failing the request.
    """


class LlamaCppClient:
    """Thin client for llama.cpp's `llama-server` OpenAI-compatible HTTP API.

    One server process backs every AI step in the pipeline (PDF extraction, the
    Stage 2 embeddings shortlist, and Stage 2 match confirmation), which is why this
    transport is shared rather than reimplemented per call site.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        env_var: str = "LLAMA_SERVER_URL",
        model: str = "local-model",
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ):
        self._base_url = (
            base_url or os.environ.get(env_var) or DEFAULT_SERVER_URL
        ).rstrip("/")
        # llama-server serves whatever single model it was launched with and ignores
        # this field; it's sent only because the OpenAI-compatible schema expects it.
        self._model = model
        self._timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get("LLAMA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        )
        self._http_client = http_client

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self) -> httpx.Client:
        # Built lazily so constructing this (as a FastAPI dependency, on every
        # request) never opens a connection for a code path that isn't used.
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            response = self._client().post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as exc:
            raise LlamaCppServerError(
                _START_SERVER_HINT.format(url=self._base_url)
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LlamaCppServerError(
                f"llama.cpp server returned {exc.response.status_code} for {path}: "
                f"{exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LlamaCppServerError(
                f"llama.cpp server request to {path} failed: {exc}"
            ) from exc

    def complete_json(
        self,
        prompt: str,
        *,
        json_schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """Prompt the model with grammar-constrained decoding against `json_schema`.

        llama.cpp compiles the schema into a GBNF grammar and restricts sampling to
        tokens that keep the output valid, so the reply parses as JSON by
        construction. This matters far more for a 3B model than for a frontier API
        model — small models are the ones that drift into prose or unclosed braces
        when merely *asked* for JSON.
        """
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            # Deterministic by default: reconciliation output should be reproducible
            # for the same inputs, which an auditor may reasonably expect.
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema},
            },
        }
        data = self._post("/v1/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlamaCppServerError(
                f"Unexpected chat completion response shape: {str(data)[:300]}"
            ) from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via the server's OpenAI-compatible endpoint.

        Requires a server started with `--embeddings` (see README.md — the setup
        runs a small dedicated embedding model on a second port, since a chat
        model's pooled hidden states make poor retrieval vectors).
        """
        if not texts:
            return []

        data = self._post("/v1/embeddings", {"model": self._model, "input": texts})
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise LlamaCppServerError(
                f"Expected {len(texts)} embeddings, got: {str(data)[:300]}"
            )

        # Preserve request order: the spec allows results to come back out of order,
        # and shortlist_candidates zips vectors back against its input list.
        ordered = sorted(rows, key=lambda row: row.get("index", 0))
        return [_as_vector(row.get("embedding")) for row in ordered]


def _as_vector(embedding: Any) -> list[float]:
    """Normalize llama.cpp's embedding payload into a flat float vector.

    Depending on the pooling mode the server was launched with, `embedding` comes
    back either as a flat vector or as a list of per-token vectors; in the latter
    case they're mean-pooled here so callers always get one vector per text.
    """
    if not isinstance(embedding, list) or not embedding:
        raise LlamaCppServerError(f"Malformed embedding in response: {embedding!r}")

    if isinstance(embedding[0], (int, float)):
        return [float(value) for value in embedding]

    if isinstance(embedding[0], list):
        token_vectors = [[float(v) for v in vec] for vec in embedding]
        width = len(token_vectors[0])
        return [
            sum(vec[i] for vec in token_vectors) / len(token_vectors)
            for i in range(width)
        ]

    raise LlamaCppServerError(f"Malformed embedding in response: {embedding!r}")
