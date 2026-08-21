"""
Unit tests for the Gemini adapter — request/response translation and error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from infrgate.exceptions import ProviderAuthError, ProviderError, ProviderTimeoutError
from infrgate.providers.gemini import GeminiAdapter
from infrgate.providers.base import ProviderRequest


@pytest.fixture
def mock_http_client():
    """Mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def adapter(mock_http_client) -> GeminiAdapter:
    """Create a GeminiAdapter with a mock HTTP client."""
    return GeminiAdapter(api_key="test-key", http_client=mock_http_client)


def _make_gemini_response(
    text: str = "Hello!",
    finish_reason: str = "STOP",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict:
    """Build a mock Gemini API response."""
    return {
        "candidates": [{
            "content": {
                "parts": [{"text": text}],
                "role": "model",
            },
            "finishReason": finish_reason,
        }],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
            "totalTokenCount": prompt_tokens + completion_tokens,
        },
    }


class TestRequestTranslation:
    """Tests for OpenAI → Gemini request translation."""

    def test_system_message_extraction(self, adapter):
        """System messages are moved to system_instruction."""
        request = ProviderRequest(
            model="gemini-2.0-flash",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
        )
        body = adapter._translate_request(request)

        assert "system_instruction" in body
        assert body["system_instruction"]["parts"][0]["text"] == "You are helpful."
        assert len(body["contents"]) == 1
        assert body["contents"][0]["role"] == "user"

    def test_assistant_role_mapping(self, adapter):
        """Assistant role maps to 'model' in Gemini."""
        request = ProviderRequest(
            model="gemini-2.0-flash",
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
        )
        body = adapter._translate_request(request)

        assert body["contents"][0]["role"] == "user"
        assert body["contents"][1]["role"] == "model"
        assert body["contents"][2]["role"] == "user"

    def test_generation_config(self, adapter):
        """Temperature, max_tokens, and top_p are mapped correctly."""
        request = ProviderRequest(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.5,
            max_tokens=100,
            top_p=0.9,
        )
        body = adapter._translate_request(request)

        config = body["generationConfig"]
        assert config["temperature"] == 0.5
        assert config["maxOutputTokens"] == 100
        assert config["topP"] == 0.9

    def test_stop_sequences(self, adapter):
        """Stop sequences are converted to array format."""
        request = ProviderRequest(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "Hi"}],
            stop="END",
        )
        body = adapter._translate_request(request)
        assert body["generationConfig"]["stopSequences"] == ["END"]


class TestResponseTranslation:
    """Tests for Gemini → OpenAI response translation."""

    def test_basic_response(self, adapter):
        """Basic response is translated correctly."""
        data = _make_gemini_response(text="Paris is the capital.", prompt_tokens=10, completion_tokens=6)
        response = adapter._translate_response(data, "gemini-2.0-flash", 200)

        assert response.content == "Paris is the capital."
        assert response.model == "gemini-2.0-flash"
        assert response.finish_reason == "stop"
        assert response.prompt_tokens == 10
        assert response.completion_tokens == 6
        assert response.total_tokens == 16
        assert response.provider_latency_ms == 200

    def test_finish_reason_mapping(self, adapter):
        """Gemini finish reasons are mapped to OpenAI equivalents."""
        for gemini_reason, expected in [
            ("STOP", "stop"),
            ("MAX_TOKENS", "length"),
            ("SAFETY", "content_filter"),
            ("RECITATION", "content_filter"),
        ]:
            data = _make_gemini_response(finish_reason=gemini_reason)
            response = adapter._translate_response(data, "gemini-2.0-flash", 100)
            assert response.finish_reason == expected

    def test_empty_candidates_raises(self, adapter):
        """Empty candidates list raises ProviderError."""
        with pytest.raises(ProviderError, match="no candidates"):
            adapter._translate_response({"candidates": []}, "gemini-2.0-flash", 100)


class TestErrorHandling:
    """Tests for Gemini error classification."""

    @pytest.mark.asyncio
    async def test_timeout_raises(self, adapter, mock_http_client):
        """Timeout raises ProviderTimeoutError."""
        mock_http_client.post.side_effect = httpx.TimeoutException("timeout")

        with pytest.raises(ProviderTimeoutError):
            await adapter.complete(ProviderRequest(
                model="gemini-2.0-flash",
                messages=[{"role": "user", "content": "Hi"}],
            ))

    @pytest.mark.asyncio
    async def test_auth_error(self, adapter, mock_http_client):
        """401/403 raises ProviderAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": {"message": "Invalid API key"}}
        mock_http_client.post.return_value = mock_response

        with pytest.raises(ProviderAuthError):
            await adapter.complete(ProviderRequest(
                model="gemini-2.0-flash",
                messages=[{"role": "user", "content": "Hi"}],
            ))

    @pytest.mark.asyncio
    async def test_server_error_is_retryable(self, adapter, mock_http_client):
        """500 errors are marked as retryable."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": {"message": "Server error"}}
        mock_response.text = "Server error"
        mock_http_client.post.return_value = mock_response

        with pytest.raises(ProviderError) as exc_info:
            await adapter.complete(ProviderRequest(
                model="gemini-2.0-flash",
                messages=[{"role": "user", "content": "Hi"}],
            ))
        assert exc_info.value.retryable is True
