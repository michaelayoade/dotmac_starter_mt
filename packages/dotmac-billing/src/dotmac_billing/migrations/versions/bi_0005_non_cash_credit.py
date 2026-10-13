"""Separate immutable noncash grants/applications from confirmed cash credit.

Every newly posted group requires a fresh V2 position snapshot. Fence older
Billing writers before migration; no historical group/snapshot is backfilled.
"""

from __future__ import annotations

# SQL identifiers/predicates are closed module-plane literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0005_non_cash_credit"
down_revision = "bi_0004_withholding_discharge"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLES = (
    "non_cash_credit_grants",
    "non_cash_credit_effects",
    "receivable_position_facts_v2",
)


def _elements(platform: bool, kind: str) -> tuple[SchemaItem, ...]:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    name = f"{prefix}{'receivable_position_facts_v2' if kind == 'position' else 'non_cash_credit_' + kind}"
    columns: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
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
                ["tenant_id"],
                ["public.tenants.id"],
                name=f"fk_{name}_tenant",
                ondelete="CASCADE",
            )
        )
    if kind == "position":
        columns.extend(
            (
                sa.Column("source_owner", sa.String(120), nullable=False),
                sa.Column("source_version", sa.BigInteger(), nullable=False),
                sa.Column("posting_group_watermark", sa.Uuid()),
                sa.Column("currency", sa.String(3), nullable=False),
                sa.Column("minor_units", sa.Integer(), nullable=False),
                *(
                    sa.Column(key, sa.Numeric(20, 6), nullable=False)
                    for key in (
                        "collectible_receivable",
                        "available_credit",
                        "prepaid_funding",
                        "available_non_cash_credit",
                    )
                ),
                *(
                    sa.Column(key, sa.String(64), nullable=False)
                    for key in ("state_fingerprint", "rebuild_fingerprint")
                ),
                sa.Column("source_authority", sa.String(40), nullable=False),
                sa.Column("projection_mode", sa.String(24), nullable=False),
                sa.Column("derived_from", sa.String(32), nullable=False),
                sa.Column("completeness", sa.String(32), nullable=False),
                sa.Column("completeness_reason_code", sa.String(120)),
                sa.Column("financial_state", sa.String(32), nullable=False),
                sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
                sa.UniqueConstraint(*scope, "id", name=f"uq_{name}_id"),
                sa.UniqueConstraint(
                    *scope,
                    "source_owner",
                    "billing_account_id",
                    "currency",
                    "source_version",
                    name=f"uq_{name}_version",
                ),
                sa.ForeignKeyConstraint(
                    [*scope, "posting_group_watermark", "billing_account_id"],
                    [
                        *(f"{_SCHEMA}.{prefix}posting_groups.{key}" for key in scope),
                        f"{_SCHEMA}.{prefix}posting_groups.id",
                        f"{_SCHEMA}.{prefix}posting_groups.billing_account_id",
                    ],
                    name=f"fk_{name}_watermark",
                    ondelete="RESTRICT",
                ),
                sa.CheckConstraint(
                    "available_non_cash_credit >= 0", name=f"ck_{name}_noncash"
                ),
            )
        )
        return tuple(columns)
    columns.extend(
        (
            sa.Column("posting_group_id", sa.Uuid(), nullable=False),
            *(
                sa.Column(key, sa.String(size), nullable=False)
                for key, size in (
                    ("source_owner", 120),
                    ("source_ref", 255),
                    ("source_version", 255),
                    ("authorization_actor_ref", 255),
                    ("authorization_evidence_ref", 255),
                    ("request_digest", 64),
                    ("evidence_digest", 64),
                )
            ),
            sa.Column("currency", sa.String(3), nullable=False),
            sa.Column("minor_units", sa.Integer(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.UniqueConstraint(*scope, "id", name=f"uq_{name}_id"),
            sa.UniqueConstraint(
                *scope, "id", "billing_account_id", name=f"uq_{name}_account"
            ),
            sa.UniqueConstraint(
                *scope, "source_owner", "source_ref", name=f"uq_{name}_source"
            ),
            sa.CheckConstraint(
                "minor_units BETWEEN 0 AND 6", name=f"ck_{name}_precision"
            ),
        )
    )
    links = [("posting_group_id", "posting_groups")]
    if kind == "effects":
        columns.extend(
            (
                sa.Column("grant_id", sa.Uuid(), nullable=False),
                sa.Column("document_id", sa.Uuid()),
                sa.Column("original_application_id", sa.Uuid()),
                sa.Column("effect_kind", sa.String(40), nullable=False),
                sa.Column("availability_delta", sa.Numeric(20, 6), nullable=False),
                sa.CheckConstraint("availability_delta <> 0", name=f"ck_{name}_amount"),
                sa.CheckConstraint(
                    "(effect_kind = 'grant' AND availability_delta > 0 AND document_id IS NULL AND original_application_id IS NULL) OR "
                    "(effect_kind = 'application' AND availability_delta < 0 AND document_id IS NOT NULL AND original_application_id IS NULL) OR "
                    "(effect_kind = 'application_reversal' AND availability_delta > 0 AND document_id IS NOT NULL AND original_application_id IS NOT NULL) OR "
                    "(effect_kind = 'unused_reduction' AND availability_delta < 0 AND document_id IS NULL AND original_application_id IS NULL)",
                    name=f"ck_{name}_direction",
                ),
            )
        )
        links.extend(
            (
                ("grant_id", "non_cash_credit_grants"),
                ("document_id", "documents"),
                ("original_application_id", "non_cash_credit_effects"),
            )
        )
    else:
        columns.extend(
            (
                sa.Column("restricted_document_id", sa.Uuid()),
                sa.Column("replaces_grant_id", sa.Uuid()),
                sa.Column("replaces_effect_id", sa.Uuid()),
                sa.Column("original_amount", sa.Numeric(20, 6), nullable=False),
                *(
                    sa.Column(key, sa.String(size), nullable=False)
                    for key, size in (
                        ("category", 120),
                        ("config_owner", 120),
                        ("config_ref", 255),
                        ("config_version", 255),
                        ("config_digest", 64),
                    )
                ),
                sa.CheckConstraint("original_amount > 0", name=f"ck_{name}_amount"),
                sa.CheckConstraint(
                    "(replaces_grant_id IS NULL) = (replaces_effect_id IS NULL)",
                    name=f"ck_{name}_replacement_pair",
                ),
                sa.UniqueConstraint(
                    *scope, "posting_group_id", name=f"uq_{name}_group"
                ),
            )
        )
        links.extend(
            (
                ("restricted_document_id", "documents"),
                ("replaces_grant_id", "non_cash_credit_grants"),
            )
        )
    for column, target in links:
        parent = f"{_SCHEMA}.{prefix}{target}"
        columns.append(
            sa.ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    *(f"{parent}.{key}" for key in scope),
                    f"{parent}.id",
                    f"{parent}.billing_account_id",
                ],
                name=f"fk_{name}_{column}",
                ondelete="RESTRICT",
            )
        )
    return tuple(columns)


