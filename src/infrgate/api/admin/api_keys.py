"""
API Key management endpoints.

Spec reference: 02-api-design.md §3.3
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.auth.dependencies import verify_admin
from infrgate.db.engine import get_db
from infrgate.schemas.api_key import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse
from infrgate.services import api_key_service
from infrgate.services.tenant_service import get_tenant

router = APIRouter(dependencies=[Depends(verify_admin)])


@router.post(
    "/tenants/{tenant_id}/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=201,
)
async def create_api_key(
    tenant_id: uuid.UUID,
    body: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new API key for a tenant.

    The full key is returned ONLY in this response. It is never
    stored or retrievable again.
    """
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail={
            "error": {"type": "not_found", "message": "Tenant not found.", "code": 404}
        })

    api_key, full_key = await api_key_service.create_api_key(
        db, tenant_id=tenant_id, name=body.name
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        tenant_id=api_key.tenant_id,
        name=api_key.name,
        prefix=api_key.prefix,
        key=full_key,
        created_at=api_key.created_at,
        revoked_at=api_key.revoked_at,
    )


@router.get("/tenants/{tenant_id}/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all API keys for a tenant (secrets redacted)."""
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail={
            "error": {"type": "not_found", "message": "Tenant not found.", "code": 404}
        })

    keys = await api_key_service.list_api_keys(db, tenant_id)
    return [ApiKeyResponse.model_validate(k) for k in keys]


@router.delete("/api-keys/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key. Revoked keys immediately fail authentication."""
    api_key = await api_key_service.revoke_api_key(db, key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail={
            "error": {"type": "not_found", "message": "API key not found.", "code": 404}
        })
    return ApiKeyResponse.model_validate(api_key)
