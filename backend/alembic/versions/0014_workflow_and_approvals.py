"""Add tenant workflows and approval records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_workflow_and_approvals"
down_revision = "0013_list_profile_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_type", sa.String(length=40), nullable=False),
        sa.Column("states", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("transitions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("approval_requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entity_id", "record_type", name="uq_ticket_workflows_entity_type"),
    )
    op.create_index("ix_ticket_workflows_entity_id", "ticket_workflows", ["entity_id"])

    op.create_table(
        "workflow_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_type", sa.String(length=40), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("transition_to", sa.String(length=40), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approver_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("approver_group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(approver_user_id IS NOT NULL AND approver_group_id IS NULL) OR "
            "(approver_user_id IS NULL AND approver_group_id IS NOT NULL)",
            name="ck_workflow_approvals_single_approver",
        ),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_workflow_approvals_status"),
    )
    op.create_index("ix_workflow_approvals_entity_id", "workflow_approvals", ["entity_id"])
    op.create_index(
        "ix_workflow_approvals_record_status",
        "workflow_approvals",
        ["entity_id", "record_type", "record_id", "transition_to", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_approvals_record_status", table_name="workflow_approvals")
    op.drop_index("ix_workflow_approvals_entity_id", table_name="workflow_approvals")
    op.drop_table("workflow_approvals")
    op.drop_index("ix_ticket_workflows_entity_id", table_name="ticket_workflows")
    op.drop_table("ticket_workflows")
