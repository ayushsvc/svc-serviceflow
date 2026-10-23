"""Add name uniqueness and owner indexes for list profiles.

Revision ID: 0013_list_profile_constraints
Revises: 0012_list_profiles
"""

from alembic import op

revision = "0013_list_profile_constraints"
down_revision = "0012_list_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            duplicate RECORD;
            suffix INTEGER;
            candidate TEXT;
        BEGIN
            FOR duplicate IN
                SELECT id, user_id, kind, name,
                       row_number() OVER (
                           PARTITION BY user_id, kind, name
                           ORDER BY id
                       ) AS duplicate_number
                FROM list_profiles
            LOOP
                IF duplicate.duplicate_number > 1 THEN
                    suffix := duplicate.duplicate_number;
                    LOOP
                        candidate := left(
                            duplicate.name,
                            120 - length(' (' || suffix || ')')
                        ) || ' (' || suffix || ')';
                        EXIT WHEN NOT EXISTS (
                            SELECT 1
                            FROM list_profiles
                            WHERE user_id = duplicate.user_id
                              AND kind = duplicate.kind
                              AND name = candidate
                              AND id <> duplicate.id
                        );
                        suffix := suffix + 1;
                    END LOOP;
                    UPDATE list_profiles
                    SET name = candidate, updated_at = now()
                    WHERE id = duplicate.id;
                END IF;
            END LOOP;
        END
        $$;
        """
    )
    op.create_unique_constraint(
        "uq_list_profiles_user_kind_name",
        "list_profiles",
        ["user_id", "kind", "name"],
    )
    op.create_index(
        "ix_list_profiles_owner_kind_name",
        "list_profiles",
        ["user_id", "entity_id", "kind", "name"],
    )


def downgrade() -> None:
    op.drop_index("ix_list_profiles_owner_kind_name", table_name="list_profiles")
    op.drop_constraint(
        "uq_list_profiles_user_kind_name",
        "list_profiles",
        type_="unique",
    )
