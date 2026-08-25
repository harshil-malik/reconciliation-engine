from __future__ import annotations

import pytest

from app.ai_matching.confirmer import ConfirmationResult, _parse_confirmation


def test_parses_plain_json_object() -> None:
    result = _parse_confirmation(
        '{"is_match": true, "confidence": 0.92, "reasoning": "same UTR"}'
    )
    assert result == ConfirmationResult(is_match=True, confidence=0.92, reasoning="same UTR")


def test_parses_json_wrapped_in_markdown_fences() -> None:
    text = '```json\n{"is_match": false, "confidence": 0.1, "reasoning": "unrelated"}\n```'
    result = _parse_confirmation(text)
    assert result.is_match is False
    assert result.confidence == 0.1


def test_raises_when_no_json_object_found() -> None:
    with pytest.raises(ValueError, match="No JSON object found"):
        _parse_confirmation("I cannot determine this from the given rows.")
