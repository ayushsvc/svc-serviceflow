"""allow requesters to submit portal-visible ticket fields"""
from alembic import op
import sqlalchemy as sa

revision = "0010_requester_field_editability"
down_revision = "0009_field_engine_api_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("""
        UPDATE ticket_type_fields
        SET editable_by_roles = '["Administrator","Technician","Requester"]'::jsonb
        WHERE show_on_portal = TRUE
          AND field_definition_id IN (
              SELECT id FROM field_definitions WHERE "key" IN ('summary', 'details', 'user', 'organisation', 'category')
          )
    """))


def downgrade() -> None:
    op.get_bind().execute(sa.text("""
        UPDATE ticket_type_fields
        SET editable_by_roles = '["Administrator","Technician"]'::jsonb
        WHERE field_definition_id IN (
            SELECT id FROM field_definitions WHERE "key" IN ('summary', 'details', 'user', 'organisation', 'category')
        )
    """))
