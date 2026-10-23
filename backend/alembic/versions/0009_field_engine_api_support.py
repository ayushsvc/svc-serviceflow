"""scope ticket types and persist configurable priority matrices"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_field_engine_api_support"
down_revision = "0008_configurable_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket_types", sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=True))
    op.create_index("ix_ticket_types_entity_id", "ticket_types", ["entity_id"])
    op.create_table(
        "priority_matrices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("entities.id"), nullable=False, unique=True),
        sa.Column("matrix", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    connection = op.get_bind()
    entities = connection.execute(sa.text("SELECT id FROM entities")).scalars().all()
    default_matrix = "[[1,1,2,2,3],[1,2,2,3,3],[2,2,3,3,4],[2,3,3,4,5],[3,3,4,5,5]]"
    for entity_id in entities:
        connection.execute(
            sa.text("INSERT INTO priority_matrices (entity_id, matrix) VALUES (:entity_id, CAST(:matrix AS jsonb)) ON CONFLICT (entity_id) DO NOTHING"),
            {"entity_id": entity_id, "matrix": default_matrix},
        )


def downgrade() -> None:
    op.drop_table("priority_matrices")
    op.drop_index("ix_ticket_types_entity_id", table_name="ticket_types")
    op.drop_column("ticket_types", "entity_id")
