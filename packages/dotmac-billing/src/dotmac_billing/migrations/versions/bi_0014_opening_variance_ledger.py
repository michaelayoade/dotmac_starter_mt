"""Opening variance ledger: append-only variances, lifecycle events, control gaps.

Every variance is a typed, append-only record (never a metadata key) created by
the restricted opening principal inside the import, the batch-variance step of
the seal, or a post-seal held-partition admission. A lifecycle event log
(``recorded -> resolving -> partially_resolved|resolved``, plus
``voided_by_import_reversal`` before the seal) is a state machine enforced here:
resolution sign and cumulative bound, kind per basis, and a ``position_corrected``
event must name a posting group of the same account, currency and lane whose
effect equals the amount.

Replaced functions keep their triggers. The seal guard now (a) defines the
carryable population as ``T != 0 or any variance``, (b) requires the sealed hold
list to be the manifest's partition-level holds plus typed operational holds,
and (c) refuses unless the recorded variance set equals the manifest set and
every closure row (source - transferred - variance) is exactly zero. The
hold-admission guard re-runs (a) and (b) for the one admitted account.

The restricted opening principal needs INSERT on the four new tables in addition
to the bi_0013 closure; ``app_user`` needs INSERT on the two event tables (the
governed resolution path) and SELECT on all four. No downgrade.
"""

from __future__ import annotations

# All identifiers are closed Billing plane/table literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0014_opening_variance_ledger"
down_revision = "bi_0013_opening_hold_admission"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"

_LANES = "('receivable','customer_credit','prepaid_funding')"
_BASES = "('ledger_lane','document_residual','stored_balance','rebuild')"
_KINDS = (
    "('invoice','settlement','lane','ledger_entry','credit_note','payment',"
    "'control_row','rebuild_account','partition_remainder')"
)
_EVENTS = "('recorded','resolving','partially_resolved','resolved','voided_by_import_reversal')"
_ITEM_CODES = (
    "v1_vat_evidence_missing|original_evidence_missing|status_residual_conflict|"
    "invoice_provenance|duplicate_identity|source_after_cutoff|currency_precision|"
    "settlement_provenance"
)


def _finish(table: str, platform: bool, *, online_insert: bool) -> None:
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
    grants = "SELECT, INSERT" if online_insert else "SELECT"
    op.execute(f"GRANT {grants} ON mod_billing.{table} TO {online};")
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table} "
        "FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();"
    )


