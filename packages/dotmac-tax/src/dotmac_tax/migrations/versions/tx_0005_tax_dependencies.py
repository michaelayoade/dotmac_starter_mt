"""Versioned, sealed commercial-source dependencies across Tax purposes.

Revision ID: tx_0005_tax_dependencies
Revises: tx_0004_rule_supersession
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "tx_0005_tax_dependencies"
down_revision = "tx_0004_rule_supersession"
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
            LOCK TABLE mod_tax.tax_rules, mod_tax.tax_determination_sets,
                mod_tax.tax_determinations, mod_tax.tax_determination_lines
                IN ACCESS EXCLUSIVE MODE;
        END; $$;
        ALTER TABLE mod_tax.tax_rules
            ADD COLUMN dependency_jurisdiction_id UUID,
            ADD COLUMN dependency_fact_kind VARCHAR(100),
            ADD COLUMN dependency_recognition_basis_code VARCHAR(100),
            ADD COLUMN dependency_transaction_side VARCHAR(20),
            ADD COLUMN dependency_selection VARCHAR(24),
            ADD CONSTRAINT fk_tax_rules_dependency_jurisdiction
                FOREIGN KEY (tenant_id, dependency_jurisdiction_id)
                REFERENCES mod_tax.tax_jurisdictions(tenant_id, id) ON DELETE RESTRICT;
        ALTER TABLE mod_tax.tax_rules DROP CONSTRAINT ck_tax_rules_calculation_base;
        ALTER TABLE mod_tax.tax_rules ADD CONSTRAINT ck_tax_rules_calculation_base
            CHECK (calculation_base_code IN ('source_amount','source_plus_prior_tax','source_plus_dependency_tax'));
        ALTER TABLE mod_tax.tax_rules ADD CONSTRAINT ck_tax_rules_dependency_policy CHECK (
            (calculation_base_code <> 'source_plus_dependency_tax'
             AND dependency_jurisdiction_id IS NULL AND dependency_fact_kind IS NULL
             AND dependency_recognition_basis_code IS NULL AND dependency_transaction_side IS NULL
             AND dependency_selection IS NULL)
            OR (calculation_base_code = 'source_plus_dependency_tax' AND NOT inclusive
             AND dependency_jurisdiction_id IS NOT NULL AND btrim(dependency_fact_kind) <> ''
             AND dependency_fact_kind IS NOT NULL AND dependency_recognition_basis_code IS NOT NULL
             AND btrim(dependency_recognition_basis_code) <> ''
             AND dependency_transaction_side IN ('input','output','withholding','liability')
             AND dependency_transaction_side IS NOT NULL
             AND dependency_selection IN ('all_prior','tax_code_ids') AND dependency_selection IS NOT NULL));
        ALTER TABLE mod_tax.tax_determination_sets
            ADD COLUMN contract_version INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN commercial_source_ref VARCHAR(240),
            ADD COLUMN commercial_source_version VARCHAR(100),
            ADD CONSTRAINT ck_tax_sets_contract_version CHECK (
                (contract_version = 1 AND commercial_source_ref IS NULL AND commercial_source_version IS NULL)
                OR (contract_version = 2 AND commercial_source_ref IS NOT NULL AND btrim(commercial_source_ref) <> ''
                    AND commercial_source_version IS NOT NULL AND btrim(commercial_source_version) <> ''));
        ALTER TABLE mod_tax.tax_determination_sets DROP CONSTRAINT ck_tax_determination_sets_result_seal;
        ALTER TABLE mod_tax.tax_determination_sets ADD CONSTRAINT ck_tax_determination_sets_result_seal CHECK (
            (contract_version = 1 AND result_seal_state IS NULL AND result_fingerprint IS NULL)
            OR (result_seal_state = 'building' AND result_fingerprint IS NULL)
            OR (result_seal_state = 'sealed' AND
                ((contract_version = 1 AND result_fingerprint ~ '^rv1:[0-9a-f]{64}$')
                 OR (contract_version = 2 AND result_fingerprint ~ '^rv2:[0-9a-f]{64}$')))) NOT VALID;
        ALTER TABLE mod_tax.tax_determinations
            ADD COLUMN selected_dependency_policy JSON;
        ALTER TABLE mod_tax.tax_determinations DROP CONSTRAINT ck_tax_determinations_calculation_base;
        ALTER TABLE mod_tax.tax_determinations ADD CONSTRAINT ck_tax_determinations_calculation_base
            CHECK (calculation_base_code IN ('source_amount','source_plus_prior_tax','source_plus_dependency_tax'));
        CREATE TABLE mod_tax.tax_rule_dependency_codes (
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            rule_id UUID NOT NULL,
            tax_code_id UUID NOT NULL,
            PRIMARY KEY (tenant_id, rule_id, tax_code_id),
            FOREIGN KEY (tenant_id, rule_id) REFERENCES mod_tax.tax_rules(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, tax_code_id) REFERENCES mod_tax.tax_codes(tenant_id,id) ON DELETE RESTRICT
        );
        CREATE TABLE mod_tax.tax_determination_prior_sets (
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            determination_set_id UUID NOT NULL,
            prior_set_id UUID NOT NULL,
            source_ref VARCHAR(240) NOT NULL,
            source_version VARCHAR(100) NOT NULL,
            source_fingerprint VARCHAR(64) NOT NULL,
            result_fingerprint VARCHAR(68) NOT NULL,
            PRIMARY KEY (tenant_id, determination_set_id, prior_set_id),
            UNIQUE (tenant_id, determination_set_id, source_ref, source_version),
            CHECK (determination_set_id <> prior_set_id),
            FOREIGN KEY (tenant_id, determination_set_id) REFERENCES mod_tax.tax_determination_sets(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, prior_set_id) REFERENCES mod_tax.tax_determination_sets(tenant_id,id) ON DELETE RESTRICT
        );
        CREATE TABLE mod_tax.tax_determination_dependencies (
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            determination_id UUID NOT NULL,
            prior_determination_id UUID NOT NULL,
            prior_set_id UUID NOT NULL,
            tax_code_id UUID NOT NULL,
            rule_id UUID NOT NULL,
            rule_version INTEGER NOT NULL,
            component_sequence INTEGER NOT NULL,
            tax_amount NUMERIC(20,6) NOT NULL,
            currency_code VARCHAR(3) NOT NULL,
            minor_units INTEGER NOT NULL,
            PRIMARY KEY (tenant_id, determination_id, prior_determination_id),
            CONSTRAINT uq_tax_dependency_sequence UNIQUE (tenant_id,determination_id,component_sequence),
            CONSTRAINT ck_tax_dependency_amount_version CHECK (tax_amount >= 0 AND rule_version > 0 AND component_sequence > 0),
            FOREIGN KEY (tenant_id, determination_id) REFERENCES mod_tax.tax_determinations(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, prior_determination_id) REFERENCES mod_tax.tax_determinations(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, prior_set_id) REFERENCES mod_tax.tax_determination_sets(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, tax_code_id) REFERENCES mod_tax.tax_codes(tenant_id,id) ON DELETE RESTRICT,
            FOREIGN KEY (tenant_id, rule_id) REFERENCES mod_tax.tax_rules(tenant_id,id) ON DELETE RESTRICT
        );
    """)
    op.execute("""
        ALTER TABLE mod_tax.tax_rule_dependency_codes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_rule_dependency_codes FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_rule_dependency_codes_tenant_isolation ON mod_tax.tax_rule_dependency_codes
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_rule_dependency_codes TO app_user;
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_rule_dependency_codes
            FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        ALTER TABLE mod_tax.tax_determination_prior_sets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_determination_prior_sets FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_determination_prior_sets_tenant_isolation ON mod_tax.tax_determination_prior_sets
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_determination_prior_sets TO app_user;
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_determination_prior_sets
            FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        ALTER TABLE mod_tax.tax_determination_dependencies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_determination_dependencies FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_determination_dependencies_tenant_isolation ON mod_tax.tax_determination_dependencies
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_determination_dependencies TO app_user;
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_determination_dependencies
            FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
    """)
    op.execute("""
        CREATE FUNCTION mod_tax.validate_tax_rule_dependency_code()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent mod_tax.tax_rules%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT parent FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.rule_id FOR NO KEY UPDATE;
            IF parent.is_published OR parent.dependency_selection IS DISTINCT FROM 'tax_code_ids'
               OR NOT EXISTS (SELECT 1 FROM mod_tax.tax_codes
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.tax_code_id
                      AND jurisdiction_id = parent.dependency_jurisdiction_id) THEN
                RAISE EXCEPTION 'invalid or published tax dependency code membership';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_tax_rule_dependency_code BEFORE INSERT ON mod_tax.tax_rule_dependency_codes
            FOR EACH ROW EXECUTE FUNCTION mod_tax.validate_tax_rule_dependency_code();
        CREATE OR REPLACE FUNCTION mod_tax.require_tax_rule_published()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent mod_tax.tax_rules%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT parent FROM mod_tax.tax_rules WHERE tenant_id = NEW.tenant_id AND id = NEW.id;
            IF NOT parent.is_published THEN
                RAISE EXCEPTION 'tax rule must be published before commit';
            END IF;
            IF (parent.dependency_selection = 'tax_code_ids' AND NOT EXISTS (
                    SELECT 1 FROM mod_tax.tax_rule_dependency_codes WHERE tenant_id = parent.tenant_id AND rule_id = parent.id))
                OR (parent.dependency_selection IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM mod_tax.tax_codes code
                    JOIN mod_tax.tax_jurisdictions current_jurisdiction
                      ON current_jurisdiction.tenant_id = code.tenant_id AND current_jurisdiction.id = code.jurisdiction_id
                    JOIN mod_tax.tax_jurisdictions prior_jurisdiction
                      ON prior_jurisdiction.tenant_id = code.tenant_id AND prior_jurisdiction.id = parent.dependency_jurisdiction_id
                    WHERE code.tenant_id = parent.tenant_id AND code.id = parent.tax_code_id
                      AND current_jurisdiction.authority_id = prior_jurisdiction.authority_id)) THEN
                RAISE EXCEPTION 'tax rule must have complete published dependency policy before commit';
            END IF;
            RETURN NEW;
        END; $$;
        CREATE FUNCTION mod_tax.validate_tax_prior_set()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent mod_tax.tax_determination_sets%ROWTYPE;
                prior mod_tax.tax_determination_sets%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT parent FROM mod_tax.tax_determination_sets
                WHERE tenant_id = NEW.tenant_id AND id = NEW.determination_set_id FOR NO KEY UPDATE;
            SELECT * INTO STRICT prior FROM mod_tax.tax_determination_sets
                WHERE tenant_id = NEW.tenant_id AND id = NEW.prior_set_id;
            IF parent.contract_version <> 2 OR parent.result_seal_state IS DISTINCT FROM 'building'
                OR prior.contract_version <> 2 OR prior.result_seal_state IS DISTINCT FROM 'sealed'
                OR prior.result_fingerprint !~ '^rv2:[0-9a-f]{64}$'
                OR ROW(NEW.source_ref,NEW.source_version,NEW.source_fingerprint,NEW.result_fingerprint)
                    IS DISTINCT FROM ROW(prior.source_ref,prior.source_version,prior.source_fingerprint,prior.result_fingerprint)
                OR ROW(parent.commercial_source_ref,parent.commercial_source_version,parent.occurred_on,
                    parent.source_amount,parent.currency_code,parent.minor_units,parent.recognition_basis_code,
                    parent.counterparty_ref,parent.supply_ref,parent.place_ref)
                    IS DISTINCT FROM ROW(prior.commercial_source_ref,prior.commercial_source_version,prior.occurred_on,
                    prior.source_amount,prior.currency_code,prior.minor_units,prior.recognition_basis_code,
                    prior.counterparty_ref,prior.supply_ref,prior.place_ref)
                OR NOT EXISTS (SELECT 1 FROM mod_tax.tax_jurisdictions current_jurisdiction
                    JOIN mod_tax.tax_jurisdictions prior_jurisdiction ON prior_jurisdiction.tenant_id = current_jurisdiction.tenant_id
                    WHERE current_jurisdiction.tenant_id = NEW.tenant_id AND current_jurisdiction.id = parent.jurisdiction_id
                    AND prior_jurisdiction.id = prior.jurisdiction_id AND current_jurisdiction.authority_id = prior_jurisdiction.authority_id)
            THEN RAISE EXCEPTION 'prior tax result must be sealed evidence for the same commercial fact'; END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_tax_prior_set BEFORE INSERT ON mod_tax.tax_determination_prior_sets
            FOR EACH ROW EXECUTE FUNCTION mod_tax.validate_tax_prior_set();
        CREATE FUNCTION mod_tax.validate_tax_dependency_component()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE component mod_tax.tax_determinations%ROWTYPE;
                prior mod_tax.tax_determinations%ROWTYPE;
                parent mod_tax.tax_determination_sets%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT component FROM mod_tax.tax_determinations
                WHERE tenant_id = NEW.tenant_id AND id = NEW.determination_id;
            SELECT * INTO STRICT parent FROM mod_tax.tax_determination_sets
                WHERE tenant_id = NEW.tenant_id AND id = component.determination_set_id FOR NO KEY UPDATE;
            SELECT * INTO STRICT prior FROM mod_tax.tax_determinations
                WHERE tenant_id = NEW.tenant_id AND id = NEW.prior_determination_id;
            IF parent.contract_version <> 2 OR parent.result_seal_state IS DISTINCT FROM 'building'
                OR component.calculation_base_code IS DISTINCT FROM 'source_plus_dependency_tax'
                OR ROW(NEW.prior_set_id,NEW.tax_code_id,NEW.rule_id,NEW.rule_version,NEW.component_sequence,
                       NEW.tax_amount,NEW.currency_code,NEW.minor_units)
                    IS DISTINCT FROM ROW(prior.determination_set_id,prior.tax_code_id,prior.rule_id,prior.rule_version,
                       prior.component_sequence,prior.tax_amount,prior.currency_code,prior.minor_units)
                OR NOT EXISTS (SELECT 1 FROM mod_tax.tax_determination_sets
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.prior_set_id
                        AND contract_version = 2 AND result_seal_state = 'sealed')
            THEN RAISE EXCEPTION 'dependency snapshot must match a complete sealed prior result'; END IF;
            RETURN NEW;
        END; $$;
        CREATE TRIGGER validate_tax_dependency_component BEFORE INSERT ON mod_tax.tax_determination_dependencies
            FOR EACH ROW EXECUTE FUNCTION mod_tax.validate_tax_dependency_component();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION mod_tax.require_building_tax_result_parent()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_state TEXT; set_id UUID; parent_version INTEGER;
                rule mod_tax.tax_rules%ROWTYPE; policy JSONB;
        BEGIN
            IF TG_TABLE_NAME = 'tax_determinations' THEN
                SELECT * INTO STRICT rule FROM mod_tax.tax_rules WHERE tenant_id = NEW.tenant_id AND id = NEW.rule_id;
                IF NEW.determination_set_id IS NULL THEN
                    IF rule.dependency_selection IS NOT NULL OR NEW.selected_dependency_policy IS NOT NULL
                        OR NEW.calculation_base_code = 'source_plus_dependency_tax' THEN
                        RAISE EXCEPTION 'dependency rules require a V2 determination set';
                    END IF;
                    RETURN NEW;
                END IF;
                set_id := NEW.determination_set_id;
            ELSE
                SELECT determination_set_id INTO set_id FROM mod_tax.tax_determinations
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.determination_id;
                IF set_id IS NULL THEN RETURN NEW; END IF;
            END IF;
            SELECT result_seal_state, contract_version INTO parent_state, parent_version
                FROM mod_tax.tax_determination_sets WHERE tenant_id = NEW.tenant_id AND id = set_id FOR NO KEY UPDATE;
            IF parent_state IS DISTINCT FROM 'building' THEN
                RAISE EXCEPTION 'tax determination result membership is sealed';
            END IF;
            IF TG_TABLE_NAME = 'tax_determinations' THEN
                IF rule.dependency_selection IS NULL THEN
                    IF NEW.selected_dependency_policy IS NOT NULL OR NEW.calculation_base_code = 'source_plus_dependency_tax' THEN
                        RAISE EXCEPTION 'ordinary tax rule cannot invent a dependency policy';
                    END IF;
                ELSE
                    SELECT jsonb_build_object('prior_signature', jsonb_build_object(
                        'jurisdiction_id',rule.dependency_jurisdiction_id::text,
                        'fact_kind',rule.dependency_fact_kind,
                        'recognition_basis_code',rule.dependency_recognition_basis_code,
                        'transaction_side',rule.dependency_transaction_side),
                        'selection',rule.dependency_selection,
                        'tax_code_ids',COALESCE((SELECT jsonb_agg(tax_code_id::text ORDER BY tax_code_id::text)
                            FROM mod_tax.tax_rule_dependency_codes WHERE tenant_id = NEW.tenant_id AND rule_id = rule.id),'[]'::jsonb)) INTO policy;
                    IF parent_version <> 2 OR NEW.selected_dependency_policy::jsonb IS DISTINCT FROM policy
                        OR NEW.calculation_base_code IS DISTINCT FROM 'source_plus_dependency_tax'
                        OR NEW.inclusive IS DISTINCT FROM false THEN
                        RAISE EXCEPTION 'dependency rules require their complete V2 policy snapshot';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
        CREATE FUNCTION mod_tax.validate_tax_dependency_set(set_tenant UUID, set_id UUID)
        RETURNS VOID LANGUAGE plpgsql AS $$
        DECLARE component mod_tax.tax_determinations%ROWTYPE;
                rule mod_tax.tax_rules%ROWTYPE; prior_id UUID; match_count INTEGER;
                expected_count INTEGER; actual_count INTEGER; selected_tax NUMERIC;
        BEGIN
            IF EXISTS (SELECT 1 FROM mod_tax.tax_determinations WHERE tenant_id = set_tenant AND determination_set_id = set_id AND calculation_base_code = 'source_plus_dependency_tax')
               AND EXISTS (SELECT 1 FROM mod_tax.tax_determinations WHERE tenant_id = set_tenant AND determination_set_id = set_id AND (calculation_base_code = 'source_plus_prior_tax' OR inclusive)) THEN
                RAISE EXCEPTION 'external dependency cannot combine with local compound or inclusive bases';
            END IF;
            FOR component IN SELECT * FROM mod_tax.tax_determinations WHERE tenant_id = set_tenant AND determination_set_id = set_id AND calculation_base_code = 'source_plus_dependency_tax' LOOP
                SELECT * INTO STRICT rule FROM mod_tax.tax_rules WHERE tenant_id = set_tenant AND id = component.rule_id;
                SELECT count(*), min(prior.id::text)::uuid INTO match_count, prior_id
                    FROM mod_tax.tax_determination_prior_sets ref JOIN mod_tax.tax_determination_sets prior
                        ON prior.tenant_id = ref.tenant_id AND prior.id = ref.prior_set_id
                    WHERE ref.tenant_id = set_tenant AND ref.determination_set_id = set_id
                      AND ROW(prior.jurisdiction_id,prior.fact_kind,prior.recognition_basis_code,prior.transaction_side)
                          IS NOT DISTINCT FROM ROW(rule.dependency_jurisdiction_id,rule.dependency_fact_kind,rule.dependency_recognition_basis_code,rule.dependency_transaction_side);
                IF match_count <> 1 THEN RAISE EXCEPTION 'dependency policy requires exactly one matching complete prior result'; END IF;
                IF EXISTS (SELECT 1 FROM mod_tax.tax_determinations prior
                    WHERE prior.tenant_id = set_tenant AND prior.determination_set_id = prior_id
                    GROUP BY prior.tax_code_id HAVING count(*) > 1) THEN
                    RAISE EXCEPTION 'prior tax code identity is ambiguous';
                END IF;
                IF rule.dependency_selection = 'tax_code_ids' AND EXISTS (
                    SELECT 1 FROM mod_tax.tax_rule_dependency_codes code
                    WHERE code.tenant_id = set_tenant AND code.rule_id = rule.id
                      AND NOT EXISTS (SELECT 1 FROM mod_tax.tax_determinations prior
                        WHERE prior.tenant_id = set_tenant AND prior.determination_set_id = prior_id
                          AND prior.tax_code_id = code.tax_code_id)) THEN
                    RAISE EXCEPTION 'dependency policy selected a missing prior tax code';
                END IF;
                SELECT count(*) INTO expected_count FROM mod_tax.tax_determinations prior
                    WHERE prior.tenant_id = set_tenant AND prior.determination_set_id = prior_id
                      AND (rule.dependency_selection = 'all_prior' OR EXISTS (
                        SELECT 1 FROM mod_tax.tax_rule_dependency_codes code WHERE code.tenant_id = set_tenant AND code.rule_id = rule.id AND code.tax_code_id = prior.tax_code_id));
                SELECT count(*),COALESCE(sum(tax_amount),0) INTO actual_count,selected_tax
                    FROM mod_tax.tax_determination_dependencies WHERE tenant_id = set_tenant AND determination_id = component.id;
                IF expected_count = 0 OR expected_count <> actual_count
                   OR (rule.dependency_selection = 'tax_code_ids' AND EXISTS (
                       SELECT 1 FROM mod_tax.tax_rule_dependency_codes code
                       WHERE code.tenant_id = set_tenant AND code.rule_id = rule.id
                         AND NOT EXISTS (SELECT 1 FROM mod_tax.tax_determination_dependencies snapshot
                           WHERE snapshot.tenant_id = set_tenant AND snapshot.determination_id = component.id
                             AND snapshot.tax_code_id = code.tax_code_id)))
                   OR (rule.dependency_selection = 'tax_code_ids' AND expected_count <> (SELECT count(*) FROM mod_tax.tax_rule_dependency_codes WHERE tenant_id = set_tenant AND rule_id = rule.id))
                   OR EXISTS (SELECT 1 FROM mod_tax.tax_determination_dependencies snapshot
                      WHERE snapshot.tenant_id = set_tenant AND snapshot.determination_id = component.id
                      AND (snapshot.prior_set_id <> prior_id OR (rule.dependency_selection = 'tax_code_ids' AND NOT EXISTS (
                        SELECT 1 FROM mod_tax.tax_rule_dependency_codes code WHERE code.tenant_id = set_tenant AND code.rule_id = rule.id AND code.tax_code_id = snapshot.tax_code_id))))
                   OR component.base_amount <> (SELECT source_amount + selected_tax FROM mod_tax.tax_determination_sets WHERE tenant_id = set_tenant AND id = set_id)
                THEN RAISE EXCEPTION 'dependency selection membership or base is incomplete'; END IF;
            END LOOP;
        END; $$;
        CREATE OR REPLACE FUNCTION mod_tax.protect_tax_determination_set_seal()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'INSERT' AND NEW.result_seal_state = 'building' AND NEW.result_fingerprint IS NULL THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' AND OLD.result_seal_state = 'building' AND OLD.result_fingerprint IS NULL
               AND NEW.result_seal_state = 'sealed'
               AND ((NEW.contract_version = 1 AND NEW.result_fingerprint ~ '^rv1:[0-9a-f]{64}$')
                   OR (NEW.contract_version = 2 AND NEW.result_fingerprint ~ '^rv2:[0-9a-f]{64}$'))
               AND (to_jsonb(OLD) - ARRAY['result_seal_state','result_fingerprint']) IS NOT DISTINCT FROM
                   (to_jsonb(NEW) - ARRAY['result_seal_state','result_fingerprint']) THEN
                PERFORM mod_tax.validate_tax_dependency_set(NEW.tenant_id,NEW.id);
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'tax evidence is append-only';
        END; $$;
        CREATE OR REPLACE FUNCTION mod_tax.require_tax_determination_set_sealed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent mod_tax.tax_determination_sets%ROWTYPE;
        BEGIN
            SELECT * INTO STRICT parent FROM mod_tax.tax_determination_sets WHERE tenant_id = NEW.tenant_id AND id = NEW.id;
            IF parent.result_seal_state IS DISTINCT FROM 'sealed' OR parent.result_fingerprint IS NULL
               OR NOT ((parent.contract_version = 1 AND parent.result_fingerprint ~ '^rv1:[0-9a-f]{64}$')
                   OR (parent.contract_version = 2 AND parent.result_fingerprint ~ '^rv2:[0-9a-f]{64}$')) THEN
                RAISE EXCEPTION 'new tax determination set must be sealed before commit';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    bind.execute(
        sa.text("SELECT set_config('lock_timeout', :previous, true)"),
        {"previous": caller_timeout},
    )


def downgrade() -> None:
    # Global evidence inspection must not be reduced to the migration session's tenant.
    op.execute("""
        DO $$ DECLARE prior_timeout TEXT := current_setting('lock_timeout'); prior_rls TEXT := current_setting('row_security');
        BEGIN
            PERFORM set_config('lock_timeout','5s',true);
            LOCK TABLE mod_tax.tax_rules, mod_tax.tax_determination_sets,
                mod_tax.tax_determinations, mod_tax.tax_rule_dependency_codes,
                mod_tax.tax_determination_prior_sets, mod_tax.tax_determination_dependencies IN ACCESS EXCLUSIVE MODE;
            PERFORM set_config('lock_timeout',prior_timeout,true);
            PERFORM set_config('row_security','off',true);
            IF EXISTS (SELECT 1 FROM mod_tax.tax_rules WHERE dependency_selection IS NOT NULL)
               OR EXISTS (SELECT 1 FROM mod_tax.tax_determination_sets WHERE contract_version = 2)
               OR EXISTS (SELECT 1 FROM mod_tax.tax_rule_dependency_codes)
               OR EXISTS (SELECT 1 FROM mod_tax.tax_determination_prior_sets)
               OR EXISTS (SELECT 1 FROM mod_tax.tax_determination_dependencies) THEN
                RAISE EXCEPTION 'cannot downgrade populated V2 tax dependency evidence';
            END IF;
            PERFORM set_config('row_security',prior_rls,true);
        END; $$;
    """)
    # Restore predecessor trigger programs as frozen SQL, with no runtime source inspection.
    op.execute("""
CREATE OR REPLACE FUNCTION mod_tax.protect_tax_determination_set_seal()
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
CREATE OR REPLACE FUNCTION mod_tax.require_tax_determination_set_sealed()
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
CREATE OR REPLACE FUNCTION mod_tax.require_building_tax_result_parent()
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
CREATE OR REPLACE FUNCTION mod_tax.require_tax_rule_published()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM mod_tax.tax_rules
                WHERE tenant_id = NEW.tenant_id AND id = NEW.id AND is_published) THEN
                RAISE EXCEPTION 'tax rule must be published before commit';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        DROP TABLE mod_tax.tax_determination_dependencies;
        DROP TABLE mod_tax.tax_determination_prior_sets;
        DROP TABLE mod_tax.tax_rule_dependency_codes;
        DROP FUNCTION mod_tax.validate_tax_dependency_component();
        DROP FUNCTION mod_tax.validate_tax_prior_set();
        DROP FUNCTION mod_tax.validate_tax_rule_dependency_code();
        DROP FUNCTION mod_tax.validate_tax_dependency_set(UUID,UUID);
        ALTER TABLE mod_tax.tax_rules DROP CONSTRAINT ck_tax_rules_dependency_policy,
            DROP CONSTRAINT fk_tax_rules_dependency_jurisdiction,
            DROP COLUMN dependency_jurisdiction_id, DROP COLUMN dependency_fact_kind,
            DROP COLUMN dependency_recognition_basis_code, DROP COLUMN dependency_transaction_side,
            DROP COLUMN dependency_selection;
        ALTER TABLE mod_tax.tax_rules DROP CONSTRAINT ck_tax_rules_calculation_base;
        ALTER TABLE mod_tax.tax_rules ADD CONSTRAINT ck_tax_rules_calculation_base CHECK (calculation_base_code IN ('source_amount','source_plus_prior_tax'));
        ALTER TABLE mod_tax.tax_determinations DROP COLUMN selected_dependency_policy;
        ALTER TABLE mod_tax.tax_determinations DROP CONSTRAINT ck_tax_determinations_calculation_base;
        ALTER TABLE mod_tax.tax_determinations ADD CONSTRAINT ck_tax_determinations_calculation_base CHECK (calculation_base_code IN ('source_amount','source_plus_prior_tax'));
        ALTER TABLE mod_tax.tax_determination_sets DROP CONSTRAINT ck_tax_determination_sets_result_seal;
        ALTER TABLE mod_tax.tax_determination_sets DROP CONSTRAINT ck_tax_sets_contract_version,
            DROP COLUMN contract_version, DROP COLUMN commercial_source_ref, DROP COLUMN commercial_source_version;
        ALTER TABLE mod_tax.tax_determination_sets ADD CONSTRAINT ck_tax_determination_sets_result_seal CHECK (
            (result_seal_state IS NULL AND result_fingerprint IS NULL)
            OR (result_seal_state = 'building' AND result_fingerprint IS NULL)
            OR (result_seal_state = 'sealed' AND result_fingerprint ~ '^rv1:[0-9a-f]{64}$')) NOT VALID;
    """)
