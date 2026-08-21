"""
API Key schemas — request/response models for key management.

Spec reference: 02-api-design.md §3.3
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    """Request body for creating an API key."""

    name: str = Field(
        default="",
        max_length=255,
        description="Human-readable key name",
    )


class ApiKeyResponse(BaseModel):
    """API key response (secret redacted)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreateResponse(ApiKeyResponse):
    """
    API key creation response — includes the full key.

    The full key is returned ONLY on creation. It is never stored
    or retrievable again. The client must save it securely.
    """

    key: str = Field(
        ...,
        description="Full API key — shown only once at creation time",
    )
