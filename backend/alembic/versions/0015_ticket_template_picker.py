"""Add scoped ticket template metadata and per-user usage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_ticket_template_picker"
down_revision = "0014_workflow_and_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_templates", sa.Column("entity_id", sa.Integer(), nullable=True))
    op.add_column("ticket_templates", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column("ticket_templates", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("ticket_templates", sa.Column("show_on_portal", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("ticket_templates", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.create_foreign_key(
        "fk_ticket_templates_entity_id_entities",
        "ticket_templates",
        "entities",
        ["entity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_ticket_templates_entity_id", "ticket_templates", ["entity_id"])
    op.execute(
        """
        UPDATE ticket_templates AS template
        SET entity_id = ticket_type.entity_id
        FROM ticket_types AS ticket_type
        WHERE template.ticket_type_id = ticket_type.id
          AND ticket_type.entity_id IS NOT NULL
        """
    )
    op.create_table(
        "ticket_template_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["ticket_templates.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("entity_id", "user_id", "template_id", name="uq_ticket_template_usage_owner"),
    )
    op.create_index("ix_ticket_template_usage_entity_id", "ticket_template_usage", ["entity_id"])
    op.create_index("ix_ticket_template_usage_user_id", "ticket_template_usage", ["user_id"])
    op.create_index("ix_ticket_template_usage_template_id", "ticket_template_usage", ["template_id"])
    op.create_index(
        "ix_ticket_template_usage_recent",
        "ticket_template_usage",
        ["entity_id", "user_id", "last_used_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_template_usage_recent", table_name="ticket_template_usage")
    op.drop_index("ix_ticket_template_usage_template_id", table_name="ticket_template_usage")
    op.drop_index("ix_ticket_template_usage_user_id", table_name="ticket_template_usage")
    op.drop_index("ix_ticket_template_usage_entity_id", table_name="ticket_template_usage")
    op.drop_table("ticket_template_usage")
    op.drop_index("ix_ticket_templates_entity_id", table_name="ticket_templates")
    op.drop_constraint("fk_ticket_templates_entity_id_entities", "ticket_templates", type_="foreignkey")
    op.drop_column("ticket_templates", "sort_order")
    op.drop_column("ticket_templates", "show_on_portal")
    op.drop_column("ticket_templates", "is_active")
    op.drop_column("ticket_templates", "description")
    op.drop_column("ticket_templates", "entity_id")
