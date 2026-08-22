"""
Database model for background jobs.
Spec reference: 03-data-model.md §3.5
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
    CheckConstraint,
)
from sqlalchemy.types import JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from infrgate.db.models import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_jobs_dequeue",
            "scheduled_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # Spend-alert idempotency constraint
        Index(
            "idx_jobs_spend_alert_idempotency",
            "tenant_id",
            "job_type",
            text("(payload->>'threshold')"),
            text("(payload->>'billing_period')"),
            unique=True,
            postgresql_where=text("job_type = 'spend_alert'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'dead_letter')",
            name="job_status_check",
        ),
    )
