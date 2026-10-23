"""timeline collaboration tables"""
from alembic import op
import sqlalchemy as sa

revision = "0004_timeline_collaboration"
down_revision = "0003_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("ticket_tasks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("body", sa.Text(), nullable=False), sa.Column("planned_start", sa.DateTime(timezone=True)), sa.Column("planned_end", sa.DateTime(timezone=True)), sa.Column("duration", sa.Integer(), nullable=False), sa.Column("state", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ticket_tasks_ticket_id", "ticket_tasks", ["ticket_id"])
    op.create_table("ticket_solutions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("solution_type", sa.String(40), nullable=False), sa.Column("body", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ticket_solutions_ticket_id", "ticket_solutions", ["ticket_id"])
    op.create_table("ticket_documents", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("filename", sa.String(255), nullable=False), sa.Column("path", sa.String(500), nullable=False), sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ticket_documents_ticket_id", "ticket_documents", ["ticket_id"])
    op.create_table("ticket_links", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("linked_ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False), sa.Column("link_type", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ticket_links_ticket_id", "ticket_links", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("ticket_links")
    op.drop_table("ticket_documents")
    op.drop_table("ticket_solutions")
    op.drop_table("ticket_tasks")
