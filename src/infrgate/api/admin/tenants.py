"""
Tenant management endpoints.

Spec reference: 02-api-design.md §3.2
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.auth.dependencies import verify_admin
from infrgate.db.engine import get_db
from infrgate.schemas.common import PaginatedResponse, PaginationParams
from infrgate.schemas.tenant import TenantCreateRequest, TenantResponse, TenantUpdateRequest
from infrgate.services import tenant_service

router = APIRouter(dependencies=[Depends(verify_admin)])


@router.post("/tenants", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant."""
    tenant = await tenant_service.create_tenant(db, body)
    return TenantResponse.model_validate(tenant)


@router.get("/tenants", response_model=PaginatedResponse[TenantResponse])
async def list_tenants(
    pagination: PaginationParams = Depends(),
    status: str | None = Query(default=None, pattern="^(active|suspended)$"),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants with optional status filter and pagination."""
    tenants, total = await tenant_service.list_tenants(
        db, status=status, limit=pagination.limit, offset=pagination.offset
    )
    return PaginatedResponse(
        items=[TenantResponse.model_validate(t) for t in tenants],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a single tenant by ID."""
    tenant = await tenant_service.get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail={
            "error": {"type": "not_found", "message": "Tenant not found.", "code": 404}
        })
    return TenantResponse.model_validate(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a tenant."""
    tenant = await tenant_service.update_tenant(db, tenant_id, body)
    if not tenant:
        raise HTTPException(status_code=404, detail={
            "error": {"type": "not_found", "message": "Tenant not found.", "code": 404}
        })
    return TenantResponse.model_validate(tenant)
