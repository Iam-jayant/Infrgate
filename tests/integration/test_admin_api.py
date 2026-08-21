"""
Integration tests for admin API — tenant CRUD, API key lifecycle, and usage queries.
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
    """Create a test FastAPI app with mocked dependencies."""
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


@pytest.fixture
def admin_headers():
    """Admin auth headers."""
    return {"Authorization": "Bearer test-admin-key"}


class TestTenantCRUD:
    """Tests for tenant management endpoints."""

    @pytest.mark.asyncio
    async def test_create_tenant(self, client, admin_headers):
        """Create a new tenant returns 201."""
        resp = await client.post(
            "/admin/tenants",
            json={"name": "Acme Corp", "plan": "standard"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Acme Corp"
        assert data["plan"] == "standard"
        assert data["status"] == "active"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_list_tenants(self, client, admin_headers):
        """List tenants returns paginated response."""
        await client.post(
            "/admin/tenants",
            json={"name": "Tenant 1", "plan": "free"},
            headers=admin_headers,
        )

        resp = await client.get("/admin/tenants", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_get_tenant(self, client, admin_headers):
        """Get a specific tenant by ID."""
        create_resp = await client.post(
            "/admin/tenants",
            json={"name": "Get Test", "plan": "standard"},
            headers=admin_headers,
        )
        tenant_id = create_resp.json()["id"]

        resp = await client.get(f"/admin/tenants/{tenant_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_tenant(self, client, admin_headers):
        """Get a non-existent tenant returns 404."""
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/admin/tenants/{fake_id}", headers=admin_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_tenant(self, client, admin_headers):
        """Partially update a tenant."""
        create_resp = await client.post(
            "/admin/tenants",
            json={"name": "Update Test", "plan": "free"},
            headers=admin_headers,
        )
        tenant_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/admin/tenants/{tenant_id}",
            json={"status": "suspended"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_unauthorized_admin(self, client):
        """Admin endpoints require admin auth."""
        resp = await client.get(
            "/admin/tenants",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


class TestApiKeyLifecycle:
    """Tests for API key management endpoints."""

    @pytest.mark.asyncio
    async def test_create_and_list_keys(self, client, admin_headers):
        """Create an API key, verify full key returned, then list shows redacted."""
        tenant_resp = await client.post(
            "/admin/tenants",
            json={"name": "Key Test", "plan": "standard"},
            headers=admin_headers,
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await client.post(
            f"/admin/tenants/{tenant_id}/api-keys",
            json={"name": "prod-key"},
            headers=admin_headers,
        )
        assert key_resp.status_code == 201
        key_data = key_resp.json()
        assert "key" in key_data  # Full key returned on creation
        assert key_data["key"].startswith("sk-infr_")
        assert "." in key_data["key"]

        list_resp = await client.get(
            f"/admin/tenants/{tenant_id}/api-keys",
            headers=admin_headers,
        )
        assert list_resp.status_code == 200
        keys = list_resp.json()
        assert len(keys) >= 1
        assert "key" not in keys[0]  # Secret redacted

    @pytest.mark.asyncio
    async def test_revoke_key(self, client, admin_headers):
        """Revoke an API key sets revoked_at."""
        tenant_resp = await client.post(
            "/admin/tenants",
            json={"name": "Revoke Test", "plan": "standard"},
            headers=admin_headers,
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await client.post(
            f"/admin/tenants/{tenant_id}/api-keys",
            json={"name": "temp-key"},
            headers=admin_headers,
        )
        key_id = key_resp.json()["id"]

        revoke_resp = await client.delete(
            f"/admin/api-keys/{key_id}",
            headers=admin_headers,
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["revoked_at"] is not None


class TestUsageQueries:
    """Tests for usage query endpoints."""

    @pytest.mark.asyncio
    async def test_empty_usage(self, client, admin_headers):
        """Usage query with no records returns empty list."""
        resp = await client.get("/admin/usage", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_usage_summary(self, client, admin_headers):
        """Usage summary returns aggregated stats."""
        resp = await client.get("/admin/usage/summary", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["total_tokens"] == 0
