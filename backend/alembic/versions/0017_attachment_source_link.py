"""Link ticket documents to the followup or task they were attached to."""

from alembic import op
import sqlalchemy as sa

revision = "0017_attachment_source_link"
down_revision = "0016_attachment_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_documents", sa.Column("followup_id", sa.Integer(), nullable=True))
    op.add_column("ticket_documents", sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ticket_documents_followup_id", "ticket_documents", "ticket_followups", ["followup_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "fk_ticket_documents_task_id", "ticket_documents", "ticket_tasks", ["task_id"], ["id"], ondelete="CASCADE"
    )
    op.create_check_constraint(
        "ck_ticket_documents_single_source", "ticket_documents", "followup_id IS NULL OR task_id IS NULL"
    )
    op.create_index("ix_ticket_documents_followup_id", "ticket_documents", ["followup_id"])
    op.create_index("ix_ticket_documents_task_id", "ticket_documents", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_documents_task_id", table_name="ticket_documents")
    op.drop_index("ix_ticket_documents_followup_id", table_name="ticket_documents")
    op.drop_constraint("ck_ticket_documents_single_source", "ticket_documents", type_="check")
    op.drop_constraint("fk_ticket_documents_task_id", "ticket_documents", type_="foreignkey")
    op.drop_constraint("fk_ticket_documents_followup_id", "ticket_documents", type_="foreignkey")
    op.drop_column("ticket_documents", "task_id")
    op.drop_column("ticket_documents", "followup_id")
