"""add configurable ticket fields, types, templates, and category restrictions"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_configurable_fields"
down_revision = "0007_ticket_custom_fields"
branch_labels = None
depends_on = None


FIELD_DEFINITIONS = [
    ("summary", "Summary", "text"),
    ("details", "Details", "textarea"),
    ("user", "User", "user"),
    ("organisation", "Organisation", "text"),
    ("category", "Category", "category"),
    ("urgency", "Urgency", "number"),
    ("impact", "Impact", "number"),
    ("priority", "Priority", "number"),
    ("team", "Team", "team"),
    ("agent", "Agent", "agent"),
    ("source", "Source", "select"),
    ("asset", "Asset", "asset"),
    ("sla", "SLA", "number"),
    ("due_date", "Due date", "datetime"),
    ("start_date", "Start date", "datetime"),
    ("followup_date", "Follow-up date", "datetime"),
    ("time_estimate", "Time estimate", "number"),
    ("tags", "Tags", "multiselect"),
    ("cc", "CC", "multiselect"),
    ("parent_ticket", "Parent ticket", "number"),
    ("related_tickets", "Related tickets", "multiselect"),
    ("flag", "Flag", "checkbox"),
]

TYPE_FIELDS = {
    "Incident": ["summary", "details", "user", "organisation", "category", "urgency", "impact", "priority", "team", "agent", "source", "asset", "sla", "due_date", "start_date", "followup_date", "time_estimate", "tags", "cc", "parent_ticket", "related_tickets", "flag"],
    "Service Request": ["summary", "details", "user", "organisation", "category", "urgency", "impact", "priority", "team", "agent", "source", "asset", "sla", "due_date", "start_date", "followup_date", "time_estimate", "tags", "cc", "parent_ticket", "related_tickets", "flag"],
    "Problem": ["summary", "details", "user", "organisation", "category", "urgency", "impact", "priority", "team", "agent", "source", "asset", "sla", "due_date", "start_date", "followup_date", "tags", "related_tickets", "flag"],
    "Change": ["summary", "details", "user", "organisation", "category", "urgency", "impact", "priority", "team", "agent", "source", "asset", "sla", "due_date", "start_date", "followup_date", "time_estimate", "tags", "cc", "parent_ticket", "related_tickets", "flag"],
}


def upgrade() -> None:
    op.add_column("categories", sa.Column("level", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_categories_level", "categories", "level BETWEEN 1 AND 4")
    connection = op.get_bind()
    for _ in range(3):
        connection.execute(sa.text("""
            UPDATE categories AS child
            SET level = parent.level + 1
            FROM categories AS parent
            WHERE child.parent_id = parent.id
              AND parent.level < 4
        """))
    op.create_table(
        "category_teams",
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "field_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False, unique=True),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("validation", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_field_definitions_key", "field_definitions", ["key"])
    op.create_table(
        "ticket_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("itil_class", sa.String(20), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column("default_team_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column("default_priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("default_sla_id", sa.Integer(), sa.ForeignKey("slas.id"), nullable=True),
        sa.Column("icon", sa.String(40), nullable=True),
        sa.Column("color", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "ticket_type_fields",
        sa.Column("ticket_type_id", sa.Integer(), sa.ForeignKey("ticket_types.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("field_definition_id", sa.Integer(), sa.ForeignKey("field_definitions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page", sa.String(40), nullable=False, server_default="details"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("visible_to_roles", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("editable_by_roles", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("default_value", postgresql.JSONB(), nullable=True),
        sa.Column("show_on_portal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_condition", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "ticket_field_values",
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("field_definition_id", sa.Integer(), sa.ForeignKey("field_definitions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "ticket_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("ticket_type_id", sa.Integer(), sa.ForeignKey("ticket_types.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True),
        sa.Column("prefilled", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("tickets", sa.Column("ticket_type_id", sa.Integer(), sa.ForeignKey("ticket_types.id"), nullable=True))
    op.create_index("ix_tickets_ticket_type_id", "tickets", ["ticket_type_id"])

    for key, label, field_type in FIELD_DEFINITIONS:
        connection.execute(sa.text("""
            INSERT INTO field_definitions ("key", label, type, is_system)
            VALUES (:key, :label, :field_type, TRUE)
            ON CONFLICT ("key") DO NOTHING
        """), {"key": key, "label": label, "field_type": field_type})
    for name, itil_class, color in [
        ("Incident", "INCIDENT", "#4f46e5"),
        ("Service Request", "REQUEST", "#0f766e"),
        ("Problem", "PROBLEM", "#b45309"),
        ("Change", "CHANGE", "#7c3aed"),
    ]:
        connection.execute(sa.text("""
            INSERT INTO ticket_types (name, itil_class, default_priority, icon, color)
            VALUES (:name, :itil_class, 3, 'ticket', :color)
            ON CONFLICT (name) DO NOTHING
        """), {"name": name, "itil_class": itil_class, "color": color})
    for type_name, keys in TYPE_FIELDS.items():
        type_id = connection.execute(sa.text("SELECT id FROM ticket_types WHERE name = :name"), {"name": type_name}).scalar_one()
        for position, key in enumerate(keys, start=1):
            field_id = connection.execute(sa.text('SELECT id FROM field_definitions WHERE "key" = :key'), {"key": key}).scalar_one()
            connection.execute(sa.text("""
                INSERT INTO ticket_type_fields
                    (ticket_type_id, field_definition_id, "order", page, required, visible_to_roles, editable_by_roles)
                VALUES (:type_id, :field_id, :position, 'details', :required, '["Administrator","Technician","Requester"]'::jsonb, '["Administrator","Technician"]'::jsonb)
                ON CONFLICT (ticket_type_id, field_definition_id) DO NOTHING
            """), {"type_id": type_id, "field_id": field_id, "position": position, "required": key in {"summary", "details", "user"}})
    connection.execute(sa.text("""
        UPDATE tickets
        SET ticket_type_id = (
            SELECT id FROM ticket_types
            WHERE name = CASE WHEN tickets.type = 'INCIDENT' THEN 'Incident' ELSE 'Service Request' END
        )
        WHERE ticket_type_id IS NULL
    """))
    tickets = connection.execute(sa.text("""
        SELECT id, title, description, requester_id, entity_id, category_id, urgency, impact, priority,
               source, sla_id, ttr_due_at, opened_at, custom_fields
        FROM tickets
    """)).mappings()
    field_ids = {row.key: row.id for row in connection.execute(sa.text('SELECT id, "key" FROM field_definitions'))}
    for ticket in tickets:
        values = {
            "summary": ticket["title"], "details": ticket["description"], "user": ticket["requester_id"],
            "organisation": ticket["entity_id"], "category": ticket["category_id"], "urgency": ticket["urgency"],
            "impact": ticket["impact"], "priority": ticket["priority"], "source": ticket["source"],
            "sla": ticket["sla_id"], "due_date": ticket["ttr_due_at"].isoformat() if ticket["ttr_due_at"] else None,
            "start_date": ticket["opened_at"].isoformat() if ticket["opened_at"] else None,
        }
        values.update(ticket["custom_fields"] or {})
        for key, value in values.items():
            if key in field_ids and value is not None:
                connection.execute(sa.text("""
                    INSERT INTO ticket_field_values (ticket_id, field_definition_id, value)
                    VALUES (:ticket_id, :field_id, CAST(:value AS jsonb))
                    ON CONFLICT (ticket_id, field_definition_id) DO NOTHING
                """), {"ticket_id": ticket["id"], "field_id": field_ids[key], "value": json.dumps(value)})


def downgrade() -> None:
    op.drop_index("ix_tickets_ticket_type_id", table_name="tickets")
    op.drop_column("tickets", "ticket_type_id")
    op.drop_table("ticket_templates")
    op.drop_table("ticket_field_values")
    op.drop_table("ticket_type_fields")
    op.drop_table("ticket_types")
    op.drop_index("ix_field_definitions_key", table_name="field_definitions")
    op.drop_table("field_definitions")
    op.drop_table("category_teams")
    op.drop_constraint("ck_categories_level", "categories", type_="check")
    op.drop_column("categories", "level")
