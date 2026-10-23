"""sla and automation tables"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_automation"
down_revision = "0004_timeline_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("slas", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("tto_minutes", sa.Integer(), nullable=False), sa.Column("ttr_minutes", sa.Integer(), nullable=False), sa.Column("calendar", postgresql.JSONB(), nullable=False), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False))
    op.create_index("ix_slas_entity_id", "slas", ["entity_id"])
    op.create_table("sla_escalations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sla_id", sa.Integer(), sa.ForeignKey("slas.id", ondelete="CASCADE"), nullable=False), sa.Column("trigger_pct", sa.Integer(), nullable=False), sa.Column("action", postgresql.JSONB(), nullable=False))
    op.create_index("ix_sla_escalations_sla_id", "sla_escalations", ["sla_id"])
    op.create_table("business_rules", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("trigger", sa.String(30), nullable=False), sa.Column("conditions", postgresql.JSONB(), nullable=False), sa.Column("actions", postgresql.JSONB(), nullable=False), sa.Column("order", sa.Integer(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False))
    op.create_index("ix_business_rules_entity_id", "business_rules", ["entity_id"])
    op.create_table("notification_templates", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("event", sa.String(60), nullable=False), sa.Column("subject", sa.String(255), nullable=False), sa.Column("body_jinja", sa.Text(), nullable=False), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False))
    op.create_index("ix_notification_templates_event", "notification_templates", ["event"])
    op.create_index("ix_notification_templates_entity_id", "notification_templates", ["entity_id"])
    op.create_table("recurring_tickets", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("title", sa.String(240), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("cron_expr", sa.String(100), nullable=False), sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False), sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False), sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("urgency", sa.Integer(), nullable=False), sa.Column("impact", sa.Integer(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False))
    op.create_index("ix_recurring_tickets_next_run_at", "recurring_tickets", ["next_run_at"])
    op.create_index("ix_recurring_tickets_entity_id", "recurring_tickets", ["entity_id"])
    op.add_column("tickets", sa.Column("tto_due_at", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("ttr_due_at", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("sla_id", sa.Integer(), sa.ForeignKey("slas.id")))
    op.create_index("ix_tickets_tto_due_at", "tickets", ["tto_due_at"])
    op.create_index("ix_tickets_ttr_due_at", "tickets", ["ttr_due_at"])


def downgrade() -> None:
    op.drop_column("tickets", "sla_id")
    op.drop_column("tickets", "ttr_due_at")
    op.drop_column("tickets", "tto_due_at")
    op.drop_table("recurring_tickets")
    op.drop_table("notification_templates")
    op.drop_table("business_rules")
    op.drop_table("sla_escalations")
    op.drop_table("slas")
