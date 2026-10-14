"""Permanent scope custody window and restricted opening admission principal.

Fence writers for upgrade. Existing receipts remain untouched; no window or
principal is inferred. Products explicitly provision a restricted principal and
grant its exact Billing/idempotency/outbox DML closure before binding it. Never
grant membership in app_user/platform_api/app_admin. No LOGIN role is created.

For each selected plane, the opening principal needs schema USAGE, SELECT on
the named Billing plane tables, UPDATE(generation) on opening_windows and
UPDATE(id) on billing_accounts, and INSERT on documents, posting_groups,
posting_effects, accounting_facts, receivable_position_facts,
receivable_position_facts_v2, receivable_exposure_facts_v2,
opening_import_receipts, opening_cash_lots, opening_cash_effects,
opening_receivable_bases, opening_historical_discharge_claims,
cash_source_claims, receivable_source_claims, opening_window_admissions and
opening_window_seals. It needs SELECT/INSERT on the selected plane's Kernel
idempotency_records and outbox_events only. Tenant tables retain FORCE RLS;
platform names carry platform_. There is no opening_windows INSERT grant to
this principal. Required grants are product-owned explicit provisioning, not
issued by this migration. Missing grants fail the caller operation closed.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0009_opening_window"
down_revision = "bi_0008_opening_receivables"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLES = ("opening_windows", "opening_window_admissions", "opening_window_seals")


def _tables(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    for kind in _TABLES:
        table = prefix + kind
        columns: list[SchemaItem] = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ]
        if not platform:
            columns.append(
                sa.ForeignKeyConstraint(
                    ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
                )
            )
        if kind == "opening_windows":
            columns.extend(
                [
                    sa.Column("epoch", sa.Uuid(), nullable=False),
                    sa.Column("manifest_version", sa.Integer(), nullable=False),
                    sa.Column("manifest_fingerprint", sa.String(64), nullable=False),
                    sa.Column("principal_name", sa.String(63), nullable=False),
                    sa.Column("principal_oid", sa.BigInteger(), nullable=False),
                    sa.Column(
                        "provision_authority_ref", sa.String(255), nullable=False
                    ),
                    sa.Column(
                        "generation",
                        sa.BigInteger(),
                        nullable=False,
                        server_default="0",
                    ),
                    sa.Column("evidence", sa.JSON(), nullable=False),
                    sa.Column("evidence_digest", sa.String(64), nullable=False),
                    sa.CheckConstraint("manifest_version IN (1,2) AND generation >= 0"),
                ]
            )
            if not platform:
                columns.extend(
                    [
                        sa.UniqueConstraint(
                            "tenant_id", name="uq_opening_window_scope"
                        ),
                        sa.UniqueConstraint(
                            "tenant_id", "id", name="uq_opening_window_scoped_id"
                        ),
                    ]
                )
        else:
            columns.extend(
                [
                    sa.Column("window_id", sa.Uuid(), nullable=False),
                    sa.ForeignKeyConstraint(
                        [*scope, "window_id"],
                        [
                            f"{_SCHEMA}.{prefix}opening_windows.{key}"
                            for key in (*scope, "id")
                        ],
                        ondelete="RESTRICT",
                    ),
                ]
            )
            if kind == "opening_window_admissions":
                columns.extend(
                    [
                        sa.Column("receipt_id", sa.Uuid(), nullable=False),
                        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
                        sa.Column("request_identity", sa.String(255), nullable=False),
                        sa.Column("request_digest", sa.String(64), nullable=False),
                        sa.Column(
                            "manifest_fingerprint", sa.String(64), nullable=False
                        ),
                        sa.UniqueConstraint(
                            *scope,
                            "receipt_id",
                            name=f"uq_{prefix}window_admission_receipt",
                        ),
                        sa.ForeignKeyConstraint(
                            [*scope, "receipt_id", "billing_account_id"],
                            [
                                f"{_SCHEMA}.{prefix}opening_import_receipts.{key}"
                                for key in (*scope, "id", "billing_account_id")
                            ],
                            ondelete="RESTRICT",
                        ),
                    ]
                )
            else:
                columns.extend(
                    [
                        sa.Column("population_digest", sa.String(64), nullable=False),
                        sa.Column("population_count", sa.BigInteger(), nullable=False),
                        sa.Column("coupled_watermark", sa.Text(), nullable=False),
                        sa.Column("command_digest", sa.String(64), nullable=False),
                        sa.Column("evidence", sa.JSON(), nullable=False),
                        sa.Column("evidence_digest", sa.String(64), nullable=False),
                        sa.UniqueConstraint(
                            *scope, "window_id", name=f"uq_{prefix}window_seal"
                        ),
                        sa.CheckConstraint("population_count >= 0"),
                    ]
                )
        op.create_table(table, *columns, schema=_SCHEMA)
        if platform:
            op.execute(
                f"REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;"
            )
        else:
            op.execute(f"""
                ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY tenant_isolation ON mod_billing.{table}
                  USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
                REVOKE ALL ON mod_billing.{table} FROM PUBLIC, app_user, platform_api;
            """)
        online = "platform_api" if platform else "app_user"
        op.execute(f"GRANT SELECT ON mod_billing.{table} TO {online};")
    if platform:
        op.execute(
            "CREATE UNIQUE INDEX uq_platform_opening_window_singleton ON mod_billing.platform_opening_windows ((true));"
        )


def _guards(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND w.tenant_id = tenant"
    parameter = "" if platform else "tenant uuid"
    argument = "" if platform else "NEW.tenant_id"
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    tenant_values = "" if platform else "tenant_id, "
    tenant_new = "" if platform else "NEW.tenant_id, "
    op.execute(f"""
    CREATE FUNCTION mod_billing.check_{prefix}opening_principal(role_oid bigint, role_name text) RETURNS void
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE principal record; online text; relation text; privilege text;
    BEGIN
      SELECT * INTO principal FROM pg_catalog.pg_roles WHERE oid = role_oid AND rolname = role_name;
      IF principal.oid IS NULL OR principal.rolname IN ('app_user','platform_api','app_admin')
        OR principal.rolsuper OR principal.rolbypassrls OR principal.rolcreaterole
        OR principal.rolcreatedb OR principal.rolreplication
        OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE
          (r.rolname IN ('app_user','platform_api','app_admin') OR r.rolsuper OR r.rolbypassrls
            OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication)
          AND pg_catalog.pg_has_role(principal.oid, r.oid, 'MEMBER'))
        OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname IN ('app_user','platform_api')
          AND pg_catalog.pg_has_role(r.oid, principal.oid, 'MEMBER'))
      THEN RAISE EXCEPTION 'opening principal binding is unavailable or unsafe' USING ERRCODE = '42501'; END IF;
      FOREACH online IN ARRAY ARRAY['app_user','platform_api'] LOOP
        FOREACH relation IN ARRAY ARRAY[
          'mod_billing.{prefix}opening_windows', 'mod_billing.{prefix}opening_window_admissions',
          'mod_billing.{prefix}opening_window_seals', 'mod_billing.{prefix}opening_cash_lots',
          'mod_billing.{prefix}opening_receivable_bases', 'mod_billing.{prefix}opening_historical_discharge_claims'] LOOP
          IF pg_catalog.has_table_privilege(online, relation, 'INSERT')
             OR pg_catalog.has_any_column_privilege(online, relation, 'INSERT')
          THEN RAISE EXCEPTION 'ordinary opening INSERT privilege remains effective' USING ERRCODE = '42501'; END IF;
          FOREACH privilege IN ARRAY ARRAY['UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] LOOP
            IF pg_catalog.has_table_privilege(online, relation, privilege)
              OR CASE WHEN privilege IN ('UPDATE','REFERENCES') THEN pg_catalog.has_any_column_privilege(online, relation, privilege) ELSE false END
            THEN RAISE EXCEPTION 'ordinary custody mutation privilege remains effective' USING ERRCODE = '42501'; END IF;
          END LOOP;
        END LOOP;
      END LOOP;
    END $$;

    CREATE FUNCTION mod_billing.touch_{prefix}opening_window({parameter}) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record;
    BEGIN
      SELECT * INTO bound FROM mod_billing.{prefix}opening_windows w WHERE true{scope};
      IF bound.id IS NULL THEN RAISE EXCEPTION 'opening window is required' USING ERRCODE = '23514'; END IF;
      PERFORM mod_billing.check_{prefix}opening_principal(bound.principal_oid, bound.principal_name);
      IF current_user <> bound.principal_name OR (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) <> bound.principal_oid
      THEN RAISE EXCEPTION 'restricted opening principal required' USING ERRCODE = '42501'; END IF;
      UPDATE mod_billing.{prefix}opening_windows w SET generation = w.generation + 1 WHERE w.id = bound.id{scope};
      IF EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_window_seals s WHERE s.window_id = bound.id)
      THEN RAISE EXCEPTION 'opening window is sealed' USING ERRCODE = '23514'; END IF;
      RETURN bound.id;
    END $$;

    CREATE FUNCTION mod_billing.guard_{prefix}opening_window() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'opening custody is permanent' USING ERRCODE = '23514'; END IF;
      PERFORM mod_billing.check_{prefix}opening_principal(NEW.principal_oid, NEW.principal_name);
      IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - 'generation') IS DISTINCT FROM (to_jsonb(OLD) - 'generation')
          OR NEW.generation <> OLD.generation + 1 OR current_user <> NEW.principal_name
          OR (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) <> NEW.principal_oid
        THEN RAISE EXCEPTION 'opening window binding is immutable' USING ERRCODE = '42501'; END IF;
      ELSE
        IF current_user <> 'app_admin' OR NEW.generation <> 0
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x WHERE x.receipt_kind = 'import'{new_scope})
          OR NEW.evidence::jsonb->'window'->'scope' IS DISTINCT FROM {scope_json}
          OR NEW.evidence::jsonb->'window'->>'window_id' IS DISTINCT FROM NEW.id::text
          OR NEW.evidence::jsonb->'window'->>'epoch' IS DISTINCT FROM NEW.epoch::text
          OR (NEW.evidence::jsonb->'window'->>'manifest_version')::integer IS DISTINCT FROM NEW.manifest_version
          OR NEW.evidence::jsonb->'window'->>'manifest_fingerprint' IS DISTINCT FROM NEW.manifest_fingerprint
          OR NEW.evidence::jsonb->'window'->>'principal_name' IS DISTINCT FROM NEW.principal_name
          OR (NEW.evidence::jsonb->'window'->>'principal_oid')::bigint IS DISTINCT FROM NEW.principal_oid
          OR NEW.evidence::jsonb->'window'->>'provision_authority_ref' IS DISTINCT FROM NEW.provision_authority_ref
          OR nullif(trim(NEW.provision_authority_ref),'') IS NULL
          OR NEW.evidence::jsonb->'window'->'manifest'->'scope' IS DISTINCT FROM {scope_json}
          OR jsonb_typeof(NEW.evidence::jsonb->'window'->'manifest'->'population') IS DISTINCT FROM 'array'
          OR jsonb_typeof(NEW.evidence::jsonb->'window'->'manifest'->'controls') IS DISTINCT FROM 'array'
          OR NEW.evidence::jsonb->'verification'->>'operation' IS DISTINCT FROM 'billing.opening.window.provision.v1'
          OR NEW.evidence::jsonb->'verification'->'scope' IS DISTINCT FROM {scope_json}
          OR NEW.evidence::jsonb->'verification'->>'window_id' IS DISTINCT FROM NEW.id::text
          OR NEW.evidence::jsonb->'verification'->>'epoch' IS DISTINCT FROM NEW.epoch::text
          OR NEW.evidence::jsonb->'verification'->>'manifest_fingerprint' IS DISTINCT FROM NEW.manifest_fingerprint
          OR NEW.evidence::jsonb->'verification'->>'principal_name' IS DISTINCT FROM NEW.principal_name
          OR (NEW.evidence::jsonb->'verification'->>'principal_oid')::bigint IS DISTINCT FROM NEW.principal_oid
          OR NEW.evidence::jsonb->'verification'->>'authority_ref' IS DISTINCT FROM NEW.provision_authority_ref
          OR nullif(trim(NEW.evidence::jsonb->'verification'->>'complete_population_proof_ref'),'') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'verification'->>'authority_proof_ref'),'') IS NULL
        THEN RAISE EXCEPTION 'explicit migrator provision required; no historical bootstrap' USING ERRCODE = '42501'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_window_guard BEFORE INSERT OR UPDATE OR DELETE ON mod_billing.{prefix}opening_windows
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_window();

    CREATE FUNCTION mod_billing.guard_{prefix}opening_entry() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE opening boolean := false; window_id uuid;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}documents' THEN
        opening := NEW.authoring_mode = 'imported_open_item';
        IF TG_OP = 'UPDATE' THEN opening := opening OR OLD.authoring_mode = 'imported_open_item'; END IF;
      ELSIF TG_TABLE_NAME = '{prefix}opening_import_receipts' THEN opening := NEW.receipt_kind = 'import';
      ELSIF TG_TABLE_NAME = '{prefix}opening_cash_effects' THEN opening := NEW.effect_kind = 'recognition';
      ELSIF TG_TABLE_NAME = '{prefix}posting_groups' THEN opening := NEW.group_kind IN ('opening_cash_imported','opening_account_imported');
      ELSIF TG_TABLE_NAME = '{prefix}posting_effects' THEN
        SELECT x.group_kind IN ('opening_cash_imported','opening_account_imported') INTO opening
          FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope};
      ELSE opening := true;
      END IF;
      IF opening THEN window_id := mod_billing.touch_{prefix}opening_window({argument}); END IF;
      RETURN NEW;
    END $$;
    """)  # nosec B608 -- closed plane identifiers and fixed scope/manifest expressions
    for table in (
        "documents",
        "opening_import_receipts",
        "opening_cash_lots",
        "opening_receivable_bases",
        "opening_historical_discharge_claims",
        "opening_cash_effects",
        "posting_groups",
        "posting_effects",
    ):
        op.execute(
            f"CREATE TRIGGER a00_{prefix}{table}_opening_window BEFORE INSERT ON mod_billing.{prefix}{table} FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_entry();"
        )
    op.execute(
        f"CREATE TRIGGER a00_{prefix}documents_opening_transition BEFORE UPDATE OF authoring_mode ON mod_billing.{prefix}documents FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_entry();"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.capture_{prefix}opening_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record;
    BEGIN
      IF NEW.receipt_kind <> 'import' THEN RETURN NEW; END IF;
      SELECT * INTO bound FROM mod_billing.{prefix}opening_windows x WHERE true{new_scope};
      IF NEW.manifest_fingerprint <> bound.manifest_fingerprint
        OR bound.manifest_version <> CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 2 ELSE 1 END
        OR NEW.evidence::jsonb->'manifest' IS DISTINCT FROM bound.evidence::jsonb->'window'->'manifest'
      THEN RAISE EXCEPTION 'opening window binds one complete manifest' USING ERRCODE = '23514'; END IF;
      INSERT INTO mod_billing.{prefix}opening_window_admissions
        (id, {tenant_values}window_id, receipt_id, billing_account_id, request_identity, request_digest, manifest_fingerprint)
        VALUES (NEW.id, {tenant_new}bound.id, NEW.id, NEW.billing_account_id, NEW.request_identity, NEW.request_digest, NEW.manifest_fingerprint);
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_window_receipt_admission AFTER INSERT ON mod_billing.{prefix}opening_import_receipts
      FOR EACH ROW EXECUTE FUNCTION mod_billing.capture_{prefix}opening_admission();
    CREATE FUNCTION mod_billing.guard_{prefix}opening_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound uuid; receipt record;
    BEGIN
      bound := mod_billing.touch_{prefix}opening_window({argument});
      SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = NEW.receipt_id{new_scope};
      IF bound <> NEW.window_id OR receipt.id IS NULL OR receipt.receipt_kind <> 'import'
        OR (NEW.billing_account_id, NEW.request_identity, NEW.request_digest, NEW.manifest_fingerprint)
          IS DISTINCT FROM (receipt.billing_account_id, receipt.request_identity, receipt.request_digest, receipt.manifest_fingerprint)
      THEN RAISE EXCEPTION 'admission requires exact original receipt' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_window_admission_guard BEFORE INSERT ON mod_billing.{prefix}opening_window_admissions
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_admission();

    CREATE FUNCTION mod_billing.guard_{prefix}opening_seal() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record; identity uuid; command jsonb; proof jsonb;
      actual jsonb; proposed jsonb; expected_accounts jsonb; actual_accounts jsonb;
    BEGIN
      identity := mod_billing.touch_{prefix}opening_window({argument});
      SELECT * INTO bound FROM mod_billing.{prefix}opening_windows x WHERE x.id = identity{new_scope};
      command := NEW.evidence::jsonb->'command'; proof := NEW.evidence::jsonb->'verification';
      SELECT coalesce(jsonb_agg(jsonb_build_object(
          'scope', {scope_json}, 'window_id', x.window_id::text,
          'receipt_id', x.receipt_id::text, 'billing_account_id', x.billing_account_id::text,
          'request_identity', x.request_identity, 'request_digest', x.request_digest,
          'manifest_fingerprint', x.manifest_fingerprint) ORDER BY x.receipt_id::text), '[]'::jsonb)
        INTO actual FROM mod_billing.{prefix}opening_window_admissions x WHERE x.window_id = identity{new_scope};
      SELECT coalesce(jsonb_agg(e ORDER BY e->>'receipt_id'), '[]'::jsonb)
        INTO proposed FROM jsonb_array_elements(command->'admissions') e;
      SELECT coalesce(jsonb_agg(account ORDER BY account), '[]'::jsonb) INTO expected_accounts FROM (
        SELECT DISTINCT e->'partition'->>'account_id' AS account
        FROM jsonb_array_elements(
          (bound.evidence::jsonb->'window'->'manifest'->'cash_lots') ||
          coalesce(bound.evidence::jsonb->'window'->'manifest'->'receivables', '[]'::jsonb)) e
        WHERE (e->>'residual_minor')::numeric > 0
      ) frozen;
      SELECT coalesce(jsonb_agg(x.billing_account_id::text ORDER BY x.billing_account_id::text), '[]'::jsonb)
        INTO actual_accounts FROM mod_billing.{prefix}opening_window_admissions x WHERE x.window_id = identity{new_scope};
      IF NEW.window_id <> identity OR command->'scope' IS DISTINCT FROM {scope_json}
        OR command->>'window_id' IS DISTINCT FROM identity::text
        OR command->>'epoch' IS DISTINCT FROM bound.epoch::text
        OR command->>'manifest_fingerprint' IS DISTINCT FROM bound.manifest_fingerprint
        OR command->>'coupled_watermark' IS DISTINCT FROM NEW.coupled_watermark
        OR actual IS DISTINCT FROM proposed OR actual_accounts IS DISTINCT FROM expected_accounts
        OR NEW.population_count <> jsonb_array_length(actual)
        OR (NEW.evidence::jsonb->>'population_count')::bigint IS DISTINCT FROM NEW.population_count
        OR NEW.evidence::jsonb->>'population_digest' IS DISTINCT FROM NEW.population_digest
        OR proof->>'operation' IS DISTINCT FROM 'billing.opening.window.seal.v1'
        OR proof->'scope' IS DISTINCT FROM {scope_json}
        OR proof->>'window_id' IS DISTINCT FROM identity::text
        OR proof->>'epoch' IS DISTINCT FROM bound.epoch::text
        OR proof->>'manifest_fingerprint' IS DISTINCT FROM bound.manifest_fingerprint
        OR proof->>'command_digest' IS DISTINCT FROM NEW.command_digest
        OR proof->>'population_digest' IS DISTINCT FROM NEW.population_digest
        OR (proof->>'population_count')::bigint IS DISTINCT FROM NEW.population_count
        OR proof->>'coupled_watermark' IS DISTINCT FROM NEW.coupled_watermark
        OR proof->>'authority_ref' IS DISTINCT FROM command->>'authority_ref'
        OR nullif(trim(proof->>'complete_population_proof_ref'),'') IS NULL
        OR nullif(trim(proof->>'coupled_watermark_proof_ref'),'') IS NULL
        OR nullif(trim(proof->>'authority_proof_ref'),'') IS NULL
        OR nullif(trim(NEW.coupled_watermark),'') IS NULL
      THEN RAISE EXCEPTION 'seal requires complete frozen population and coupled watermark authority' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_window_seal_guard BEFORE INSERT ON mod_billing.{prefix}opening_window_seals
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_seal();
    """)  # nosec B608 -- closed plane identifiers and fixed scope/manifest expressions
    for table in ("opening_window_admissions", "opening_window_seals"):
        op.execute(
            f"CREATE TRIGGER {prefix}{table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{prefix}{table} FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();"
        )
    online = "platform_api" if platform else "app_user"
    for table in (
        "opening_cash_lots",
        "opening_receivable_bases",
        "opening_historical_discharge_claims",
    ):
        op.execute(
            f"REVOKE INSERT ON mod_billing.{prefix}{table} FROM PUBLIC, {online};"
        )


def upgrade() -> None:
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in selected_module_planes("billing"):
            platform = plane is ModulePlane.PLATFORM
            _tables(platform)
            _guards(platform)


def downgrade() -> None:
    raise RuntimeError(
        "Billing opening custody cannot be erased or reopened by downgrade"
    )
