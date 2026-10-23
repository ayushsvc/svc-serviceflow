"""security hardening audit and soft deletes"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_hardening"
down_revision = "0005_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tickets_deleted_at", "tickets", ["deleted_at"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("diff", postgresql.JSONB(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ["entity_id", "action", "actor_id", "ts"]:
        op.create_index(f"ix_audit_log_{column}", "audit_log", [column])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_index("ix_tickets_deleted_at", table_name="tickets")
    op.drop_column("tickets", "deleted_at")
