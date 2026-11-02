"""
Pydantic models for all SDK request and response types.

Mirrors the gateway's OpenAI-compatible schema so that every response
is fully typed and IDE-autocomplete-friendly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ─────────────────────────────────────────────────────────────────────────────
# Chat Completions
# ─────────────────────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: Literal["system", "user", "assistant"] = Field(..., description="Message role")
    content: str = Field(..., description="Message content")


class UsageInfo(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatChoice(BaseModel):
    """A single completion choice."""

    index: int
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: UsageInfo


# ─────────────────────────────────────────────────────────────────────────────
# Streaming
# ─────────────────────────────────────────────────────────────────────────────


class StreamDelta(BaseModel):
    """Delta content in a streaming chunk."""

    role: str | None = None
    content: str | None = None


class StreamChoice(BaseModel):
    """A single streaming choice."""

    index: int
    delta: StreamDelta
    finish_reason: str | None = None


class StreamChunk(BaseModel):
    """OpenAI-compatible streaming chunk."""

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]
    usage: UsageInfo | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Tenant Management (Admin)
# ─────────────────────────────────────────────────────────────────────────────


class TenantConfig(BaseModel):
    """Tenant-specific configuration overrides."""

    allowed_models: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None


class Tenant(BaseModel):
    """Tenant record returned from the gateway."""

    id: uuid.UUID
    name: str
    plan: str
    status: str
    spend_cap_cents: int | None = None
    current_spend_cents: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# API Keys (Admin)
# ─────────────────────────────────────────────────────────────────────────────


class ApiKey(BaseModel):
    """API key record (secret redacted)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None = None


class ApiKeyWithSecret(ApiKey):
    """API key including the full secret — returned only on creation."""

    key: str = Field(..., description="Full API key — shown only once at creation time")


# ─────────────────────────────────────────────────────────────────────────────
# Usage (Admin)
# ─────────────────────────────────────────────────────────────────────────────


class UsageRecord(BaseModel):
    """Single usage ledger record."""

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
    latency_ms: int | None = None
    created_at: datetime


class UsageSummaryByModel(BaseModel):
    """Usage summary for a single model."""

    requests: int
    total_tokens: int
    cost_cents: int


class UsageSummary(BaseModel):
    """Aggregated usage summary."""

    tenant_id: uuid.UUID | None = None
    period_start: datetime
    period_end: datetime
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost_cents: int
    by_model: dict[str, UsageSummaryByModel] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────────────────────


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int
