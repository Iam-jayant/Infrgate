"""
Tests for InfrGateAdmin — tenant, API key, and usage management.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from infrgate_sdk import (
    ApiKey,
    ApiKeyWithSecret,
    InfrGateAdmin,
    NotFoundError,
    PaginatedResponse,
    Tenant,
    UsageSummary,
)

BASE_URL = "https://test.infrgate.dev"
ADMIN_KEY = "test-admin-key"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_tenant() -> dict:
    return {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "Acme Corp",
        "plan": "standard",
        "status": "active",
        "spend_cap_cents": 5000,
        "current_spend_cents": 120,
        "config": {"allowed_models": ["gpt-4o-mini"]},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_api_key() -> dict:
    return {
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "production",
        "prefix": "a1b2c3d4",
        "key": "a1b2c3d4.full-secret-key-here",
        "created_at": "2026-01-01T00:00:00Z",
        "revoked_at": None,
    }


@pytest.fixture
def mock_usage_summary() -> dict:
    return {
        "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "period_start": "2026-09-01T00:00:00Z",
        "period_end": "2026-10-01T00:00:00Z",
        "total_requests": 150,
        "total_prompt_tokens": 45000,
        "total_completion_tokens": 30000,
        "total_tokens": 75000,
        "total_cost_cents": 350,
        "by_model": {
            "gpt-4o-mini": {"requests": 100, "total_tokens": 50000, "cost_cents": 200},
            "gemini-2.5-flash": {"requests": 50, "total_tokens": 25000, "cost_cents": 150},
        },
    }


# ── Tenant Tests ──────────────────────────────────────────────────────────────


@respx.mock
def test_create_tenant(mock_tenant):
    """Creating a tenant returns a typed Tenant model."""
    respx.post(f"{BASE_URL}/admin/tenants").mock(
        return_value=httpx.Response(201, json=mock_tenant)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        tenant = admin.tenants.create(name="Acme Corp", plan="standard", spend_cap_cents=5000)

    assert isinstance(tenant, Tenant)
    assert tenant.name == "Acme Corp"
    assert tenant.plan == "standard"
    assert tenant.spend_cap_cents == 5000


@respx.mock
def test_list_tenants(mock_tenant):
    """Listing tenants returns a paginated response."""
    respx.get(f"{BASE_URL}/admin/tenants").mock(
        return_value=httpx.Response(
            200,
            json={"items": [mock_tenant], "total": 1, "limit": 50, "offset": 0},
        )
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        result = admin.tenants.list()

    assert isinstance(result, PaginatedResponse)
    assert result.total == 1
    assert isinstance(result.items[0], Tenant)
    assert result.items[0].name == "Acme Corp"


@respx.mock
def test_get_tenant(mock_tenant):
    """Getting a tenant by ID returns a typed Tenant."""
    tenant_id = mock_tenant["id"]
    respx.get(f"{BASE_URL}/admin/tenants/{tenant_id}").mock(
        return_value=httpx.Response(200, json=mock_tenant)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        tenant = admin.tenants.get(tenant_id)

    assert tenant.id == tenant.id
    assert tenant.status == "active"


@respx.mock
def test_update_tenant(mock_tenant):
    """Updating a tenant returns the modified Tenant."""
    tenant_id = mock_tenant["id"]
    updated = {**mock_tenant, "plan": "enterprise"}
    route = respx.patch(f"{BASE_URL}/admin/tenants/{tenant_id}").mock(
        return_value=httpx.Response(200, json=updated)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        tenant = admin.tenants.update(tenant_id, plan="enterprise")

    assert tenant.plan == "enterprise"


@respx.mock
def test_get_tenant_not_found():
    """404 raises NotFoundError."""
    tenant_id = "nonexistent-id"
    respx.get(f"{BASE_URL}/admin/tenants/{tenant_id}").mock(
        return_value=httpx.Response(
            404,
            json={"error": {"type": "not_found", "message": "Tenant not found.", "code": 404}},
        )
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY, max_retries=0) as admin:
        with pytest.raises(NotFoundError):
            admin.tenants.get(tenant_id)


# ── API Key Tests ─────────────────────────────────────────────────────────────


@respx.mock
def test_create_api_key(mock_api_key):
    """Creating an API key returns ApiKeyWithSecret including the full key."""
    tenant_id = mock_api_key["tenant_id"]
    respx.post(f"{BASE_URL}/admin/tenants/{tenant_id}/api-keys").mock(
        return_value=httpx.Response(201, json=mock_api_key)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        key = admin.api_keys.create(tenant_id=tenant_id, name="production")

    assert isinstance(key, ApiKeyWithSecret)
    assert key.key == "a1b2c3d4.full-secret-key-here"
    assert key.name == "production"


@respx.mock
def test_list_api_keys(mock_api_key):
    """Listing API keys returns redacted key objects."""
    tenant_id = mock_api_key["tenant_id"]
    redacted = {k: v for k, v in mock_api_key.items() if k != "key"}
    respx.get(f"{BASE_URL}/admin/tenants/{tenant_id}/api-keys").mock(
        return_value=httpx.Response(200, json=[redacted])
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        keys = admin.api_keys.list(tenant_id=tenant_id)

    assert len(keys) == 1
    assert isinstance(keys[0], ApiKey)
    assert keys[0].prefix == "a1b2c3d4"


@respx.mock
def test_revoke_api_key(mock_api_key):
    """Revoking a key returns the updated ApiKey."""
    key_id = mock_api_key["id"]
    revoked = {k: v for k, v in mock_api_key.items() if k != "key"}
    revoked["revoked_at"] = "2026-10-01T00:00:00Z"
    respx.delete(f"{BASE_URL}/admin/api-keys/{key_id}").mock(
        return_value=httpx.Response(200, json=revoked)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        key = admin.api_keys.revoke(key_id=key_id)

    assert key.revoked_at is not None


# ── Usage Tests ──────────────────────────────────────────────────────────────


@respx.mock
def test_usage_summary(mock_usage_summary):
    """Usage summary returns a typed UsageSummary."""
    respx.get(f"{BASE_URL}/admin/usage/summary").mock(
        return_value=httpx.Response(200, json=mock_usage_summary)
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        summary = admin.usage.summary(
            tenant_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        )

    assert isinstance(summary, UsageSummary)
    assert summary.total_requests == 150
    assert summary.total_cost_cents == 350
    assert "gpt-4o-mini" in summary.by_model
    assert summary.by_model["gpt-4o-mini"].requests == 100


@respx.mock
def test_usage_query():
    """Usage query returns a paginated response of usage records."""
    respx.get(f"{BASE_URL}/admin/usage").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                        "request_id": "d4e5f6a7-b8c9-0123-defa-234567890123",
                        "tenant_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "model": "gpt-4o-mini",
                        "provider": "openai",
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                        "cost_cents": 2,
                        "status": "completed",
                        "latency_ms": 450,
                        "created_at": "2026-09-15T12:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 50,
                "offset": 0,
            },
        )
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        result = admin.usage.query(
            tenant_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        )

    assert result.total == 1
    assert result.items[0].model == "gpt-4o-mini"
    assert result.items[0].cost_cents == 2


# ── Admin Auth Test ──────────────────────────────────────────────────────────


@respx.mock
def test_admin_auth_header():
    """Admin client sends the admin key as Bearer token."""
    route = respx.get(f"{BASE_URL}/admin/tenants").mock(
        return_value=httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 50, "offset": 0},
        )
    )

    with InfrGateAdmin(base_url=BASE_URL, admin_key=ADMIN_KEY) as admin:
        admin.tenants.list()

    assert route.calls[0].request.headers["Authorization"] == f"Bearer {ADMIN_KEY}"
