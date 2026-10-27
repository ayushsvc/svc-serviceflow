"""Add attachment metadata and soft delete columns to ticket documents."""

from alembic import op
import sqlalchemy as sa

revision = "0016_attachment_soft_delete"
down_revision = "0015_ticket_template_picker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_documents", sa.Column("content_type", sa.String(length=120), nullable=True))
    op.add_column("ticket_documents", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("ticket_documents", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ticket_documents", sa.Column("removed_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_ticket_documents_removed_by_users",
        "ticket_documents",
        "users",
        ["removed_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_ticket_documents_removed_by_users", "ticket_documents", type_="foreignkey")
    op.drop_column("ticket_documents", "removed_by")
    op.drop_column("ticket_documents", "removed_at")
    op.drop_column("ticket_documents", "size_bytes")
    op.drop_column("ticket_documents", "content_type")