def _create(platform: bool) -> None:
    # Actual literal declarations remain visible to the composed DDL scanner.
    if platform:
        op.create_table(
            "platform_non_cash_credit_grants",
            *_elements(True, "grants"),
            schema=_SCHEMA,
        )
        op.create_table(
            "platform_non_cash_credit_effects",
            *_elements(True, "effects"),
            schema=_SCHEMA,
        )
        op.create_table(
            "platform_receivable_position_facts_v2",
            *_elements(True, "position"),
            schema=_SCHEMA,
        )
    else:
        op.create_table(
            "non_cash_credit_grants", *_elements(False, "grants"), schema=_SCHEMA
        )
        op.create_table(
            "non_cash_credit_effects", *_elements(False, "effects"), schema=_SCHEMA
        )
        op.create_table(
            "receivable_position_facts_v2",
            *_elements(False, "position"),
            schema=_SCHEMA,
        )
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    op.create_foreign_key(
        f"fk_{prefix}non_cash_credit_grants_replaces_effect_id",
        f"{prefix}non_cash_credit_grants",
        f"{prefix}non_cash_credit_effects",
        [*scope, "replaces_effect_id", "billing_account_id"],
        [*scope, "id", "billing_account_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    for name in _TABLES:
        table = f"{prefix}{name}"
        if platform:
            op.execute(
                f"REVOKE ALL ON mod_billing.{table} FROM app_user; GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;"
            )  # nosec B608 -- fixed plane/table names
        else:
            op.execute(f"""
                ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY {table}_tenant_isolation ON mod_billing.{table}
                  USING (tenant_id = public.app_current_tenant_id())
                  WITH CHECK (tenant_id = public.app_current_tenant_id());
                GRANT SELECT, INSERT ON mod_billing.{table} TO app_user;
                REVOKE ALL ON mod_billing.{table} FROM platform_api;
            """)  # nosec B608 -- fixed table names
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table}
            FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """)  # nosec B608 -- fixed plane/table names
        if name != "receivable_position_facts_v2":
            # Invoker FOR UPDATE requires UPDATE on at least one column. The
            # append-only trigger still refuses every UPDATE, including id=id.
            op.execute(
                f"GRANT UPDATE (id) ON mod_billing.{table} TO {'platform_api' if platform else 'app_user'};"
            )  # nosec B608 -- closed table/role choices
    _guards(platform)


def _guards(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_evidence = (
        "(NEW.evidence->'scope')::jsonb IS DISTINCT FROM '{}'::jsonb"
        if platform
        else "(NEW.evidence->'scope'->>'tenant_id') IS DISTINCT FROM NEW.tenant_id::text"
    )
    live = f"mod_billing.has_{prefix}live_invoice_issuance(NEW.restricted_document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{'' if platform else ', NEW.tenant_id'})"
    # SQL owns the envelope and relational/money invariants. Kernel canonical
    # JSON digest verification remains with public immutable reads/consumers;
    # PostgreSQL does not introduce a second JSON/hash encoding here.
    envelope = f"""
        jsonb_typeof(NEW.evidence::jsonb) IS DISTINCT FROM 'object' OR {scope_evidence}
        OR (NEW.evidence->>'billing_account_id') IS DISTINCT FROM NEW.billing_account_id::text
        OR (NEW.evidence->>'source_owner') IS DISTINCT FROM NEW.source_owner
        OR (NEW.evidence->>'source_ref') IS DISTINCT FROM NEW.source_ref
        OR (NEW.evidence->>'source_version') IS DISTINCT FROM NEW.source_version
        OR (NEW.evidence->>'authorization_actor_ref') IS DISTINCT FROM NEW.authorization_actor_ref
        OR (NEW.evidence->>'authorization_evidence_ref') IS DISTINCT FROM NEW.authorization_evidence_ref
        OR trim(NEW.source_owner) = '' OR trim(NEW.source_ref) = '' OR trim(NEW.source_version) = ''
        OR trim(NEW.authorization_actor_ref) = '' OR trim(NEW.authorization_evidence_ref) = ''
        OR NEW.request_digest !~ '^[0-9a-f]{{64}}$' OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$'
        OR (NEW.evidence->>'occurred_at') IS NULL
        OR (NEW.evidence->>'occurred_at') !~ '(Z|[+-][0-9]{{2}}:[0-9]{{2}}(:[0-9]{{2}}(\\.[0-9]+)?)?)$'
        OR (NEW.evidence->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
    """
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}noncash_grant() RETURNS trigger AS $$
    DECLARE account record; grp record; original record; reduction record; replaced numeric;
    BEGIN
        SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x
          WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
          WHERE x.id = NEW.posting_group_id{scope};
        IF account.id IS NULL OR grp.id IS NULL OR grp.billing_account_id <> NEW.billing_account_id
          OR account.currency <> NEW.currency OR account.minor_units <> NEW.minor_units
          OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
          OR grp.group_kind <> 'non_cash_credit_granted' OR grp.source_ref <> NEW.source_ref
          OR grp.reverses_group_id IS NOT NULL
          OR NEW.original_amount::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.original_amount <> round(NEW.original_amount, NEW.minor_units)
          OR {envelope}
          OR (NEW.evidence->>'grant_id') IS DISTINCT FROM NEW.id::text
          OR (NEW.evidence->>'restricted_document_id') IS DISTINCT FROM NEW.restricted_document_id::text
          OR (NEW.evidence->>'replaces_grant_id') IS DISTINCT FROM NEW.replaces_grant_id::text
          OR (NEW.evidence->>'replaces_effect_id') IS DISTINCT FROM NEW.replaces_effect_id::text
          OR ((NEW.replaces_grant_id IS NULL) <> (NEW.replaces_effect_id IS NULL))
          OR (NEW.evidence->'original_amount'->>'amount')::numeric IS DISTINCT FROM NEW.original_amount
          OR (NEW.evidence->'original_amount'->>'currency') IS DISTINCT FROM NEW.currency
          OR (NEW.evidence->'original_amount'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
          OR (NEW.evidence->'classification'->>'category') IS DISTINCT FROM NEW.category
          OR (NEW.evidence->'classification'->>'config_owner') IS DISTINCT FROM NEW.config_owner
          OR (NEW.evidence->'classification'->>'config_ref') IS DISTINCT FROM NEW.config_ref
          OR (NEW.evidence->'classification'->>'config_version') IS DISTINCT FROM NEW.config_version
          OR (NEW.evidence->'classification'->>'config_digest') IS DISTINCT FROM NEW.config_digest
          OR trim(NEW.category) = '' OR trim(NEW.config_owner) = '' OR trim(NEW.config_ref) = ''
          OR trim(NEW.config_version) = '' OR NEW.config_digest !~ '^[0-9a-f]{{64}}$'
          OR jsonb_typeof(NEW.evidence::jsonb->'classification'->'accepted_snapshot') IS DISTINCT FROM 'array'
        THEN RAISE EXCEPTION 'invalid noncash grant envelope' USING ERRCODE = 'check_violation'; END IF;
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.evidence::jsonb->'classification'->'accepted_snapshot') p
            WHERE jsonb_typeof(p) IS DISTINCT FROM 'object' OR jsonb_typeof(p->'name') IS DISTINCT FROM 'string'
               OR nullif(trim(p->>'name'), '') IS NULL OR jsonb_typeof(p->'value') IS DISTINCT FROM 'string')
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.evidence::jsonb->'classification'->'accepted_snapshot') p
              GROUP BY p->>'name' HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'invalid accepted commercial policy snapshot' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.restricted_document_id IS NOT NULL AND NOT {live} THEN
            RAISE EXCEPTION 'noncash restriction requires live invoice issuance' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.replaces_grant_id IS NOT NULL THEN
            SELECT * INTO original FROM mod_billing.{prefix}non_cash_credit_grants x
              WHERE x.id = NEW.replaces_grant_id{scope} FOR UPDATE;
            SELECT * INTO reduction FROM mod_billing.{prefix}non_cash_credit_effects x
              WHERE x.id = NEW.replaces_effect_id{scope} FOR UPDATE;
            SELECT coalesce(sum(x.original_amount), 0) INTO replaced
              FROM mod_billing.{prefix}non_cash_credit_grants x
              WHERE x.replaces_effect_id = NEW.replaces_effect_id{scope};
            IF original.id IS NULL OR original.id = NEW.id OR original.billing_account_id <> NEW.billing_account_id
              OR original.currency <> NEW.currency OR original.minor_units <> NEW.minor_units
              OR original.restricted_document_id IS DISTINCT FROM NEW.restricted_document_id
              OR reduction.id IS NULL OR reduction.effect_kind <> 'unused_reduction'
              OR reduction.grant_id <> original.id OR reduction.billing_account_id <> NEW.billing_account_id
              OR reduction.currency <> NEW.currency OR reduction.minor_units <> NEW.minor_units
              OR NEW.original_amount + replaced > -reduction.availability_delta THEN
                RAISE EXCEPTION 'noncash replacement exceeds named unused reduction' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}non_cash_credit_grants_admission BEFORE INSERT ON mod_billing.{prefix}non_cash_credit_grants
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}noncash_grant();
    """)  # nosec B608 -- closed plane names, scope predicates and JSON envelope
    live_application = live.replace("NEW.restricted_document_id", "NEW.document_id")
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}noncash_effect() RETURNS trigger AS $$
    DECLARE account record; grp record; instrument record; original record; doc record;
            room numeric; restored numeric; used numeric;
    BEGIN
        SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x
          WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        SELECT * INTO instrument FROM mod_billing.{prefix}non_cash_credit_grants x
          WHERE x.id = NEW.grant_id{scope} FOR UPDATE;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
          WHERE x.id = NEW.posting_group_id{scope};
        IF account.id IS NULL OR instrument.id IS NULL OR grp.id IS NULL
          OR instrument.billing_account_id <> NEW.billing_account_id OR grp.billing_account_id <> NEW.billing_account_id
          OR account.currency <> NEW.currency OR account.minor_units <> NEW.minor_units
          OR instrument.currency <> NEW.currency OR instrument.minor_units <> NEW.minor_units
          OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
          OR grp.reverses_group_id IS NOT NULL
          OR NEW.availability_delta::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.availability_delta <> round(NEW.availability_delta, NEW.minor_units)
          OR {envelope}
          OR (NEW.evidence->>'effect_id') IS DISTINCT FROM NEW.id::text
          OR (NEW.evidence->>'grant_id') IS DISTINCT FROM NEW.grant_id::text
          OR (NEW.evidence->>'effect_kind') IS DISTINCT FROM NEW.effect_kind
          OR (NEW.evidence->>'document_id') IS DISTINCT FROM NEW.document_id::text
          OR (NEW.evidence->>'original_application_id') IS DISTINCT FROM NEW.original_application_id::text
          OR (NEW.evidence->'availability_delta'->>'amount')::numeric IS DISTINCT FROM NEW.availability_delta
          OR (NEW.evidence->'availability_delta'->>'currency') IS DISTINCT FROM NEW.currency
          OR (NEW.evidence->'availability_delta'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        THEN RAISE EXCEPTION 'invalid noncash effect envelope' USING ERRCODE = 'check_violation'; END IF;
        IF NOT (grp.group_kind = 'non_cash_credit_granted' AND NEW.effect_kind = 'application')
           AND grp.source_ref <> NEW.source_ref THEN
            RAISE EXCEPTION 'noncash source must bind posting group' USING ERRCODE = 'check_violation';
        END IF;
        SELECT coalesce(sum(x.availability_delta), 0) INTO room FROM mod_billing.{prefix}non_cash_credit_effects x
          WHERE x.grant_id = NEW.grant_id{scope};
        IF NEW.effect_kind = 'grant' THEN
            IF grp.group_kind <> 'non_cash_credit_granted' OR instrument.posting_group_id <> NEW.posting_group_id
              OR NEW.availability_delta <> instrument.original_amount
              OR NEW.source_owner <> instrument.source_owner OR NEW.source_ref <> instrument.source_ref
              OR NEW.source_version <> instrument.source_version
              OR EXISTS (SELECT 1 FROM mod_billing.{prefix}non_cash_credit_effects x
                  WHERE x.grant_id = NEW.grant_id AND x.effect_kind = 'grant'{scope}) THEN
                RAISE EXCEPTION 'grant has exactly one original availability effect' USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.effect_kind = 'application' THEN
            IF grp.group_kind NOT IN ('non_cash_credit_applied', 'non_cash_credit_granted')
              OR (instrument.restricted_document_id IS NOT NULL AND instrument.restricted_document_id <> NEW.document_id)
              OR (grp.group_kind = 'non_cash_credit_granted' AND
                  (instrument.posting_group_id <> NEW.posting_group_id OR NEW.source_owner <> 'dotmac-billing'
                   OR NEW.source_ref <> 'apply-on-issue:' || NEW.grant_id::text)) THEN
                RAISE EXCEPTION 'noncash application violates grant restriction or group' USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO doc FROM mod_billing.{prefix}documents x
              WHERE x.id = NEW.document_id{scope} FOR UPDATE;
            IF doc.id IS NULL OR doc.billing_account_id <> NEW.billing_account_id
              OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
              OR NOT {live_application}
              OR EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events x
                  WHERE x.document_id = NEW.document_id AND x.event_kind = 'voided'{scope}) THEN
                RAISE EXCEPTION 'noncash application requires live matching invoice' USING ERRCODE = 'check_violation';
            END IF;
            SELECT coalesce(sum(x.amount_delta), 0) INTO used FROM mod_billing.{prefix}allocation_effects x
              WHERE x.document_id = NEW.document_id{scope};
            used := used + (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}withholding_discharges x
              WHERE x.document_id = NEW.document_id{scope});
            used := used - (SELECT coalesce(sum(x.availability_delta), 0) FROM mod_billing.{prefix}non_cash_credit_effects x
              WHERE x.document_id = NEW.document_id{scope});
            used := used + (SELECT coalesce(sum(x.grand_total), 0) FROM mod_billing.{prefix}documents x
              WHERE x.credits_document_id = NEW.document_id AND x.document_kind = 'credit_note'
                AND x.issued_at IS NOT NULL{scope}
                AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events e
                  WHERE e.document_id = x.id AND e.event_kind = 'voided'
                  {'' if platform else 'AND e.tenant_id = x.tenant_id'}));
            IF -NEW.availability_delta > doc.grand_total - used THEN
                RAISE EXCEPTION 'noncash application exceeds invoice outstanding' USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.effect_kind = 'application_reversal' THEN
            SELECT * INTO original FROM mod_billing.{prefix}non_cash_credit_effects x
              WHERE x.id = NEW.original_application_id{scope} FOR UPDATE;
            SELECT coalesce(sum(x.availability_delta), 0) INTO restored FROM mod_billing.{prefix}non_cash_credit_effects x
              WHERE x.original_application_id = NEW.original_application_id{scope};
            IF grp.group_kind <> 'non_cash_credit_application_reversed' OR original.id IS NULL
              OR original.effect_kind <> 'application' OR original.original_application_id IS NOT NULL
              OR original.grant_id <> NEW.grant_id OR original.billing_account_id <> NEW.billing_account_id
              OR original.document_id IS DISTINCT FROM NEW.document_id
              OR original.currency <> NEW.currency OR original.minor_units <> NEW.minor_units
              OR NEW.availability_delta + restored > -original.availability_delta THEN
                RAISE EXCEPTION 'noncash reversal exceeds original application room' USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.effect_kind = 'unused_reduction' THEN
            IF grp.group_kind <> 'non_cash_credit_reduced' THEN
                RAISE EXCEPTION 'noncash unused reduction requires its own group' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF room + NEW.availability_delta < 0 OR room + NEW.availability_delta > instrument.original_amount THEN
            RAISE EXCEPTION 'noncash effect exceeds per-grant availability' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}non_cash_credit_effects_admission BEFORE INSERT ON mod_billing.{prefix}non_cash_credit_effects
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}noncash_effect();
    """)  # nosec B608 -- fixed module planes and literal scope/envelope fragments
    _position_guard(platform)
    _posting_guard(platform)
    _invoice_capacity_guard(platform)


def _invoice_capacity_guard(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.check_{prefix}noncash_invoice_room() RETURNS trigger AS $$
    DECLARE invoice_id uuid; account_id uuid; doc record; used numeric;
    BEGIN
        IF TG_TABLE_NAME = '{prefix}documents' THEN
            IF NEW.document_kind = 'credit_note' THEN invoice_id := NEW.credits_document_id;
            ELSE invoice_id := NEW.id; END IF;
        ELSE invoice_id := NEW.document_id; END IF;
        IF invoice_id IS NULL THEN RETURN NEW; END IF;
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = invoice_id{scope};
        IF doc.document_kind = 'credit_note' THEN
            invoice_id := doc.credits_document_id;
            SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = invoice_id{scope};
        END IF;
        IF doc.id IS NULL OR doc.document_kind <> 'invoice' THEN RETURN NEW; END IF;
        account_id := doc.billing_account_id;
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = account_id{scope} FOR UPDATE;
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = invoice_id{scope} FOR UPDATE;
        SELECT coalesce(sum(x.amount_delta), 0) INTO used FROM mod_billing.{prefix}allocation_effects x
          WHERE x.document_id = invoice_id{scope};
        used := used + (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}withholding_discharges x
          WHERE x.document_id = invoice_id{scope});
        used := used - (SELECT coalesce(sum(x.availability_delta), 0) FROM mod_billing.{prefix}non_cash_credit_effects x
          WHERE x.document_id = invoice_id{scope});
        used := used + (SELECT coalesce(sum(x.grand_total), 0) FROM mod_billing.{prefix}documents x
          WHERE x.credits_document_id = invoice_id AND x.document_kind = 'credit_note'
            AND x.issued_at IS NOT NULL{scope}
            AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events e
              WHERE e.document_id = x.id AND e.event_kind = 'voided'
              {'' if platform else 'AND e.tenant_id = x.tenant_id'}));
        IF used::text IN ('NaN', 'Infinity', '-Infinity') OR used < 0 OR used > doc.grand_total THEN
            RAISE EXCEPTION 'invoice resolution exceeds exact live receivable room' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- fixed planes and literal scope predicates
    for table in (
        "withholding_discharges",
        "allocation_effects",
        "non_cash_credit_effects",
        "document_events",
        "documents",
    ):
        operation = "INSERT OR UPDATE" if table == "documents" else "INSERT"
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {prefix}{table}_noncash_invoice_room AFTER {operation} ON mod_billing.{prefix}{table}
              DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}noncash_invoice_room();
        """)  # nosec B608 -- fixed tables and operation literals


