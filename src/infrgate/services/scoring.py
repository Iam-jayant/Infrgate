"""
Scoring and Health tracking engine for routing.

Spec reference: 07-routing-engine.md
"""

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from redis.asyncio import Redis

from infrgate.services.reliability import EligibleProvider, RoutingDecision


@dataclass
class HealthSignals:
    availability: float
    ewma_error_rate: float
    ewma_latency_ms: float


@dataclass
class RoutingWeights:
    """Configurable weights for routing score calculation."""
    availability: float = 0.35
    error_rate: float = 0.30
    latency: float = 0.20
    cost: float = 0.15


def ewma_update(current: float, new_sample: float, alpha: float = 0.3) -> float:
    """Update EWMA with a new sample."""
    return alpha * new_sample + (1 - alpha) * current


async def record_health_signal(
    redis: Redis,
    provider_name: str,
    latency_ms: int,
    is_error: bool,
    alpha: float = 0.3,
):
    key = f"infrgate:health:{provider_name}"

    current = await redis.hgetall(key)  # type: ignore
    ewma_latency = ewma_update(
        float(current.get(b"ewma_latency_ms", latency_ms)),
        latency_ms,
        alpha,
    )
    ewma_error = ewma_update(
        float(current.get(b"ewma_error_rate", 0.0)),
        1.0 if is_error else 0.0,
        alpha,
    )

    await redis.hset(key, mapping={  # type: ignore
        "ewma_latency_ms": str(ewma_latency),
        "ewma_error_rate": str(ewma_error),
        "last_updated": str(time.time()),
        "sample_count": str(int(current.get(b"sample_count", 0)) + 1),
    })
    await redis.expire(key, 300)  # type: ignore


async def get_health_signals(redis: Redis, provider_name: str) -> HealthSignals:
    circuit_key = f"infrgate:circuit:{provider_name}"
    health_key = f"infrgate:health:{provider_name}"

    circuit_data = await redis.hgetall(circuit_key)  # type: ignore
    state_str = circuit_data.get(b"state", b"closed").decode() if circuit_data else "closed"
    
    availability = 1.0
    if state_str == "open":
        availability = 0.0
    elif state_str == "half_open":
        availability = 0.5

    health_data = await redis.hgetall(health_key)  # type: ignore
    if health_data:
        ewma_latency = float(health_data.get(b"ewma_latency_ms", 1000.0))
        ewma_error = float(health_data.get(b"ewma_error_rate", 0.0))
    else:
        ewma_latency = 1000.0
        ewma_error = 0.0

    return HealthSignals(
        availability=availability,
        ewma_error_rate=ewma_error,
        ewma_latency_ms=ewma_latency,
    )


def calculate_score(
    provider: EligibleProvider,
    model: str,
    health: HealthSignals,
    weights: RoutingWeights,
) -> float:
    # Availability: 1.0 (closed), 0.5 (half_open), 0.0 (open)
    availability_score = health.availability

    # Error rate: invert so lower error rate = higher score
    error_score = 1.0 - health.ewma_error_rate

    # Latency: normalize and invert (lower latency = higher score)
    latency_score = max(0.0, 1.0 - (health.ewma_latency_ms / 5000.0))

    # Cost: Use cost per 1K for the requested model, fallback to a sensible default
    cost_config = provider.config.cost_per_1k_tokens.get(model, {}) if provider.config else {}
    prompt_cost = cost_config.get("prompt", 1.0)
    cost_score = max(0.0, 1.0 - (prompt_cost / 10.0))

    return (
        weights.availability * availability_score
        + weights.error_rate * error_score
        + weights.latency * latency_score
        + weights.cost * cost_score
    )


async def score_providers(
    eligible: list[EligibleProvider],
    model: str,
    redis: Redis,
    weights: RoutingWeights | None = None,
) -> tuple[list[EligibleProvider], dict[str, dict]]:
    if weights is None:
        weights = RoutingWeights()
        
    scores = {}
    scored_providers = []

    for provider in eligible:
        health = await get_health_signals(redis, provider.adapter.provider_name)
        score = calculate_score(provider, model, health, weights)
        
        cost_config = provider.config.cost_per_1k_tokens.get(model, {}) if provider.config else {}
        prompt_cost = cost_config.get("prompt", 1.0)
        
        scores[provider.adapter.provider_name] = {
            "health": health.availability,
            "error": 1.0 - health.ewma_error_rate,
            "latency": max(0.0, 1.0 - health.ewma_latency_ms / 5000.0),
            "cost": max(0.0, 1.0 - (prompt_cost / 10.0)),
            "total": score,
        }
        scored_providers.append((score, provider))

    # Sort descending by score
    scored_providers.sort(key=lambda x: x[0], reverse=True)
    sorted_eligible = [p for _, p in scored_providers]
    
    return sorted_eligible, scores


async def log_routing_decision(redis: Redis, decision: RoutingDecision):
    decision_dict = asdict(decision)
    if isinstance(decision_dict.get("timestamp"), datetime):
        decision_dict["timestamp"] = decision_dict["timestamp"].isoformat()
    else:
        decision_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
        
    decision_json = json.dumps(decision_dict)
    
    key = "infrgate:routing_decisions"
    await redis.lpush(key, decision_json)  # type: ignore
    await redis.ltrim(key, 0, 999)  # type: ignore
