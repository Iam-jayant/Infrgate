"""
Routing Engine.

Handles model-to-provider resolution and health filtering.
Spec reference: 07-routing-engine.md
"""

from fastapi import HTTPException
from redis.asyncio import Redis

from infrgate.db.models.provider_config import ProviderConfig
from infrgate.providers.registry import ProviderRegistry
from infrgate.services.reliability import (
    CircuitBreakerConfig,
    CircuitState,
    EligibleProvider,
    RetryPolicy,
    TimeoutConfig,
    get_circuit_state,
)


def _get_aliases(config: ProviderConfig, model: str) -> list[str]:
    """Extract aliases for a given model from the config."""
    for m in config.models:
        if m.get("model_id") == model or model in m.get("aliases", []):
            return m.get("aliases", [])
    return []


def resolve_providers(
    model: str,
    registry: ProviderRegistry,
    provider_configs: list[ProviderConfig],
) -> list[EligibleProvider]:
    """
    Resolve a model name to an ordered list of eligible providers.
    Returns providers sorted by priority (lower number = higher priority).
    """
    eligible = []
    for config in provider_configs:
        if not config.enabled:
            continue
        adapter = registry.get(config.provider_name)
        if not adapter:
            continue

        supported_by_adapter = model in adapter.supported_models
        supported_in_config = any(
            m.get("model_id") == model or model in m.get("aliases", [])
            for m in config.models
        )

        if supported_by_adapter or supported_in_config:
            t_cfg = config.timeout_config or {}
            timeout_config = TimeoutConfig(
                connect_timeout_s=t_cfg.get("connect_timeout_s", 5.0),
                read_timeout_s=t_cfg.get("read_timeout_s", 30.0),
                total_timeout_s=t_cfg.get("total_timeout_s", 60.0),
                stream_read_timeout_s=t_cfg.get("stream_read_timeout_s", 30.0),
            )
            retry_policy = RetryPolicy()  # Use defaults for now

            eligible.append(
                EligibleProvider(
                    adapter=adapter,
                    config=config,
                    timeout_config=timeout_config,
                    retry_policy=retry_policy,
                )
            )

    if not eligible:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "type": "invalid_request",
                    "message": f"No provider available for model '{model}'.",
                }
            },
        )

    return sorted(eligible, key=lambda p: p.config.priority)


async def filter_healthy(
    providers: list[EligibleProvider],
    redis: Redis,
    cb_config: CircuitBreakerConfig,
) -> list[EligibleProvider]:
    """Remove providers with open circuit breakers."""
    healthy = []
    for provider in providers:
        state = await get_circuit_state(redis, provider.adapter.provider_name, cb_config)
        if state != CircuitState.OPEN:
            healthy.append(provider)

    if not healthy:
        raise HTTPException(
            503,
            detail={
                "error": {
                    "type": "provider_unavailable",
                    "message": "All providers for this model are currently unavailable.",
                }
            },
        )

    return healthy
