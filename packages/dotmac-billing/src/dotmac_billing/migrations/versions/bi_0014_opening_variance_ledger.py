"""Opening variance ledger: append-only variances, lifecycle events, control gaps.

Every variance is a typed, append-only record (never a metadata key) created by
the restricted opening principal inside the import, the batch-variance step of
the seal, or a post-seal held-partition admission. A lifecycle event log
(``recorded -> resolving -> partially_resolved|resolved``, plus
``voided_by_import_reversal`` before the seal) is a state machine enforced here:
resolution sign and cumulative bound, kind per basis, and a ``position_corrected``
event must name a posting group of the same account, currency and lane whose
effect equals the amount.

Each ledger-lane recorded, voided and recognised/corrected event names an
effect-free posting group (``fact_posting_group_id``, unique, checked at commit)
carrying one AccountingFactV6; ``guard_opening_entry`` adds the recorded and
voided group kinds to the opening-principal set.

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
from sqlalchemy.dialects.postgresql import JSONB
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
            "import_receipt_id",
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
        sa.Column("maker_ref", sa.String(255)),
        sa.Column("approver_refs", JSONB()),
        sa.Column("approval_request_id", sa.Uuid()),
        # The effect-free posting group carrying this event's AccountingFactV6.
        sa.Column("fact_posting_group_id", sa.Uuid()),
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
        sa.UniqueConstraint(
            *scope, "fact_posting_group_id", name=f"uq_{events}_fact_group"
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
            "import_receipt_id",
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
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("evidence_ref", sa.String(255)),
        sa.Column("maker_ref", sa.String(255)),
        sa.Column("approver_refs", JSONB()),
        sa.Column("approval_request_id", sa.Uuid()),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('recorded','resolved','voided_by_import_reversal')",
            name=f"ck_{gap_events}_kind",
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
          AND (basis <> 'rebuild' OR lane = 'receivable')
        WHEN reason ~ '^rebuild_[a-z0-9]+(_[a-z0-9]+)*$' THEN lvl = 'partition'
          AND basis = 'rebuild' AND kind = 'rebuild_account' AND lane = 'receivable'
        ELSE false END
    $$;

    -- bi_0013 hold-admission parity for a variance/gap resolution: a non-empty
    -- maker, a non-empty array of distinct non-empty checker strings, the maker
    -- not among them, and an approval request id.
    CREATE FUNCTION mod_billing.opening_approval_ok(
      maker text, approvers jsonb, request uuid) RETURNS boolean
    LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
      SELECT CASE WHEN nullif(trim(maker), '') IS NULL OR request IS NULL
          OR jsonb_typeof(approvers) IS DISTINCT FROM 'array' THEN false
        ELSE jsonb_array_length(approvers) > 0
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(approvers) e
                WHERE jsonb_typeof(e) <> 'string' OR nullif(trim(e #>> '{{}}'), '') IS NULL)
          AND (SELECT count(DISTINCT e #>> '{{}}') FROM jsonb_array_elements(approvers) e) = jsonb_array_length(approvers)
          AND NOT (approvers ? maker) END
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

    """)  # nosec B608 -- closed plane/table identifiers and fixed scope expressions; no input reaches this DDL


