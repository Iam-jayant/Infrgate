"""
Tenant model — multi-tenant identity, plan, spend cap, and status.

Spec reference: 03-data-model.md §3.1
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from infrgate.db.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Multi-tenant identity with plan configuration and spend tracking."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, server_default="free")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    spend_cap_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_spend_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    config: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, server_default="{}")

    # ── Relationships ─────────────────────────────────────────────────────
    api_keys = relationship("ApiKey", back_populates="tenant", cascade="all, delete-orphan")
    usage_records = relationship("UsageLedger", back_populates="tenant")

    # ── Constraints ───────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "plan IN ('free', 'standard', 'enterprise')",
            name="chk_tenant_plan",
        ),
        CheckConstraint(
            "status IN ('active', 'suspended')",
            name="chk_tenant_status",
        ),
        Index("idx_tenants_status", "status"),
        Index("idx_tenants_plan", "plan"),
    )
