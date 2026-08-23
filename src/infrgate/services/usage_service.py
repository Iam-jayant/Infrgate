"""
Usage service — durable usage recording with idempotency.

Records every inference in the usage_ledger table. Uses
ON CONFLICT (request_id) DO NOTHING for at-most-once recording.
Updates tenant spend only if the record was actually inserted.

Spec reference: 10-usage-accounting.md §3, §5, §6
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.db.models.tenant import Tenant
from infrgate.db.models.usage_ledger import UsageLedger
from infrgate.providers.base import ProviderResponse

logger = structlog.get_logger()


def estimate_prompt_tokens(messages: list[dict]) -> int:
    """
    Rough token estimation from message content.

    Average English: ~4 characters per token.
    This is a fallback for TPM rate limiting (before provider call).
    """
    total_chars = sum(len(m.get("content", "")) for m in messages)
    overhead = len(messages) * 4  # Per-message overhead
    return max(1, (total_chars + overhead) // 4)


def calculate_cost_cents(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_config: dict[str, dict[str, float]],
) -> int:
    """
    Calculate cost in cents from token counts and per-model rates.

    Cost = (prompt_tokens × prompt_rate / 1000) + (completion_tokens × completion_rate / 1000)
    """
    rates = cost_config.get(model, {"prompt": 0.0, "completion": 0.0})
    cost = (
        prompt_tokens * rates["prompt"] / 1000
        + completion_tokens * rates["completion"] / 1000
    )
    return int(cost)


async def record_usage(
    db: AsyncSession,
    request_id: str,
    tenant_id: uuid.UUID,
    model: str,
    provider: str,
    response: ProviderResponse,
    latency_ms: int,
    cost_config: dict[str, dict[str, float]],
    status: str = "completed",
    metadata: dict | None = None,
) -> None:
    """
    Record a usage entry in the ledger. Idempotent on request_id.

    Also updates the tenant's current_spend_cents, but only if the
    record was actually inserted (not a duplicate).
    """
    cost_cents = calculate_cost_cents(
        model=model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost_config=cost_config,
    )

    if db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    stmt = (
        insert(UsageLedger)
        .values(
            id=uuid.uuid4(),
            request_id=uuid.UUID(request_id) if isinstance(request_id, str) else request_id,
            tenant_id=tenant_id,
            model=model,
            provider=provider,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            cost_cents=cost_cents,
            status=status,
            latency_ms=latency_ms,
            metadata_=metadata or {},
        )
        .on_conflict_do_nothing(index_elements=["request_id"])
    )

    result = await db.execute(stmt)

    if result.rowcount > 0:
        update_result = await db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                current_spend_cents=Tenant.current_spend_cents + cost_cents,
                updated_at=func.now(),
            )
            .returning(Tenant.spend_cap_cents, Tenant.current_spend_cents)
        )
        row = update_result.first()
        if row:
            spend_cap_cents = row[0]
            new_spend = row[1]
            old_spend = new_spend - cost_cents
            
            if spend_cap_cents and spend_cap_cents > 0:
                old_pct = (old_spend / spend_cap_cents) * 100
                new_pct = (new_spend / spend_cap_cents) * 100
                
                thresholds = [50, 75, 90, 100]
                crossed_thresholds = [t for t in thresholds if old_pct < t <= new_pct]
                
                if crossed_thresholds:
                    from infrgate.services.job_service import enqueue_spend_alert
                    import datetime
                    
                    billing_period = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
                    
                    for t in crossed_thresholds:
                        await enqueue_spend_alert(
                            session=db,
                            tenant_id=tenant_id,
                            threshold=str(t),
                            billing_period=billing_period
                        )

        logger.info(
            "usage_recorded",
            request_id=request_id,
            tenant_id=str(tenant_id),
            model=model,
            provider=provider,
            total_tokens=response.total_tokens,
            cost_cents=cost_cents,
        )

    await db.commit()
