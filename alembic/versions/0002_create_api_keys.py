"""Create api_keys table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20

Spec reference: 03-data-model.md §3.2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("prefix", sa.String(30), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_api_keys_tenant", "api_keys", ["tenant_id"])
    # Partial index: only active (non-revoked) keys for fast auth lookups
    op.create_index(
        "idx_api_keys_prefix_active",
        "api_keys",
        ["prefix"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_api_keys_prefix_active", table_name="api_keys")
    op.drop_index("idx_api_keys_tenant", table_name="api_keys")
    op.drop_table("api_keys")
