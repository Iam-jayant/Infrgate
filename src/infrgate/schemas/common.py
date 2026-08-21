"""
Common schemas — error envelope, pagination, and shared types.

Spec reference: 02-api-design.md §4, §5
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")


# ── Error response ────────────────────────────────────────────────────────


class ErrorDetail(BaseModel):
    """Error detail within the error envelope."""

    type: str
    message: str
    request_id: str | None = None
    code: int


class ErrorResponse(BaseModel):
    """Standard error response envelope per spec §4.1."""

    error: ErrorDetail


# ── Pagination ────────────────────────────────────────────────────────────


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response envelope per spec §5."""

    items: list[T]
    total: int
    limit: int
    offset: int


class PaginationParams:
    """FastAPI dependency for pagination query parameters."""

    def __init__(
        self,
        limit: int = Query(default=50, ge=1, le=100, description="Page size (max 100)"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ):
        self.limit = limit
        self.offset = offset
