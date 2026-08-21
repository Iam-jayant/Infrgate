"""
Tenant service — business logic for tenant lifecycle management.

Spec reference: 02-api-design.md §3.2
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.db.models.tenant import Tenant
from infrgate.schemas.tenant import TenantCreateRequest, TenantResponse, TenantUpdateRequest


async def create_tenant(db: AsyncSession, request: TenantCreateRequest) -> Tenant:
    """Create a new tenant."""
    tenant = Tenant(
        name=request.name,
        plan=request.plan,
        spend_cap_cents=request.spend_cap_cents,
        config=request.config.model_dump(exclude_none=True),
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def list_tenants(
    db: AsyncSession,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Tenant], int]:
    """List tenants with optional status filter and pagination."""
    query = select(Tenant)
    count_query = select(func.count()).select_from(Tenant)

    if status:
        query = query.where(Tenant.status == status)
        count_query = count_query.where(Tenant.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    tenants = list(result.scalars().all())

    return tenants, total


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    """Get a single tenant by ID."""
    return await db.get(Tenant, tenant_id)


async def update_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    request: TenantUpdateRequest,
) -> Tenant | None:
    """Partially update a tenant. Only supplied fields are modified."""
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        return None

    update_data = request.model_dump(exclude_unset=True)

    if "config" in update_data and update_data["config"] is not None:
        config_data = update_data.pop("config")
        existing_config = dict(tenant.config) if tenant.config else {}
        for key, value in config_data.items():
            if value is not None:
                existing_config[key] = value
        tenant.config = existing_config

    for key, value in update_data.items():
        setattr(tenant, key, value)

    await db.commit()
    await db.refresh(tenant)
    return tenant
