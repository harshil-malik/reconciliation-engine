from __future__ import annotations

import os
from typing import Protocol

from app.local_llm import DEFAULT_EMBEDDING_SERVER_URL, LlamaCppClient


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddingClient:
    """Embeds via a llama.cpp server started with `--embeddings` — the default, so the
    Stage 2 shortlist runs offline with no API key.

    Defaults to port 8081 rather than the chat server's 8080: llama-server hosts one
    model per process, and embeddings need a dedicated embedding model (a chat
    model's pooled hidden states make poor retrieval vectors). Override with
    LLAMA_EMBEDDING_SERVER_URL. See README.md for the two-server setup.
    """

    def __init__(self, client: LlamaCppClient | None = None):
        self._client = client or LlamaCppClient(
            base_url=os.environ.get(
                "LLAMA_EMBEDDING_SERVER_URL", DEFAULT_EMBEDDING_SERVER_URL
            )
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed(texts)


class VoyageEmbeddingClient:
    """Voyage AI is Anthropic's recommended embeddings partner — Claude has no
    first-party embeddings endpoint, so this is the natural pairing for the
    Claude-based confirmation step in confirmer.py."""

    def __init__(self, api_key: str | None = None, model: str = "voyage-3.5"):
        import voyageai

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings


class GeminiEmbeddingClient:
    """Gemini's own embeddings endpoint — used when running the whole pipeline off a
    single Gemini API key instead of pairing Claude with Voyage."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-embedding-001"):
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        # Lazy for the same reason as GeminiVisionExtractor._get_client(): this
        # client is instantiated as a FastAPI dependency on every /reconcile call,
        # even when Stage 1 leaves nothing unmatched and embed() is never called.
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                api_key=self._api_key or os.environ.get("GEMINI_API_KEY")
            )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._get_client().models.embed_content(model=self._model, contents=texts)
        return [embedding.values for embedding in result.embeddings]