def _guards(platform: bool) -> None:
    p = "platform_" if platform else ""
    param = "" if platform else "tenant uuid, "
    new_scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    x_scope = "" if platform else " AND x.tenant_id = tenant"
    r_scope = "" if platform else " AND r.tenant_id = tenant"
    e_scope = "" if platform else " AND e.tenant_id = tenant"
    u_scope = "" if platform else " AND u.tenant_id = tenant"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
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
      FOR EACH ROW WHEN (NEW.kind IN ('recorded','voided_by_import_reversal'))
      EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();
    CREATE TRIGGER opening_gap_entry BEFORE INSERT ON mod_billing.{p}opening_control_gaps
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();
    CREATE TRIGGER opening_gap_event_entry BEFORE INSERT ON mod_billing.{p}opening_control_gap_events
      FOR EACH ROW WHEN (NEW.kind IN ('recorded','voided_by_import_reversal'))
      EXECUTE FUNCTION mod_billing.guard_{p}opening_entry();

    CREATE FUNCTION mod_billing.guard_{p}opening_variance_event() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE v record; prev record; grp record; sealed boolean; done numeric; total numeric;
      eff numeric; lane_name text; adverse boolean; needs_fact boolean;
    BEGIN
      SELECT * INTO v FROM mod_billing.{p}opening_variances x WHERE x.id = NEW.variance_id{new_scope};
      IF v.id IS NULL OR v.window_id <> NEW.window_id
        OR v.billing_account_id IS DISTINCT FROM NEW.billing_account_id
        OR nullif(trim(NEW.actor), '') IS NULL
      THEN RAISE EXCEPTION 'variance event must name its variance, window and actor' USING ERRCODE = '23514'; END IF;
      SELECT EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = NEW.window_id{s_new}) INTO sealed;
      SELECT * INTO prev FROM mod_billing.{p}opening_variance_events x
        WHERE x.variance_id = NEW.variance_id{new_scope} ORDER BY x.seq DESC LIMIT 1;
      needs_fact := v.basis = 'ledger_lane' AND v.scope_level = 'partition' AND (
        NEW.kind IN ('recorded','voided_by_import_reversal')
        OR (NEW.kind IN ('resolved','partially_resolved')
            AND NEW.resolution_kind IN ('recognised','position_corrected')));
      IF needs_fact <> (NEW.fact_posting_group_id IS NOT NULL)
      THEN RAISE EXCEPTION 'a ledger-lane recorded, voided or recognised/corrected event carries exactly one fact group' USING ERRCODE = '23514'; END IF;
      IF NEW.kind IN ('recorded','voided_by_import_reversal') AND (
        NEW.resolution_kind IS NOT NULL OR NEW.amount_minor IS NOT NULL
        OR NEW.posting_group_id IS NOT NULL OR NEW.gl_target_class IS NOT NULL OR NEW.evidence_ref IS NOT NULL
        OR NEW.maker_ref IS NOT NULL OR NEW.approver_refs IS NOT NULL OR NEW.approval_request_id IS NOT NULL)
      THEN RAISE EXCEPTION 'only a resolution event carries resolution fields' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'resolving' AND (
        NEW.resolution_kind IS NOT NULL OR NEW.amount_minor IS NOT NULL
        OR NEW.posting_group_id IS NOT NULL OR NEW.gl_target_class IS NOT NULL OR NEW.evidence_ref IS NOT NULL)
      THEN RAISE EXCEPTION 'only a resolution event carries resolution fields' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'recorded' THEN
        -- A void is terminal: a re-import records fresh rows, never this one again.
        IF prev.id IS NOT NULL THEN
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
          OR NOT mod_billing.opening_approval_ok(NEW.maker_ref, NEW.approver_refs, NEW.approval_request_id)
          OR NEW.actor IS DISTINCT FROM NEW.maker_ref
        THEN RAISE EXCEPTION 'resolving needs a sealed window, an open variance and maker-checker approval' USING ERRCODE = '23514'; END IF;
      ELSE
        IF NOT sealed OR prev.kind IS DISTINCT FROM 'resolving'
          OR NEW.maker_ref IS DISTINCT FROM prev.maker_ref
          OR NEW.approver_refs IS DISTINCT FROM prev.approver_refs
          OR NEW.approval_request_id IS DISTINCT FROM prev.approval_request_id
          OR NEW.actor IS DISTINCT FROM NEW.maker_ref
          OR NEW.resolution_kind IS NULL OR NEW.amount_minor IS NULL OR NEW.amount_minor = 0
          OR sign(NEW.amount_minor) <> sign(v.amount_minor)
        THEN RAISE EXCEPTION 'resolution must equal its approved resolving event and carry the variance sign' USING ERRCODE = '23514'; END IF;
        SELECT coalesce(sum(x.amount_minor), 0) INTO done FROM mod_billing.{p}opening_variance_events x
          WHERE x.variance_id = NEW.variance_id AND x.kind IN ('resolved','partially_resolved'){new_scope};
        total := done + NEW.amount_minor;
        IF abs(total) > abs(v.amount_minor)
          OR (NEW.kind = 'resolved') <> (total = v.amount_minor)
          OR NEW.kind NOT IN ('resolved','partially_resolved')
        THEN RAISE EXCEPTION 'resolution over-resolves, or the kind does not match the remaining amount' USING ERRCODE = '23514'; END IF;
        adverse := (v.lane = 'receivable' AND v.amount_minor < 0) OR (v.lane <> 'receivable' AND v.amount_minor > 0);
        IF NEW.resolution_kind = 'recognised' THEN
          IF v.basis <> 'ledger_lane' OR adverse OR nullif(trim(NEW.gl_target_class), '') IS NULL
            OR NEW.posting_group_id IS NOT NULL OR NEW.evidence_ref IS NOT NULL
            OR NOT (CASE WHEN v.lane = 'receivable' THEN NEW.gl_target_class IN ('bad_debt','revenue_adjustment')
                         ELSE NEW.gl_target_class = 'customer_liability' END)
          THEN RAISE EXCEPTION 'recognised applies only to a non-adverse ledger-lane variance with a GL target class valid for its lane' USING ERRCODE = '23514'; END IF;
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
          IF v.basis <> 'ledger_lane' OR grp.id IS NULL OR grp.reverses_group_id IS NOT NULL
            OR grp.billing_account_id <> v.billing_account_id OR grp.currency <> v.currency
            OR grp.group_kind IN ('opening_cash_imported','opening_account_imported')
            OR eff IS DISTINCT FROM NEW.amount_minor::numeric
            OR NEW.gl_target_class IS NOT NULL OR NEW.evidence_ref IS NOT NULL
          THEN RAISE EXCEPTION 'position_corrected needs a non-reversal posting group of the same account, currency and lane with an equal amount' USING ERRCODE = '23514'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_variance_event_guard BEFORE INSERT ON mod_billing.{p}opening_variance_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_variance_event();

    -- The fact group must exist by commit, with the kind implied by the event,
    -- the variance's account and currency, and no posting effects.
    CREATE FUNCTION mod_billing.check_{p}variance_fact_group() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE v record; g record; want text;
    BEGIN
      IF NEW.fact_posting_group_id IS NULL THEN RETURN NEW; END IF;
      SELECT * INTO v FROM mod_billing.{p}opening_variances x WHERE x.id = NEW.variance_id{new_scope};
      SELECT * INTO g FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.fact_posting_group_id{new_scope};
      want := CASE WHEN NEW.kind = 'recorded' THEN 'opening_variance_recorded'
        WHEN NEW.kind = 'voided_by_import_reversal' THEN 'opening_variance_voided'
        WHEN NEW.resolution_kind = 'recognised' THEN 'opening_variance_recognised'
        WHEN NEW.resolution_kind = 'position_corrected' THEN 'opening_variance_position_corrected'
        ELSE NULL END;
      IF g.id IS NULL OR want IS NULL
      THEN RAISE EXCEPTION 'variance event has no fact posting group' USING ERRCODE = '23514'; END IF;
      IF g.group_kind <> want
      THEN RAISE EXCEPTION 'variance fact group kind differs from its event' USING ERRCODE = '23514'; END IF;
      IF g.billing_account_id <> v.billing_account_id
      THEN RAISE EXCEPTION 'variance fact group account differs from its variance' USING ERRCODE = '23514'; END IF;
      IF g.currency <> v.currency OR g.minor_units <> v.minor_units
      THEN RAISE EXCEPTION 'variance fact group currency differs from its variance' USING ERRCODE = '23514'; END IF;
      IF g.reverses_group_id IS NOT NULL OR EXISTS (SELECT 1 FROM mod_billing.{p}posting_effects e WHERE e.posting_group_id = g.id{"" if platform else " AND e.tenant_id = NEW.tenant_id"})
      THEN RAISE EXCEPTION 'variance fact group must be effect-free and reverse nothing' USING ERRCODE = '23514'; END IF;
      IF (SELECT count(*) FROM mod_billing.{p}accounting_facts f
            WHERE f.posting_group_id = g.id AND f.fact_version = 6 AND f.effect_kind = want{"" if platform else " AND f.tenant_id = NEW.tenant_id"}) <> 1
      THEN RAISE EXCEPTION 'variance event requires exactly one V6 fact on its group' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE CONSTRAINT TRIGGER variance_event_fact_group AFTER INSERT ON mod_billing.{p}opening_variance_events
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{p}variance_fact_group();

    -- Group side: a variance-kind posting group is complete only with exactly
    -- one matching event and exactly one V6 fact of its own kind (checked at
    -- commit), and it can never carry or gain a posting effect.
    CREATE FUNCTION mod_billing.check_{p}variance_group_complete() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    BEGIN
      IF (SELECT count(*) FROM mod_billing.{p}opening_variance_events x
            WHERE x.fact_posting_group_id = NEW.id{new_scope}
              AND NEW.group_kind = CASE WHEN x.kind = 'recorded' THEN 'opening_variance_recorded'
                WHEN x.kind = 'voided_by_import_reversal' THEN 'opening_variance_voided'
                WHEN x.resolution_kind = 'recognised' THEN 'opening_variance_recognised'
                WHEN x.resolution_kind = 'position_corrected' THEN 'opening_variance_position_corrected'
                ELSE NULL END) <> 1
      THEN RAISE EXCEPTION 'a variance posting group needs exactly one matching event' USING ERRCODE = '23514'; END IF;
      IF (SELECT count(*) FROM mod_billing.{p}accounting_facts f
            WHERE f.posting_group_id = NEW.id AND f.fact_version = 6
              AND f.effect_kind = NEW.group_kind{"" if platform else " AND f.tenant_id = NEW.tenant_id"}) <> 1
      THEN RAISE EXCEPTION 'a variance posting group needs exactly one V6 fact of its kind' USING ERRCODE = '23514'; END IF;
      IF EXISTS (SELECT 1 FROM mod_billing.{p}posting_effects e WHERE e.posting_group_id = NEW.id{"" if platform else " AND e.tenant_id = NEW.tenant_id"})
      THEN RAISE EXCEPTION 'a variance posting group carries no posting effects' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE CONSTRAINT TRIGGER variance_group_complete AFTER INSERT ON mod_billing.{p}posting_groups
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      WHEN (NEW.group_kind IN ('opening_variance_recorded','opening_variance_voided',
        'opening_variance_recognised','opening_variance_position_corrected'))
      EXECUTE FUNCTION mod_billing.check_{p}variance_group_complete();

    CREATE FUNCTION mod_billing.guard_{p}variance_group_effect() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope}
            AND x.group_kind IN ('opening_variance_recorded','opening_variance_voided',
              'opening_variance_recognised','opening_variance_position_corrected'))
      THEN RAISE EXCEPTION 'a variance fact group carries no posting effects' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER variance_group_no_effects BEFORE INSERT ON mod_billing.{p}posting_effects
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}variance_group_effect();

    -- Fact side: a V6 fact exists only for a variance group, and its payload
    -- must be exactly what the referencing event and variance say.
    CREATE FUNCTION mod_billing.guard_{p}variance_fact() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE g record; e record; v record; payload jsonb; want_minor numeric; rec_fact uuid;
      is_resolution boolean;
    BEGIN
      SELECT * INTO g FROM mod_billing.{p}posting_groups x WHERE x.id = NEW.posting_group_id{new_scope};
      IF g.id IS NULL OR g.group_kind NOT IN ('opening_variance_recorded','opening_variance_voided',
          'opening_variance_recognised','opening_variance_position_corrected') THEN
        IF NEW.fact_version = 6 OR left(NEW.effect_kind, 17) = 'opening_variance_' THEN
          RAISE EXCEPTION 'a V6 variance fact needs a variance posting group' USING ERRCODE = '23514'; END IF;
        RETURN NEW;
      END IF;
      SELECT * INTO e FROM mod_billing.{p}opening_variance_events x WHERE x.fact_posting_group_id = g.id{new_scope};
      SELECT * INTO v FROM mod_billing.{p}opening_variances x WHERE x.id = e.variance_id{new_scope};
      IF e.id IS NULL OR v.id IS NULL OR NEW.fact_version <> 6 OR NEW.effect_kind <> g.group_kind
        OR NEW.source_system <> 'dotmac-billing' OR NEW.source_authority <> 'internal'
      THEN RAISE EXCEPTION 'a variance fact needs its event and the V6 contract' USING ERRCODE = '23514'; END IF;
      payload := NEW.fact_payload::jsonb;
      is_resolution := e.kind IN ('resolved','partially_resolved');
      want_minor := CASE WHEN is_resolution THEN e.amount_minor ELSE v.amount_minor END;
      IF payload->>'fact_id' IS DISTINCT FROM NEW.id::text
        OR payload->>'posting_group_id' IS DISTINCT FROM g.id::text
        OR payload->>'billing_account_id' IS DISTINCT FROM g.billing_account_id::text
        OR payload->>'effect_kind' IS DISTINCT FROM g.group_kind
        OR payload->>'fact_version' IS DISTINCT FROM '6'
        OR (payload->'variance'->>'customer_adverse')::boolean IS DISTINCT FROM
           (CASE WHEN v.lane = 'receivable' THEN want_minor < 0 ELSE want_minor > 0 END)
        OR payload->'scope' IS DISTINCT FROM {scope_json}
        OR payload->>'source_ref' IS DISTINCT FROM 'opening-variance-event:' || e.id::text
        OR payload->'variance'->>'event_id' IS DISTINCT FROM e.id::text
        OR payload->'variance'->>'event_seq' IS DISTINCT FROM e.seq::text
        OR payload->'variance'->>'variance_id' IS DISTINCT FROM v.id::text
        OR payload->'variance'->>'window_id' IS DISTINCT FROM v.window_id::text
        OR payload->'variance'->>'lane' IS DISTINCT FROM v.lane
        OR payload->'variance'->>'basis' IS DISTINCT FROM v.basis
        OR payload->'variance'->>'reason_code' IS DISTINCT FROM v.reason_code
        OR payload->'variance'->>'source_kind' IS DISTINCT FROM v.source_kind
        OR payload->'variance'->>'source_ref' IS DISTINCT FROM v.source_ref
        OR payload->'variance'->>'import_receipt_id' IS DISTINCT FROM v.import_receipt_id::text
        OR payload->'variance'->>'evidence_digest' IS DISTINCT FROM v.evidence_digest
        OR payload->'variance'->>'rules_digest' IS DISTINCT FROM v.rules_digest
        OR (payload->'variance'->'amount'->>'minor_units')::int IS DISTINCT FROM v.minor_units
        OR payload->'variance'->'amount'->>'currency' IS DISTINCT FROM v.currency
        OR (payload->'variance'->'amount'->>'amount')::numeric * power(10::numeric, (payload->'variance'->'amount'->>'minor_units')::int)
           IS DISTINCT FROM want_minor
      THEN RAISE EXCEPTION 'variance fact payload differs from its event and variance' USING ERRCODE = '23514'; END IF;
      IF is_resolution THEN
        IF payload->'resolution'->>'resolution_kind' IS DISTINCT FROM e.resolution_kind
          OR payload->'resolution'->>'resolved_state' IS DISTINCT FROM e.kind
          OR payload->'resolution'->>'approval_request_id' IS DISTINCT FROM e.approval_request_id::text
          OR payload->'resolution'->>'maker_ref' IS DISTINCT FROM e.maker_ref
          OR payload->'resolution'->'approver_refs' IS DISTINCT FROM e.approver_refs
          OR payload->'resolution'->>'gl_target_class' IS DISTINCT FROM e.gl_target_class
          OR payload->'resolution'->>'corrected_by_posting_group_id' IS DISTINCT FROM
             (CASE WHEN e.resolution_kind = 'position_corrected' THEN e.posting_group_id::text END)
        THEN RAISE EXCEPTION 'variance fact resolution differs from its approved event' USING ERRCODE = '23514'; END IF;
      ELSIF jsonb_typeof(payload->'resolution') IS DISTINCT FROM 'null' THEN
        RAISE EXCEPTION 'only a resolution fact carries a resolution block' USING ERRCODE = '23514';
      END IF;
      IF e.kind = 'voided_by_import_reversal' THEN
        SELECT f.id INTO rec_fact FROM mod_billing.{p}opening_variance_events r
          JOIN mod_billing.{p}accounting_facts f ON f.posting_group_id = r.fact_posting_group_id
          WHERE r.variance_id = e.variance_id AND r.kind = 'recorded'{"" if platform else " AND r.tenant_id = NEW.tenant_id AND f.tenant_id = NEW.tenant_id"};
        IF rec_fact IS NULL OR NEW.reverses_fact_id IS DISTINCT FROM rec_fact
          OR payload->>'reverses_fact_id' IS DISTINCT FROM rec_fact::text
        THEN RAISE EXCEPTION 'a voided fact must reverse the recorded fact of its variance' USING ERRCODE = '23514'; END IF;
      ELSIF NEW.reverses_fact_id IS NOT NULL OR payload->>'reverses_fact_id' IS NOT NULL THEN
        RAISE EXCEPTION 'only a voided fact reverses a fact' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER variance_fact_guard BEFORE INSERT ON mod_billing.{p}accounting_facts
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}variance_fact();

    CREATE FUNCTION mod_billing.guard_{p}opening_gap_event() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE g record; prev record; sealed boolean;
    BEGIN
      SELECT * INTO g FROM mod_billing.{p}opening_control_gaps x WHERE x.id = NEW.gap_id{new_scope};
      SELECT EXISTS (SELECT 1 FROM mod_billing.{p}opening_window_seals s WHERE s.window_id = NEW.window_id{s_new}) INTO sealed;
      SELECT * INTO prev FROM mod_billing.{p}opening_control_gap_events x
        WHERE x.gap_id = NEW.gap_id{new_scope} ORDER BY x.seq DESC LIMIT 1;
      IF g.id IS NULL OR g.window_id <> NEW.window_id OR g.billing_account_id <> NEW.billing_account_id
        OR nullif(trim(NEW.actor), '') IS NULL
      THEN RAISE EXCEPTION 'control gap event requires its gap, window and actor' USING ERRCODE = '23514'; END IF;
      IF NEW.kind IN ('recorded','voided_by_import_reversal') AND (
        NEW.approval_request_id IS NOT NULL OR NEW.maker_ref IS NOT NULL
        OR NEW.approver_refs IS NOT NULL OR NEW.evidence_ref IS NOT NULL)
      THEN RAISE EXCEPTION 'only a resolution carries evidence and approval' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'recorded' AND prev.id IS NOT NULL
      THEN RAISE EXCEPTION 'control gap is already recorded' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'voided_by_import_reversal' AND (sealed OR prev.kind IS DISTINCT FROM 'recorded' OR NOT EXISTS (
          SELECT 1 FROM mod_billing.{p}opening_import_receipts r
          WHERE r.original_receipt_id = g.import_receipt_id AND r.receipt_kind = 'unwind'{"" if platform else " AND r.tenant_id = NEW.tenant_id"}))
      THEN RAISE EXCEPTION 'only a recorded gap of an unwound import is voided, before the seal' USING ERRCODE = '23514'; END IF;
      IF NEW.kind = 'resolved' AND (NOT sealed OR prev.kind IS DISTINCT FROM 'recorded'
          OR nullif(trim(NEW.evidence_ref), '') IS NULL
          OR NOT mod_billing.opening_approval_ok(NEW.maker_ref, NEW.approver_refs, NEW.approval_request_id)
          OR NEW.actor IS DISTINCT FROM NEW.maker_ref)
      THEN RAISE EXCEPTION 'a control gap resolves after the seal with evidence and maker-checker approval' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_gap_event_guard BEFORE INSERT ON mod_billing.{p}opening_control_gap_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{p}opening_gap_event();

    -- Finding 8: a posting group a resolution event references is not reversible.
    CREATE FUNCTION mod_billing.guard_{p}variance_resolution_lock() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM mod_billing.{p}opening_variance_events e
          WHERE e.posting_group_id = NEW.reverses_group_id{"" if platform else " AND e.tenant_id = NEW.tenant_id"})
      THEN RAISE EXCEPTION 'variance_resolution_locked: posting group resolves an opening variance' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER variance_resolution_lock BEFORE INSERT ON mod_billing.{p}posting_groups
      FOR EACH ROW WHEN (NEW.reverses_group_id IS NOT NULL)
      EXECUTE FUNCTION mod_billing.guard_{p}variance_resolution_lock();

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
          AND g.billing_account_id::text = ANY(accounts)
          AND coalesce((SELECT e.kind FROM mod_billing.{p}opening_control_gap_events e
                WHERE e.gap_id = g.id{e_scope} ORDER BY e.seq DESC LIMIT 1), '') = 'recorded') q;
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
              * power(10::numeric, (r.evidence::jsonb->'total_receivable'->>'minor_units')::int)) AS t,
          sum((r.evidence::jsonb->'total_residual'->>'amount')::numeric
              * power(10::numeric, (r.evidence::jsonb->'total_residual'->>'minor_units')::int)) AS lot
        FROM mod_billing.{p}opening_import_receipts r
        WHERE r.receipt_kind = 'import'{r_scope} AND r.billing_account_id::text = ANY(accounts)
          AND NOT EXISTS (SELECT 1 FROM mod_billing.{p}opening_import_receipts u
                WHERE u.original_receipt_id = r.id AND u.receipt_kind = 'unwind'{u_scope})
        GROUP BY 1),
      lots AS (
        SELECT DISTINCT c->'partition'->>'account_id' AS acct
        FROM jsonb_array_elements(coalesce(manifest->'cash_lots','[]'::jsonb)) c),
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
        OR (mea.reb IS NOT NULL AND mea.reb <> coalesce(tr.t, 0) - coalesce(tr.lot, 0) + coalesce((SELECT sum(v) FROM va WHERE va.acct = ctl.acct AND basis = 'rebuild'), 0))
        OR (ctl.acct::text IN (SELECT acct FROM lots) AND (ctl.cred <> 0 OR ctl.fund <> 0
            OR EXISTS (SELECT 1 FROM va WHERE va.acct = ctl.acct AND lane IN ('customer_credit','prepaid_funding'))))
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
    """)  # nosec B608 -- closed plane/table identifiers and fixed scope expressions; no input reaches this DDL


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
    seal_scope = "" if platform else " WHERE s.tenant_id = NEW.tenant_id"
    receipt_scope = "" if platform else " AND r.tenant_id = NEW.tenant_id"
    kinds = (
        "('opening_cash_imported','opening_account_imported',"
        "'opening_variance_recorded','opening_variance_voided')"
    )
    op.execute(f"""
    -- bi_0013's function with the two opening-principal variance fact kinds added
    -- (both the posting_groups and posting_effects branches). The two online
    -- kinds (recognised, position_corrected) are deliberately not listed.
    CREATE OR REPLACE FUNCTION mod_billing.guard_{p}opening_entry() RETURNS trigger
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $$
    DECLARE opening boolean := false; window_id uuid; acct uuid; sealed boolean;
    BEGIN
      IF TG_TABLE_NAME = '{p}documents' THEN
        opening := NEW.authoring_mode = 'imported_open_item';
        IF TG_OP = 'UPDATE' THEN opening := opening OR OLD.authoring_mode = 'imported_open_item'; END IF;
      ELSIF TG_TABLE_NAME = '{p}opening_import_receipts' THEN opening := NEW.receipt_kind = 'import';
      ELSIF TG_TABLE_NAME = '{p}opening_cash_effects' THEN opening := NEW.effect_kind = 'recognition';
      ELSIF TG_TABLE_NAME = '{p}posting_groups' THEN opening := NEW.group_kind IN {kinds};
      ELSIF TG_TABLE_NAME = '{p}posting_effects' THEN
        SELECT x.group_kind IN {kinds} INTO opening
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
      -- Finding 5: closure covers EVERY control partition minus the held ones,
      -- with T = 0 where nothing was imported; none can leave silently.
      SELECT coalesce(array_agg(DISTINCT c->'partition'->>'account_id'), ARRAY[]::text[]) INTO admitted
        FROM jsonb_array_elements(manifest->'controls') c WHERE NOT (held ? (c->'partition'->>'account_id'));
      -- A V1 (cash-only) manifest carries no variance ledger: nothing to close.
      IF bound.manifest_version = 2 THEN
        IF NOT mod_billing.opening_variance_set_matches({new_args}identity, manifest, admitted, true)
        THEN RAISE EXCEPTION 'seal refused: recorded variance set differs from the manifest variance set' USING ERRCODE = '23514'; END IF;
        IF mod_billing.opening_closure_defects({new_args}identity, manifest, admitted, true) <> 0
        THEN RAISE EXCEPTION 'seal refused: an opening control row does not close to zero' USING ERRCODE = '23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)  # nosec B608 -- closed plane/table identifiers and fixed scope expressions; no input reaches this DDL


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
