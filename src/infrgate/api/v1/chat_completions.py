"""
Chat completions endpoint — OpenAI-compatible POST /v1/chat/completions.

This is the core inference endpoint. It authenticates the request,
enforces policy and rate limits, claims an idempotency slot, calls
the provider, records usage, and returns an OpenAI-compatible response.

Idempotency: The Idempotency-Key header (client-supplied) is used to
deduplicate requests. A pending ledger row is claimed BEFORE calling
the upstream provider. If the key was already claimed:
- If pending, returns 409 Conflict.
- If completed, replays the cached response.

X-Request-ID is a separate concept — a per-HTTP-call correlation ID
for log tracing. It is server-generated fresh per call.

Spec reference: 02-api-design.md §3.1
"""

from __future__ import annotations
import asyncio
import anyio
import time
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from infrgate.api.dependencies import enforce_rate_limit
from infrgate.auth.policy import check_model_authorization, check_spend_cap
from infrgate.db.engine import get_db
from infrgate.db.models.tenant import Tenant
from infrgate.db.models.provider_config import ProviderConfig
from infrgate.providers.base import ProviderRequest
from infrgate.schemas.chat import ChatCompletionRequest, build_completion_response
from infrgate.schemas.streaming import StreamUsageTracker
from infrgate.services.idempotency import claim_or_replay_request, finalize_request
from infrgate.services.usage_service import calculate_cost_cents
from infrgate.services.routing import resolve_providers, filter_healthy
from infrgate.services.reliability import execute_with_failover, execute_stream_with_failover, CircuitBreakerConfig
from infrgate.services.scoring import score_providers

logger = structlog.get_logger()

