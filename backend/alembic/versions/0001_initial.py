"""initial identity and tenancy tables"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("entities", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("entities.id")))
    op.create_index("ix_entities_name", "entities", ["name"], unique=True)
    op.create_table("profiles", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(80), nullable=False), sa.Column("interface", sa.String(30), nullable=False), sa.Column("rights", postgresql.JSONB(), nullable=False))
    op.create_index("ix_profiles_name", "profiles", ["name"], unique=True)
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("email", sa.String(320), nullable=False), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("name", sa.String(160), nullable=False), sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_entity_id", "users", ["entity_id"])
    op.create_index("ix_users_is_active", "users", ["is_active"])


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("profiles")
    op.drop_table("entities")
