"""
InfrGate Admin client — tenant, API key, and usage management.

Provides both synchronous (`InfrGateAdmin`) and asynchronous
(`AsyncInfrGateAdmin`) clients for gateway administration.

Usage::

    from infrgate_sdk import InfrGateAdmin

    admin = InfrGateAdmin(
        base_url="https://infrgate.onrender.com",
        admin_key="your-admin-key",
    )

    tenant = admin.tenants.create(name="Acme Corp", plan="standard")
    key = admin.api_keys.create(tenant_id=tenant.id, name="production")
    summary = admin.usage.summary(tenant_id=tenant.id)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx

from infrgate_sdk._http import AsyncTransport, SyncTransport
from infrgate_sdk.models import (
    ApiKey,
    ApiKeyWithSecret,
    PaginatedResponse,
    Tenant,
    TenantConfig,
    UsageRecord,
    UsageSummary,
)


# ─────────────────────────────────────────────────────────────────────────────
# Sync Admin Resources
# ─────────────────────────────────────────────────────────────────────────────


class _SyncTenants:
    """Synchronous tenant management."""

    def __init__(self, transport: SyncTransport):
        self._transport = transport

    def create(
        self,
        *,
        name: str,
        plan: str = "free",
        spend_cap_cents: int | None = None,
        config: TenantConfig | dict | None = None,
    ) -> Tenant:
        """Create a new tenant."""
        body: dict[str, Any] = {"name": name, "plan": plan}
        if spend_cap_cents is not None:
            body["spend_cap_cents"] = spend_cap_cents
        if config is not None:
            body["config"] = config.model_dump() if isinstance(config, TenantConfig) else config

        data = self._transport.request("POST", "/admin/tenants", json=body)
        return Tenant.model_validate(data)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[Tenant]:
        """List tenants with optional status filter and pagination."""
        data = self._transport.request(
            "GET",
            "/admin/tenants",
            params={"status": status, "limit": limit, "offset": offset},
        )
        data["items"] = [Tenant.model_validate(t) for t in data["items"]]
        return PaginatedResponse[Tenant].model_validate(data)

    def get(self, tenant_id: uuid.UUID | str) -> Tenant:
        """Get a single tenant by ID."""
        data = self._transport.request("GET", f"/admin/tenants/{tenant_id}")
        return Tenant.model_validate(data)

    def update(
        self,
        tenant_id: uuid.UUID | str,
        *,
        name: str | None = None,
        plan: str | None = None,
        status: str | None = None,
        spend_cap_cents: int | None = None,
        config: TenantConfig | dict | None = None,
    ) -> Tenant:
        """Partially update a tenant."""
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if plan is not None:
            body["plan"] = plan
        if status is not None:
            body["status"] = status
        if spend_cap_cents is not None:
            body["spend_cap_cents"] = spend_cap_cents
        if config is not None:
            body["config"] = config.model_dump() if isinstance(config, TenantConfig) else config

        data = self._transport.request("PATCH", f"/admin/tenants/{tenant_id}", json=body)
        return Tenant.model_validate(data)


class _SyncApiKeys:
    """Synchronous API key management."""

    def __init__(self, transport: SyncTransport):
        self._transport = transport

    def create(
        self,
        *,
        tenant_id: uuid.UUID | str,
        name: str = "",
    ) -> ApiKeyWithSecret:
        """
        Create a new API key for a tenant.

        The full key is returned ONLY in this response.
        Store it securely — it cannot be retrieved again.
        """
        data = self._transport.request(
            "POST",
            f"/admin/tenants/{tenant_id}/api-keys",
            json={"name": name},
        )
        return ApiKeyWithSecret.model_validate(data)

    def list(self, *, tenant_id: uuid.UUID | str) -> list[ApiKey]:
        """List all API keys for a tenant (secrets redacted)."""
        data = self._transport.request("GET", f"/admin/tenants/{tenant_id}/api-keys")
        return [ApiKey.model_validate(k) for k in data]

    def revoke(self, *, key_id: uuid.UUID | str) -> ApiKey:
        """Revoke an API key. Takes effect immediately."""
        data = self._transport.request("DELETE", f"/admin/api-keys/{key_id}")
        return ApiKey.model_validate(data)


class _SyncUsage:
    """Synchronous usage queries."""

    def __init__(self, transport: SyncTransport):
        self._transport = transport

    def query(
        self,
        *,
        tenant_id: uuid.UUID | str | None = None,
        model: str | None = None,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[UsageRecord]:
        """Query usage records with filters and pagination."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tenant_id is not None:
            params["tenant_id"] = str(tenant_id)
        if model is not None:
            params["model"] = model
        if start_date is not None:
            params["start_date"] = (
                start_date.isoformat() if isinstance(start_date, datetime) else start_date
            )
        if end_date is not None:
            params["end_date"] = (
                end_date.isoformat() if isinstance(end_date, datetime) else end_date
            )

        data = self._transport.request("GET", "/admin/usage", params=params)
        data["items"] = [UsageRecord.model_validate(r) for r in data["items"]]
        return PaginatedResponse[UsageRecord].model_validate(data)

    def summary(
        self,
        *,
        tenant_id: uuid.UUID | str | None = None,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
    ) -> UsageSummary:
        """Get aggregated usage summary."""
        params: dict[str, Any] = {}
        if tenant_id is not None:
            params["tenant_id"] = str(tenant_id)
        if start_date is not None:
            params["start_date"] = (
                start_date.isoformat() if isinstance(start_date, datetime) else start_date
            )
        if end_date is not None:
            params["end_date"] = (
                end_date.isoformat() if isinstance(end_date, datetime) else end_date
            )

        data = self._transport.request("GET", "/admin/usage/summary", params=params)
        return UsageSummary.model_validate(data)


