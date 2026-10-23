"""ticket core tables"""
from alembic import op
import sqlalchemy as sa

revision = "0002_ticket_core"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("categories", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id")), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False))
    op.create_index("ix_categories_name", "categories", ["name"])
    op.create_index("ix_categories_entity_id", "categories", ["entity_id"])
    op.create_table("tickets",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("title", sa.String(240), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("urgency", sa.Integer(), nullable=False),
        sa.Column("impact", sa.Integer(), nullable=False), sa.Column("priority", sa.Integer(), nullable=False), sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False), sa.Column("source", sa.String(30), nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("solved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("action_time_seconds", sa.Integer(), nullable=False), sa.Column("cost", sa.Integer(), nullable=False))
    for column in ("status", "priority", "entity_id", "assignee_id"):
        op.create_index(f"ix_tickets_{column}", "tickets", [column])
    op.create_index("ix_tickets_opened_at", "tickets", ["opened_at"])
    op.create_table("ticket_actors", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("actor_type", sa.String(20), nullable=False), sa.Column("actor_id", sa.Integer(), nullable=False), sa.Column("role", sa.String(20), nullable=False))
    op.create_index("ix_ticket_actors_ticket_id", "ticket_actors", ["ticket_id"])
    op.create_table("ticket_followups", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("is_private", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ticket_followups_ticket_id", "ticket_followups", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("ticket_followups")
    op.drop_table("ticket_actors")
    op.drop_table("tickets")
    op.drop_table("categories")
