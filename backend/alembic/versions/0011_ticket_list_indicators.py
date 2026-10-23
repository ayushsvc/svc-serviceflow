"""Add ticket list flag indicator.

Revision ID: 0011_ticket_list_indicators
Revises: 0010_requester_field_editability
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_ticket_list_indicators"
down_revision = "0010_requester_field_editability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_tickets_flagged", "tickets", ["flagged"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tickets_flagged", table_name="tickets")
    op.drop_column("tickets", "flagged")