# ─────────────────────────────────────────────────────────────────────────────
# Async Admin Resources
# ─────────────────────────────────────────────────────────────────────────────


class _AsyncTenants:
    """Asynchronous tenant management."""

    def __init__(self, transport: AsyncTransport):
        self._transport = transport

    async def create(
        self,
        *,
        name: str,
        plan: str = "free",
        spend_cap_cents: int | None = None,
        config: TenantConfig | dict | None = None,
    ) -> Tenant:
        body: dict[str, Any] = {"name": name, "plan": plan}
        if spend_cap_cents is not None:
            body["spend_cap_cents"] = spend_cap_cents
        if config is not None:
            body["config"] = config.model_dump() if isinstance(config, TenantConfig) else config

        data = await self._transport.request("POST", "/admin/tenants", json=body)
        return Tenant.model_validate(data)

    async def list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[Tenant]:
        data = await self._transport.request(
            "GET",
            "/admin/tenants",
            params={"status": status, "limit": limit, "offset": offset},
        )
        data["items"] = [Tenant.model_validate(t) for t in data["items"]]
        return PaginatedResponse[Tenant].model_validate(data)

    async def get(self, tenant_id: uuid.UUID | str) -> Tenant:
        data = await self._transport.request("GET", f"/admin/tenants/{tenant_id}")
        return Tenant.model_validate(data)

    async def update(
        self,
        tenant_id: uuid.UUID | str,
        *,
        name: str | None = None,
        plan: str | None = None,
        status: str | None = None,
        spend_cap_cents: int | None = None,
        config: TenantConfig | dict | None = None,
    ) -> Tenant:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if plan is not None:
            body["plan"] = plan
        if status is not None:
            body["status"] = status
        if spend_cap_cents is not None:
            body["spend_cap_cents"] = spend_cap_cents
        if config is not None:
            body["config"] = config.model_dump() if isinstance(config, TenantConfig) else config

        data = await self._transport.request("PATCH", f"/admin/tenants/{tenant_id}", json=body)
        return Tenant.model_validate(data)


