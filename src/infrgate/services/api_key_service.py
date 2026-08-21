"""
API Key service — key generation, listing, and revocation.

Keys use a prefix + SHA-256 hash scheme. The full key is returned
only at creation time and never stored or retrievable again.

Spec reference: 04-authentication-tenancy.md §2
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.db.models.api_key import ApiKey


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (full_key, prefix, key_hash):
        - full_key: ``sk-infr_<prefix>.<secret>`` — shown to user once
        - prefix: ``sk-infr_<8chars>`` — stored for O(1) lookup
        - key_hash: SHA-256 hex digest of full_key — stored for verification
    """
    prefix_random = secrets.token_urlsafe(8)[:8]
    secret = secrets.token_urlsafe(32)[:32]
    prefix = f"sk-infr_{prefix_random}"
    full_key = f"{prefix}.{secret}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


async def create_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str = "",
) -> tuple[ApiKey, str]:
    """
    Create a new API key for a tenant.

    Returns:
        Tuple of (api_key_model, full_key_string).
        The full key is returned only here — it is never stored.
    """
    full_key, prefix, key_hash = generate_api_key()

    api_key = ApiKey(
        tenant_id=tenant_id,
        name=name,
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return api_key, full_key


async def list_api_keys(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[ApiKey]:
    """List all API keys for a tenant (secrets are never returned)."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.tenant_id == tenant_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(
    db: AsyncSession,
    key_id: uuid.UUID,
) -> ApiKey | None:
    """
    Revoke an API key by setting revoked_at.

    Revoked keys immediately fail authentication due to the
    partial index on prefix WHERE revoked_at IS NULL.
    """
    api_key = await db.get(ApiKey, key_id)
    if not api_key:
        return None

    if api_key.revoked_at is not None:
        return api_key  # Already revoked

    api_key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(api_key)
    return api_key