def _base(platform: bool) -> list[SchemaItem]:
    scope = [] if platform else [sa.Column("tenant_id", sa.Uuid(), nullable=False)]
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *scope,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _tables(platform: bool) -> None:
    p = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]

    def fk(cols: list[str], target: str, tcols: list[str]) -> sa.ForeignKeyConstraint:
        return sa.ForeignKeyConstraint(
            [*scope, *cols],
            [f"{_SCHEMA}.{p}{target}.{k}" for k in (*scope, *tcols)],
            ondelete="RESTRICT",
        )

    tenant_fk: list[SchemaItem] = (
        []
        if platform
        else [
            sa.ForeignKeyConstraint(
                ["tenant_id"], ["public.tenants.id"], ondelete="RESTRICT"
            )
        ]
    )
    variances = p + "opening_variances"
    op.create_table(
        variances,
        *_base(platform),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("scope_level", sa.String(16), nullable=False),
        sa.Column("billing_account_id", sa.Uuid()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(32), nullable=False),
        sa.Column("basis", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(96), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("import_receipt_id", sa.Uuid()),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("rules_digest", sa.String(64), nullable=False),
        sa.CheckConstraint("amount_minor <> 0", name=f"ck_{variances}_nonzero"),
        sa.CheckConstraint(
            f"lane IN {_LANES} AND basis IN {_BASES} AND source_kind IN {_KINDS}",
            name=f"ck_{variances}_closed",
        ),
        sa.CheckConstraint(
            "(scope_level = 'partition' AND billing_account_id IS NOT NULL "
            "AND import_receipt_id IS NOT NULL) OR (scope_level = 'batch' "
            "AND billing_account_id IS NULL AND import_receipt_id IS NULL)",
            name=f"ck_{variances}_scope",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name=f"ck_{variances}_precision"
        ),
        sa.UniqueConstraint(
            *scope,
            "window_id",
            "billing_account_id",
            "currency",
            "lane",
            "basis",
            "reason_code",
            "source_kind",
            "source_ref",
            name=f"uq_{variances}_identity",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint(*scope, "id", name=f"uq_{variances}_id"),
        sa.UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{variances}_account"
        ),
        fk(["window_id"], "opening_windows", ["id"]),
        fk(
            ["import_receipt_id", "billing_account_id"],
            "opening_import_receipts",
            ["id", "billing_account_id"],
        ),
        *tenant_fk,
        schema=_SCHEMA,
    )
    _finish(variances, platform, online_insert=False)

    events = p + "opening_variance_events"
    op.create_table(
        events,
        *_base(platform),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("variance_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("resolution_kind", sa.String(32)),
        sa.Column("amount_minor", sa.BigInteger()),
        sa.Column("posting_group_id", sa.Uuid()),
        sa.Column("gl_target_class", sa.String(32)),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN {_EVENTS}", name=f"ck_{events}_kind"),
        sa.CheckConstraint(
            "resolution_kind IS NULL OR resolution_kind IN "
            "('position_corrected','recognised','evidence_confirmed')",
            name=f"ck_{events}_resolution",
        ),
        sa.UniqueConstraint(*scope, "seq", name=f"uq_{events}_seq"),
        sa.UniqueConstraint(
            *scope, "posting_group_id", name=f"uq_{events}_posting_group"
        ),
        fk(["variance_id"], "opening_variances", ["id"]),
        fk(["window_id"], "opening_windows", ["id"]),
        *tenant_fk,
        schema=_SCHEMA,
    )
    _finish(events, platform, online_insert=True)

    gaps = p + "opening_control_gaps"
    op.create_table(
        gaps,
        *_base(platform),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("basis", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(96), nullable=False),
        sa.Column("import_receipt_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.CheckConstraint("basis = 'rebuild'", name=f"ck_{gaps}_basis"),
        sa.UniqueConstraint(
            *scope,
            "window_id",
            "billing_account_id",
            "basis",
            name=f"uq_{gaps}_identity",
        ),
        sa.UniqueConstraint(*scope, "id", name=f"uq_{gaps}_id"),
        fk(["window_id"], "opening_windows", ["id"]),
        fk(
            ["import_receipt_id", "billing_account_id"],
            "opening_import_receipts",
            ["id", "billing_account_id"],
        ),
        *tenant_fk,
        schema=_SCHEMA,
    )
    _finish(gaps, platform, online_insert=False)

    gap_events = p + "opening_control_gap_events"
    op.create_table(
        gap_events,
        *_base(platform),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column("gap_id", sa.Uuid(), nullable=False),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("approval_ref", sa.String(255)),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('recorded','resolved')", name=f"ck_{gap_events}_kind"
        ),
        sa.UniqueConstraint(*scope, "gap_id", "kind", name=f"uq_{gap_events}_once"),
        fk(["gap_id"], "opening_control_gaps", ["id"]),
        fk(["window_id"], "opening_windows", ["id"]),
        *tenant_fk,
        schema=_SCHEMA,
    )
    _finish(gap_events, platform, online_insert=True)


def _shared() -> None:
    op.execute(f"""
    CREATE FUNCTION mod_billing.opening_variance_policy_ok(
      reason text, basis text, kind text, lane text, lvl text) RETURNS boolean
    LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
      SELECT CASE
        WHEN reason = 'lane_document_difference' THEN lvl = 'partition' AND basis = 'ledger_lane'
          AND kind IN ('lane','invoice','ledger_entry') AND lane = 'receivable'
        WHEN reason = 'stored_balance_difference' THEN lvl = 'partition' AND basis = 'stored_balance'
          AND kind = 'invoice' AND lane = 'receivable'
        WHEN reason = 'closed_invoice_residue' THEN lvl = 'partition'
          AND basis IN ('document_residual','stored_balance') AND kind = 'invoice' AND lane = 'receivable'
        WHEN reason = 'negative_residue' THEN lvl = 'partition'
          AND basis IN ('ledger_lane','document_residual') AND kind = 'invoice' AND lane = 'receivable'
        WHEN reason ~ '^item_not_transferred:({_ITEM_CODES})$' THEN lvl = 'partition'
          AND basis = 'ledger_lane' AND kind = 'invoice' AND lane = 'receivable'
        WHEN reason ~ '^lane_not_transferred:(customer_credit|prepaid_funding)$' THEN lvl = 'partition'
          AND basis = 'ledger_lane' AND kind = 'lane' AND reason = 'lane_not_transferred:' || lane
        WHEN reason = 'draft_allocation' THEN lvl = 'partition' AND basis = 'ledger_lane'
          AND kind = 'settlement' AND lane = 'customer_credit'
        WHEN reason = 'reseller_fold_unavailable' THEN lvl = 'partition' AND basis = 'ledger_lane'
          AND kind = 'lane'
        WHEN reason = 'unattributed_source' THEN lvl = 'batch' AND basis = 'ledger_lane'
          AND kind IN ('control_row','payment')
        WHEN reason = 'unexplained_difference' THEN lvl = 'partition' AND kind = 'partition_remainder'
        WHEN reason ~ '^rebuild_[a-z0-9]+(_[a-z0-9]+)*$' THEN lvl = 'partition'
          AND basis = 'rebuild' AND kind = 'rebuild_account'
        ELSE false END
    $$;

    -- The one carryable definition: T != 0 or any variance on the partition.
    CREATE FUNCTION mod_billing.opening_carryable_accounts(manifest jsonb) RETURNS SETOF text
    LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
      SELECT account FROM (
        SELECT e->'partition'->>'account_id' AS account, sum((e->>'residual_minor')::numeric) AS total
        FROM jsonb_array_elements(coalesce(manifest->'cash_lots','[]'::jsonb) || coalesce(manifest->'receivables','[]'::jsonb)) e
        GROUP BY 1) t WHERE total <> 0
      UNION
      SELECT v->'partition'->>'account_id' FROM jsonb_array_elements(coalesce(manifest->'variances','[]'::jsonb)) v
        WHERE v->'partition'->>'account_id' IS NOT NULL
    $$;

    """)


def _guards(platform: bool) -> None:
    p = "platform_" if platform else ""
    param = "" if platform else "tenant uuid, "
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    x_scope = "" if platform else " AND x.tenant_id = tenant"
    r_scope = "" if platform else " AND r.tenant_id = tenant"
    e_scope = "" if platform else " AND e.tenant_id = tenant"
    s_new = "" if platform else " AND s.tenant_id = NEW.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.guard_{p}opening_variance() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE w record;
    BEGIN
      SELECT * INTO w FROM mod_billing.{p}opening_windows x WHERE x.id = NEW.window_id{new_scope};
      IF w.id IS NULL OR NOT mod_billing.opening_variance_policy_ok(
        NEW.reason_code, NEW.basis, NEW.source_kind, NEW.lane, NEW.scope_level)
        OR nullif(trim(NEW.source_ref), '') IS NULL
        OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$' OR NEW.rules_digest !~ '^[0-9a-f]{{64}}$'
      THEN RAISE EXCEPTION 'opening variance requires a closed reason, basis and source kind' USING ERRCODE = '23514'; END IF;
      IF NEW.billing_account_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM mod_billing.{p}billing_accounts x WHERE x.id = NEW.billing_account_id
          AND x.currency = NEW.currency AND x.minor_units = NEW.minor_units{new_scope})
      THEN RAISE EXCEPTION 'opening variance money identity differs from its account' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_variance_policy BEFORE INSERT ON mod_billing.{p}opening_variances
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_variance();
    CREATE TRIGGER opening_variance_entry BEFORE INSERT ON mod_billing.{p}opening_variances
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();
    CREATE TRIGGER opening_variance_event_entry BEFORE INSERT ON mod_billing.{p}opening_variance_events
      FOR EACH ROW WHEN (NEW.kind = 'recorded')
      EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();
    CREATE TRIGGER opening_gap_entry BEFORE INSERT ON mod_billing.{p}opening_control_gaps
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();
    CREATE TRIGGER opening_gap_event_entry BEFORE INSERT ON mod_billing.{p}opening_control_gap_events
      FOR EACH ROW WHEN (NEW.kind = 'recorded')
      EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();

    CREATE FUNCTION mod_billing.guard_{p}opening_variance_event() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE v record; prev record; grp record; sealed boolean; done numeric; total numeric;
      eff numeric; lane_name text;
    BEGIN
      SELECT * INTO v FROM mod_billing.{p}opening_variances x WHERE x.id = NEW.variance_id{new_scope};
      IF v.id IS NULL OR v.window_id <> NEW.window_id
        OR v.billing_account_id IS DISTINCT FROM NEW.billing_account_id
        OR nullif(trim(NEW.actor), '') IS NULL
      THEN RAISE EXCEPTION 'variance event must name its variance, window and actor' USING ERRCODE = '23514'; END IF;
      SELECT EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = NEW.window_id{s_new}) INTO sealed;
      SELECT * INTO prev FROM mod_billing.{p}opening_variance_events x
        WHERE x.variance_id = NEW.variance_id{new_scope} ORDER BY x.seq DESC LIMIT 1;
      IF NEW.kind IN ('recorded','voided_by_import_reversal','resolving') AND (
        NEW.resolution_kind IS NOT NULL OR NEW.amount_minor IS NOT NULL
        OR NEW.posting_group_id IS NOT NULL OR NEW.gl_target_class IS NOT NULL OR NEW.evidence_ref IS NOT NULL)
      THEN RAISE EXCEPTION 'only a resolution event carries resolution fields' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'recorded' THEN
        IF prev.id IS NOT NULL AND prev.kind <> 'voided_by_import_reversal' THEN
          RAISE EXCEPTION 'variance is already recorded' USING ERRCODE = '23514'; END IF;
        IF sealed AND v.scope_level = 'batch' THEN
          RAISE EXCEPTION 'batch variances are recorded before the seal' USING ERRCODE = '23514'; END IF;
      ELSIF NEW.kind = 'voided_by_import_reversal' THEN
        IF sealed OR prev.kind IS DISTINCT FROM 'recorded' OR NOT EXISTS (
          SELECT 1 FROM mod_billing.{p}opening_import_receipts r
          WHERE r.original_receipt_id = v.import_receipt_id AND r.receipt_kind = 'unwind'{"" if platform else " AND r.tenant_id = NEW.tenant_id"})
        THEN RAISE EXCEPTION 'only a recorded variance of an unwound import is voided, before the seal' USING ERRCODE = '23514'; END IF;
      ELSIF NEW.kind = 'resolving' THEN
        IF NOT sealed OR prev.kind NOT IN ('recorded','partially_resolved')
          OR nullif(trim(NEW.approval_ref), '') IS NULL
        THEN RAISE EXCEPTION 'resolving needs a sealed window, an open variance and its approval' USING ERRCODE = '23514'; END IF;
      ELSE
        IF NOT sealed OR prev.kind IS DISTINCT FROM 'resolving'
          OR NEW.approval_ref IS DISTINCT FROM prev.approval_ref
          OR NEW.resolution_kind IS NULL OR NEW.amount_minor IS NULL OR NEW.amount_minor = 0
          OR sign(NEW.amount_minor) <> sign(v.amount_minor)
        THEN RAISE EXCEPTION 'resolution must follow its approved resolving event with the variance sign' USING ERRCODE = '23514'; END IF;
        SELECT coalesce(sum(x.amount_minor), 0) INTO done FROM mod_billing.{p}opening_variance_events x
          WHERE x.variance_id = NEW.variance_id AND x.kind IN ('resolved','partially_resolved'){new_scope};
        total := done + NEW.amount_minor;
        IF abs(total) > abs(v.amount_minor)
          OR (NEW.kind = 'resolved') <> (total = v.amount_minor)
          OR NEW.kind NOT IN ('resolved','partially_resolved')
        THEN RAISE EXCEPTION 'resolution over-resolves, or the kind does not match the remaining amount' USING ERRCODE = '23514'; END IF;
        IF NEW.resolution_kind = 'recognised' THEN
          IF v.basis <> 'ledger_lane' OR nullif(trim(NEW.gl_target_class), '') IS NULL
            OR NEW.posting_group_id IS NOT NULL OR NEW.evidence_ref IS NOT NULL
          THEN RAISE EXCEPTION 'recognised applies only to the ledger lane with a GL target class' USING ERRCODE = '23514'; END IF;
        ELSIF NEW.resolution_kind = 'evidence_confirmed' THEN
          IF v.basis = 'ledger_lane' OR nullif(trim(NEW.evidence_ref), '') IS NULL
            OR NEW.posting_group_id IS NOT NULL OR NEW.gl_target_class IS NOT NULL
          THEN RAISE EXCEPTION 'evidence_confirmed applies only to memo bases and never touches money' USING ERRCODE = '23514'; END IF;
        ELSE
          lane_name := CASE v.lane WHEN 'customer_credit' THEN 'available_credit' ELSE v.lane END;
          SELECT * INTO grp FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope};
          SELECT sum(e.amount_delta * power(10::numeric, e.minor_units)) INTO eff
            FROM mod_billing.{p}posting_effects e
            WHERE e.posting_group_id = NEW.posting_group_id AND e.lane = lane_name{"" if platform else " AND e.tenant_id = NEW.tenant_id"};
          IF v.basis <> 'ledger_lane' OR grp.id IS NULL
            OR grp.billing_account_id <> v.billing_account_id OR grp.currency <> v.currency
            OR grp.group_kind IN ('opening_cash_imported','opening_account_imported')
            OR eff IS DISTINCT FROM NEW.amount_minor::numeric
            OR NEW.gl_target_class IS NOT NULL OR NEW.evidence_ref IS NOT NULL
          THEN RAISE EXCEPTION 'position_corrected needs a posting group of the same account, currency and lane with an equal amount' USING ERRCODE = '23514'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_variance_event_guard BEFORE INSERT ON mod_billing.{p}opening_variance_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_variance_event();

    CREATE FUNCTION mod_billing.guard_{p}opening_gap_event() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE g record; sealed boolean;
    BEGIN
      SELECT * INTO g FROM mod_billing.{p}opening_control_gaps x WHERE x.id = NEW.gap_id{new_scope};
      SELECT EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = NEW.window_id{s_new}) INTO sealed;
      IF g.id IS NULL OR g.window_id <> NEW.window_id OR g.billing_account_id <> NEW.billing_account_id
        OR nullif(trim(NEW.actor), '') IS NULL
        OR (NEW.kind = 'resolved' AND (NOT sealed OR nullif(trim(NEW.approval_ref), '') IS NULL
            OR nullif(trim(NEW.evidence_ref), '') IS NULL))
        OR (NEW.kind = 'recorded' AND (NEW.approval_ref IS NOT NULL OR NEW.evidence_ref IS NOT NULL))
        OR (NEW.kind = 'resolved' AND NOT EXISTS (
          SELECT 1 FROM mod_billing.{p}opening_control_gap_events x WHERE x.gap_id = NEW.gap_id AND x.kind = 'recorded'{new_scope}))
      THEN RAISE EXCEPTION 'control gap event requires its gap, and a sealed window with evidence to resolve' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_gap_event_guard BEFORE INSERT ON mod_billing.{p}opening_control_gap_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_gap_event();

    -- Recorded (non-voided) variances and gaps of the accounts under check, as
    -- jsonb, against the frozen manifest set for the same accounts. Batch scope
    -- is compared only when include_batch is true.
    CREATE FUNCTION mod_billing.opening_variance_set_matches(
      {param}identity uuid, manifest jsonb, accounts text[], include_batch boolean) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE proposed jsonb; actual jsonb; gap_proposed jsonb; gap_actual jsonb;
    BEGIN
      SELECT coalesce(jsonb_agg(row ORDER BY row::text), '[]'::jsonb) INTO proposed FROM (
        SELECT jsonb_build_object('a', v->'partition'->>'account_id', 'c', v->>'currency',
          'p', (v->>'precision')::int, 'l', v->>'lane', 'b', v->>'basis', 'r', v->>'reason_code',
          'k', v->'source'->>'kind', 's', v->'source'->>'key', 'm', (v->>'amount_minor')::numeric,
          'e', v->>'evidence_digest', 'd', v->>'rules_digest') AS row
        FROM jsonb_array_elements(coalesce(manifest->'variances','[]'::jsonb)) v
        WHERE CASE WHEN v->'partition'->>'account_id' IS NULL THEN include_batch
              ELSE (v->'partition'->>'account_id') = ANY(accounts) END) q;
      SELECT coalesce(jsonb_agg(row ORDER BY row::text), '[]'::jsonb) INTO actual FROM (
        SELECT jsonb_build_object('a', x.billing_account_id::text, 'c', x.currency,
          'p', x.minor_units, 'l', x.lane, 'b', x.basis, 'r', x.reason_code,
          'k', x.source_kind, 's', x.source_ref, 'm', x.amount_minor::numeric,
          'e', x.evidence_digest, 'd', x.rules_digest) AS row
        FROM mod_billing.{p}opening_variances x
        WHERE x.window_id = identity{"" if platform else " AND x.tenant_id = tenant"}
          AND CASE WHEN x.billing_account_id IS NULL THEN include_batch
              ELSE x.billing_account_id::text = ANY(accounts) END
          AND coalesce((SELECT e.kind FROM mod_billing.{p}opening_variance_events e
                WHERE e.variance_id = x.id{e_scope} ORDER BY e.seq DESC LIMIT 1), '') = 'recorded') q;
      SELECT coalesce(jsonb_agg(row ORDER BY row::text), '[]'::jsonb) INTO gap_proposed FROM (
        SELECT jsonb_build_object('a', m->'partition'->>'account_id', 'r', m->>'rebuild_gap_reason') AS row
        FROM jsonb_array_elements(coalesce(manifest->'measures','[]'::jsonb)) m
        WHERE m->>'rebuild_minor' IS NULL AND (m->'partition'->>'account_id') = ANY(accounts)) q;
      SELECT coalesce(jsonb_agg(row ORDER BY row::text), '[]'::jsonb) INTO gap_actual FROM (
        SELECT jsonb_build_object('a', g.billing_account_id::text, 'r', g.reason_code) AS row
        FROM mod_billing.{p}opening_control_gaps g
        WHERE g.window_id = identity{"" if platform else " AND g.tenant_id = tenant"}
          AND g.billing_account_id::text = ANY(accounts)) q;
      RETURN proposed IS NOT DISTINCT FROM actual AND gap_proposed IS NOT DISTINCT FROM gap_actual;
    END $$;

    -- Count of closure rows that are not exactly zero for the accounts under
    -- check: source - transferred - variance, per lane and per memo basis, plus
    -- (batch) the stated source of unattributed rows against batch variances.
    CREATE FUNCTION mod_billing.opening_closure_defects(
      {param}identity uuid, manifest jsonb, accounts text[], include_batch boolean) RETURNS bigint
    LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bad bigint; batch_bad bigint;
    BEGIN
      WITH ctl AS (
        SELECT (c->'partition'->>'account_id')::uuid AS acct,
          (c->>'collectible_receivable_minor')::numeric AS rec,
          (c->>'unapplied_customer_credit_minor')::numeric AS cred,
          (c->>'prepaid_funding_reserved_minor')::numeric AS fund
        FROM jsonb_array_elements(manifest->'controls') c
        WHERE (c->'partition'->>'account_id') = ANY(accounts)),
      mea AS (
        SELECT (m->'partition'->>'account_id')::uuid AS acct, (m->>'document_residual_minor')::numeric AS doc,
          (m->>'stored_balance_minor')::numeric AS sto, (m->>'rebuild_minor')::numeric AS reb
        FROM jsonb_array_elements(coalesce(manifest->'measures','[]'::jsonb)) m),
      tr AS (
        SELECT r.billing_account_id AS acct,
          sum((r.evidence::jsonb->'total_receivable'->>'amount')::numeric
              * power(10::numeric, (r.evidence::jsonb->'total_receivable'->>'minor_units')::int)) AS t
        FROM mod_billing.{p}opening_import_receipts r
        WHERE r.receipt_kind = 'import'{r_scope} AND r.billing_account_id::text = ANY(accounts)
        GROUP BY 1),
      va AS (
        SELECT x.billing_account_id AS acct, x.lane, x.basis, sum(x.amount_minor)::numeric AS v
        FROM mod_billing.{p}opening_variances x
        WHERE x.window_id = identity{x_scope} AND x.billing_account_id IS NOT NULL
          AND coalesce((SELECT e.kind FROM mod_billing.{p}opening_variance_events e
                WHERE e.variance_id = x.id{e_scope} ORDER BY e.seq DESC LIMIT 1), '') = 'recorded'
        GROUP BY 1, 2, 3)
      SELECT count(*) INTO bad FROM ctl
        LEFT JOIN mea ON mea.acct = ctl.acct LEFT JOIN tr ON tr.acct = ctl.acct
      WHERE mea.acct IS NULL
        OR ctl.rec <> coalesce(tr.t, 0) + coalesce((SELECT v FROM va WHERE va.acct = ctl.acct AND lane = 'receivable' AND basis = 'ledger_lane'), 0)
        OR ctl.cred <> coalesce((SELECT v FROM va WHERE va.acct = ctl.acct AND lane = 'customer_credit' AND basis = 'ledger_lane'), 0)
        OR ctl.fund <> coalesce((SELECT v FROM va WHERE va.acct = ctl.acct AND lane = 'prepaid_funding' AND basis = 'ledger_lane'), 0)
        OR mea.doc <> coalesce(tr.t, 0) + coalesce((SELECT sum(v) FROM va WHERE va.acct = ctl.acct AND basis = 'document_residual'), 0)
        OR mea.sto <> coalesce(tr.t, 0) + coalesce((SELECT sum(v) FROM va WHERE va.acct = ctl.acct AND basis = 'stored_balance'), 0)
        OR (mea.reb IS NOT NULL AND mea.reb <> coalesce(tr.t, 0) + coalesce((SELECT sum(v) FROM va WHERE va.acct = ctl.acct AND basis = 'rebuild'), 0))
        OR (mea.reb IS NULL AND EXISTS (SELECT 1 FROM va WHERE va.acct = ctl.acct AND basis = 'rebuild'));
      batch_bad := 0;
      IF include_batch THEN
        WITH stated AS (
          SELECT b->>'currency' AS currency, b->>'lane' AS lane, b->>'basis' AS basis,
            (b->>'source_minor')::numeric AS source
          FROM jsonb_array_elements(coalesce(manifest->'batch_controls','[]'::jsonb)) b),
        got AS (
          SELECT x.currency, x.lane, x.basis, sum(x.amount_minor)::numeric AS v
          FROM mod_billing.{p}opening_variances x
          WHERE x.window_id = identity{x_scope} AND x.billing_account_id IS NULL
            AND coalesce((SELECT e.kind FROM mod_billing.{p}opening_variance_events e
                  WHERE e.variance_id = x.id{e_scope} ORDER BY e.seq DESC LIMIT 1), '') = 'recorded'
          GROUP BY 1, 2, 3)
        SELECT count(*) INTO batch_bad FROM stated FULL JOIN got USING (currency, lane, basis)
          WHERE coalesce(stated.source, 0) <> coalesce(got.v, 0);
      END IF;
      RETURN bad + batch_bad;
    END $$;
    """)


def _replace_guards(platform: bool) -> None:
    p = "platform_" if platform else ""
    argument = "" if platform else "NEW.tenant_id"
    new_args = "NEW.tenant_id, " if not platform else ""
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    held_args = "NEW.tenant_id, " if not platform else ""
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_hold_admission() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound uuid; receipt record; refs jsonb; ev jsonb; manifest jsonb;
    BEGIN
      bound := mod_billing.touch_{p}opening_window_held({held_args}NEW.billing_account_id);
      SELECT * INTO receipt FROM mod_billing.{p}opening_import_receipts x WHERE x.id = NEW.receipt_id{new_scope};
      refs := NEW.approver_refs::jsonb; ev := NEW.evidence::jsonb;
      SELECT w.evidence::jsonb->'window'->'manifest' INTO manifest
        FROM mod_billing.{p}opening_windows w WHERE w.id = bound{"" if platform else " AND w.tenant_id = NEW.tenant_id"};
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
      -- ST-4c: the admitted partition closes exactly like a pre-seal one.
      IF NOT mod_billing.opening_variance_set_matches({"" if platform else "NEW.tenant_id, "}bound, manifest, ARRAY[NEW.billing_account_id::text], false)
        OR mod_billing.opening_closure_defects({"" if platform else "NEW.tenant_id, "}bound, manifest, ARRAY[NEW.billing_account_id::text], false) <> 0
      THEN RAISE EXCEPTION 'held partition does not close: recorded variances differ from the manifest or a control row is not zero' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;

    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_seal() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE bound record; identity uuid; command jsonb; proof jsonb;
      actual jsonb; proposed jsonb; expected_accounts jsonb; actual_accounts jsonb;
      held jsonb; manifest jsonb; operational jsonb; carryable text[];
      admitted text[];
    BEGIN
      identity := mod_billing.touch_{p}opening_window({argument});
      SELECT * INTO bound FROM mod_billing.{p}opening_windows x WHERE x.id = identity{new_scope};
      command := NEW.evidence::jsonb->'command'; proof := NEW.evidence::jsonb->'verification';
      manifest := bound.evidence::jsonb->'window'->'manifest';
      held := coalesce(command->'held_account_ids', '[]'::jsonb);
      operational := coalesce(command->'operational_holds', '[]'::jsonb);
      SELECT coalesce(array_agg(a), ARRAY[]::text[]) INTO carryable FROM mod_billing.opening_carryable_accounts(manifest) a;
      SELECT coalesce(jsonb_agg(jsonb_build_object(
          'scope', {scope_json}, 'window_id', x.window_id::text,
          'receipt_id', x.receipt_id::text, 'billing_account_id', x.billing_account_id::text,
          'request_identity', x.request_identity, 'request_digest', x.request_digest,
          'manifest_fingerprint', x.manifest_fingerprint) ORDER BY x.receipt_id::text), '[]'::jsonb)
        INTO actual FROM mod_billing.{p}opening_window_admissions x WHERE x.window_id = identity{new_scope};
      SELECT coalesce(jsonb_agg(e ORDER BY e->>'receipt_id'), '[]'::jsonb)
        INTO proposed FROM jsonb_array_elements(command->'admissions') e;
      -- Hold list: unique, carryable, covers every marked partition, and every
      -- other listed partition carries exactly one typed operational reason.
      IF jsonb_typeof(held) IS DISTINCT FROM 'array'
        OR jsonb_typeof(operational) IS DISTINCT FROM 'array'
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(held) h GROUP BY h HAVING count(*) > 1)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(held) h WHERE NOT (h = ANY(carryable)))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(operational) o
                   WHERE NOT (held ? (o->>'account_id'))
                      OR o->>'reason_code' NOT IN ('target_not_pristine','import_refused'))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(operational) o GROUP BY o->>'account_id' HAVING count(*) > 1)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(held) h
                   WHERE NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements(operational) o WHERE o->>'account_id' = h))
      THEN RAISE EXCEPTION 'seal hold list must equal the typed operational holds' USING ERRCODE = '23514'; END IF;
      SELECT coalesce(jsonb_agg(account ORDER BY account), '[]'::jsonb) INTO expected_accounts FROM (
        SELECT c AS account FROM unnest(carryable) c WHERE NOT (held ? c)) frozen;
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
      -- Seal-time closure: (a) the recorded variance and gap sets equal the
      -- manifest sets for the admitted accounts and the batch; (b) every
      -- closure row is exactly zero.
      SELECT coalesce(array_agg(c), ARRAY[]::text[]) INTO admitted FROM unnest(carryable) c WHERE NOT (held ? c);
      IF NOT mod_billing.opening_variance_set_matches({new_args}identity, manifest, admitted, true)
      THEN RAISE EXCEPTION 'seal refused: recorded variance set differs from the manifest variance set' USING ERRCODE = '23514'; END IF;
      IF mod_billing.opening_closure_defects({new_args}identity, manifest, admitted, true) <> 0
      THEN RAISE EXCEPTION 'seal refused: an opening control row does not close to zero' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    """)


def upgrade() -> None:
    _shared()
    for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM):
        if plane in selected_module_planes("billing"):
            platform = plane is ModulePlane.PLATFORM
            _tables(platform)
            _guards(platform)
            _replace_guards(platform)


def downgrade() -> None:
    raise RuntimeError(
        "Billing opening custody cannot be erased or reopened by downgrade"
    )