class _AsyncApiKeys:
    """Asynchronous API key management."""

    def __init__(self, transport: AsyncTransport):
        self._transport = transport

    async def create(
        self,
        *,
        tenant_id: uuid.UUID | str,
        name: str = "",
    ) -> ApiKeyWithSecret:
        data = await self._transport.request(
            "POST",
            f"/admin/tenants/{tenant_id}/api-keys",
            json={"name": name},
        )
        return ApiKeyWithSecret.model_validate(data)

    async def list(self, *, tenant_id: uuid.UUID | str) -> list[ApiKey]:
        data = await self._transport.request("GET", f"/admin/tenants/{tenant_id}/api-keys")
        return [ApiKey.model_validate(k) for k in data]

    async def revoke(self, *, key_id: uuid.UUID | str) -> ApiKey:
        data = await self._transport.request("DELETE", f"/admin/api-keys/{key_id}")
        return ApiKey.model_validate(data)


class _AsyncUsage:
    """Asynchronous usage queries."""

    def __init__(self, transport: AsyncTransport):
        self._transport = transport

    async def query(
        self,
        *,
        tenant_id: uuid.UUID | str | None = None,
        model: str | None = None,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedResponse[UsageRecord]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tenant_id is not None:
            params["tenant_id"] = str(tenant_id)
        if model is not None:
            params["model"] = model
        if start_date is not None:
            params["start_date"] = (
                start_date.isoformat() if isinstance(start_date, datetime) else start_date
            )
        if end_date is not None:
            params["end_date"] = (
                end_date.isoformat() if isinstance(end_date, datetime) else end_date
            )

        data = await self._transport.request("GET", "/admin/usage", params=params)
        data["items"] = [UsageRecord.model_validate(r) for r in data["items"]]
        return PaginatedResponse[UsageRecord].model_validate(data)

    async def summary(
        self,
        *,
        tenant_id: uuid.UUID | str | None = None,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
    ) -> UsageSummary:
        params: dict[str, Any] = {}
        if tenant_id is not None:
            params["tenant_id"] = str(tenant_id)
        if start_date is not None:
            params["start_date"] = (
                start_date.isoformat() if isinstance(start_date, datetime) else start_date
            )
        if end_date is not None:
            params["end_date"] = (
                end_date.isoformat() if isinstance(end_date, datetime) else end_date
            )

        data = await self._transport.request("GET", "/admin/usage/summary", params=params)
        return UsageSummary.model_validate(data)


# ─────────────────────────────────────────────────────────────────────────────
# Public Admin Clients
# ─────────────────────────────────────────────────────────────────────────────


class InfrGateAdmin:
    """
    Synchronous admin client for InfrGate gateway management.

    Usage::

        admin = InfrGateAdmin(
            base_url="https://infrgate.onrender.com",
            admin_key="your-admin-key",
        )
        tenant = admin.tenants.create(name="Acme Corp", plan="standard")
        key = admin.api_keys.create(tenant_id=tenant.id, name="production")
    """

    def __init__(
        self,
        *,
        base_url: str,
        admin_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        default_headers: dict[str, str] | None = None,
    ):
        self._transport = SyncTransport(
            base_url=base_url,
            api_key=admin_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self.tenants = _SyncTenants(self._transport)
        self.api_keys = _SyncApiKeys(self._transport)
        self.usage = _SyncUsage(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncInfrGateAdmin:
    """
    Asynchronous admin client for InfrGate gateway management.

    Usage::

        async with AsyncInfrGateAdmin(
            base_url="https://infrgate.onrender.com",
            admin_key="your-admin-key",
        ) as admin:
            tenant = await admin.tenants.create(name="Acme Corp")
    """

    def __init__(
        self,
        *,
        base_url: str,
        admin_key: str,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 2,
        default_headers: dict[str, str] | None = None,
    ):
        self._transport = AsyncTransport(
            base_url=base_url,
            api_key=admin_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self.tenants = _AsyncTenants(self._transport)
        self.api_keys = _AsyncApiKeys(self._transport)
        self.usage = _AsyncUsage(self._transport)

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
