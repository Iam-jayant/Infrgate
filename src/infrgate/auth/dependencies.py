"""
Authentication dependencies — API key verification and admin guard.

Implements the full auth flow: prefix extraction → hash verification →
tenant loading → status check. Uses constant-time comparison to prevent
timing attacks.

Spec reference: 04-authentication-tenancy.md §3
"""

from __future__ import annotations

import hashlib
import secrets

import structlog
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.config import get_settings
from infrgate.db.engine import get_db
from infrgate.db.models.api_key import ApiKey
from infrgate.db.models.tenant import Tenant

logger = structlog.get_logger()


async def get_current_tenant(
    request: Request,
    authorization: str = Header(..., description="Bearer <api-key>"),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """
    FastAPI dependency that authenticates the request and returns the tenant.

    Flow:
      1. Extract Bearer token from Authorization header
      2. Parse prefix (before '.') for O(1) lookup
      3. Query api_keys by prefix (active keys only)
      4. SHA-256 hash the full key and compare with stored hash
      5. Load tenant and verify status is 'active'

    Raises:
      HTTPException(401): Missing/invalid auth or key not found
      HTTPException(403): Tenant suspended
    """
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("auth_failed", reason="missing_bearer_scheme")
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "authentication_required", "message": "Missing or malformed Authorization header.", "code": 401}},
        )

    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "authentication_required", "message": "Empty bearer token.", "code": 401}},
        )

    dot_index = token.find(".")
    if dot_index == -1:
        logger.warning("auth_failed", reason="invalid_key_format")
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_api_key", "message": "Invalid API key format.", "code": 401}},
        )

    prefix = token[:dot_index]

    result = await db.execute(
        select(ApiKey).where(
            ApiKey.prefix == prefix,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        logger.warning("auth_failed", reason="key_not_found", prefix=prefix[:10])
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_api_key", "message": "Invalid API key.", "code": 401}},
        )

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    if not secrets.compare_digest(key_hash, api_key.key_hash):
        logger.warning("auth_failed", reason="hash_mismatch", prefix=prefix[:10])
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_api_key", "message": "Invalid API key.", "code": 401}},
        )

    tenant = await db.get(Tenant, api_key.tenant_id)
    if tenant is None:
        logger.error("auth_failed", reason="tenant_not_found", tenant_id=str(api_key.tenant_id))
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_api_key", "message": "Invalid API key.", "code": 401}},
        )

    if tenant.status != "active":
        logger.warning("auth_failed", reason="tenant_suspended", tenant_id=str(tenant.id))
        raise HTTPException(
            status_code=403,
            detail={"error": {"type": "tenant_suspended", "message": "Tenant account is suspended.", "code": 403}},
        )

    request.state.tenant = tenant
    return tenant


async def verify_admin(
    request: Request,
    authorization: str = Header(...),
) -> None:
    """
    FastAPI dependency that verifies admin access.

    Admin access is granted via the ADMIN_API_KEY environment variable.
    This is separate from tenant API keys.

    Raises:
      HTTPException(401): Missing or invalid admin key
    """
    settings = get_settings()

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "authentication_required", "message": "Missing or malformed Authorization header.", "code": 401}},
        )

    token = parts[1].strip()

    if not settings.ADMIN_API_KEY or not secrets.compare_digest(token, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=401,
            detail={"error": {"type": "invalid_api_key", "message": "Invalid admin API key.", "code": 401}},
        )
