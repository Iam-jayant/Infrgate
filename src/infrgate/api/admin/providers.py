"""
Admin API for Provider Configurations.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from infrgate.auth.dependencies import verify_admin
from infrgate.db.engine import get_db
from infrgate.db.models.provider_config import ProviderConfig
from infrgate.services.reliability import CircuitBreakerConfig, get_circuit_state

router = APIRouter(
    prefix="/providers",
    tags=["admin-providers"],
    dependencies=[Depends(verify_admin)],
)


@router.get("")
async def list_providers(db: AsyncSession = Depends(get_db)):
    """List all provider configurations."""
    result = await db.execute(select(ProviderConfig).order_by(ProviderConfig.priority))
    configs = result.scalars().all()
    return {
        "data": [
            {
                "id": str(c.id),
                "provider_name": c.provider_name,
                "display_name": c.display_name,
                "models": c.models,
                "priority": c.priority,
                "cost_per_1k_tokens": c.cost_per_1k_tokens,
                "timeout_config": c.timeout_config,
                "enabled": c.enabled,
            }
            for c in configs
        ]
    }


@router.get("/health")
async def list_providers_health(request: Request):
    """List health (circuit breaker state) for all registered providers."""
    registry = request.app.state.provider_registry
    redis = request.app.state.redis
    cb_config = CircuitBreakerConfig()
    
    health_data = {}
    for provider_name in registry.list_providers():
        state = await get_circuit_state(redis, provider_name, cb_config)
        health_data[provider_name] = {
            "circuit_state": state.value,
        }
        
    return {"data": health_data}