def _position_guard(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}position_v2() RETURNS trigger AS $$
    DECLARE account record; grp record;
    BEGIN
        SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x
          WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
          WHERE x.id = NEW.posting_group_watermark{scope};
        IF account.id IS NULL OR grp.id IS NULL OR grp.billing_account_id <> NEW.billing_account_id
          OR grp.source_version <> NEW.source_version OR NEW.source_version < 1
          OR account.currency <> NEW.currency OR account.minor_units <> NEW.minor_units
          OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
          OR NEW.source_owner <> 'dotmac-billing' OR NEW.source_authority <> 'internal'
          OR NEW.projection_mode <> 'authoritative' OR NEW.derived_from <> 'posting_groups'
          OR NEW.completeness <> 'complete' OR NEW.completeness_reason_code IS NOT NULL
          OR NEW.financial_state <> CASE WHEN NEW.collectible_receivable = 0 THEN 'resolved' ELSE 'open' END
          OR NEW.state_fingerprint !~ '^[0-9a-f]{{64}}$' OR NEW.rebuild_fingerprint <> NEW.state_fingerprint
          OR NEW.collectible_receivable::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.available_credit::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.prepaid_funding::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.available_non_cash_credit::text IN ('NaN', 'Infinity', '-Infinity')
          OR NEW.collectible_receivable < 0 OR NEW.available_credit < 0 OR NEW.prepaid_funding < 0
          OR NEW.collectible_receivable <> round(NEW.collectible_receivable, NEW.minor_units)
          OR NEW.available_credit <> round(NEW.available_credit, NEW.minor_units)
          OR NEW.prepaid_funding <> round(NEW.prepaid_funding, NEW.minor_units)
          OR NEW.available_non_cash_credit <> round(NEW.available_non_cash_credit, NEW.minor_units)
        THEN RAISE EXCEPTION 'invalid V2 position envelope' USING ERRCODE = 'check_violation'; END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}receivable_position_facts_v2_admission BEFORE INSERT ON mod_billing.{prefix}receivable_position_facts_v2
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}position_v2();
    """)  # nosec B608 -- fixed module-plane names and predicates


def _posting_guard(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    joined_scope = "" if platform else " AND g.tenant_id = x.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.guard_{prefix}noncash_group() RETURNS trigger AS $$
    DECLARE original record;
    BEGIN
        -- Every post-migration SQL writer uses the same account-first order.
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x
          WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        IF NEW.source_version IS DISTINCT FROM (SELECT coalesce(max(x.source_version), 0) + 1
            FROM mod_billing.{prefix}posting_groups x WHERE x.billing_account_id = NEW.billing_account_id{scope}) THEN
            RAISE EXCEPTION 'posting versions must advance the locked account' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.reverses_group_id IS NOT NULL THEN
            SELECT * INTO original FROM mod_billing.{prefix}posting_groups x
              WHERE x.id = NEW.reverses_group_id{scope};
            IF original.group_kind IN ('non_cash_credit_granted', 'non_cash_credit_applied',
                'non_cash_credit_application_reversed', 'non_cash_credit_reduced') THEN
                RAISE EXCEPTION 'noncash corrections require their dedicated owner' USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts x
                WHERE x.posting_group_watermark = NEW.reverses_group_id{scope}
                  AND original.group_kind = 'invoice_issued'
                  AND (SELECT coalesce(sum(e.availability_delta), 0)
                       FROM mod_billing.{prefix}non_cash_credit_effects e
                       WHERE e.document_id = x.document_id
                         {'' if platform else 'AND e.tenant_id = x.tenant_id'}) < 0) THEN
                RAISE EXCEPTION 'reverse noncash applications before invoice issuance' USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}posting_groups_noncash_admission BEFORE INSERT ON mod_billing.{prefix}posting_groups
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}noncash_group();
    CREATE FUNCTION mod_billing.guard_{prefix}noncash_void() RETURNS trigger AS $$
    DECLARE doc record;
    BEGIN
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = doc.billing_account_id{scope} FOR UPDATE;
        IF NEW.event_kind = 'voided' AND (SELECT coalesce(sum(x.availability_delta), 0)
            FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.document_id = NEW.document_id{scope}) < 0 THEN
            RAISE EXCEPTION 'reverse noncash applications before invoice void' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}document_events_noncash_void BEFORE INSERT ON mod_billing.{prefix}document_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}noncash_void();
    """)  # nosec B608 -- fixed qualified planes and scoped predicates
    op.execute(f"""
    CREATE FUNCTION mod_billing.check_{prefix}noncash_posting() RETURNS trigger AS $$
    DECLARE group_id uuid; grp record; snapshot record; prior record;
            n integer; ar numeric; receivable numeric; cash_credit numeric; prepaid numeric; noncash numeric;
    BEGIN
        IF TG_TABLE_NAME = '{prefix}posting_groups' THEN group_id := NEW.id;
        ELSIF TG_TABLE_NAME IN ('{prefix}receivable_position_facts', '{prefix}receivable_position_facts_v2') THEN
            group_id := NEW.posting_group_watermark;
        ELSE group_id := NEW.posting_group_id; END IF;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = group_id{scope};
        IF grp.id IS NULL THEN RETURN NEW; END IF;
        SELECT * INTO snapshot FROM mod_billing.{prefix}receivable_position_facts_v2 x
          WHERE x.posting_group_watermark = group_id AND x.source_owner = 'dotmac-billing'{scope};
        SELECT * INTO prior FROM mod_billing.{prefix}receivable_position_facts x
          WHERE x.posting_group_watermark = group_id AND x.source_owner = 'dotmac-billing'{scope};
        SELECT coalesce(sum(x.amount_delta) FILTER (WHERE x.lane = 'receivable'), 0),
               coalesce(sum(x.amount_delta) FILTER (WHERE x.lane = 'available_credit'), 0),
               coalesce(sum(x.amount_delta) FILTER (WHERE x.lane = 'prepaid_funding'), 0)
          INTO receivable, cash_credit, prepaid FROM mod_billing.{prefix}posting_effects x
          JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id{joined_scope}
          WHERE x.billing_account_id = grp.billing_account_id AND g.source_version <= grp.source_version{scope};
        SELECT coalesce(sum(x.availability_delta), 0) INTO noncash FROM mod_billing.{prefix}non_cash_credit_effects x
          JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id{joined_scope}
          WHERE x.billing_account_id = grp.billing_account_id AND g.source_version <= grp.source_version{scope};
        IF snapshot.id IS NULL OR prior.id IS NULL OR snapshot.source_version <> grp.source_version
          OR snapshot.billing_account_id <> grp.billing_account_id OR snapshot.currency <> grp.currency
          OR snapshot.minor_units <> grp.minor_units OR snapshot.collectible_receivable <> receivable
          OR snapshot.available_credit <> cash_credit OR snapshot.prepaid_funding <> prepaid
          OR snapshot.available_non_cash_credit <> noncash OR noncash < 0
          OR prior.source_version <> grp.source_version OR prior.billing_account_id <> grp.billing_account_id
          OR prior.currency <> grp.currency OR prior.minor_units <> grp.minor_units
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x
              JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id{joined_scope}
              WHERE x.billing_account_id = grp.billing_account_id AND g.source_version <= grp.source_version{scope}
                AND (x.currency <> grp.currency OR x.minor_units <> grp.minor_units))
          OR prior.collectible_receivable <> snapshot.collectible_receivable
          OR prior.available_credit <> snapshot.available_credit OR prior.prepaid_funding <> snapshot.prepaid_funding THEN
            RAISE EXCEPTION 'posting requires exact fresh V1 and V2 position snapshots; fence old writers' USING ERRCODE = 'check_violation';
        END IF;
        IF grp.group_kind NOT IN ('non_cash_credit_granted', 'non_cash_credit_applied',
            'non_cash_credit_application_reversed', 'non_cash_credit_reduced') THEN RETURN NEW; END IF;
        SELECT count(*), coalesce(sum(x.availability_delta) FILTER (WHERE x.document_id IS NOT NULL), 0)
          INTO n, ar FROM mod_billing.{prefix}non_cash_credit_effects x
          WHERE x.posting_group_id = group_id{scope};
        IF EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope})
          OR n = 0 OR (grp.group_kind <> 'non_cash_credit_granted' AND n <> 1)
          OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id{scope}) <> CASE WHEN ar = 0 THEN 0 ELSE 1 END
          OR (ar <> 0 AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x
              WHERE x.posting_group_id = group_id{scope} AND x.lane = 'receivable' AND x.amount_delta = ar
                AND x.currency = grp.currency AND x.minor_units = grp.minor_units
                AND x.billing_account_id = grp.billing_account_id)) THEN
            RAISE EXCEPTION 'noncash posting permits only its exact AR effect and no cash allocation' USING ERRCODE = 'check_violation';
        END IF;
        IF grp.group_kind = 'non_cash_credit_granted' AND
          ((SELECT count(*) FROM mod_billing.{prefix}non_cash_credit_grants x WHERE x.posting_group_id = group_id{scope}) <> 1
           OR (SELECT count(*) FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.posting_group_id = group_id AND x.effect_kind = 'grant'{scope}) <> 1
           OR n > 2) THEN
            RAISE EXCEPTION 'noncash issuance requires its one grant effect and optional auto application' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- fixed planes and literal JSON/scoping fragments
    for table in (
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "non_cash_credit_grants",
        "non_cash_credit_effects",
        "receivable_position_facts",
        "receivable_position_facts_v2",
    ):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {prefix}{table}_noncash_complete AFTER INSERT ON mod_billing.{prefix}{table}
              DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}noncash_posting();
        """)  # nosec B608 -- fixed plane/table names


