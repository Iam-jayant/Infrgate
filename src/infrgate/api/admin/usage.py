"""
Usage query endpoints.

Spec reference: 02-api-design.md §3.4
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrgate.auth.dependencies import verify_admin
from infrgate.db.engine import get_db
from infrgate.db.models.usage_ledger import UsageLedger
from infrgate.schemas.common import PaginatedResponse, PaginationParams
from infrgate.schemas.usage import UsageRecordResponse, UsageSummaryByModel, UsageSummaryResponse

router = APIRouter(dependencies=[Depends(verify_admin)])


@router.get("/usage", response_model=PaginatedResponse[UsageRecordResponse])
async def query_usage(
    pagination: PaginationParams = Depends(),
    tenant_id: uuid.UUID | None = Query(default=None),
    model: str | None = Query(default=None),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Query usage records with filters and pagination."""
    query = select(UsageLedger)
    count_query = select(func.count()).select_from(UsageLedger)

    if tenant_id:
        query = query.where(UsageLedger.tenant_id == tenant_id)
        count_query = count_query.where(UsageLedger.tenant_id == tenant_id)
    if model:
        query = query.where(UsageLedger.model == model)
        count_query = count_query.where(UsageLedger.model == model)
    if start_date:
        query = query.where(UsageLedger.created_at >= start_date)
        count_query = count_query.where(UsageLedger.created_at >= start_date)
    if end_date:
        query = query.where(UsageLedger.created_at <= end_date)
        count_query = count_query.where(UsageLedger.created_at <= end_date)

    if not start_date and not end_date:
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        query = query.where(UsageLedger.created_at >= thirty_days_ago)
        count_query = count_query.where(UsageLedger.created_at >= thirty_days_ago)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(UsageLedger.created_at.desc()).limit(pagination.limit).offset(pagination.offset)
    result = await db.execute(query)
    records = list(result.scalars().all())

    return PaginatedResponse(
        items=[UsageRecordResponse.model_validate(r) for r in records],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    tenant_id: uuid.UUID | None = Query(default=None),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated usage summary with per-model breakdown."""
    now = datetime.now(timezone.utc)
    period_start = start_date or (now - timedelta(days=30))
    period_end = end_date or now

    query = select(UsageLedger).where(
        UsageLedger.created_at >= period_start,
        UsageLedger.created_at <= period_end,
    )
    if tenant_id:
        query = query.where(UsageLedger.tenant_id == tenant_id)

    result = await db.execute(query)
    records = list(result.scalars().all())

    total_requests = len(records)
    total_prompt = sum(r.prompt_tokens for r in records)
    total_completion = sum(r.completion_tokens for r in records)
    total_tokens = sum(r.total_tokens for r in records)
    total_cost = sum(r.cost_cents for r in records)

    by_model: dict[str, UsageSummaryByModel] = {}
    for record in records:
        if record.model not in by_model:
            by_model[record.model] = UsageSummaryByModel(requests=0, total_tokens=0, cost_cents=0)
        entry = by_model[record.model]
        by_model[record.model] = UsageSummaryByModel(
            requests=entry.requests + 1,
            total_tokens=entry.total_tokens + record.total_tokens,
            cost_cents=entry.cost_cents + record.cost_cents,
        )

    return UsageSummaryResponse(
        tenant_id=tenant_id,
        period_start=period_start,
        period_end=period_end,
        total_requests=total_requests,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        total_cost_cents=total_cost,
        by_model=by_model,
    )
