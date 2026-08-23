"""
Idempotency service — claim-first pattern for exactly-once provider calls.

The key insight: deduplication must happen BEFORE the upstream provider call,
not after. If we call the provider first and deduplicate the ledger second,
two concurrent duplicate requests both hit the provider and both get billed
upstream — the ON CONFLICT only protects our bookkeeping, not our wallet.

Flow:
  1. Client sends Idempotency-Key header.
  2. claim_or_replay_request() inserts a 'pending' row into usage_ledger.
     - If INSERT succeeds → proceed to call the provider.
     - If INSERT conflicts (duplicate key for this tenant) → we read the existing row:
        - If 'pending' → raise 409 Conflict (mid-flight duplicate).
        - If 'completed'/'failed' → return the cached response (Replay).
  3. After provider returns, finalize_request() updates the pending row
     with actual token counts, cost, status, and the JSON response.

The unique constraint is (tenant_id, idempotency_key) — scoped per tenant.
"""

from __future__ import annotations

import uuid
from typing import Any, Tuple, Optional

import structlog
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
import datetime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.db.models.usage_ledger import UsageLedger

logger = structlog.get_logger()


async def claim_or_replay_request(
    db: AsyncSession,
    idempotency_key: str | None,
    tenant_id: uuid.UUID,
    request_id: str,
    model: str,
) -> JSONResponse | None:
    """
    Attempt to claim a ledger slot for this (tenant, idempotency_key).
    
    If no idempotency_key is provided, we just insert and proceed (no dedup).
    
    Returns None if the claim succeeded (proceed to call provider).
    Returns JSONResponse if a completed request was found (Replay).
    Raises HTTPException(409) if the request is still pending.
    """
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    stmt = (
        insert(UsageLedger)
        .values(
            id=uuid.uuid4(),
            request_id=uuid.UUID(request_id) if isinstance(request_id, str) else request_id,
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            model=model,
            provider="pending",
            status="pending",
        )
    )

    if idempotency_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
        
    try:
        result = await db.execute(stmt)
        await db.flush()
    except IntegrityError:
        # Fallback for SQLite which might not support ON CONFLICT DO NOTHING in all environments
        await db.rollback()
        result = None

    claimed = result is not None and result.rowcount > 0

    if claimed:
        logger.info(
            "idempotency_claimed",
            idempotency_key=idempotency_key,
            tenant_id=str(tenant_id),
            request_id=request_id,
        )
        return None

    # If we get here, the key exists. We must fetch the existing row to see its status.
    existing_stmt = select(UsageLedger).where(
        UsageLedger.tenant_id == tenant_id,
        UsageLedger.idempotency_key == idempotency_key,
    )
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()

    if not existing:
        # Race condition: row was inserted, but we can't read it?
        raise HTTPException(status_code=500, detail="Idempotency conflict but row missing")

    if existing.status == "pending":
        LEASE_WINDOW_SECONDS = 90
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Check if the lease has expired
        if existing.claimed_at and existing.claimed_at.tzinfo is None:
            existing_claimed_at = existing.claimed_at.replace(tzinfo=datetime.timezone.utc)
        else:
            existing_claimed_at = existing.claimed_at
            
        if existing_claimed_at and (now - existing_claimed_at).total_seconds() > LEASE_WINDOW_SECONDS:
            # Attempt CAS reclaim
            req_uuid = uuid.UUID(request_id) if isinstance(request_id, str) else request_id
            cas_stmt = (
                update(UsageLedger)
                .where(
                    UsageLedger.tenant_id == tenant_id,
                    UsageLedger.idempotency_key == idempotency_key,
                    UsageLedger.request_id == existing.request_id,
                    UsageLedger.status == "pending"
                )
                .values(
                    request_id=req_uuid,
                    claimed_at=now
                )
            )
            cas_res = await db.execute(cas_stmt)
            await db.flush()
            
            if cas_res.rowcount > 0:
                logger.info(
                    "idempotency_reclaimed_expired_lease",
                    idempotency_key=idempotency_key,
                    tenant_id=str(tenant_id),
                    new_request_id=request_id,
                    old_request_id=str(existing.request_id)
                )
                return None
            else:
                logger.warning(
                    "idempotency_reclaim_cas_conflict",
                    idempotency_key=idempotency_key,
                    tenant_id=str(tenant_id)
                )

        logger.info(
            "idempotency_conflict_pending",
            idempotency_key=idempotency_key,
            tenant_id=str(tenant_id),
        )
        raise HTTPException(
            status_code=409,
            detail=f"A request with Idempotency-Key '{idempotency_key}' is currently processing."
        )

    # REPLAY logic
    logger.info(
        "idempotency_replayed",
        idempotency_key=idempotency_key,
        tenant_id=str(tenant_id),
        original_status_code=existing.response_status_code,
    )
    
    # If for some reason response_status_code is missing, default to 200
    status_code = existing.response_status_code or 200
    body = existing.response_body or {}
    
    return JSONResponse(content=body, status_code=status_code)


async def finalize_request(
    db: AsyncSession,
    idempotency_key: str | None,
    request_id: str,
    tenant_id: uuid.UUID,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_cents: int,
    latency_ms: int,
    response_status_code: int,
    response_body: dict[str, Any],
    status: str = "completed",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Update the pending ledger row with actual usage data and response payload.
    Also updates tenant spend atomically.
    """
    from sqlalchemy import func
    from infrgate.db.models.tenant import Tenant

    req_uuid = uuid.UUID(request_id) if isinstance(request_id, str) else request_id
    where_clause = [
        UsageLedger.tenant_id == tenant_id,
        UsageLedger.request_id == req_uuid,
        UsageLedger.status == "pending"
    ]
    if idempotency_key is not None:
        where_clause.append(UsageLedger.idempotency_key == idempotency_key)

    update_res = await db.execute(
        update(UsageLedger)
        .where(*where_clause)
        .values(
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_cents=cost_cents,
            latency_ms=latency_ms,
            response_status_code=response_status_code,
            response_body=response_body,
            status=status,
            metadata_=metadata or {},
        )
    )

    if update_res.rowcount == 0:
        # CAS failed! We lost the race, our row was reclaimed (or we are finalizing twice)
        from infrgate.metrics import IDEMPOTENCY_CAS_CONFLICTS
        IDEMPOTENCY_CAS_CONFLICTS.inc()
        logger.warning(
            "idempotency_finalize_cas_conflict",
            idempotency_key=idempotency_key,
            tenant_id=str(tenant_id),
            request_id=request_id,
            msg="Failed to finalize pending idempotency row - it was likely reclaimed by a concurrent request due to lease expiration."
        )
        # We must NOT update tenant spend if we failed to finalize the usage ledger row!
        await db.rollback()
        return

    # Update tenant spend
    if cost_cents > 0:
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
                            billing_period=billing_period,
                        )

    await db.commit()

    logger.info(
        "idempotency_finalized",
        idempotency_key=idempotency_key,
        tenant_id=str(tenant_id),
        provider=provider,
        total_tokens=total_tokens,
        cost_cents=cost_cents,
        status=status,
    )
