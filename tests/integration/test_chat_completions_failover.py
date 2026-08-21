"""
Integration tests for failover orchestration in chat completions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from infrgate.exceptions import ProviderTimeoutError
from infrgate.providers.base import ProviderResponse


@pytest.mark.asyncio
async def test_failover_success(client, auth_headers, mock_registry):
    """
    Test that if the primary provider fails with a retryable error,
    it fails over to the next eligible provider and succeeds.
    """
    
    gemini_adapter = mock_registry.get("gemini")
    
    gemini_adapter.complete.side_effect = ProviderTimeoutError("gemini", 10.0)
    
    openai_adapter = mock_registry.get("openai")
    openai_adapter.complete = AsyncMock(
        return_value=ProviderResponse(
            content="This is a test response from OpenAI (failover).",
            model="gpt-4o",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
            provider_latency_ms=100,
        )
    )

    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "auto-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        headers=auth_headers,
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "This is a test response from OpenAI (failover)."

