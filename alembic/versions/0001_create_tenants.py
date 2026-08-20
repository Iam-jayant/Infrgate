"""Create tenants table.

Revision ID: 0001
Revises: None
Create Date: 2026-08-20

Spec reference: 03-data-model.md §3.1
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan", sa.String(50), nullable=False, server_default="free"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("spend_cap_cents", sa.BigInteger(), nullable=True),
        sa.Column("current_spend_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("plan IN ('free', 'standard', 'enterprise')", name="chk_tenant_plan"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="chk_tenant_status"),
    )
    op.create_index("idx_tenants_status", "tenants", ["status"])
    op.create_index("idx_tenants_plan", "tenants", ["plan"])


def downgrade() -> None:
    op.drop_index("idx_tenants_plan", table_name="tenants")
    op.drop_index("idx_tenants_status", table_name="tenants")
    op.drop_table("tenants")
