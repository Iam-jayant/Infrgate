"""
Redis connection management.

Provides the Redis client dependency for FastAPI routes.
The actual Redis pool is created in the application lifespan (main.py).
"""

from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis


async def get_redis(request: Request) -> Redis:
    """FastAPI dependency that returns the Redis client from app state."""
    return request.app.state.redis
