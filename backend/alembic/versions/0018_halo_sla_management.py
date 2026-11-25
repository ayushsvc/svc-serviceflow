"""HaloITSM-grade SLA Management System: WorkdayCalendars, SLAHoldIntervals, SLABreachCodes, and Ticket SLA fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy import JSON

JSONB = PG_JSONB().with_variant(JSON(), "sqlite")

revision = "0018_halo_sla_management"
down_revision = "0017_attachment_source_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create workday_calendars table
    op.create_table(
        "workday_calendars",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), unique=True, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("is_24_7", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("workdays", JSONB, nullable=False),
        sa.Column("start_time", sa.String(10), nullable=False, server_default="09:00"),
        sa.Column("end_time", sa.String(10), nullable=False, server_default="17:30"),
        sa.Column("breaks", JSONB, nullable=False),
        sa.Column("holidays", JSONB, nullable=False),
        sa.Column("delayed_start_cutoff", sa.String(10), nullable=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workday_calendars_entity_id", "workday_calendars", ["entity_id"])

    # 2. Add columns to slas table
    op.add_column("slas", sa.Column("calendar_id", sa.Integer(), sa.ForeignKey("workday_calendars.id"), nullable=True))
    op.add_column("slas", sa.Column("priorities_config", JSONB, nullable=False, server_default="{}"))
    op.add_column("slas", sa.Column("warning1_pct", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("slas", sa.Column("warning2_pct", sa.Integer(), nullable=False, server_default="75"))
    op.add_column("slas", sa.Column("auto_escalate_on_breach", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("slas", sa.Column("require_breach_code", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("slas", sa.Column("reset_response_on_user_reply", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("slas", sa.Column("extend_to_eod", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index("ix_slas_calendar_id", "slas", ["calendar_id"])

    # 3. Create sla_hold_intervals table
    op.create_table(
        "sla_hold_intervals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False, server_default="on_hold"),
        sa.Column("start_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sla_hold_intervals_ticket_id", "sla_hold_intervals", ["ticket_id"])

    # 4. Create sla_breach_codes table
    op.create_table(
        "sla_breach_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sla_breach_codes_code", "sla_breach_codes", ["code"])

    # 5. Add columns to tickets table
    op.add_column("tickets", sa.Column("priority_code", sa.String(10), nullable=True))
    op.add_column("tickets", sa.Column("sla_effective_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("first_responded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("sla_matched_tier", sa.String(80), nullable=True))
    op.add_column("tickets", sa.Column("sla_matched_reason", sa.String(255), nullable=True))
    op.add_column("tickets", sa.Column("warn1_triggered", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tickets", sa.Column("warn2_triggered", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tickets", sa.Column("breach_triggered", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tickets", sa.Column("response_breach_triggered", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("tickets", sa.Column("breach_code", sa.String(50), nullable=True))
    op.add_column("tickets", sa.Column("breach_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "breach_reason")
    op.drop_column("tickets", "breach_code")
    op.drop_column("tickets", "response_breach_triggered")
    op.drop_column("tickets", "breach_triggered")
    op.drop_column("tickets", "warn2_triggered")
    op.drop_column("tickets", "warn1_triggered")
    op.drop_column("tickets", "sla_matched_reason")
    op.drop_column("tickets", "sla_matched_tier")
    op.drop_column("tickets", "first_responded_at")
    op.drop_column("tickets", "sla_effective_start_at")
    op.drop_column("tickets", "priority_code")

    op.drop_index("ix_sla_breach_codes_code", table_name="sla_breach_codes")
    op.drop_table("sla_breach_codes")

    op.drop_index("ix_sla_hold_intervals_ticket_id", table_name="sla_hold_intervals")
    op.drop_table("sla_hold_intervals")

    op.drop_index("ix_slas_calendar_id", table_name="slas")
    op.drop_column("slas", "extend_to_eod")
    op.drop_column("slas", "reset_response_on_user_reply")
    op.drop_column("slas", "require_breach_code")
    op.drop_column("slas", "auto_escalate_on_breach")
    op.drop_column("slas", "warning2_pct")
    op.drop_column("slas", "warning1_pct")
    op.drop_column("slas", "priorities_config")
    op.drop_column("slas", "calendar_id")

    op.drop_index("ix_workday_calendars_entity_id", table_name="workday_calendars")
    op.drop_table("workday_calendars")
