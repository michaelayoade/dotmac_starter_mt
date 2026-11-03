"""Post-seal admission of sealed-hold partitions (recorded widening, D-5).

The seal stays permanent. It now records a hold list (``held_account_ids`` in the
sealed command): manifest partitions deliberately left out of the admitted
population. Only those accounts may later receive an opening import, one
partition at a time, and every such import must be followed in the same
transaction by an immutable ``opening_hold_admissions`` row carrying a reviewed
disposition digest and maker-checker evidence (maker is never a checker).

Replaced functions keep their triggers. The seal guard compares the admitted
population with the frozen positive-residual accounts MINUS the hold list. The
entry guard consults ``touch_*opening_window_held`` only once a seal exists; an
account not on the hold list, or already admitted, is refused exactly as before.
The restricted opening principal needs INSERT on ``opening_hold_admissions`` in
addition to the bi_0009 closure; product-owned explicit provisioning grants it.
No downgrade: custody cannot be reopened.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0013_opening_hold_admission"
down_revision = "bi_0012_tax_evidence"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"


def _table(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    table = prefix + "opening_hold_admissions"
    columns: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("disposition_digest", sa.String(71), nullable=False),
        sa.Column("maker_ref", sa.String(255), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("approver_refs", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            *scope,
            "billing_account_id",
            name=f"uq_{prefix}hold_admission_account",
        ),
        sa.UniqueConstraint(
            *scope, "receipt_id", name=f"uq_{prefix}hold_admission_receipt"
        ),
        sa.ForeignKeyConstraint(
            [*scope, "window_id"],
            [f"{_SCHEMA}.{prefix}opening_windows.{k}" for k in (*scope, "id")],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [*scope, "receipt_id", "billing_account_id"],
            [
                f"{_SCHEMA}.{prefix}opening_import_receipts.{k}"
                for k in (*scope, "id", "billing_account_id")
            ],
            ondelete="RESTRICT",
        ),
    ]
    if not platform:
        columns.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
            )
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
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table} FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();"
    )


def _guards(platform: bool) -> None:
    p = "platform_" if platform else ""
    scope = "" if platform else " AND w.tenant_id = tenant"
    param = "" if platform else "tenant uuid, "
    argument = "" if platform else "NEW.tenant_id"
    held_args = "NEW.tenant_id, " if not platform else ""
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    seal_scope = "" if platform else " WHERE s.tenant_id = NEW.tenant_id"
    receipt_scope = "" if platform else " AND r.tenant_id = NEW.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.touch_{p}opening_window_held({param}acct uuid) RETURNS uuid
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record; sealed record;
    BEGIN
      SELECT * INTO bound FROM mod_billing.{p}opening_windows w WHERE true{scope};
      IF bound.id IS NULL THEN RAISE EXCEPTION 'opening window is required' USING ERRCODE = '23514'; END IF;
      PERFORM mod_billing.check_{p}opening_principal(bound.principal_oid, bound.principal_name);
      IF current_user <> bound.principal_name OR (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user) <> bound.principal_oid
      THEN RAISE EXCEPTION 'restricted opening principal required' USING ERRCODE = '42501'; END IF;
      UPDATE mod_billing.{p}opening_windows w SET generation = w.generation + 1 WHERE w.id = bound.id{scope};
      SELECT * INTO sealed FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = bound.id;
      IF sealed.id IS NULL THEN RAISE EXCEPTION 'opening window is not sealed' USING ERRCODE = '23514'; END IF;
      IF acct IS NULL OR NOT ((sealed.evidence::jsonb->'command'->'held_account_ids') ? acct::text)
      THEN RAISE EXCEPTION 'opening window is sealed' USING ERRCODE = '23514'; END IF;
      IF EXISTS (SELECT 1 FROM mod_billing.{p}opening_hold_admissions h WHERE h.window_id = bound.id AND h.billing_account_id = acct)
      THEN RAISE EXCEPTION 'held partition is already admitted' USING ERRCODE = '23514'; END IF;
      RETURN bound.id;
    END $$;

    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_entry() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE opening boolean := false; window_id uuid; acct uuid; sealed boolean;
    BEGIN
      IF TG_TABLE_NAME = '{p}documents' THEN
        opening := NEW.authoring_mode = 'imported_open_item';
        IF TG_OP = 'UPDATE' THEN opening := opening OR OLD.authoring_mode = 'imported_open_item'; END IF;
      ELSIF TG_TABLE_NAME = '{p}opening_import_receipts' THEN opening := NEW.receipt_kind = 'import';
      ELSIF TG_TABLE_NAME = '{p}opening_cash_effects' THEN opening := NEW.effect_kind = 'recognition';
      ELSIF TG_TABLE_NAME = '{p}posting_groups' THEN opening := NEW.group_kind IN ('opening_cash_imported','opening_account_imported');
      ELSIF TG_TABLE_NAME = '{p}posting_effects' THEN
        SELECT x.group_kind IN ('opening_cash_imported','opening_account_imported') INTO opening
          FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope};
      ELSE opening := true;
      END IF;
      IF opening THEN
        IF TG_TABLE_NAME = '{p}posting_effects' THEN
          SELECT x.billing_account_id INTO acct FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope};
        ELSE
          acct := nullif(to_jsonb(NEW)->>'billing_account_id','')::uuid;
        END IF;
        SELECT EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s{seal_scope}) INTO sealed;
        IF sealed THEN
          IF TG_TABLE_NAME = '{p}opening_import_receipts' AND EXISTS (
            SELECT 1 FROM mod_billing.{p}opening_import_receipts r
            WHERE r.billing_account_id = acct AND r.receipt_kind = 'import'{receipt_scope})
          THEN RAISE EXCEPTION 'held partition already has an opening import' USING ERRCODE = '23514'; END IF;
          window_id := mod_billing.touch_{p}opening_window_held({held_args}acct);
        ELSE
          window_id := mod_billing.touch_{p}opening_window({argument});
        END IF;
      END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION mod_billing.capture_{p}opening_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record;
    BEGIN
      IF NEW.receipt_kind <> 'import' THEN RETURN NEW; END IF;
      SELECT * INTO bound FROM mod_billing.{p}opening_windows x WHERE true{new_scope};
      IF NEW.manifest_fingerprint <> bound.manifest_fingerprint
        OR bound.manifest_version <> (CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 2 ELSE 1 END)
        OR NEW.evidence::jsonb->'manifest' IS DISTINCT FROM bound.evidence::jsonb->'window'->'manifest'
      THEN RAISE EXCEPTION 'opening window binds one complete manifest' USING ERRCODE = '23514'; END IF;
      -- A sealed window's population is immutable: a held admission is audited in
      -- opening_hold_admissions, never added to the sealed population.
      IF EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = bound.id) THEN RETURN NEW; END IF;
      INSERT INTO mod_billing.{p}opening_window_admissions
        (id, {"" if platform else "tenant_id, "}window_id, receipt_id, billing_account_id, request_identity, request_digest, manifest_fingerprint)
        VALUES (NEW.id, {held_args}bound.id, NEW.id, NEW.billing_account_id, NEW.request_identity, NEW.request_digest, NEW.manifest_fingerprint);
      RETURN NEW;
    END $$;

    CREATE FUNCTION mod_billing.guard_{p}opening_hold_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound uuid; receipt record; refs jsonb; ev jsonb;
    BEGIN
      bound := mod_billing.touch_{p}opening_window_held({held_args}NEW.billing_account_id);
      SELECT * INTO receipt FROM mod_billing.{p}opening_import_receipts x WHERE x.id = NEW.receipt_id{new_scope};
      refs := NEW.approver_refs::jsonb; ev := NEW.evidence::jsonb;
      IF bound <> NEW.window_id OR receipt.id IS NULL OR receipt.receipt_kind <> 'import'
        OR receipt.billing_account_id <> NEW.billing_account_id
        OR NEW.disposition_digest !~ '^sha256:[0-9a-f]{{64}}$'
        OR nullif(trim(NEW.maker_ref),'') IS NULL
        OR jsonb_typeof(refs) IS DISTINCT FROM 'array' OR jsonb_array_length(refs) = 0
        OR refs ? NEW.maker_ref
        OR ev->>'maker_ref' IS DISTINCT FROM NEW.maker_ref
        OR ev->'approval'->>'requester_ref' IS DISTINCT FROM NEW.maker_ref
        OR ev->'approval'->'approver_refs' IS DISTINCT FROM refs
        OR ev->'approval'->>'request_id' IS DISTINCT FROM NEW.approval_request_id::text
        OR ev->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text
        OR ev->>'billing_account_id' IS DISTINCT FROM NEW.billing_account_id::text
        OR ev->>'window_id' IS DISTINCT FROM NEW.window_id::text
        OR ev->'disposition'->>'disposition_digest' IS DISTINCT FROM NEW.disposition_digest
      THEN RAISE EXCEPTION 'hold admission requires exact receipt, disposition and maker-checker evidence' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_hold_admission_guard BEFORE INSERT ON mod_billing.{p}opening_hold_admissions
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_hold_admission();

    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_seal() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record; identity uuid; command jsonb; proof jsonb;
      actual jsonb; proposed jsonb; expected_accounts jsonb; actual_accounts jsonb;
      held jsonb;
    BEGIN
      identity := mod_billing.touch_{p}opening_window({argument});
      SELECT * INTO bound FROM mod_billing.{p}opening_windows x WHERE x.id = identity{new_scope};
      command := NEW.evidence::jsonb->'command'; proof := NEW.evidence::jsonb->'verification';
      held := coalesce(command->'held_account_ids', '[]'::jsonb);
      SELECT coalesce(jsonb_agg(jsonb_build_object(
          'scope', {scope_json}, 'window_id', x.window_id::text,
          'receipt_id', x.receipt_id::text, 'billing_account_id', x.billing_account_id::text,
          'request_identity', x.request_identity, 'request_digest', x.request_digest,
          'manifest_fingerprint', x.manifest_fingerprint) ORDER BY x.receipt_id::text), '[]'::jsonb)
        INTO actual FROM mod_billing.{p}opening_window_admissions x WHERE x.window_id = identity{new_scope};
      SELECT coalesce(jsonb_agg(e ORDER BY e->>'receipt_id'), '[]'::jsonb)
        INTO proposed FROM jsonb_array_elements(command->'admissions') e;
      IF jsonb_typeof(held) IS DISTINCT FROM 'array'
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(held) h GROUP BY h HAVING count(*) > 1)
        OR EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(held) h WHERE NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(
              (bound.evidence::jsonb->'window'->'manifest'->'cash_lots') ||
              coalesce(bound.evidence::jsonb->'window'->'manifest'->'receivables', '[]'::jsonb)) e
            WHERE e->'partition'->>'account_id' = h AND (e->>'residual_minor')::numeric > 0))
      THEN RAISE EXCEPTION 'seal hold list must name frozen manifest partitions' USING ERRCODE = '23514'; END IF;
      SELECT coalesce(jsonb_agg(account ORDER BY account), '[]'::jsonb) INTO expected_accounts FROM (
        SELECT DISTINCT e->'partition'->>'account_id' AS account
        FROM jsonb_array_elements(
          (bound.evidence::jsonb->'window'->'manifest'->'cash_lots') ||
          coalesce(bound.evidence::jsonb->'window'->'manifest'->'receivables', '[]'::jsonb)) e
        WHERE (e->>'residual_minor')::numeric > 0
          AND NOT (held ? (e->'partition'->>'account_id'))
      ) frozen;
      SELECT coalesce(jsonb_agg(x.billing_account_id::text ORDER BY x.billing_account_id::text), '[]'::jsonb)
        INTO actual_accounts FROM mod_billing.{p}opening_window_admissions x WHERE x.window_id = identity{new_scope};
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
    """)  # nosec B608 -- closed plane identifiers and fixed scope/manifest expressions


def upgrade() -> None:
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in selected_module_planes("billing"):
            platform = plane is ModulePlane.PLATFORM
            _table(platform)
            _guards(platform)


def downgrade() -> None:
    raise RuntimeError(
        "Billing opening custody cannot be erased or reopened by downgrade"
    )
