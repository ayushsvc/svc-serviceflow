"""Add user-scoped ticket list profiles.

Revision ID: 0012_list_profiles
Revises: 0011_ticket_list_indicators
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_list_profiles"
down_revision = "0011_ticket_list_indicators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "list_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('filter', 'column')", name="ck_list_profiles_kind"),
    )
    op.create_index("ix_list_profiles_user_id", "list_profiles", ["user_id"])
    op.create_index("ix_list_profiles_entity_id", "list_profiles", ["entity_id"])
    op.create_index("ix_list_profiles_is_default", "list_profiles", ["is_default"])
    op.create_index(
        "uq_list_profiles_default",
        "list_profiles",
        ["user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_list_profiles_default", table_name="list_profiles")
    op.drop_index("ix_list_profiles_is_default", table_name="list_profiles")
    op.drop_index("ix_list_profiles_entity_id", table_name="list_profiles")
    op.drop_index("ix_list_profiles_user_id", table_name="list_profiles")
    op.drop_table("list_profiles")
