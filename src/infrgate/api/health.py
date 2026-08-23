from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

from infrgate.db.engine import get_db
from infrgate.redis import get_redis
from infrgate.providers.registry import ProviderRegistry
from infrgate.services.reliability import get_circuit_state, CircuitState, CircuitBreakerConfig

router = APIRouter(tags=["Health"])

@router.get("/health/live", summary="Liveness probe")
async def liveness():
    return {"status": "ok"}

@router.get("/health/ready", summary="Readiness probe")
async def readiness(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    checks = {}

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "error"

    # Redis
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    # Providers
    registry: ProviderRegistry = request.app.state.provider_registry
    
    checks["providers"] = {}
    config = CircuitBreakerConfig()
    
    for provider_name in registry.list_providers():
        circuit = await get_circuit_state(redis, provider_name, config)
        checks["providers"][provider_name] = (
            "healthy" if circuit != CircuitState.OPEN else "unhealthy"
        )

    # Ready if Postgres and Redis are ok
    is_ready = checks["postgres"] == "ok" and checks["redis"] == "ok"
    status_code = 200 if is_ready else 503

    return JSONResponse(
        content={
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
        },
        status_code=status_code,
    )
