"""Record the original engine verification context without historical backfill.

Platform-only additive receipt columns retain existing RLS/ACL semantics: no
RLS or tenant_id, and app_user has no table or column privileges. This is
provenance, never merchant identity, credential generation or cash proof.

The final AFTER UPDATE guard compares OLD to the completed NEW row, including
changes by other BEFORE triggers. Legacy null context cannot be promoted into
historical evidence. Receipt lifecycle and bounded retention remain available.
Downgrade refuses to erase any recorded original context.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0016_receipt_origin"
down_revision = "ig_0015_descriptor_contract"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "inbox_receipts"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("verification_connector_key", sa.String(120), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("verification_manifest_digest", sa.String(64), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("verification_config_revision_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("verification_mode", sa.String(16), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_inbox_receipts_verification_origin",
        _TABLE,
        "(verification_connector_key IS NULL "
        "AND verification_manifest_digest IS NULL "
        "AND verification_config_revision_id IS NULL AND verification_mode IS NULL) OR "
        "(verification_connector_key IS NOT NULL "
        "AND length(trim(verification_connector_key)) > 0 "
        "AND verification_connector_key = trim(verification_connector_key) "
        "AND verification_manifest_digest IS NOT NULL "
        "AND length(verification_manifest_digest) = 64 "
        "AND verification_mode IS NOT NULL "
        "AND verification_mode IN ('ingress', 'poll'))",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_inbox_receipts_origin_digest",
        _TABLE,
        "verification_manifest_digest IS NULL "
        "OR verification_manifest_digest ~ '^[0-9a-f]{64}$'",
        schema=_SCHEMA,
    )
    op.execute("""
        CREATE FUNCTION mod_intg.guard_receipt_verification_origin()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF ROW(OLD.id, OLD.installation_id, OLD.capability_binding_id,
                       OLD.verification_connector_key, OLD.verification_manifest_digest,
                       OLD.verification_config_revision_id, OLD.verification_mode)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.installation_id, NEW.capability_binding_id,
                       NEW.verification_connector_key, NEW.verification_manifest_digest,
                       NEW.verification_config_revision_id, NEW.verification_mode) THEN
                    RAISE EXCEPTION 'receipt verification origin is immutable';
                END IF;
            ELSIF NEW.verification_connector_key IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM mod_intg.capability_bindings b
                    JOIN mod_intg.connector_installations i ON i.id = b.installation_id
                    WHERE b.id = NEW.capability_binding_id
                      AND i.id = NEW.installation_id
                      AND i.connector_key = NEW.verification_connector_key
                ) THEN
                    RAISE EXCEPTION 'receipt verification origin address differs';
                END IF;
                IF NEW.verification_config_revision_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM mod_intg.connector_config_revisions r
                    WHERE r.id = NEW.verification_config_revision_id
                      AND r.installation_id = NEW.installation_id
                ) THEN
                    RAISE EXCEPTION 'receipt verification origin revision differs';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER receipt_verification_origin_final
        AFTER INSERT OR UPDATE ON mod_intg.inbox_receipts
        FOR EACH ROW EXECUTE FUNCTION mod_intg.guard_receipt_verification_origin();
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION mod_intg.guard_receipt_verification_origin() FROM PUBLIC;"
    )
    # Existing table grants apply to additive columns. Explicitly retain the
    # tenant-role denial; this module has no tenant data plane to expand.
    op.execute("REVOKE ALL ON mod_intg.inbox_receipts FROM app_user;")


def downgrade() -> None:
    # Fence concurrent first-context appends before checking protected history.
    op.execute("LOCK TABLE mod_intg.inbox_receipts IN ACCESS EXCLUSIVE MODE;")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM mod_intg.inbox_receipts
                       WHERE verification_connector_key IS NOT NULL) THEN
                RAISE EXCEPTION 'cannot erase recorded receipt verification origin';
            END IF;
        END $$;
    """)
    op.execute(
        "DROP TRIGGER receipt_verification_origin_final ON mod_intg.inbox_receipts;"
    )
    op.execute("DROP FUNCTION mod_intg.guard_receipt_verification_origin();")
    op.drop_constraint(
        "ck_inbox_receipts_origin_digest", _TABLE, type_="check", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_inbox_receipts_verification_origin", _TABLE, type_="check", schema=_SCHEMA
    )
    op.drop_column(_TABLE, "verification_mode", schema=_SCHEMA)
    op.drop_column(_TABLE, "verification_config_revision_id", schema=_SCHEMA)
    op.drop_column(_TABLE, "verification_manifest_digest", schema=_SCHEMA)
    op.drop_column(_TABLE, "verification_connector_key", schema=_SCHEMA)
