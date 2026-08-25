"""Seal determination-set membership and result content.

Revision ID: tx_0003_result_fingerprint
Revises: tx_0002_multi_tax
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "tx_0003_result_fingerprint"
down_revision = "tx_0002_multi_tax"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_tax"
_CONSTRAINT = "ck_tax_determination_sets_result_seal"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "tax_determination_sets",
        sa.Column("result_seal_state", sa.String(16), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "tax_determination_sets",
        sa.Column("result_fingerprint", sa.String(68), nullable=True),
        schema=_SCHEMA,
    )
    # a2 determination sets are protected by the append-only trigger, so no
    # truthful result fingerprint can be written without replacing history.
    # NOT VALID preserves those NULL/NULL rows. New rows may exist as
    # building/NULL only inside their creating transaction; the deferred
    # trigger below refuses commit until the owner seals them.
    op.execute(
        "ALTER TABLE mod_tax.tax_determination_sets "
        "ADD CONSTRAINT ck_tax_determination_sets_result_seal CHECK ("
        "(result_seal_state IS NULL AND result_fingerprint IS NULL) OR "
        "(result_seal_state = 'building' AND result_fingerprint IS NULL) OR "
        "(result_seal_state = 'sealed' "
        "AND result_fingerprint ~ '^rv1:[0-9a-f]{64}$')) NOT VALID"
    )

    # Replace only the determination-set trigger. Every other evidence table
    # keeps the released append-only trigger. This permits one narrow state
    # transition without making any tax-relevant parent field mutable.
    op.execute("DROP TRIGGER protect_tax_evidence " "ON mod_tax.tax_determination_sets")
    op.execute(
        """
        CREATE FUNCTION mod_tax.protect_tax_determination_set_seal()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'INSERT'
               AND NEW.result_seal_state = 'building'
               AND NEW.result_fingerprint IS NULL
            THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.result_seal_state = 'building'
               AND OLD.result_fingerprint IS NULL
               AND NEW.result_seal_state = 'sealed'
               AND NEW.result_fingerprint ~ '^rv1:[0-9a-f]{64}$'
               AND (to_jsonb(OLD) - ARRAY['result_seal_state','result_fingerprint'])
                   IS NOT DISTINCT FROM
                   (to_jsonb(NEW) - ARRAY['result_seal_state','result_fingerprint'])
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'tax evidence is append-only';
        END; $$;
        CREATE TRIGGER protect_tax_evidence
        BEFORE UPDATE OR DELETE ON mod_tax.tax_determination_sets
        FOR EACH ROW EXECUTE FUNCTION
        mod_tax.protect_tax_determination_set_seal();
        CREATE TRIGGER require_building_tax_determination_set
        BEFORE INSERT ON mod_tax.tax_determination_sets
        FOR EACH ROW EXECUTE FUNCTION
        mod_tax.protect_tax_determination_set_seal();
        """
    )
    op.execute(
        "GRANT UPDATE (result_seal_state, result_fingerprint) "
        "ON mod_tax.tax_determination_sets TO app_user"
    )

    # The constraint trigger observes the final row state at transaction end,
    # so neither the module nor a direct app-role caller can strand a partial
    # result by inserting a parent and omitting the seal transition.
    op.execute(
        """
        CREATE FUNCTION mod_tax.require_tax_determination_set_sealed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            final_state text;
            final_fingerprint text;
        BEGIN
            SELECT result_seal_state, result_fingerprint
              INTO final_state, final_fingerprint
              FROM mod_tax.tax_determination_sets
             WHERE tenant_id = NEW.tenant_id AND id = NEW.id;
            IF final_state IS DISTINCT FROM 'sealed'
               OR final_fingerprint IS NULL
               OR final_fingerprint !~ '^rv1:[0-9a-f]{64}$'
            THEN
                RAISE EXCEPTION
                    'new tax determination set must be sealed before commit';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE CONSTRAINT TRIGGER require_tax_determination_set_sealed
        AFTER INSERT ON mod_tax.tax_determination_sets
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
        mod_tax.require_tax_determination_set_sealed();
        """
    )

    # Component and line membership is open only during the parent's building
    # phase. Standalone legacy determinations remain writable for the published
    # determine_tax compatibility API, but a3 reports refuse them explicitly.
    op.execute(
        """
        CREATE FUNCTION mod_tax.require_building_tax_result_parent()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_state text;
            set_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'tax_determinations' THEN
                IF NEW.determination_set_id IS NULL THEN
                    RETURN NEW;
                END IF;
                SELECT result_seal_state INTO parent_state
                  FROM mod_tax.tax_determination_sets
                 WHERE tenant_id = NEW.tenant_id
                   AND id = NEW.determination_set_id;
            ELSE
                SELECT determination_set_id INTO set_id
                  FROM mod_tax.tax_determinations
                 WHERE tenant_id = NEW.tenant_id
                   AND id = NEW.determination_id;
                IF set_id IS NULL THEN
                    RETURN NEW;
                END IF;
                SELECT result_seal_state INTO parent_state
                  FROM mod_tax.tax_determination_sets
                 WHERE tenant_id = NEW.tenant_id AND id = set_id;
            END IF;
            IF parent_state IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION
                    'tax determination result membership is sealed';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER require_building_tax_result_parent
        BEFORE INSERT ON mod_tax.tax_determinations
        FOR EACH ROW EXECUTE FUNCTION
        mod_tax.require_building_tax_result_parent();
        CREATE TRIGGER require_building_tax_result_parent
        BEFORE INSERT ON mod_tax.tax_determination_lines
        FOR EACH ROW EXECUTE FUNCTION
        mod_tax.require_building_tax_result_parent();
        """
    )


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE (result_seal_state, result_fingerprint) "
        "ON mod_tax.tax_determination_sets FROM app_user"
    )
    op.execute(
        "DROP TRIGGER require_building_tax_result_parent "
        "ON mod_tax.tax_determination_lines"
    )
    op.execute(
        "DROP TRIGGER require_building_tax_result_parent "
        "ON mod_tax.tax_determinations"
    )
    op.execute("DROP FUNCTION mod_tax.require_building_tax_result_parent()")
    op.execute(
        "DROP TRIGGER require_tax_determination_set_sealed "
        "ON mod_tax.tax_determination_sets"
    )
    op.execute("DROP FUNCTION mod_tax.require_tax_determination_set_sealed()")
    op.execute("DROP TRIGGER protect_tax_evidence " "ON mod_tax.tax_determination_sets")
    op.execute(
        "DROP TRIGGER require_building_tax_determination_set "
        "ON mod_tax.tax_determination_sets"
    )
    op.execute("DROP FUNCTION mod_tax.protect_tax_determination_set_seal()")
    op.execute(
        "CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE "
        "ON mod_tax.tax_determination_sets FOR EACH ROW EXECUTE FUNCTION "
        "mod_tax.protect_tax_evidence()"
    )
    op.drop_constraint(
        _CONSTRAINT,
        "tax_determination_sets",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_column("tax_determination_sets", "result_fingerprint", schema=_SCHEMA)
    op.drop_column("tax_determination_sets", "result_seal_state", schema=_SCHEMA)
