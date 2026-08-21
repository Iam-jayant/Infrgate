"""
Tenant schemas — request/response models for tenant management.

Spec reference: 02-api-design.md §3.2
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TenantConfigSchema(BaseModel):
    """Tenant-specific configuration overrides."""

    allowed_models: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None


class TenantCreateRequest(BaseModel):
    """Request body for creating a new tenant."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable tenant name")
    plan: str = Field(
        default="free",
        pattern="^(free|standard|enterprise)$",
        description="Plan tier",
    )
    spend_cap_cents: int | None = Field(
        default=None,
        ge=0,
        description="Monthly spend cap in cents (null = unlimited)",
    )
    config: TenantConfigSchema = Field(
        default_factory=TenantConfigSchema,
        description="Optional configuration overrides",
    )


class TenantUpdateRequest(BaseModel):
    """Request body for partially updating a tenant."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    plan: str | None = Field(default=None, pattern="^(free|standard|enterprise)$")
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    spend_cap_cents: int | None = Field(default=None, ge=0)
    config: TenantConfigSchema | None = None


class TenantResponse(BaseModel):
    """Tenant response representation."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    plan: str
    status: str
    spend_cap_cents: int | None
    current_spend_cents: int
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
