"""
Chat completions endpoint — OpenAI-compatible POST /v1/chat/completions.

This is the core inference endpoint. It authenticates the request,
enforces policy and rate limits, calls the Gemini adapter, records
usage, and returns an OpenAI-compatible response.

Spec reference: 02-api-design.md §3.1
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from infrgate.api.dependencies import enforce_rate_limit
from infrgate.auth.policy import check_model_authorization, check_spend_cap
from infrgate.db.engine import get_db
from infrgate.db.models.tenant import Tenant
from infrgate.db.models.provider_config import ProviderConfig
from infrgate.providers.base import ProviderRequest
from infrgate.schemas.chat import ChatCompletionRequest, build_completion_response
from infrgate.services.usage_service import record_usage
from infrgate.services.routing import resolve_providers, filter_healthy
from infrgate.services.reliability import execute_with_failover, CircuitBreakerConfig

logger = structlog.get_logger()

router = APIRouter()


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    tenant: Tenant = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """
    OpenAI-compatible chat completion endpoint.

    Flow:
      1. Authentication + rate limiting (via dependencies)
      2. Model authorization check
      3. Spend cap check
      4. Resolve providers and filter out unhealthy ones
      5. Execute request with failover support
      6. Record usage in ledger
      7. Return OpenAI-compatible response with rate limit headers
    """
    request_id = request.state.request_id

    logger.info(
        "request_started",
        method="POST",
        path="/v1/chat/completions",
        tenant_id=str(tenant.id),
        model=body.model,
    )

    if body.stream:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request",
                    "message": "Streaming is not supported in Phase 1. Set stream=false.",
                    "request_id": request_id,
                    "code": 400,
                }
            },
        )

    check_model_authorization(tenant, body.model)
    check_spend_cap(tenant)

    provider_request = ProviderRequest(
        model=body.model,
        messages=[m.model_dump() for m in body.messages],
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        top_p=body.top_p,
        stop=body.stop,
        stream=False,
        request_id=request_id,
    )

    result = await db.execute(select(ProviderConfig))
    provider_configs = result.scalars().all()
    registry = request.app.state.provider_registry
    redis = request.app.state.redis
    cb_config = CircuitBreakerConfig()
    
    eligible = resolve_providers(body.model, registry, provider_configs)
    healthy = await filter_healthy(eligible, redis, cb_config)

    start = time.monotonic()
    response, decision = await execute_with_failover(healthy, provider_request, redis, cb_config)
    total_latency_ms = int((time.monotonic() - start) * 1000)

    selected_config = next(
        (c for c in provider_configs if c.provider_name == decision.selected_provider), None
    )
    cost_config = selected_config.cost_per_1k_tokens if selected_config else {}

    await record_usage(
        db=db,
        request_id=request_id,
        tenant_id=tenant.id,
        model=body.model,
        provider=decision.selected_provider,
        response=response,
        latency_ms=total_latency_ms,
        cost_config=cost_config,
        metadata={
            "finish_reason": response.finish_reason,
            "routing_reason": decision.reason,
            "fallback_used": decision.fallback_used,
        },
    )

    completion = build_completion_response(
        request_id=request_id,
        model=body.model,
        content=response.content,
        finish_reason=response.finish_reason,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.total_tokens,
    )

    logger.info(
        "request_completed",
        status_code=200,
        latency_ms=total_latency_ms,
        model=body.model,
        provider=decision.selected_provider,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
    )

    rate_limit = getattr(request.state, "rate_limit", None)
    headers = {"X-Request-ID": request_id}
    if rate_limit:
        headers["X-RateLimit-Limit"] = str(rate_limit.limit)
        headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
        headers["X-RateLimit-Reset"] = str(rate_limit.reset_at)

    return JSONResponse(
        content=completion.model_dump(),
        headers=headers,
    )
