"""
API Key model — prefix + hash key storage with revocation support.

Keys use a prefix-based lookup scheme for O(1) authentication:
  1. Extract prefix from ``sk-infr_<prefix>.<secret>``
  2. Look up row by prefix (partial index on active keys)
  3. Verify SHA-256 hash of the full key

Spec reference: 03-data-model.md §3.2, 04-authentication-tenancy.md §2
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrgate.db.models import Base, UUIDPrimaryKeyMixin


class ApiKey(Base, UUIDPrimaryKeyMixin):
    """API key with prefix-based lookup and SHA-256 hash verification."""

    __tablename__ = "api_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    prefix: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────
    tenant = relationship("Tenant", back_populates="api_keys")

    # ── Indexes ───────────────────────────────────────────────────────────
    __table_args__ = (
        Index(
            "idx_api_keys_prefix_active",
            "prefix",
            postgresql_where=(revoked_at.is_(None)),
        ),
        Index("idx_api_keys_tenant", "tenant_id"),
    )
