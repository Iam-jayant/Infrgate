"""
Integration tests for the chat completions endpoint.

Tests the full request lifecycle with a mock Gemini adapter.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from infrgate.main import create_app
from infrgate.providers.base import ProviderResponse


class TestChatCompletions:
    """Integration tests for POST /v1/chat/completions."""

    @pytest.mark.asyncio
    async def test_successful_completion(self, client, setup_tenant):
        """Full E2E: authenticated request returns OpenAI-compatible response."""
        tenant, api_key = setup_tenant

        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.0-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

        assert resp.status_code == 200
        data = resp.json()

        assert data["object"] == "chat.completion"
        assert data["id"].startswith("chatcmpl-")
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] == 10
        assert data["usage"]["completion_tokens"] == 8

    @pytest.mark.asyncio
    async def test_request_id_in_response(self, client, setup_tenant):
        """X-Request-ID is present in the response."""
        _, api_key = setup_tenant

        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.0-flash",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

        assert "x-request-id" in resp.headers

    @pytest.mark.asyncio
    async def test_unauthenticated_request(self, client):
        """Request without auth returns 401."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.0-flash",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code in (400, 401)  # 400 if header is missing

    @pytest.mark.asyncio
    async def test_streaming_not_supported(self, client, setup_tenant):
        """stream=true returns 400 in Phase 1."""
        _, api_key = setup_tenant

        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.0-flash",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

        assert resp.status_code == 400
        assert "streaming" in resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_invalid_request_body(self, client, setup_tenant):
        """Missing required fields returns 400."""
        _, api_key = setup_tenant

        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gemini-2.0-flash"},  # missing messages
            headers={"Authorization": f"Bearer {api_key}"},
        )

        assert resp.status_code in (400, 422)
