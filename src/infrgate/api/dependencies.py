"""
Shared API dependencies — rate limit enforcement and response header injection.

Spec reference: 05-rate-limiting.md §3, §4
"""

from __future__ import annotations

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis

from infrgate.auth.dependencies import get_current_tenant
from infrgate.auth.policy import resolve_rpm_limit
from infrgate.db.models.tenant import Tenant
from infrgate.exceptions import RateLimitExceeded
from infrgate.redis import get_redis
from infrgate.services.rate_limiter import check_rate_limit

logger = structlog.get_logger()


async def enforce_rate_limit(
    request: Request,
    tenant: Tenant = Depends(get_current_tenant),
    redis: Redis = Depends(get_redis),
) -> Tenant:
    """
    FastAPI dependency that enforces per-tenant RPM rate limiting.

    Attaches rate limit metadata to request.state for response header injection.
    Raises RateLimitExceeded (429) if the tenant is over limit.

    Returns the authenticated tenant (pass-through from get_current_tenant).
    """
    rpm_limit = resolve_rpm_limit(tenant)

    result = await check_rate_limit(
        redis=redis,
        tenant_id=str(tenant.id),
        limit=rpm_limit,
    )

    request.state.rate_limit = result

    if not result.allowed:
        logger.warning(
            "rate_limit_exceeded",
            tenant_id=str(tenant.id),
            limit=result.limit,
        )
        raise RateLimitExceeded(retry_after=result.retry_after or 1)

    return tenant
