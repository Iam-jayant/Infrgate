"""
Reliability layer: Timeout, Retry, Circuit Breaker, and Failover.

Spec reference: 08-reliability.md
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from fastapi import HTTPException
from redis.asyncio import Redis

from infrgate.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from infrgate.providers.base import ProviderAdapter, ProviderRequest, ProviderResponse

logger = structlog.get_logger()


@dataclass
class TimeoutConfig:
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 30.0
    total_timeout_s: float = 60.0
    stream_read_timeout_s: float = 10.0


async def execute_with_timeout(
    adapter: ProviderAdapter,
    request: ProviderRequest,
    timeout: TimeoutConfig,
) -> ProviderResponse:
    try:
        return await asyncio.wait_for(
            adapter.complete(request),
            timeout=timeout.total_timeout_s,
        )
    except asyncio.TimeoutError:
        raise ProviderTimeoutError(adapter.provider_name, timeout.total_timeout_s)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter: bool = True
    retryable_errors: set = field(
        default_factory=lambda: {
            ProviderTimeoutError,
            ProviderRateLimitError,
        }
    )


def calculate_delay(attempt: int, policy: RetryPolicy) -> float:
    delay = min(
        policy.base_delay_s * (2**attempt),
        policy.max_delay_s,
    )
    if policy.jitter:
        delay = random.uniform(0, delay)
    return delay


async def execute_with_retry(
    adapter: ProviderAdapter,
    request: ProviderRequest,
    timeout: TimeoutConfig,
    retry_policy: RetryPolicy,
) -> ProviderResponse:
    last_error: ProviderError | None = None

    for attempt in range(retry_policy.max_attempts):
        try:
            return await execute_with_timeout(adapter, request, timeout)
        except ProviderError as e:
            last_error = e
            if not getattr(e, "retryable", False) or attempt == retry_policy.max_attempts - 1:
                raise

            delay = calculate_delay(attempt, retry_policy)
            logger.warning(
                "provider_retry",
                provider=adapter.provider_name,
                attempt=attempt + 1,
                max_attempts=retry_policy.max_attempts,
                delay_s=delay,
                error=str(e),
                request_id=request.request_id,
            )
            await asyncio.sleep(delay)

    if last_error:
        raise last_error
    raise RuntimeError("Unexpected end of retry loop")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    failure_window_s: int = 60
    recovery_timeout_s: int = 30
    success_threshold: int = 2
    half_open_max_requests: int = 3


async def get_circuit_state(redis: Redis, provider: str, config: CircuitBreakerConfig) -> CircuitState:
    key = f"infrgate:circuit:{provider}"
    data = await redis.hgetall(key)
    if not data:
        return CircuitState.CLOSED

    state_str = data.get(b"state", b"closed").decode()

    if state_str == "open":
        opened_at_bytes = data.get(b"opened_at", b"0")
        opened_at = float(opened_at_bytes.decode() if opened_at_bytes else 0)
        if time.time() - opened_at >= config.recovery_timeout_s:
            await redis.hset(
                key,
                mapping={
                    "state": "half_open",
                    "half_open_at": str(time.time()),
                    "success_count": "0",
                },
            )
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    return CircuitState(state_str)


async def record_circuit_result(
    redis: Redis,
    provider: str,
    success: bool,
    config: CircuitBreakerConfig,
):
    key = f"infrgate:circuit:{provider}"
    state = await get_circuit_state(redis, provider, config)

    if state == CircuitState.CLOSED:
        if not success:
            failure_count = await redis.hincrby(key, "failure_count", 1)
            await redis.hset(key, "last_failure", str(time.time()))
            if failure_count >= config.failure_threshold:
                await redis.hset(
                    key,
                    mapping={
                        "state": "open",
                        "opened_at": str(time.time()),
                        "failure_count": "0",
                    },
                )
                logger.error("circuit_opened", provider=provider)
        else:
            await redis.hset(key, "failure_count", "0")

    elif state == CircuitState.HALF_OPEN:
        if success:
            success_count = await redis.hincrby(key, "success_count", 1)
            if success_count >= config.success_threshold:
                await redis.hset(
                    key,
                    mapping={
                        "state": "closed",
                        "failure_count": "0",
                        "success_count": "0",
                    },
                )
                logger.info("circuit_closed", provider=provider)
        else:
            await redis.hset(
                key,
                mapping={
                    "state": "open",
                    "opened_at": str(time.time()),
                    "success_count": "0",
                },
            )
            logger.warning("circuit_reopened", provider=provider)


@dataclass
class EligibleProvider:
    adapter: ProviderAdapter
    config: Any  # Actually ProviderConfig, but typed to avoid circular import
    timeout_config: TimeoutConfig
    retry_policy: RetryPolicy


@dataclass
class RoutingDecision:
    request_id: str
    requested_model: str
    eligible_providers: list[str]
    selected_provider: str | None = None
    fallback_used: bool = False
    reason: str = ""


async def execute_with_failover(
    providers: list[EligibleProvider],
    request: ProviderRequest,
    redis: Redis,
    cb_config: CircuitBreakerConfig,
) -> tuple[ProviderResponse, RoutingDecision]:
    """Execute request with failover across providers."""
    errors = []
    decision = RoutingDecision(
        request_id=request.request_id,
        requested_model=request.model,
        eligible_providers=[p.adapter.provider_name for p in providers],
    )

    for i, provider in enumerate(providers):
        circuit_state = await get_circuit_state(redis, provider.adapter.provider_name, cb_config)
        if circuit_state == CircuitState.OPEN:
            errors.append(f"{provider.adapter.provider_name}: circuit open")
            continue

        try:
            response = await execute_with_retry(
                provider.adapter,
                request,
                provider.timeout_config,
                provider.retry_policy,
            )
            await record_circuit_result(redis, provider.adapter.provider_name, True, cb_config)

            decision.selected_provider = provider.adapter.provider_name
            decision.fallback_used = i > 0
            decision.reason = "fallback" if i > 0 else "primary"

            return response, decision

        except ProviderError as e:
            await record_circuit_result(redis, provider.adapter.provider_name, False, cb_config)
            errors.append(f"{provider.adapter.provider_name}: {e.message}")

            if not getattr(e, "retryable", False):
                raise

    raise HTTPException(
        503,
        detail={
            "error": {
                "type": "provider_unavailable",
                "message": "All providers failed.",
                "details": errors,
            }
        },
    )
