"""Create usage_ledger table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-20

Spec reference: 03-data-model.md §3.3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="completed"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('completed', 'failed', 'partial')", name="chk_usage_status"),
    )
    op.create_index("idx_usage_tenant_created", "usage_ledger", ["tenant_id", sa.text("created_at DESC")])
    op.create_index("idx_usage_model", "usage_ledger", ["model"])
    op.create_index("idx_usage_created", "usage_ledger", [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_usage_created", table_name="usage_ledger")
    op.drop_index("idx_usage_model", table_name="usage_ledger")
    op.drop_index("idx_usage_tenant_created", table_name="usage_ledger")
    op.drop_table("usage_ledger")
