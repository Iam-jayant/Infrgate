"""
Unit tests for the sliding window rate limiter.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infrgate.services.rate_limiter import check_rate_limit


@pytest.fixture
def mock_redis_for_rate_limit():
    """Mock Redis that simulates sorted set operations."""
    redis = AsyncMock()

    pipe = MagicMock()
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.zadd = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[
        0,  # zremrangebyscore result
        0,  # zcard result (0 requests in window)
        1,  # zadd result
        True,  # expire result
    ])
    redis.pipeline = MagicMock(return_value=pipe)
    redis.zrem = AsyncMock()

    return redis, pipe


class TestRateLimiter:
    """Tests for the sliding window rate limiter."""

    @pytest.mark.asyncio
    async def test_under_limit_allowed(self, mock_redis_for_rate_limit):
        """Requests under the limit are allowed."""
        redis, pipe = mock_redis_for_rate_limit
        pipe.execute.return_value = [0, 5, 1, True]  # 5 existing requests

        result = await check_rate_limit(redis, "tenant-1", limit=60)

        assert result.allowed is True
        assert result.limit == 60
        assert result.remaining == 54  # 60 - 5 - 1

    @pytest.mark.asyncio
    async def test_at_limit_rejected(self, mock_redis_for_rate_limit):
        """Request at the limit is rejected."""
        redis, pipe = mock_redis_for_rate_limit
        pipe.execute.return_value = [0, 60, 1, True]  # 60 existing (at limit)

        redis.zrangebyscore = AsyncMock(return_value=[(b"req1", time.time() - 50)])

        result = await check_rate_limit(redis, "tenant-1", limit=60)

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after is not None
        assert result.retry_after >= 1

    @pytest.mark.asyncio
    async def test_redis_failure_allows_request(self):
        """Redis failure results in fail-open (request allowed)."""
        from redis.exceptions import RedisError

        redis = AsyncMock()
        redis.pipeline = MagicMock(side_effect=RedisError("Connection refused"))

        result = await check_rate_limit(redis, "tenant-1", limit=60)

        assert result.allowed is True
        assert result.degraded is True

    @pytest.mark.asyncio
    async def test_zero_remaining_at_limit_minus_one(self, mock_redis_for_rate_limit):
        """Last allowed request shows 0 remaining."""
        redis, pipe = mock_redis_for_rate_limit
        pipe.execute.return_value = [0, 59, 1, True]  # 59 existing, limit 60

        result = await check_rate_limit(redis, "tenant-1", limit=60)

        assert result.allowed is True
        assert result.remaining == 0  # 60 - 59 - 1 = 0
