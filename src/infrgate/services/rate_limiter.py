"""
Rate limiter — sliding window log algorithm backed by Redis sorted sets.

Provides per-tenant RPM and TPM rate limiting with precise sliding
windows. Falls open (allows requests) if Redis is unavailable.

Spec reference: 05-rate-limiting.md §2, §6
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = structlog.get_logger()


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int | None = None
    degraded: bool = False  # True if Redis was unavailable


async def check_rate_limit(
    redis: Redis,
    tenant_id: str,
    limit: int,
    window_seconds: int = 60,
) -> RateLimitResult:
    """
    Sliding window rate limit check using Redis sorted sets.

    Algorithm:
      1. Remove all entries older than (now - window) from the sorted set
      2. Count remaining entries
      3. If count >= limit → REJECT
      4. If count < limit → ADD current request, ALLOW

    All operations execute in a single Redis pipeline for atomicity.
    """
    key = f"infrgate:ratelimit:{tenant_id}:rpm:{window_seconds}"
    now = time.time()
    window_start = now - window_seconds
    request_member = f"{uuid.uuid4()}"

    try:
        pipe = redis.pipeline(transaction=True)

        pipe.zremrangebyscore(key, 0, window_start)

        pipe.zcard(key)

        pipe.zadd(key, {request_member: now})

        pipe.expire(key, window_seconds + 10)

        results = await pipe.execute()
        current_count = results[1]  # ZCARD result (before our add)

        if current_count >= limit:
            await redis.zrem(key, request_member)

            retry_after = await _calc_retry_after(redis, key, window_start, window_seconds)

            from infrgate.metrics import RATE_LIMIT_REJECTIONS_TOTAL
            RATE_LIMIT_REJECTIONS_TOTAL.labels(tenant=tenant_id).inc()

            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_at=int(now + window_seconds),
                retry_after=retry_after,
            )

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - current_count - 1),
            reset_at=int(now + window_seconds),
        )

    except RedisError as e:
        logger.warning(
            "rate_limit_degraded",
            tenant_id=tenant_id,
            error=str(e),
        )
        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=0,
            reset_at=int(now + window_seconds),
            degraded=True,
        )


async def check_tpm_limit(
    redis: Redis,
    tenant_id: str,
    estimated_tokens: int,
    limit: int,
    window_seconds: int = 60,
) -> RateLimitResult:
    """
    Token-per-minute rate limit check.

    Similar to RPM but uses estimated prompt tokens to weight entries.
    """
    key = f"infrgate:ratelimit:{tenant_id}:tpm:{window_seconds}"
    now = time.time()
    window_start = now - window_seconds

    try:
        pipe = redis.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zrangebyscore(key, window_start, "+inf", withscores=True)
        results = await pipe.execute()

        entries = results[1]
        current_tokens = 0
        for member, _ in entries:
            parts = str(member).split(":", 1)
            if len(parts) == 2:
                try:
                    current_tokens += int(parts[1])
                except ValueError:
                    pass

        if current_tokens + estimated_tokens > limit:
            from infrgate.metrics import RATE_LIMIT_REJECTIONS_TOTAL
            RATE_LIMIT_REJECTIONS_TOTAL.labels(tenant=tenant_id).inc()
            
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=max(0, limit - current_tokens),
                reset_at=int(now + window_seconds),
                retry_after=await _calc_retry_after(redis, key, window_start, window_seconds),
            )

        member = f"{uuid.uuid4()}:{estimated_tokens}"
        await redis.zadd(key, {member: now})
        await redis.expire(key, window_seconds + 10)

        return RateLimitResult(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - current_tokens - estimated_tokens),
            reset_at=int(now + window_seconds),
        )

    except RedisError as e:
        logger.warning("rate_limit_degraded", tenant_id=tenant_id, error=str(e))
        return RateLimitResult(
            allowed=True, limit=limit, remaining=0,
            reset_at=int(now + window_seconds), degraded=True,
        )


async def _calc_retry_after(
    redis: Redis,
    key: str,
    window_start: float,
    window_seconds: int,
) -> int:
    """
    Calculate seconds until the oldest entry in the window expires,
    freeing a slot for the next request.
    """
    try:
        oldest = await redis.zrangebyscore(key, window_start, "+inf", start=0, num=1, withscores=True)
        if oldest:
            _, oldest_score = oldest[0]
            seconds_until_free = int(float(oldest_score) + window_seconds - time.time()) + 1
            return max(1, seconds_until_free)
    except RedisError:
        pass
    return 1