router = APIRouter()


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    tenant: Tenant = Depends(enforce_rate_limit),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """
    OpenAI-compatible chat completion endpoint.

    Flow:
      1. Authentication + rate limiting (via dependencies)
      2. Model authorization check
      3. Spend cap check
      4. Claim idempotency slot (or Replay if duplicate)
      5. Resolve providers and filter out unhealthy ones
      6. Execute request with failover support
      7. Finalize usage and cache response in ledger
      8. Return OpenAI-compatible response with rate limit headers
    """
    request_id = request.state.request_id

    logger.info(
        "request_started",
        method="POST",
        path="/v1/chat/completions",
        tenant_id=str(tenant.id),
        model=body.model,
        idempotency_key=idempotency_key,
    )

    check_model_authorization(tenant, body.model)
    
    tenant_id_str = str(tenant.id)
    tenant_uuid = tenant.id

    # ── Claim-first: insert pending row BEFORE calling provider ──────
    # If the request is a duplicate, this will raise 409 or return a Replay JSONResponse
    replay_response = await claim_or_replay_request(
        db=db,
        idempotency_key=idempotency_key,
        tenant_id=tenant_uuid,
        request_id=request_id,
        model=body.model,
    )

    if replay_response:
        # Replay the cached response
        # Ensure rate limit headers are added to the replay
        rate_limit = getattr(request.state, "rate_limit", None)
        replay_response.headers["X-Request-ID"] = request_id
        if rate_limit:
            replay_response.headers["X-RateLimit-Limit"] = str(rate_limit.limit)
            replay_response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
            replay_response.headers["X-RateLimit-Reset"] = str(rate_limit.reset_at)
        return replay_response
        
    already_finalized = False
    try:
        check_spend_cap(tenant)

        # ── Streaming path ───────────────────────────────────────────────
        if body.stream:
            provider_request = ProviderRequest(
                model=body.model,
                messages=[m.model_dump() for m in body.messages],
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                top_p=body.top_p,
                stop=body.stop,
                stream=True,
                request_id=request_id,
            )

            async def _stream_response():
                usage_tracker = StreamUsageTracker()
                status = "completed"
                selected_provider = None
                start_time = time.monotonic()
                decision = None
                
                # We buffer the streamed chunks to save as a JSON response for replay
                buffered_response_body = {
                    "id": f"chatcmpl-{request_id}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": None}],
                    "usage": {}
                }

                try:
                    result = await db.execute(select(ProviderConfig))
                    provider_configs = result.scalars().all()
                    registry = request.app.state.provider_registry
                    redis = request.app.state.redis
                    cb_config = CircuitBreakerConfig()

                    eligible = resolve_providers(body.model, registry, provider_configs)
                    healthy = await filter_healthy(eligible, redis, cb_config)
                    sorted_eligible, scores = await score_providers(healthy, body.model, redis)

                    stream, decision = await execute_stream_with_failover(
                        sorted_eligible, provider_request, redis, cb_config, scores=scores, tenant_id=str(tenant.id)
                    )
                    selected_provider = decision.selected_provider

                    async for chunk in stream:
                        usage_tracker.update(chunk)
                        if chunk.delta_content:
                            buffered_response_body["choices"][0]["message"]["content"] += chunk.delta_content
                        if chunk.finish_reason:
                            buffered_response_body["choices"][0]["finish_reason"] = chunk.finish_reason
                        yield chunk.to_sse_event()

                    yield "data: [DONE]\n\n"

                except asyncio.CancelledError:
                    status = "partial"
                    raise
                except Exception as e:
                    from fastapi import HTTPException
                    from infrgate.exceptions import ProviderError
                    import json
                    if isinstance(e, ProviderError):
                        status = "failed"
                        err_chunk = {
                            "error": {
                                "type": e.error_type,
                                "message": e.message,
                                "code": e.status_code
                            }
                        }
                        buffered_response_body = err_chunk
                        yield f"data: {json.dumps(err_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                    elif isinstance(e, HTTPException):
                        status = "failed"
                        err_chunk = {
                            "error": {
                                "type": e.detail.get("error", {}).get("type", "api_error") if isinstance(e.detail, dict) else "api_error",
                                "message": e.detail.get("error", {}).get("message", str(e.detail)) if isinstance(e.detail, dict) else str(e.detail),
                                "code": e.status_code
                            }
                        }
                        buffered_response_body = err_chunk
                        yield f"data: {json.dumps(err_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                    else:
                        status = "failed"
                        logger.error("stream_unexpected_error", error=str(e), request_id=request_id)
                        raise

                finally:
                    total_latency_ms = int((time.monotonic() - start_time) * 1000)
                    final_usage = usage_tracker.finalize(status=status)
                    
                    if status == "completed":
                        buffered_response_body["usage"] = {
                            "prompt_tokens": final_usage["prompt_tokens"],
                            "completion_tokens": final_usage["completion_tokens"],
                            "total_tokens": final_usage["total_tokens"],
                        }

                    selected_config = None
                    if selected_provider:
                        try:
                            result2 = await db.execute(select(ProviderConfig))
                            provider_configs2 = result2.scalars().all()
                            selected_config = next((c for c in provider_configs2 if c.provider_name == selected_provider), None)
                        except Exception:
                            pass

                    cost_config = selected_config.cost_per_1k_tokens if selected_config else {}
                    cost_cents = calculate_cost_cents(
                        model=body.model,
                        prompt_tokens=final_usage["prompt_tokens"],
                        completion_tokens=final_usage["completion_tokens"],
                        cost_config=cost_config,
                    )

                    try:
                        await asyncio.shield(finalize_request(
                            db=db,
                            idempotency_key=idempotency_key,
                            request_id=request_id,
                            tenant_id=tenant.id,
                            provider=selected_provider or "unknown",
                            prompt_tokens=final_usage["prompt_tokens"],
                            completion_tokens=final_usage["completion_tokens"],
                            total_tokens=final_usage["total_tokens"],
                            cost_cents=cost_cents,
                            latency_ms=total_latency_ms,
                            response_status_code=200 if status == "completed" else 500,
                            response_body=buffered_response_body,
                            status=status,
                            metadata={
                                "finish_reason": final_usage["finish_reason"],
                                "routing_reason": decision.reason if decision else "unknown",
                                "fallback_used": decision.fallback_used if decision else False,
                            },
                        ))
                    except Exception as e:
                        logger.error("stream_usage_recording_failed", error=str(e), request_id=request_id)

                    from infrgate.metrics import TOKENS_TOTAL
                    if final_usage["prompt_tokens"]:
                        TOKENS_TOTAL.labels(tenant=tenant_id_str, type="prompt").inc(final_usage["prompt_tokens"])
                    if final_usage["completion_tokens"]:
                        TOKENS_TOTAL.labels(tenant=tenant_id_str, type="completion").inc(final_usage["completion_tokens"])

            headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Request-ID": request_id,
            }
            rate_limit = getattr(request.state, "rate_limit", None)
            if rate_limit:
                headers["X-RateLimit-Limit"] = str(rate_limit.limit)
                headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
                headers["X-RateLimit-Reset"] = str(rate_limit.reset_at)

            return StreamingResponse(
                _stream_response(),
                media_type="text/event-stream",
                headers=headers
            )

        # ── Non-streaming path ───────────────────────────────────────────
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
        sorted_eligible, scores = await score_providers(healthy, body.model, redis)

        start = time.monotonic()
        response, decision = await execute_with_failover(
            sorted_eligible, provider_request, redis, cb_config, scores=scores, tenant_id=tenant_id_str
        )
        total_latency_ms = int((time.monotonic() - start) * 1000)

        selected_config = next(
            (c for c in provider_configs if c.provider_name == decision.selected_provider), None
        )
        cost_config = selected_config.cost_per_1k_tokens if selected_config else {}
        cost_cents = calculate_cost_cents(
            model=body.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost_config=cost_config,
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

        with anyio.CancelScope(shield=True):
            await finalize_request(
                db=db,
                idempotency_key=idempotency_key,
                request_id=request_id,
                tenant_id=tenant_uuid,
                provider=decision.selected_provider,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                total_tokens=response.total_tokens,
                cost_cents=cost_cents,
                latency_ms=total_latency_ms,
                response_status_code=200,
                response_body=completion.model_dump(),
                status="completed",
                metadata={
                    "finish_reason": response.finish_reason,
                    "routing_reason": decision.reason,
                    "fallback_used": decision.fallback_used,
                },
            )
        already_finalized = True

        from infrgate.metrics import TOKENS_TOTAL
        if response.prompt_tokens:
            TOKENS_TOTAL.labels(tenant=tenant_id_str, type="prompt").inc(response.prompt_tokens)
        if response.completion_tokens:
            TOKENS_TOTAL.labels(tenant=tenant_id_str, type="completion").inc(response.completion_tokens)

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

    except asyncio.CancelledError:
        if not already_finalized:
            with anyio.CancelScope(shield=True):
                try:
                    await finalize_request(
                        db=db,
                        idempotency_key=idempotency_key,
                        request_id=request_id,
                        tenant_id=tenant_uuid,
                        provider="unknown",
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        cost_cents=0,
                        latency_ms=0,
                        response_status_code=499,
                        response_body={"error": {"message": "Request cancelled", "code": 499, "type": "error"}},
                        status="partial",
                        metadata={"error": "cancelled"}
                    )
                except Exception as finalize_err:
                    logger.error("idempotency_finalize_failed_on_cancel", error=str(finalize_err), request_id=request_id)
        raise
    except Exception as e:
        if already_finalized:
            raise
            
        status_code = getattr(e, "status_code", 500)
        err_msg = getattr(e, "detail", str(e))
        try:
            with anyio.CancelScope(shield=True):
                await finalize_request(
                    db=db,
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                    tenant_id=tenant_uuid,
                    provider="unknown",
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    cost_cents=0,
                    latency_ms=0,
                    response_status_code=status_code,
                    response_body={"error": {"message": err_msg, "code": status_code, "type": "error"}},
                    status="failed",
                    metadata={"error": str(e)}
                )
        except Exception as finalize_err:
            logger.error("idempotency_finalize_failed_on_error", error=str(finalize_err), request_id=request_id)
        raise
