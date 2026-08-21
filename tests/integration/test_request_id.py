"""
Integration tests for X-Request-ID middleware.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from infrgate.main import create_app


@pytest_asyncio.fixture
async def app(db_engine, mock_redis, mock_gemini_adapter):
    """Create a test FastAPI app."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    application = create_app()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    os.environ["ADMIN_API_KEY"] = "test-admin-key"
    application.state.db_engine = db_engine
    application.state.db_session_factory = session_factory
    application.state.redis = mock_redis
    application.state.gemini_adapter = mock_gemini_adapter
    application.state.settings = type("Settings", (), {
        "ADMIN_API_KEY": "test-admin-key",
        "GEMINI_API_KEY": "test-key",
        "LOG_LEVEL": "WARNING",
    })()
    return application


@pytest_asyncio.fixture
async def client(app):
    """Create an async HTTP client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


class TestRequestID:
    """Tests for X-Request-ID behavior."""

    @pytest.mark.asyncio
    async def test_auto_generated_request_id(self, client):
        """Gateway generates a UUID when no X-Request-ID is sent."""
        resp = await client.get(
            "/admin/tenants",
            headers={"Authorization": "Bearer test-admin-key"},
        )

        request_id = resp.headers.get("x-request-id")
        assert request_id is not None
        uuid.UUID(request_id)

    @pytest.mark.asyncio
    async def test_client_supplied_request_id(self, client):
        """Client-supplied X-Request-ID is echoed back."""
        custom_id = str(uuid.uuid4())
        resp = await client.get(
            "/admin/tenants",
            headers={
                "Authorization": "Bearer test-admin-key",
                "X-Request-ID": custom_id,
            },
        )

        assert resp.headers.get("x-request-id") == custom_id

    @pytest.mark.asyncio
    async def test_invalid_request_id_regenerated(self, client):
        """Invalid X-Request-ID is replaced with a generated UUID."""
        resp = await client.get(
            "/admin/tenants",
            headers={
                "Authorization": "Bearer test-admin-key",
                "X-Request-ID": "not-a-uuid",
            },
        )

        request_id = resp.headers.get("x-request-id")
        assert request_id is not None
        assert request_id != "not-a-uuid"
        uuid.UUID(request_id)
