"""
Usage Ledger model — one row per inference, idempotent on request_id.

The UNIQUE constraint on request_id ensures at-most-once recording.
INSERT uses ON CONFLICT (request_id) DO NOTHING for idempotency.

Spec reference: 03-data-model.md §3.3, 10-usage-accounting.md
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from infrgate.db.models import Base, UUIDPrimaryKeyMixin

from sqlalchemy import ForeignKey


class UsageLedger(Base, UUIDPrimaryKeyMixin):
    """Durable record of every inference request for usage accounting."""

    __tablename__ = "usage_ledger"

    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="completed")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    tenant = relationship("Tenant", back_populates="usage_records")

    # ── Constraints & Indexes ─────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed', 'partial')",
            name="chk_usage_status",
        ),
        Index("idx_usage_tenant_created", "tenant_id", created_at.desc()),
        Index("idx_usage_model", "model"),
        Index("idx_usage_created", created_at.desc()),
    )
