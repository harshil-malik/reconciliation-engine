from __future__ import annotations

import base64
import io
import os
from typing import Protocol

from app.local_llm import LlamaCppClient, LlamaCppServerError


class VisionExtractor(Protocol):
    def extract(self, pdf_bytes: bytes, prompt: str) -> str: ...


# Every template (bank statement or ledger) asks for this same row shape, so the
# grammar constraint can be defined once here rather than per template. Amounts stay
# strings because the templates instruct the model to preserve them exactly as
# printed ("1,234.50"); app/ingestion/amounts.py does the numeric parsing.
_ROWS_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "reference": {"type": ["string", "null"]},
                    "debit": {"type": "string"},
                    "credit": {"type": "string"},
                },
                # `reference` is required even though it is nullable: with it
                # optional, grammar-constrained decoding let the model omit the key
                # entirely and every extracted row came back with reference=None,
                # silently losing the cheque/UTR numbers that are Stage 1's
                # strongest matching signal. Requiring it forces an explicit value
                # or an explicit null.
                "required": ["date", "description", "reference", "debit", "credit"],
            },
        }
    },
    "required": ["rows"],
}


class LocalPDFExtractor:
    """Reads a text-based PDF with a locally hosted model via llama.cpp — the default,
    so the pipeline runs fully offline with no API key.

    Unlike the hosted extractors below, this reads the PDF's embedded *text layer*
    (via pypdf) and prompts a text-only model, because a 3B chat model has no vision
    capability. That's a deliberate fit with the v1 scope, which covers text-based
    PDFs and explicitly excludes scanned/image statements — a scanned page yields no
    text layer and raises here rather than silently returning nothing.

    The per-bank templates are unchanged: the same prompt that instructs a vision
    model to read the statement table works on the extracted text, and decoding is
    grammar-constrained to `_ROWS_SCHEMA` so a small model can't drift out of JSON.
    """

    def __init__(self, client: LlamaCppClient | None = None, *, max_tokens: int = 4096):
        self._client = client or LlamaCppClient()
        self._max_tokens = max_tokens

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        text = _pdf_text(pdf_bytes)
        if not text.strip():
            raise LlamaCppServerError(
                "No text layer found in this PDF — it looks scanned/image-based, "
                "which is outside v1 scope. Re-export a text PDF from the bank "
                "portal, or point get_vision_extractor() at a hosted vision model."
            )

        full_prompt = (
            f"{prompt}\n\n"
            "Return the rows under a top-level \"rows\" key.\n\n"
            "--- BEGIN STATEMENT TEXT ---\n"
            f"{text}\n"
            "--- END STATEMENT TEXT ---\n"
        )
        # Returned as a JSON string because the templates' parse_response() expects
        # raw model text — keeping this a drop-in for the hosted extractors.
        return self._client.complete_json(
            full_prompt, json_schema=_ROWS_SCHEMA, max_tokens=self._max_tokens
        )


def _pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class ClaudeVisionExtractor:
    """Reads a text-based PDF via Claude's native document (vision) support.

    Used instead of a traditional table parser (pdfplumber/camelot) because bank
    statement layouts vary bank to bank — per-bank prompts (see bank_templates/) plus
    vision-based reading is more robust across the target banks than layout parsing.
    """

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"):
        import anthropic

        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._model = model

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode(
                                    "ascii"
                                ),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )


class GeminiVisionExtractor:
    """Reads a text-based PDF via Gemini's native document understanding — an
    alternate VisionExtractor implementation to ClaudeVisionExtractor above. Swapping
    providers only means changing which extractor get_vision_extractor() in main.py
    returns; parse_pdf() and the bank/ledger templates don't change either way.
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-3.6-flash"):
        self._api_key = api_key
        self._model = model
        self._client = None

    def _get_client(self):
        # Built lazily, not in __init__: genai.Client() validates the API key
        # eagerly at construction (unlike Anthropic's client), and this extractor
        # gets instantiated as a FastAPI dependency on every request — including
        # plain CSV/Excel uploads that never call extract() at all.
        if self._client is None:
            from google import genai

            self._client = genai.Client(
                api_key=self._api_key or os.environ.get("GEMINI_API_KEY")
            )
        return self._client

    def extract(self, pdf_bytes: bytes, prompt: str) -> str:
        from google.genai import types

        response = self._get_client().models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt,
            ],
        )
        return response.text or ""
