"""
Usage schemas — request/response models for usage queries.

Spec reference: 02-api-design.md §3.4
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UsageRecordResponse(BaseModel):
    """Single usage ledger record."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    request_id: uuid.UUID
    tenant_id: uuid.UUID
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: int
    status: str
    latency_ms: int | None
    created_at: datetime


class UsageSummaryByModel(BaseModel):
    """Usage summary for a single model."""

    requests: int
    total_tokens: int
    cost_cents: int


class UsageSummaryResponse(BaseModel):
    """Aggregated usage summary."""

    tenant_id: uuid.UUID | None
    period_start: datetime
    period_end: datetime
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost_cents: int
    by_model: dict[str, UsageSummaryByModel]
