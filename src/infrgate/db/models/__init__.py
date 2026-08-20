"""
SQLAlchemy declarative base and model registry.

All models must be imported here so Alembic can discover them
for autogenerate migrations.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all InfrGate models."""

    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Mixin that adds a UUID primary key column."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )


# ── Import all models so Alembic can discover them ───────────────────────
from infrgate.db.models.api_key import ApiKey  # noqa: E402, F401
from infrgate.db.models.tenant import Tenant  # noqa: E402, F401
from infrgate.db.models.usage_ledger import UsageLedger  # noqa: E402, F401
from infrgate.db.models.provider_config import ProviderConfig  # noqa: E402, F401