def _apply(*, upgrade: bool) -> None:
    prefixes = [
        "platform_" if plane == ModulePlane.PLATFORM else ""
        for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM)
        if plane in selected_module_planes("billing")
    ]
    connection = op.get_bind()
    previous = {
        name: connection.execute(sa.text(f"SHOW {name}")).scalar_one()
        for name in ("lock_timeout", "statement_timeout", "row_security")
    }
    tables = [
        f"{prefix}{table}"
        for table in (
            "billing_accounts",
            "documents",
            "document_events",
            "posting_groups",
            "posting_effects",
            "allocation_effects",
            "withholding_discharges",
            "receivable_exposure_facts",
            *((_TABLES) if not upgrade else ()),
        )
        for prefix in prefixes
    ]
    op.execute(f"""
        SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '60s';
        LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE;
        {'SET LOCAL row_security = off;' if not upgrade else ''}
    """)  # nosec B608 -- closed qualified module table names
    if not upgrade:
        for prefix in prefixes:
            for table in _TABLES:
                if (
                    connection.execute(
                        sa.text(f"SELECT 1 FROM mod_billing.{prefix}{table} LIMIT 1")
                    ).first()
                    is not None
                ):
                    raise RuntimeError(
                        "cannot downgrade Billing while noncash or V2 position evidence exists"
                    )
    for prefix in prefixes:
        if upgrade:
            _create(bool(prefix))
        else:
            _drop(bool(prefix))
    for name, value in previous.items():
        connection.execute(
            sa.text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def _drop(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    for table in (
        "withholding_discharges",
        "allocation_effects",
        "document_events",
        "documents",
    ):
        op.execute(
            f"DROP TRIGGER {prefix}{table}_noncash_invoice_room ON mod_billing.{prefix}{table};"
        )  # nosec B608 -- fixed plane/table names
    for table in (
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "receivable_position_facts",
    ):
        op.execute(
            f"DROP TRIGGER {prefix}{table}_noncash_complete ON mod_billing.{prefix}{table};"
        )  # nosec B608 -- fixed plane/table names
    op.execute(f"""
        DROP TRIGGER {prefix}posting_groups_noncash_admission ON mod_billing.{prefix}posting_groups;
        DROP TRIGGER {prefix}document_events_noncash_void ON mod_billing.{prefix}document_events;
    """)  # nosec B608 -- fixed plane names
    op.drop_constraint(
        f"fk_{prefix}non_cash_credit_grants_replaces_effect_id",
        f"{prefix}non_cash_credit_grants",
        type_="foreignkey",
        schema=_SCHEMA,
    )
    for table in reversed(_TABLES):
        op.drop_table(f"{prefix}{table}", schema=_SCHEMA)
    op.execute(f"""
        DROP FUNCTION mod_billing.admit_{prefix}noncash_grant();
        DROP FUNCTION mod_billing.admit_{prefix}noncash_effect();
        DROP FUNCTION mod_billing.admit_{prefix}position_v2();
        DROP FUNCTION mod_billing.guard_{prefix}noncash_group();
        DROP FUNCTION mod_billing.guard_{prefix}noncash_void();
        DROP FUNCTION mod_billing.check_{prefix}noncash_posting();
        DROP FUNCTION mod_billing.check_{prefix}noncash_invoice_room();
    """)  # nosec B608 -- fixed plane names


def upgrade() -> None:
    _apply(upgrade=True)


def downgrade() -> None:
    _apply(upgrade=False)
