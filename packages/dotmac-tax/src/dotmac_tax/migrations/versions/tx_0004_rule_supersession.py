"""Append explicit, immutable effective-dated tax-policy replacement edges.

Revision ID: tx_0004_rule_supersession
Revises: tx_0003_result_fingerprint
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "tx_0004_rule_supersession"
down_revision = "tx_0003_result_fingerprint"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    bind = op.get_bind()
    caller_timeout = bind.execute(
        sa.text("SELECT current_setting('lock_timeout')")
    ).scalar_one()
    op.execute("""
        DO $$ BEGIN
            PERFORM set_config('lock_timeout', '5s', true);
            LOCK TABLE mod_tax.tax_rules, mod_tax.tax_rule_bands IN ACCESS EXCLUSIVE MODE;
        END; $$;
    """)
    op.execute("""
        ALTER TABLE mod_tax.tax_rules ADD COLUMN is_published BOOLEAN NOT NULL DEFAULT true;
        ALTER TABLE mod_tax.tax_rules ALTER COLUMN is_published SET DEFAULT false;
        DROP TRIGGER protect_tax_evidence ON mod_tax.tax_rules;
        CREATE FUNCTION mod_tax.protect_tax_rule_publication()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'INSERT' AND NOT NEW.is_published THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND NOT OLD.is_published AND NEW.is_published
               AND (to_jsonb(OLD) - 'is_published') IS NOT DISTINCT FROM
                   (to_jsonb(NEW) - 'is_published') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'tax rule publication is immutable';
        END; $$;
        CREATE TRIGGER protect_tax_evidence BEFORE INSERT OR UPDATE OR DELETE
            ON mod_tax.tax_rules FOR EACH ROW
            EXECUTE FUNCTION mod_tax.protect_tax_rule_publication();
        GRANT UPDATE (is_published) ON mod_tax.tax_rules TO app_user;
        CREATE FUNCTION mod_tax.require_tax_rule_published()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.id AND is_published) THEN
                RAISE EXCEPTION 'tax rule must be published before commit';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE CONSTRAINT TRIGGER require_tax_rule_published AFTER INSERT
            ON mod_tax.tax_rules DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION mod_tax.require_tax_rule_published();
        CREATE FUNCTION mod_tax.require_unpublished_tax_rule_parent()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_published BOOLEAN;
        BEGIN
            SELECT is_published INTO STRICT parent_published FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.rule_id FOR NO KEY UPDATE;
            IF parent_published THEN
                RAISE EXCEPTION 'tax rule band membership is published';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER require_unpublished_tax_rule_parent BEFORE INSERT
            ON mod_tax.tax_rule_bands FOR EACH ROW
            EXECUTE FUNCTION mod_tax.require_unpublished_tax_rule_parent();
    """)
    op.execute("""
        CREATE TABLE mod_tax.tax_rule_supersessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            predecessor_rule_id UUID NOT NULL,
            predecessor_version INTEGER NOT NULL,
            replacement_rule_id UUID NOT NULL,
            replacement_version INTEGER NOT NULL,
            effective_from DATE NOT NULL,
            actor_id UUID NOT NULL,
            published_by_ref VARCHAR(240) NOT NULL,
            reason VARCHAR(500) NOT NULL,
            evidence_ref VARCHAR(240) NOT NULL,
            source_ref VARCHAR(240) NOT NULL,
            source_version VARCHAR(100) NOT NULL,
            published_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_tax_rule_supersessions_tenant_id_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_tax_rule_supersessions_predecessor UNIQUE (tenant_id, predecessor_rule_id),
            CONSTRAINT uq_tax_rule_supersessions_replacement UNIQUE (tenant_id, replacement_rule_id),
            CONSTRAINT uq_tax_rule_supersessions_source UNIQUE (tenant_id, source_ref, source_version),
            CONSTRAINT ck_tax_rule_supersessions_versions CHECK
                (replacement_version > predecessor_version AND predecessor_version > 0),
            CONSTRAINT fk_tax_rule_supersessions_predecessor FOREIGN KEY (tenant_id, predecessor_rule_id)
                REFERENCES mod_tax.tax_rules(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_tax_rule_supersessions_replacement FOREIGN KEY (tenant_id, replacement_rule_id)
                REFERENCES mod_tax.tax_rules(tenant_id, id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_tax_rule_supersessions_selection
            ON mod_tax.tax_rule_supersessions(tenant_id, effective_from, predecessor_rule_id);
        ALTER TABLE mod_tax.tax_rule_supersessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_rule_supersessions FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_rule_supersessions_tenant_isolation
            ON mod_tax.tax_rule_supersessions
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_rule_supersessions TO app_user;
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE
            ON mod_tax.tax_rule_supersessions FOR EACH ROW
            EXECUTE FUNCTION mod_tax.protect_tax_evidence();
    """)
    op.execute("""
        CREATE FUNCTION mod_tax.validate_tax_rule_supersession()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE predecessor mod_tax.tax_rules%ROWTYPE;
                replacement mod_tax.tax_rules%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT predecessor FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.predecessor_rule_id;
            -- NO KEY UPDATE serializes authors without conflicting with the
            -- ordinary rule insertion's FK KEY SHARE lock on the code row.
            PERFORM id FROM mod_tax.tax_codes
                WHERE tenant_id = NEW.tenant_id AND id = predecessor.tax_code_id
                FOR NO KEY UPDATE;
            SELECT * INTO STRICT replacement FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.replacement_rule_id;
            IF NOT predecessor.is_published OR NOT replacement.is_published
                OR predecessor.version <> NEW.predecessor_version
                OR replacement.version <> NEW.replacement_version
                OR replacement.version <= predecessor.version
                OR replacement.effective_from <> NEW.effective_from
                OR NEW.effective_from < predecessor.effective_from
                OR (predecessor.effective_to IS NOT NULL AND NEW.effective_from > predecessor.effective_to)
                OR replacement.effective_to IS DISTINCT FROM predecessor.effective_to
                OR ROW(predecessor.tax_code_id, predecessor.priority, predecessor.fact_kind,
                    predecessor.recognition_basis_code, predecessor.transaction_side,
                    predecessor.party_category, predecessor.supply_category, predecessor.place_code)
                   IS DISTINCT FROM
                   ROW(replacement.tax_code_id, replacement.priority, replacement.fact_kind,
                    replacement.recognition_basis_code, replacement.transaction_side,
                    replacement.party_category, replacement.supply_category, replacement.place_code)
                OR EXISTS (SELECT 1 FROM mod_tax.tax_rule_supersessions
                    WHERE tenant_id = NEW.tenant_id AND predecessor_rule_id = predecessor.id)
                OR btrim(NEW.published_by_ref) = '' OR btrim(NEW.reason) = ''
                OR btrim(NEW.evidence_ref) = '' OR btrim(NEW.source_ref) = ''
                OR btrim(NEW.source_version) = ''
            THEN
                RAISE EXCEPTION 'invalid or stale tax rule supersession';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_tax_rule_supersession BEFORE INSERT
            ON mod_tax.tax_rule_supersessions FOR EACH ROW
            EXECUTE FUNCTION mod_tax.validate_tax_rule_supersession();
    """)

    bind.execute(
        sa.text("SELECT set_config('lock_timeout', :previous, true)"),
        {"previous": caller_timeout},
    )


def downgrade() -> None:
    # Dropping a populated edge table would silently reactivate predecessors.
    op.execute("""
        DO $$
        DECLARE caller_timeout TEXT := current_setting('lock_timeout');
                caller_row_security TEXT := current_setting('row_security');
        BEGIN
            PERFORM set_config('lock_timeout', '5s', true);
            LOCK TABLE mod_tax.tax_rules, mod_tax.tax_rule_bands,
                mod_tax.tax_rule_supersessions IN ACCESS EXCLUSIVE MODE;
            PERFORM set_config('lock_timeout', caller_timeout, true);
            PERFORM set_config('row_security', 'off', true);
            IF EXISTS (SELECT 1 FROM mod_tax.tax_rule_supersessions)
                OR EXISTS (SELECT 1 FROM mod_tax.tax_rules) THEN
                RAISE EXCEPTION 'cannot downgrade tax policy with rule or supersession evidence';
            END IF;
            PERFORM set_config('row_security', caller_row_security, true);
        END; $$;
    """)
    op.execute("DROP TABLE mod_tax.tax_rule_supersessions")
    op.execute("DROP FUNCTION mod_tax.validate_tax_rule_supersession()")
    op.execute("""
        DROP TRIGGER require_unpublished_tax_rule_parent ON mod_tax.tax_rule_bands;
        DROP FUNCTION mod_tax.require_unpublished_tax_rule_parent();
        DROP TRIGGER require_tax_rule_published ON mod_tax.tax_rules;
        DROP FUNCTION mod_tax.require_tax_rule_published();
        DROP TRIGGER protect_tax_evidence ON mod_tax.tax_rules;
        DROP FUNCTION mod_tax.protect_tax_rule_publication();
        REVOKE UPDATE (is_published) ON mod_tax.tax_rules FROM app_user;
        ALTER TABLE mod_tax.tax_rules DROP COLUMN is_published;
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE
            ON mod_tax.tax_rules FOR EACH ROW
            EXECUTE FUNCTION mod_tax.protect_tax_evidence();
    """)
