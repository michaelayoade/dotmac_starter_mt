"""Complete immutable cash-only opening funding and correction lifecycle.

Fence old writers before upgrade. No historical financial or retry row is
rewritten; claims retain their exact occupied natural identity indefinitely.
"""

from __future__ import annotations

# Every interpolated identifier is a closed Billing plane/table literal.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0007_opening_cash"
down_revision = "bi_0006_cash_source_claim"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLES = ("opening_import_receipts", "opening_cash_lots", "opening_cash_effects")


def _elements(platform: bool, kind: str) -> tuple[SchemaItem, ...]:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    table = f"{prefix}opening_{kind}"
    elements: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(sa.Column(key, sa.Uuid(), nullable=False) for key in scope),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("posting_group_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            *scope, "id", "billing_account_id", name=f"uq_{table}_account"
        ),
        sa.ForeignKeyConstraint(
            [*scope, "posting_group_id", "billing_account_id"],
            [
                f"{_SCHEMA}.{prefix}posting_groups.{key}"
                for key in (*scope, "id", "billing_account_id")
            ],
            name=f"fk_{table}_group",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("minor_units BETWEEN 0 AND 6", name=f"ck_{table}_precision"),
    ]
    if not platform:
        elements.append(
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["public.tenants.id"],
                name=f"fk_{table}_tenant",
                ondelete="CASCADE",
            )
        )
    links: list[tuple[str, str]] = []
    if kind == "import_receipts":
        elements.extend(
            (
                sa.Column("receipt_kind", sa.String(16), nullable=False),
                sa.Column("original_receipt_id", sa.Uuid()),
                sa.Column("manifest_fingerprint", sa.String(64), nullable=False),
                sa.Column("request_identity", sa.String(255), nullable=False),
                sa.Column("request_digest", sa.String(64), nullable=False),
                sa.Column("total_residual", sa.Numeric(20, 6), nullable=False),
                sa.Column("expected_target_fingerprint", sa.String(64), nullable=False),
                sa.Column("expected_target_watermark", sa.String(255), nullable=False),
                sa.Column("migration_authority_ref", sa.String(255), nullable=False),
                sa.UniqueConstraint(
                    *scope,
                    "receipt_kind",
                    "request_identity",
                    name=f"uq_{table}_request",
                ),
                sa.UniqueConstraint(
                    *scope, "original_receipt_id", name=f"uq_{table}_inverse"
                ),
            )
        )
        links.append(("original_receipt_id", "opening_import_receipts"))
    elif kind == "cash_lots":
        elements.extend(
            (
                sa.Column("receipt_id", sa.Uuid(), nullable=False),
                sa.Column("sequence", sa.Integer(), nullable=False),
                sa.Column("source_system", sa.String(120), nullable=False),
                sa.Column("source_settlement_key", sa.String(255), nullable=False),
                *(
                    sa.Column(key, sa.Numeric(20, 6), nullable=False)
                    for key in (
                        "gross_amount",
                        "historical_consumed",
                        "residual_amount",
                    )
                ),
                sa.UniqueConstraint(
                    *scope, "receipt_id", "sequence", name=f"uq_{table}_sequence"
                ),
                sa.UniqueConstraint(
                    *scope,
                    "id",
                    "billing_account_id",
                    "source_system",
                    "source_settlement_key",
                    "currency",
                    "minor_units",
                    name=f"uq_{prefix}opening_lot_claim_binding",
                ),
            )
        )
        links.append(("receipt_id", "opening_import_receipts"))
    else:
        elements.extend(
            (
                sa.Column("lot_id", sa.Uuid(), nullable=False),
                sa.Column("sequence", sa.Integer(), nullable=False),
                sa.Column("effect_kind", sa.String(32), nullable=False),
                sa.Column("amount_delta", sa.Numeric(20, 6), nullable=False),
                sa.Column("document_id", sa.Uuid()),
                sa.Column("original_effect_id", sa.Uuid()),
                sa.Column("source_ref", sa.String(255), nullable=False),
                sa.Column("authorization_ref", sa.String(255), nullable=False),
                sa.Column("request_digest", sa.String(64), nullable=False),
                sa.UniqueConstraint(
                    *scope, "posting_group_id", "sequence", name=f"uq_{table}_sequence"
                ),
                sa.UniqueConstraint(
                    *scope, "id", "lot_id", "billing_account_id", name=f"uq_{table}_lot"
                ),
                sa.ForeignKeyConstraint(
                    [*scope, "original_effect_id", "lot_id", "billing_account_id"],
                    [
                        f"{_SCHEMA}.{table}.{key}"
                        for key in (*scope, "id", "lot_id", "billing_account_id")
                    ],
                    name=f"fk_{table}_original",
                    ondelete="RESTRICT",
                ),
            )
        )
        links.extend((("lot_id", "opening_cash_lots"), ("document_id", "documents")))
    for column, parent in links:
        elements.append(
            sa.ForeignKeyConstraint(
                [*scope, column, "billing_account_id"],
                [
                    f"{_SCHEMA}.{prefix}{parent}.{key}"
                    for key in (*scope, "id", "billing_account_id")
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
        )
    return tuple(elements)


def _create(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    if platform:
        op.create_table(
            "platform_opening_import_receipts",
            *_elements(True, "import_receipts"),
            schema=_SCHEMA,
        )
        op.create_table(
            "platform_opening_cash_lots", *_elements(True, "cash_lots"), schema=_SCHEMA
        )
        op.create_table(
            "platform_opening_cash_effects",
            *_elements(True, "cash_effects"),
            schema=_SCHEMA,
        )
    else:
        op.create_table(
            "opening_import_receipts",
            *_elements(False, "import_receipts"),
            schema=_SCHEMA,
        )
        op.create_table(
            "opening_cash_lots", *_elements(False, "cash_lots"), schema=_SCHEMA
        )
        op.create_table(
            "opening_cash_effects", *_elements(False, "cash_effects"), schema=_SCHEMA
        )
    claim = f"{prefix}cash_source_claims"
    op.alter_column(
        claim, "settlement_id", nullable=True, existing_type=sa.Uuid(), schema=_SCHEMA
    )
    op.add_column(claim, sa.Column("opening_cash_lot_id", sa.Uuid()), schema=_SCHEMA)
    op.create_check_constraint(
        f"ck_{prefix}cash_claim_owner",
        claim,
        "(settlement_id IS NULL) <> (opening_cash_lot_id IS NULL)",
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        f"uq_{prefix}cash_claim_opening_lot",
        claim,
        [*scope, "opening_cash_lot_id"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        f"fk_{prefix}cash_claim_opening_lot",
        claim,
        f"{prefix}opening_cash_lots",
        [
            *scope,
            "opening_cash_lot_id",
            "billing_account_id",
            "source_system",
            "source_settlement_key",
            "currency",
            "minor_units",
        ],
        [
            *scope,
            "id",
            "billing_account_id",
            "source_system",
            "source_settlement_key",
            "currency",
            "minor_units",
        ],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    for name in _TABLES:
        table = f"{prefix}{name}"
        if platform:
            op.execute(
                f"REVOKE ALL ON mod_billing.{table} FROM app_user; GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;"
            )  # nosec B608 -- fixed qualified plane tables
        else:
            op.execute(f"""
                ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY {table}_tenant_isolation ON mod_billing.{table}
                  USING (tenant_id = public.app_current_tenant_id())
                  WITH CHECK (tenant_id = public.app_current_tenant_id());
                GRANT SELECT, INSERT ON mod_billing.{table} TO app_user;
                REVOKE ALL ON mod_billing.{table} FROM platform_api;
            """)  # nosec B608 -- fixed qualified plane tables
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table}
            FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """)  # nosec B608 -- fixed qualified plane tables
    _admission(platform)
    _completeness(platform)
    _invoice_guards(platform, restore=False)


def _admission(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_evidence = (
        "NEW.evidence::jsonb->'scope' IS DISTINCT FROM '{}'::jsonb"
        if platform
        else "(NEW.evidence::jsonb->'scope'->>'tenant_id') IS DISTINCT FROM NEW.tenant_id::text"
    )
    envelope = f"""
        jsonb_typeof(NEW.evidence::jsonb) IS DISTINCT FROM 'object' OR {scope_evidence}
        OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$'
        OR (NEW.evidence::jsonb->>'billing_account_id') IS DISTINCT FROM NEW.billing_account_id::text
        OR (NEW.evidence::jsonb->>'posting_group_id') IS DISTINCT FROM NEW.posting_group_id::text
        OR (NEW.evidence::jsonb->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.recorded_at
        OR account.id IS NULL OR grp.id IS NULL OR grp.billing_account_id <> NEW.billing_account_id
        OR account.currency <> NEW.currency OR account.minor_units <> NEW.minor_units
        OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units
        OR NEW.minor_units NOT BETWEEN 0 AND 6 OR grp.reverses_group_id IS NOT NULL
    """
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}opening_receipt() RETURNS trigger AS $$
    DECLARE account record; grp record; original record;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      IF {envelope}
        OR NEW.total_residual::text IN ('NaN', 'Infinity', '-Infinity') OR NEW.total_residual <= 0
        OR NEW.total_residual <> round(NEW.total_residual, NEW.minor_units)
        OR NEW.request_digest !~ '^[0-9a-f]{{64}}$' OR NEW.manifest_fingerprint !~ '^[0-9a-f]{{64}}$'
        OR trim(NEW.request_identity) = '' OR trim(NEW.migration_authority_ref) = ''
        OR grp.source_ref <> NEW.request_identity
        OR (NEW.evidence::jsonb->>'receipt_id') IS DISTINCT FROM NEW.id::text
        OR (NEW.evidence::jsonb->>'receipt_kind') IS DISTINCT FROM NEW.receipt_kind
        OR (NEW.evidence::jsonb->>'original_receipt_id') IS DISTINCT FROM NEW.original_receipt_id::text
        OR (NEW.evidence::jsonb->>'manifest_fingerprint') IS DISTINCT FROM NEW.manifest_fingerprint
        OR (NEW.evidence::jsonb->>'request_identity') IS DISTINCT FROM NEW.request_identity
        OR (NEW.evidence::jsonb->>'request_digest') IS DISTINCT FROM NEW.request_digest
        OR (NEW.evidence::jsonb->'total_residual'->>'amount')::numeric IS DISTINCT FROM NEW.total_residual
        OR (NEW.evidence::jsonb->'total_residual'->>'currency') IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'total_residual'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        OR (NEW.evidence::jsonb->'partition'->>'account_id') IS DISTINCT FROM NEW.billing_account_id::text
        OR (NEW.evidence::jsonb->'partition'->>'currency') IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'partition'->>'precision')::integer IS DISTINCT FROM NEW.minor_units
        OR (NEW.evidence::jsonb->'target'->>'expected_position_fingerprint') IS DISTINCT FROM NEW.expected_target_fingerprint
        OR (NEW.evidence::jsonb->'target'->>'posting_watermark') IS DISTINCT FROM NEW.expected_target_watermark
        OR (NEW.evidence::jsonb->'target'->>'account_id') IS DISTINCT FROM NEW.billing_account_id::text
        OR (NEW.evidence::jsonb->'target'->>'currency') IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'target'->>'precision')::integer IS DISTINCT FROM NEW.minor_units
        OR (NEW.evidence::jsonb->'target'->'scope') IS DISTINCT FROM NEW.evidence::jsonb->'scope'
        OR (NEW.evidence::jsonb->'manifest'->'scope') IS DISTINCT FROM NEW.evidence::jsonb->'scope'
        OR (NEW.evidence::jsonb->'verification'->'scope') IS DISTINCT FROM NEW.evidence::jsonb->'scope'
        OR (NEW.evidence::jsonb->'verification'->>'command_digest') IS DISTINCT FROM NEW.request_digest
        OR (NEW.evidence::jsonb->'verification'->>'migration_authority_ref') IS DISTINCT FROM NEW.migration_authority_ref
        OR nullif(trim(NEW.evidence::jsonb->'verification'->>'migration_authority_proof_ref'), '') IS NULL
        OR (NEW.evidence::jsonb->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
        OR jsonb_typeof(NEW.evidence::jsonb->'lot_ids') IS DISTINCT FROM 'array'
      THEN RAISE EXCEPTION 'invalid immutable opening receipt envelope' USING ERRCODE = 'check_violation'; END IF;
      IF NEW.receipt_kind = 'import' THEN
        IF NEW.original_receipt_id IS NOT NULL OR grp.group_kind <> 'opening_cash_imported'
          OR grp.source_version <> 1 OR NEW.expected_target_watermark <> 'none'
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_groups x WHERE x.billing_account_id = NEW.billing_account_id AND x.id <> NEW.posting_group_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}confirmed_settlements x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}rated_obligations x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}documents x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR (NEW.evidence::jsonb->'verification'->>'operation') IS DISTINCT FROM 'billing.opening.import.v1'
          OR (NEW.evidence::jsonb->'verification'->>'manifest_fingerprint') IS DISTINCT FROM NEW.manifest_fingerprint
          OR (NEW.evidence::jsonb->'verification'->'partition') IS DISTINCT FROM NEW.evidence::jsonb->'partition'
          OR (NEW.evidence::jsonb->'verification'->'target') IS DISTINCT FROM NEW.evidence::jsonb->'target'
          OR (NEW.evidence::jsonb->'verification'->>'installation_id') IS DISTINCT FROM (NEW.evidence::jsonb->'manifest'->'capture'->>'installation_id')
          OR (NEW.evidence::jsonb->'verification'->>'source_authority_ref') IS DISTINCT FROM (NEW.evidence::jsonb->'manifest'->'capture'->>'source_authority_ref')
          OR nullif(trim(NEW.evidence::jsonb->'verification'->>'source_binding_proof_ref'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'verification'->>'cash_lifecycle_proof_ref'), '') IS NULL
        THEN RAISE EXCEPTION 'opening import requires pristine target and exact import-qualified proof' USING ERRCODE = 'check_violation'; END IF;
      ELSIF NEW.receipt_kind = 'unwind' THEN
        SELECT * INTO original FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = NEW.original_receipt_id{scope};
        IF original.id IS NULL OR original.id = NEW.id OR original.receipt_kind <> 'import'
          OR original.billing_account_id <> NEW.billing_account_id OR original.currency <> NEW.currency OR original.minor_units <> NEW.minor_units
          OR original.total_residual <> NEW.total_residual OR original.manifest_fingerprint <> NEW.manifest_fingerprint
          OR grp.group_kind <> 'opening_cash_import_reversed'
          OR NEW.evidence::jsonb->'lot_ids' IS DISTINCT FROM original.evidence::jsonb->'lot_ids'
          OR NEW.evidence::jsonb->'manifest' IS DISTINCT FROM original.evidence::jsonb->'manifest'
          OR NEW.evidence::jsonb->'target' IS DISTINCT FROM original.evidence::jsonb->'target'
          OR (NEW.evidence::jsonb->'verification'->>'operation') IS DISTINCT FROM 'billing.opening.import.reverse.v1'
          OR (NEW.evidence::jsonb->'verification'->>'receipt_id') IS DISTINCT FROM original.id::text
          OR (NEW.evidence::jsonb->'verification'->>'receipt_evidence_digest') IS DISTINCT FROM original.evidence_digest
          OR (NEW.evidence::jsonb->'verification'->>'original_posting_group_id') IS DISTINCT FROM original.posting_group_id::text
          OR (NEW.evidence::jsonb->'verification'->>'billing_account_id') IS DISTINCT FROM NEW.billing_account_id::text
          OR (NEW.evidence::jsonb->'verification'->>'currency') IS DISTINCT FROM NEW.currency
          OR (NEW.evidence::jsonb->'verification'->>'precision')::integer IS DISTINCT FROM NEW.minor_units
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x
              WHERE x.receipt_id = original.id{scope} AND x.residual_amount <>
                (SELECT coalesce(sum(e.amount_delta), 0) FROM mod_billing.{prefix}opening_cash_effects e WHERE e.lot_id = x.id {'' if platform else 'AND e.tenant_id = x.tenant_id'}))
        THEN RAISE EXCEPTION 'opening unwind requires exact original and zero net dependent use' USING ERRCODE = 'check_violation'; END IF;
      ELSE RAISE EXCEPTION 'unsupported opening receipt kind' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}opening_import_receipts_admission BEFORE INSERT ON mod_billing.{prefix}opening_import_receipts
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}opening_receipt();
    CREATE FUNCTION mod_billing.admit_{prefix}opening_lot() RETURNS trigger AS $$
    DECLARE account record; grp record; receipt record; source jsonb; source_sequence bigint;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = NEW.receipt_id{scope};
      source := NEW.evidence::jsonb->'source';
      IF {envelope} OR receipt.id IS NULL OR receipt.receipt_kind <> 'import'
        OR receipt.billing_account_id <> NEW.billing_account_id OR receipt.posting_group_id <> NEW.posting_group_id
        OR receipt.currency <> NEW.currency OR receipt.minor_units <> NEW.minor_units
        OR grp.group_kind <> 'opening_cash_imported' OR NEW.sequence < 1
        OR NEW.gross_amount::text IN ('NaN', 'Infinity', '-Infinity')
        OR NEW.historical_consumed::text IN ('NaN', 'Infinity', '-Infinity')
        OR NEW.residual_amount::text IN ('NaN', 'Infinity', '-Infinity')
        OR NEW.gross_amount < 0 OR NEW.historical_consumed < 0 OR NEW.residual_amount < 0
        OR NEW.gross_amount - NEW.historical_consumed <> NEW.residual_amount
        OR NEW.gross_amount <> round(NEW.gross_amount, NEW.minor_units)
        OR NEW.historical_consumed <> round(NEW.historical_consumed, NEW.minor_units)
        OR NEW.residual_amount <> round(NEW.residual_amount, NEW.minor_units)
        OR (NEW.evidence::jsonb->>'lot_id') IS DISTINCT FROM NEW.id::text
        OR (NEW.evidence::jsonb->>'receipt_id') IS DISTINCT FROM NEW.receipt_id::text
        OR (NEW.evidence::jsonb->>'sequence')::integer IS DISTINCT FROM NEW.sequence
        OR (source->>'source_system') IS DISTINCT FROM NEW.source_system
        OR (source->>'source_settlement_key') IS DISTINCT FROM NEW.source_settlement_key
        OR (source->'partition') IS DISTINCT FROM receipt.evidence::jsonb->'partition'
        OR (source->>'gross_minor')::numeric / power(10::numeric, NEW.minor_units) IS DISTINCT FROM NEW.gross_amount
        OR (source->>'residual_minor')::numeric / power(10::numeric, NEW.minor_units) IS DISTINCT FROM NEW.residual_amount
        OR (NEW.evidence::jsonb->'initial_amount'->>'amount')::numeric IS DISTINCT FROM NEW.residual_amount
        OR (NEW.evidence::jsonb->'initial_amount'->>'currency') IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'initial_amount'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        OR nullif(trim(source->'confirmation'->>'owner'), '') IS NULL
        OR nullif(trim(source->'confirmation'->>'reference'), '') IS NULL
        OR nullif(trim(source->'confirmation'->>'version'), '') IS NULL
        OR nullif(trim(source->'confirmation'->>'evidence_digest'), '') IS NULL
        OR (source->'confirmation'->>'cash_amount_minor')::numeric / power(10::numeric, NEW.minor_units) IS DISTINCT FROM NEW.gross_amount
        OR (source->'confirmation'->>'currency') IS DISTINCT FROM NEW.currency
        OR (source->'confirmation'->>'precision')::integer IS DISTINCT FROM NEW.minor_units
        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'cash_lots') e WHERE e = source)
        OR (SELECT coalesce(sum((e->>'amount_minor')::numeric), 0) / power(10::numeric, NEW.minor_units)
            FROM jsonb_array_elements(source->'consumptions') e) IS DISTINCT FROM NEW.historical_consumed
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(source->'consumptions') e
            WHERE e->>'kind' NOT IN ('allocation', 'refund', 'reservation')
              OR nullif(trim(e->>'ledger_ref'), '') IS NULL OR nullif(trim(e->>'target_ref'), '') IS NULL
              OR e->>'reverses_ref' IS NOT NULL OR (e->>'amount_minor')::numeric < 0)
      THEN RAISE EXCEPTION 'invalid exact opening lot evidence' USING ERRCODE = 'check_violation'; END IF;
      SELECT position INTO source_sequence FROM (
          SELECT e.value, row_number() OVER (ORDER BY e.ordinality) AS position
          FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'cash_lots') WITH ORDINALITY e(value, ordinality)
          WHERE e.value->'partition' = receipt.evidence::jsonb->'partition'
      ) ordered WHERE value = source;
      IF source_sequence IS DISTINCT FROM NEW.sequence THEN
        RAISE EXCEPTION 'opening lot must preserve selected source order' USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}opening_cash_lots_admission BEFORE INSERT ON mod_billing.{prefix}opening_cash_lots
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}opening_lot();
    """)  # nosec B608 -- fixed plane tables and scoped envelope expressions
    _effect_admission(platform, envelope)


def _effect_admission(platform: bool, envelope: str) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    live = f"mod_billing.has_{prefix}live_invoice_issuance(NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{'' if platform else ', NEW.tenant_id'})"
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}opening_effect() RETURNS trigger AS $$
    DECLARE account record; grp record; lot record; original record; receipt record;
            room numeric; restored numeric; verification jsonb; subject jsonb;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      SELECT * INTO lot FROM mod_billing.{prefix}opening_cash_lots x WHERE x.id = NEW.lot_id{scope};
      IF {envelope} OR lot.id IS NULL OR lot.billing_account_id <> NEW.billing_account_id
        OR lot.currency <> NEW.currency OR lot.minor_units <> NEW.minor_units
        OR NEW.amount_delta::text IN ('NaN', 'Infinity', '-Infinity')
        OR NEW.amount_delta <> round(NEW.amount_delta, NEW.minor_units) OR NEW.sequence < 1
        OR NEW.request_digest !~ '^[0-9a-f]{{64}}$' OR trim(NEW.source_ref) = '' OR trim(NEW.authorization_ref) = ''
        OR grp.source_ref <> NEW.source_ref
        OR (NEW.evidence::jsonb->>'effect_id') IS DISTINCT FROM NEW.id::text
        OR (NEW.evidence::jsonb->>'lot_id') IS DISTINCT FROM NEW.lot_id::text
        OR (NEW.evidence::jsonb->>'sequence')::integer IS DISTINCT FROM NEW.sequence
        OR (NEW.evidence::jsonb->>'effect_kind') IS DISTINCT FROM NEW.effect_kind
        OR (NEW.evidence::jsonb->>'document_id') IS DISTINCT FROM NEW.document_id::text
        OR (NEW.evidence::jsonb->>'original_effect_id') IS DISTINCT FROM NEW.original_effect_id::text
        OR (NEW.evidence::jsonb->>'source_ref') IS DISTINCT FROM NEW.source_ref
        OR (NEW.evidence::jsonb->>'authorization_ref') IS DISTINCT FROM NEW.authorization_ref
        OR (NEW.evidence::jsonb->>'request_digest') IS DISTINCT FROM NEW.request_digest
        OR (NEW.evidence::jsonb->'amount'->>'amount')::numeric IS DISTINCT FROM abs(NEW.amount_delta)
        OR (NEW.evidence::jsonb->'amount'->>'currency') IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'amount'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        OR (NEW.evidence::jsonb->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
      THEN RAISE EXCEPTION 'invalid immutable opening effect envelope' USING ERRCODE = 'check_violation'; END IF;
      SELECT coalesce(sum(x.amount_delta), 0) INTO room FROM mod_billing.{prefix}opening_cash_effects x WHERE x.lot_id = NEW.lot_id{scope};
      IF room < 0 OR room > lot.residual_amount THEN
        RAISE EXCEPTION 'opening source room is inconsistent' USING ERRCODE = 'check_violation';
      END IF;
      IF EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x
          WHERE x.original_receipt_id = lot.receipt_id{scope} AND x.posting_group_id <> NEW.posting_group_id) THEN
        RAISE EXCEPTION 'opening principal is permanently unwound' USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.effect_kind = 'recognition' THEN
        IF grp.group_kind <> 'opening_cash_imported' OR grp.id <> lot.posting_group_id
          OR NEW.amount_delta <> lot.residual_amount OR NEW.document_id IS NOT NULL OR NEW.original_effect_id IS NOT NULL
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.lot_id = NEW.lot_id AND x.effect_kind = 'recognition'{scope}) THEN
            RAISE EXCEPTION 'opening lot requires one exact recognition' USING ERRCODE = 'check_violation';
        END IF;
      ELSIF NEW.effect_kind = 'application' THEN
        IF grp.group_kind <> 'opening_cash_applied' OR NEW.amount_delta >= 0
          OR NEW.original_effect_id IS NOT NULL OR NEW.document_id IS NULL OR NOT {live}
          OR -NEW.amount_delta > room
          OR -NEW.amount_delta > mod_billing.opening_{prefix}invoice_room(NEW.document_id{'' if platform else ', NEW.tenant_id'}) THEN
            RAISE EXCEPTION 'opening application exceeds exact live cash or invoice affinity' USING ERRCODE = 'check_violation';
        END IF;
      ELSIF NEW.effect_kind = 'application_inverse' THEN
        SELECT * INTO original FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = NEW.original_effect_id{scope};
        SELECT coalesce(sum(x.amount_delta), 0) INTO restored FROM mod_billing.{prefix}opening_cash_effects x
          WHERE x.original_effect_id = NEW.original_effect_id AND x.effect_kind = 'application_inverse'{scope};
        IF grp.group_kind <> 'cash_credit_draw_reversed' OR NEW.amount_delta <= 0
          OR original.id IS NULL OR original.effect_kind <> 'application' OR original.lot_id <> NEW.lot_id
          OR original.billing_account_id <> NEW.billing_account_id OR original.currency <> NEW.currency OR original.minor_units <> NEW.minor_units
          OR original.document_id IS DISTINCT FROM NEW.document_id OR NEW.document_id IS NULL
          OR restored < 0 OR restored + NEW.amount_delta > -original.amount_delta THEN
            RAISE EXCEPTION 'opening inverse exceeds its concrete original application' USING ERRCODE = 'check_violation';
        END IF;
      ELSIF NEW.effect_kind IN ('refund', 'refund_correction') THEN
        verification := NEW.evidence::jsonb->'movement_verification'; subject := verification->'subject';
        IF NEW.document_id IS NOT NULL
          OR (verification->>'command_digest') IS DISTINCT FROM NEW.request_digest
          OR (subject->'scope') IS DISTINCT FROM NEW.evidence::jsonb->'scope'
          OR (subject->>'billing_account_id') IS DISTINCT FROM NEW.billing_account_id::text
          OR (subject->>'currency') IS DISTINCT FROM NEW.currency
          OR (subject->>'precision')::integer IS DISTINCT FROM NEW.minor_units
          OR (subject->>'lot_id') IS DISTINCT FROM NEW.lot_id::text
          OR (subject->>'lot_evidence_digest') IS DISTINCT FROM lot.evidence_digest
          OR (subject->>'source_system') IS DISTINCT FROM lot.source_system
          OR (subject->>'source_settlement_key') IS DISTINCT FROM lot.source_settlement_key
          OR nullif(trim(verification->>'authorization_proof_ref'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'movement_evidence'->>'owner'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'movement_evidence'->>'reference'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'movement_evidence'->>'version'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'movement_evidence'->>'digest'), '') IS NULL
          OR nullif(trim(NEW.evidence::jsonb->'movement_evidence'->>'outcome'), '') IS NULL THEN
            RAISE EXCEPTION 'refund proof must bind exact immutable source and command' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.effect_kind = 'refund' THEN
          IF grp.group_kind <> 'opening_cash_refunded' OR NEW.amount_delta >= 0 OR -NEW.amount_delta > room
            OR NEW.original_effect_id IS NOT NULL
            OR (verification->>'operation') IS DISTINCT FROM 'billing.opening.cash.refund.v1'
            OR nullif(trim(verification->>'outcome_proof_ref'), '') IS NULL THEN
              RAISE EXCEPTION 'refund requires exact unused cash and qualified outcome' USING ERRCODE = 'check_violation';
          END IF;
        ELSE
          SELECT * INTO original FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = NEW.original_effect_id{scope};
          IF grp.group_kind <> 'opening_cash_refund_corrected' OR NEW.amount_delta <= 0
            OR original.id IS NULL OR original.effect_kind <> 'refund' OR original.lot_id <> NEW.lot_id
            OR original.billing_account_id <> NEW.billing_account_id OR original.currency <> NEW.currency OR original.minor_units <> NEW.minor_units
            OR NEW.amount_delta <> -original.amount_delta
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.original_effect_id = NEW.original_effect_id AND x.effect_kind = 'refund_correction'{scope})
            OR (verification->>'operation') IS DISTINCT FROM 'billing.opening.cash.refund.correct.v1'
            OR (verification->>'refund_effect_id') IS DISTINCT FROM original.id::text
            OR (verification->>'refund_evidence_digest') IS DISTINCT FROM original.evidence_digest
            OR nullif(trim(verification->>'correction_outcome_proof_ref'), '') IS NULL
            OR (verification->>'correction_outcome_proof_ref') IS NOT DISTINCT FROM (original.evidence::jsonb->'movement_verification'->>'outcome_proof_ref')
            OR (NEW.evidence::jsonb->'movement_evidence'->>'digest') IS NOT DISTINCT FROM (original.evidence::jsonb->'movement_evidence'->>'digest') THEN
              RAISE EXCEPTION 'refund correction is full, once and independently qualified' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      ELSIF NEW.effect_kind = 'import_unwind' THEN
        SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = NEW.posting_group_id{scope};
        IF grp.group_kind <> 'opening_cash_import_reversed' OR NEW.amount_delta >= 0
          OR NEW.document_id IS NOT NULL OR NEW.original_effect_id IS NOT NULL
          OR receipt.id IS NULL OR receipt.receipt_kind <> 'unwind' OR receipt.original_receipt_id <> lot.receipt_id
          OR NEW.amount_delta <> -lot.residual_amount OR room <> lot.residual_amount THEN
            RAISE EXCEPTION 'opening unwind requires exact unused full principal' USING ERRCODE = 'check_violation';
        END IF;
      ELSE RAISE EXCEPTION 'unsupported opening effect kind' USING ERRCODE = 'check_violation'; END IF;
      IF room + NEW.amount_delta < 0 OR room + NEW.amount_delta > lot.residual_amount THEN
        RAISE EXCEPTION 'opening effect exceeds exact source capacity' USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}opening_cash_effects_admission BEFORE INSERT ON mod_billing.{prefix}opening_cash_effects
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}opening_effect();
    CREATE UNIQUE INDEX {prefix}opening_refund_one_correction ON mod_billing.{prefix}opening_cash_effects
      ({'tenant_id, ' if not platform else ''}original_effect_id) WHERE effect_kind = 'refund_correction';
    CREATE UNIQUE INDEX {prefix}opening_lot_one_recognition ON mod_billing.{prefix}opening_cash_effects
      ({'tenant_id, ' if not platform else ''}lot_id) WHERE effect_kind = 'recognition';
    """)  # nosec B608 -- fixed planes and source/capacity envelope expressions


def _completeness(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    joined = "" if platform else " AND e.tenant_id = x.tenant_id"
    payload_scope = (
        "payload->'scope' IS DISTINCT FROM '{}'::jsonb"
        if platform
        else "(payload->'scope'->>'tenant_id') IS DISTINCT FROM NEW.tenant_id::text"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.check_{prefix}opening_lot_claim() RETURNS trigger AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}cash_source_claims x
          WHERE x.opening_cash_lot_id = NEW.id{scope} AND x.settlement_id IS NULL
            AND x.billing_account_id = NEW.billing_account_id AND x.source_system = NEW.source_system
            AND x.source_settlement_key = NEW.source_settlement_key AND x.currency = NEW.currency AND x.minor_units = NEW.minor_units) THEN
        RAISE EXCEPTION 'opening lot requires its exact permanently occupied cash-source claim' USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE CONSTRAINT TRIGGER {prefix}opening_cash_lots_claim_complete AFTER INSERT ON mod_billing.{prefix}opening_cash_lots
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}opening_lot_claim();
    CREATE FUNCTION mod_billing.check_{prefix}opening_posting() RETURNS trigger AS $$
    DECLARE group_id uuid; grp record; fact record; receipt record; original record;
            payload jsonb; cash numeric; ar numeric; ordinary_count bigint; opening_count bigint; n bigint;
            invoice_id uuid; expected_room numeric; affected_count bigint;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}posting_groups' THEN group_id := NEW.id;
      ELSE group_id := NEW.posting_group_id; END IF;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = group_id{scope};
      IF grp.group_kind NOT IN ('opening_cash_imported', 'opening_cash_applied', 'cash_credit_draw_reversed',
          'opening_cash_refunded', 'opening_cash_refund_corrected', 'opening_cash_import_reversed') THEN RETURN NEW; END IF;
      PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = grp.billing_account_id{scope} FOR UPDATE;
      SELECT count(*) INTO n FROM mod_billing.{prefix}accounting_facts x WHERE x.posting_group_id = group_id{scope};
      SELECT * INTO fact FROM mod_billing.{prefix}accounting_facts x WHERE x.posting_group_id = group_id AND x.fact_version = 4{scope};
      payload := fact.fact_payload::jsonb;
      IF grp.id IS NULL OR grp.reverses_group_id IS NOT NULL OR n <> 1 OR fact.id IS NULL
        OR fact.source_authority <> 'internal' OR fact.source_system <> 'dotmac-billing' OR fact.effect_kind <> grp.group_kind
        OR fact.occurred_at <> grp.occurred_at OR fact.fact_digest !~ '^[0-9a-f]{{64}}$'
        OR {payload_scope}
        OR (payload->>'fact_id') IS DISTINCT FROM fact.id::text OR (payload->>'fact_version')::integer IS DISTINCT FROM 4
        OR (payload->>'billing_account_id') IS DISTINCT FROM grp.billing_account_id::text
        OR (payload->>'posting_group_id') IS DISTINCT FROM grp.id::text
        OR (payload->>'effect_kind') IS DISTINCT FROM grp.group_kind
        OR (payload->>'source_system') IS DISTINCT FROM 'dotmac-billing'
        OR (payload->>'source_authority') IS DISTINCT FROM 'internal'
        OR (payload->>'source_ref') IS DISTINCT FROM grp.source_ref
        OR (payload->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
        OR jsonb_typeof(payload->'effects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(payload->'settlement_allocations') IS DISTINCT FROM 'array'
        OR jsonb_typeof(payload->'opening_cash_effects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(payload->'cash_source_order') IS DISTINCT FROM 'array'
      THEN RAISE EXCEPTION 'opening posting requires its exact source-bound V4 fact' USING ERRCODE = 'check_violation'; END IF;
      SELECT coalesce(sum(x.amount_delta), 0), count(*) INTO cash, opening_count
        FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id{scope};
      SELECT coalesce(sum(x.amount_delta), 0) INTO ar FROM mod_billing.{prefix}opening_cash_effects x
        WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope};
      cash := cash - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope});
      ar := ar - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope});
      SELECT count(*) INTO ordinary_count FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope};
      IF cash = 0 OR (grp.group_kind IN ('opening_cash_applied', 'opening_cash_refunded', 'opening_cash_import_reversed') AND cash >= 0)
        OR (grp.group_kind IN ('opening_cash_imported', 'cash_credit_draw_reversed', 'opening_cash_refund_corrected') AND cash <= 0)
        OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id{scope}) <> (CASE WHEN ar = 0 THEN 1 ELSE 2 END)
        OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id AND x.lane = 'available_credit' AND x.amount_delta = cash AND x.currency = grp.currency AND x.minor_units = grp.minor_units AND x.billing_account_id = grp.billing_account_id{scope})
        OR (ar <> 0 AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id AND x.lane = 'receivable' AND x.amount_delta = ar AND x.currency = grp.currency AND x.minor_units = grp.minor_units AND x.billing_account_id = grp.billing_account_id{scope}))
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id AND x.lane NOT IN ('receivable', 'available_credit'){scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}withholding_discharges x WHERE x.posting_group_id = group_id{scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.posting_group_id = group_id{scope})
        OR jsonb_array_length(payload->'effects') <> (CASE WHEN ar = 0 THEN 1 ELSE 2 END)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'effects') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id{scope}
              AND x.lane = p->>'lane' AND x.amount_delta = (p->'amount_delta'->>'amount')::numeric
              AND x.currency = p->'amount_delta'->>'currency' AND x.minor_units = (p->'amount_delta'->>'minor_units')::integer))
        OR jsonb_array_length(payload->'opening_cash_effects') <> opening_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'opening_cash_effects') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = (p->>'effect_id')::uuid AND x.posting_group_id = group_id{scope} AND x.evidence::jsonb = p))
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id{scope}
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'opening_cash_effects') p WHERE (p->>'effect_id')::uuid = x.id))
        OR jsonb_array_length(payload->'settlement_allocations') <> ordinary_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'settlement_allocations') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.id = (p->>'allocation_effect_id')::uuid AND x.posting_group_id = group_id{scope}
              AND (p->'allocation'->>'settlement_id') IS NOT DISTINCT FROM x.settlement_id::text
              AND (p->'allocation'->>'document_id') IS NOT DISTINCT FROM x.document_id::text
              AND (p->'allocation'->>'effect_kind') IS NOT DISTINCT FROM x.effect_kind
              AND (p->'allocation'->>'offsets_allocation_id') IS NOT DISTINCT FROM x.offsets_allocation_id::text
              AND (p->'allocation'->'amount_delta'->>'amount')::numeric = x.amount_delta
              AND (p->'allocation'->'amount_delta'->>'currency') = x.currency
              AND (p->'allocation'->'amount_delta'->>'minor_units')::integer = x.minor_units))
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope}
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'settlement_allocations') p WHERE (p->>'allocation_effect_id')::uuid = x.id))
      THEN RAISE EXCEPTION 'opening V4 must exactly represent every posting and concrete source effect' USING ERRCODE = 'check_violation'; END IF;
      IF jsonb_array_length(payload->'cash_source_order') <> ordinary_count + opening_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'cash_source_order') WITH ORDINALITY p(value, ordinality)
            WHERE (p.value->>'sequence')::bigint IS DISTINCT FROM p.ordinality
              OR ((p.value->>'allocation_effect_id') IS NULL) = ((p.value->>'opening_effect_id') IS NULL)
              OR (p.value->>'allocation_effect_id' IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.id = (p.value->>'allocation_effect_id')::uuid AND x.posting_group_id = group_id{scope}))
              OR (p.value->>'opening_effect_id' IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = (p.value->>'opening_effect_id')::uuid AND x.posting_group_id = group_id{scope})))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'cash_source_order') p
            GROUP BY p->>'allocation_effect_id', p->>'opening_effect_id' HAVING count(*) <> 1)
      THEN RAISE EXCEPTION 'V4 source ordering must bind each actual source effect once' USING ERRCODE = 'check_violation'; END IF;
      IF grp.group_kind IN ('opening_cash_imported', 'opening_cash_import_reversed') THEN
        SELECT count(*) INTO n FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = group_id{scope};
        SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = group_id{scope};
        IF n <> 1 OR payload->'import_receipt' IS DISTINCT FROM receipt.evidence::jsonb OR ordinary_count <> 0
          OR ar <> 0 OR abs(cash) <> receipt.total_residual THEN
            RAISE EXCEPTION 'opening recognition/unwind requires its exact receipt and no new cash settlement or invoice' USING ERRCODE = 'check_violation';
        END IF;
        IF grp.group_kind = 'opening_cash_imported' THEN
          IF receipt.receipt_kind <> 'import'
            OR (SELECT coalesce(sum(x.residual_amount), 0) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.id{scope}) <> receipt.total_residual
            OR (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.id{scope}) <> jsonb_array_length(receipt.evidence::jsonb->'lot_ids')
            OR (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.id{scope}) <> (SELECT count(*) FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'cash_lots') p WHERE p->'partition' = receipt.evidence::jsonb->'partition')
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.id{scope}
                AND ((receipt.evidence::jsonb->'lot_ids'->>(x.sequence - 1)) IS DISTINCT FROM x.id::text
                    OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects e WHERE e.lot_id = x.id AND e.posting_group_id = group_id{joined} AND e.effect_kind = 'recognition' AND e.amount_delta = x.residual_amount)))
            OR opening_count <> jsonb_array_length(receipt.evidence::jsonb->'lot_ids') THEN
              RAISE EXCEPTION 'opening import must represent every ordered selected lot exactly' USING ERRCODE = 'check_violation';
          END IF;
        ELSE
          IF receipt.receipt_kind <> 'unwind' OR (payload->>'corrects_posting_group_id') IS DISTINCT FROM (receipt.evidence::jsonb->'verification'->>'original_posting_group_id')
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.original_receipt_id{scope} AND x.residual_amount > 0
                AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects e WHERE e.lot_id = x.id AND e.posting_group_id = group_id{joined} AND e.effect_kind = 'import_unwind' AND e.amount_delta = -x.residual_amount))
            OR opening_count <> (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.original_receipt_id AND x.residual_amount > 0{scope}) THEN
              RAISE EXCEPTION 'opening unwind must reverse each nonzero principal exactly' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      ELSE
        IF payload->>'import_receipt' IS NOT NULL
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = group_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.posting_group_id = group_id{scope}) THEN
            RAISE EXCEPTION 'opening use cannot create new principal or receipts' USING ERRCODE = 'check_violation';
        END IF;
        IF grp.group_kind = 'opening_cash_applied' AND (opening_count = 0 OR ar <> cash
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id AND x.effect_kind <> 'application'{scope})
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id AND (x.effect_kind <> 'allocation' OR x.amount_delta <= 0 OR x.offsets_allocation_id IS NOT NULL OR x.document_id IS NULL){scope})) THEN
          RAISE EXCEPTION 'opening-bearing draw requires exact positive application sources' USING ERRCODE = 'check_violation';
        END IF;
        IF grp.group_kind = 'cash_credit_draw_reversed' THEN
          SELECT * INTO original FROM mod_billing.{prefix}posting_groups x WHERE x.id = (payload->>'corrects_posting_group_id')::uuid{scope};
          IF original.id IS NULL OR original.group_kind <> 'opening_cash_applied' OR original.billing_account_id <> grp.billing_account_id
            OR original.currency <> grp.currency OR original.minor_units <> grp.minor_units OR ar <> cash
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id{scope}
                AND (x.effect_kind <> 'application_inverse' OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects e WHERE e.id = x.original_effect_id AND e.posting_group_id = original.id{joined})))
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope}
                AND (x.effect_kind <> 'reversal' OR x.amount_delta >= 0 OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects e WHERE e.id = x.offsets_allocation_id AND e.posting_group_id = original.id{joined}))) THEN
              RAISE EXCEPTION 'mixed draw inverse requires both-source concrete original affinity' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
        IF grp.group_kind IN ('opening_cash_refunded', 'opening_cash_refund_corrected') AND (ordinary_count <> 0 OR opening_count <> 1 OR ar <> 0) THEN
          RAISE EXCEPTION 'opening refund/correction has one exact lot and no invoice allocation' USING ERRCODE = 'check_violation';
        END IF;
      END IF;
      SELECT count(*) INTO affected_count FROM (
        SELECT x.document_id FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope}
      ) documents;
      IF (SELECT count(*) FROM mod_billing.{prefix}receivable_exposure_facts x WHERE x.posting_group_watermark = group_id{scope}) <> affected_count THEN
        RAISE EXCEPTION 'opening posting requires one exact exposure per affected invoice' USING ERRCODE = 'check_violation';
      END IF;
      FOR invoice_id IN (
        SELECT x.document_id FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id AND x.document_id IS NOT NULL{scope}
      ) LOOP
        expected_room := mod_billing.opening_{prefix}invoice_room_at(invoice_id, grp.billing_account_id, grp.source_version{'' if platform else ', NEW.tenant_id'});
        IF expected_room IS NULL OR expected_room < 0 OR NOT EXISTS (
          SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts x WHERE x.document_id = invoice_id AND x.posting_group_watermark = group_id{scope}
            AND x.billing_account_id = grp.billing_account_id AND x.source_version = grp.source_version
            AND x.currency = grp.currency AND x.minor_units = grp.minor_units
            AND x.source_authority = 'internal' AND x.projection_mode = 'authoritative'
            AND x.collectible_receivable = expected_room
        ) THEN RAISE EXCEPTION 'opening invoice exposure must match exact resolved source room' USING ERRCODE = 'check_violation'; END IF;
      END LOOP;
      IF NOT EXISTS (SELECT 1 FROM public.{prefix}outbox_events x WHERE x.event_type = 'billing.accounting.fact.v4' AND x.payload::jsonb = payload{scope}) THEN
        RAISE EXCEPTION 'opening V4 requires its atomic outbox evidence' USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane/table names and scoped completeness expressions
    for table in (
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "accounting_facts",
        *_TABLES,
    ):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {prefix}{table}_opening_complete AFTER INSERT ON mod_billing.{prefix}{table}
              DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}opening_posting();
        """)  # nosec B608 -- fixed qualified plane tables


def _historical_invoice_room(platform: bool) -> None:
    """Resolve actual source contributions at one immutable account watermark."""
    prefix = "platform_" if platform else ""
    tenant = "" if platform else " AND x.tenant_id = tenant"
    effects_scope = "" if platform else " AND e.tenant_id = doc.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.opening_{prefix}invoice_room_at(
        invoice_id uuid, account_id uuid, target_version bigint{'' if platform else ', tenant uuid'}
    ) RETURNS numeric AS $$
    DECLARE invoice record; doc record; issuance record; inverse record; void_event record;
            n bigint; room numeric; credit_total numeric := 0; expected_issue numeric;
            invoice_void boolean := false; void_at_target boolean;
    BEGIN
      SELECT * INTO invoice FROM mod_billing.{prefix}documents x WHERE x.id = invoice_id{tenant};
      IF invoice.id IS NULL OR invoice.document_kind <> 'invoice' OR invoice.issued_at IS NULL
        OR invoice.billing_account_id <> account_id OR target_version < 1 THEN
        RAISE EXCEPTION 'historical invoice room requires its exact issued account' USING ERRCODE = 'check_violation';
      END IF;
      -- Every issued document needs a unique actual canonical issuance and
      -- exact AR effect. Current lifecycle flags never supply chronology.
      FOR doc IN SELECT * FROM mod_billing.{prefix}documents x
          WHERE (x.id = invoice_id OR (x.credits_document_id = invoice_id AND x.document_kind = 'credit_note' AND x.issued_at IS NOT NULL)){tenant}
      LOOP
        IF doc.billing_account_id <> account_id OR doc.currency <> invoice.currency OR doc.minor_units <> invoice.minor_units
          OR doc.grand_total::text IN ('NaN', 'Infinity', '-Infinity') OR doc.grand_total < 0 THEN
          RAISE EXCEPTION 'historical document money affinity is inconsistent' USING ERRCODE = 'check_violation';
        END IF;
        SELECT count(*) INTO n FROM mod_billing.{prefix}posting_groups x
          WHERE (x.source_ref = 'document:' || doc.id::text || '\\:issue' OR (doc.document_kind = 'credit_note' AND x.source_ref = 'credit_note:' || doc.id::text)){tenant};
        IF n <> 1 THEN
          RAISE EXCEPTION 'historical document issuance is missing or ambiguous' USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO issuance FROM mod_billing.{prefix}posting_groups x
          WHERE (x.source_ref = 'document:' || doc.id::text || '\\:issue' OR (doc.document_kind = 'credit_note' AND x.source_ref = 'credit_note:' || doc.id::text)){tenant};
        expected_issue := CASE WHEN doc.document_kind = 'invoice' THEN doc.grand_total ELSE -doc.grand_total END;
        IF issuance.billing_account_id <> account_id OR issuance.currency <> invoice.currency OR issuance.minor_units <> invoice.minor_units
          OR issuance.reverses_group_id IS NOT NULL
          OR issuance.group_kind <> CASE WHEN doc.document_kind = 'invoice' THEN 'invoice_issued' ELSE 'credit_note_issued' END
          OR (doc.id = invoice_id AND issuance.source_version > target_version)
          OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects e WHERE e.posting_group_id = issuance.id{effects_scope}) <> 1
          OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects e WHERE e.posting_group_id = issuance.id{effects_scope}
            AND e.billing_account_id = account_id AND e.currency = invoice.currency AND e.minor_units = invoice.minor_units
            AND e.lane = 'receivable' AND e.amount_delta = expected_issue) THEN
          RAISE EXCEPTION 'historical issuance has no exact monetary binding' USING ERRCODE = 'check_violation';
        END IF;
        SELECT count(*) INTO n FROM mod_billing.{prefix}document_events x WHERE x.document_id = doc.id AND x.event_kind = 'voided'{tenant};
        IF n > 1 THEN
          RAISE EXCEPTION 'historical document void is ambiguous' USING ERRCODE = 'check_violation';
        END IF;
        void_at_target := false;
        IF n = 1 THEN
          SELECT * INTO void_event FROM mod_billing.{prefix}document_events x WHERE x.document_id = doc.id AND x.event_kind = 'voided'{tenant};
          SELECT count(*) INTO n FROM mod_billing.{prefix}posting_groups x
            WHERE x.reverses_group_id = issuance.id AND x.source_ref = void_event.reason{tenant};
          IF n <> 1 THEN
            RAISE EXCEPTION 'historical document void has no unique canonical inverse' USING ERRCODE = 'check_violation';
          END IF;
          SELECT * INTO inverse FROM mod_billing.{prefix}posting_groups x
            WHERE x.reverses_group_id = issuance.id AND x.source_ref = void_event.reason{tenant};
          IF inverse.billing_account_id <> account_id OR inverse.currency <> invoice.currency OR inverse.minor_units <> invoice.minor_units
            OR inverse.group_kind <> 'reversal' OR inverse.source_version <= issuance.source_version
            OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects e WHERE e.posting_group_id = inverse.id{effects_scope}) <> 1
            OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects e WHERE e.posting_group_id = inverse.id{effects_scope}
              AND e.billing_account_id = account_id AND e.currency = invoice.currency AND e.minor_units = invoice.minor_units
              AND e.lane = 'receivable' AND e.amount_delta = -expected_issue) THEN
            RAISE EXCEPTION 'historical void inverse has no exact monetary binding' USING ERRCODE = 'check_violation';
          END IF;
          void_at_target := inverse.source_version <= target_version;
        END IF;
        IF doc.id = invoice_id THEN invoice_void := void_at_target;
        ELSIF issuance.source_version <= target_version AND NOT void_at_target THEN credit_total := credit_total + doc.grand_total;
        END IF;
      END LOOP;
      -- Bound each real source row by its own scoped account posting group,
      -- never a previous projection or the final state of this transaction.
      WITH contributions AS (
        SELECT x.posting_group_id, x.billing_account_id, x.currency, x.minor_units, -x.amount_delta AS delta
          FROM mod_billing.{prefix}allocation_effects x WHERE x.document_id = invoice_id{tenant}
        UNION ALL SELECT x.posting_group_id, x.billing_account_id, x.currency, x.minor_units, -x.amount_delta
          FROM mod_billing.{prefix}withholding_discharges x WHERE x.document_id = invoice_id{tenant}
        UNION ALL SELECT x.posting_group_id, x.billing_account_id, x.currency, x.minor_units, x.availability_delta
          FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.document_id = invoice_id{tenant}
        UNION ALL SELECT x.posting_group_id, x.billing_account_id, x.currency, x.minor_units, x.amount_delta
          FROM mod_billing.{prefix}opening_cash_effects x WHERE x.document_id = invoice_id{tenant}
      ) SELECT invoice.grand_total - credit_total + coalesce(sum(x.delta) FILTER (WHERE g.source_version <= target_version), 0),
          count(*) FILTER (WHERE g.id IS NULL OR g.billing_account_id <> account_id OR x.billing_account_id <> account_id
            OR g.currency <> invoice.currency OR x.currency <> invoice.currency
            OR g.minor_units <> invoice.minor_units OR x.minor_units <> invoice.minor_units
            OR x.delta::text IN ('NaN', 'Infinity', '-Infinity')) INTO room, n
        FROM contributions x LEFT JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id{'' if platform else ' AND g.tenant_id = tenant'};
      IF n <> 0 THEN
        RAISE EXCEPTION 'historical contribution has no exact scoped monetary group' USING ERRCODE = 'check_violation';
      END IF;
      IF room IS NULL OR room::text IN ('NaN', 'Infinity', '-Infinity') OR room < 0 OR room > invoice.grand_total THEN
        RAISE EXCEPTION 'historical invoice resolution exceeds exact room' USING ERRCODE = 'check_violation';
      END IF;
      IF invoice_void THEN RETURN 0; END IF;
      RETURN room;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and exact version-bound source predicates


def _invoice_guards(platform: bool, *, restore: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    opening = (
        ""
        if restore
        else f"""
        used := used - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}opening_cash_effects x
          WHERE x.document_id = invoice_id{scope});
    """
    )
    if not restore:
        _historical_invoice_room(platform)
        op.execute(f"""
        CREATE FUNCTION mod_billing.opening_{prefix}invoice_room(invoice_id uuid{'' if platform else ', tenant uuid'}) RETURNS numeric AS $$
        SELECT d.grand_total
          - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects x WHERE x.document_id = d.id {'' if platform else 'AND x.tenant_id = d.tenant_id'})
          - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}withholding_discharges x WHERE x.document_id = d.id {'' if platform else 'AND x.tenant_id = d.tenant_id'})
          + (SELECT coalesce(sum(x.availability_delta), 0) FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.document_id = d.id {'' if platform else 'AND x.tenant_id = d.tenant_id'})
          + (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}opening_cash_effects x WHERE x.document_id = d.id {'' if platform else 'AND x.tenant_id = d.tenant_id'})
          - (SELECT coalesce(sum(x.grand_total), 0) FROM mod_billing.{prefix}documents x
              WHERE x.credits_document_id = d.id AND x.document_kind = 'credit_note' AND x.issued_at IS NOT NULL {'' if platform else 'AND x.tenant_id = d.tenant_id'}
                AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events e WHERE e.document_id = x.id AND e.event_kind = 'voided' {'' if platform else 'AND e.tenant_id = x.tenant_id'}))
        FROM mod_billing.{prefix}documents d WHERE d.id = invoice_id {'' if platform else 'AND d.tenant_id = tenant'};
        $$ LANGUAGE sql;
        """)  # nosec B608 -- fixed qualified planes and exact numeric source sums
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.check_{prefix}noncash_invoice_room() RETURNS trigger AS $$
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
        {opening}
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
    """)  # nosec B608 -- fixed plane names and scoped room contribution
    if restore:
        return
    op.execute(f"""
        CREATE CONSTRAINT TRIGGER {prefix}opening_cash_effects_invoice_room AFTER INSERT ON mod_billing.{prefix}opening_cash_effects
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}noncash_invoice_room();
    """)  # nosec B608 -- fixed qualified plane tables
    _legacy_guards(platform)


def _legacy_guards(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    same_parent = "" if platform else " AND e.tenant_id = x.tenant_id"
    op.execute(f"""
    CREATE FUNCTION mod_billing.has_{prefix}active_opening_draw(invoice_id uuid{'' if platform else ', tenant uuid'}) RETURNS boolean AS $$
    SELECT EXISTS (
      SELECT 1 FROM mod_billing.{prefix}posting_groups g
      WHERE g.group_kind = 'opening_cash_applied' {'' if platform else 'AND g.tenant_id = tenant'}
        AND EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects a WHERE a.posting_group_id = g.id AND a.document_id = invoice_id AND a.effect_kind = 'application' {'' if platform else 'AND a.tenant_id = g.tenant_id'})
        AND (
          (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = g.id {'' if platform else 'AND x.tenant_id = g.tenant_id'})
          + (SELECT coalesce(sum(e.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects x JOIN mod_billing.{prefix}allocation_effects e ON e.offsets_allocation_id = x.id{same_parent} WHERE x.posting_group_id = g.id {'' if platform else 'AND x.tenant_id = g.tenant_id'})
          - (SELECT coalesce(sum(x.amount_delta), 0) FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = g.id {'' if platform else 'AND x.tenant_id = g.tenant_id'})
          - (SELECT coalesce(sum(e.amount_delta), 0) FROM mod_billing.{prefix}opening_cash_effects x JOIN mod_billing.{prefix}opening_cash_effects e ON e.original_effect_id = x.id{same_parent} WHERE x.posting_group_id = g.id {'' if platform else 'AND x.tenant_id = g.tenant_id'})
        ) > 0
    );
    $$ LANGUAGE sql;
    CREATE FUNCTION mod_billing.guard_{prefix}opening_owner() RETURNS trigger AS $$
    DECLARE original record; root record; allocation record; settlement_id uuid; visited uuid[] := ARRAY[]::uuid[];
    BEGIN
      IF TG_TABLE_NAME = '{prefix}posting_groups' THEN
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        IF NEW.reverses_group_id IS NOT NULL THEN
          SELECT * INTO original FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.reverses_group_id{scope};
          IF original.group_kind IN ('opening_cash_imported', 'opening_cash_applied', 'cash_credit_draw_reversed',
              'opening_cash_refunded', 'opening_cash_refund_corrected', 'opening_cash_import_reversed')
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = original.id{scope}) THEN
              RAISE EXCEPTION 'opening cash corrections require the complete owner' USING ERRCODE = 'check_violation';
          END IF;
          root := original;
          WHILE root.reverses_group_id IS NOT NULL LOOP
            IF root.id = ANY(visited) THEN
              RAISE EXCEPTION 'posting reversal lineage is cyclic' USING ERRCODE = 'check_violation';
            END IF;
            visited := array_append(visited, root.id);
            SELECT * INTO root FROM mod_billing.{prefix}posting_groups x WHERE x.id = root.reverses_group_id{scope};
          END LOOP;
          IF root.group_kind = 'settlement_accepted' THEN
            SELECT x.id INTO settlement_id FROM mod_billing.{prefix}confirmed_settlements x
              WHERE 'settlement:' || x.id::text = root.source_ref AND x.billing_account_id = root.billing_account_id{scope};
            IF EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id {'' if platform else 'AND g.tenant_id = x.tenant_id'}
                WHERE x.settlement_id = settlement_id AND g.group_kind = 'opening_cash_applied'{scope}
                  AND x.amount_delta + (SELECT coalesce(sum(e.amount_delta), 0) FROM mod_billing.{prefix}allocation_effects e WHERE e.offsets_allocation_id = x.id{same_parent}) > 0) THEN
              RAISE EXCEPTION 'reverse complete mixed draws before correcting their settlement backing' USING ERRCODE = 'check_violation';
            END IF;
          END IF;
          IF original.group_kind = 'invoice_issued' AND EXISTS (
            SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts x WHERE x.posting_group_watermark = original.id{scope}
              AND mod_billing.has_{prefix}active_opening_draw(x.document_id{'' if platform else ', NEW.tenant_id'})) THEN
            RAISE EXCEPTION 'reverse opening-bearing draws before invoice issuance' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      ELSE
        SELECT * INTO original FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = original.billing_account_id{scope} FOR UPDATE;
        IF NEW.event_kind = 'voided' AND mod_billing.has_{prefix}active_opening_draw(NEW.document_id{'' if platform else ', NEW.tenant_id'}) THEN
          RAISE EXCEPTION 'reverse opening-bearing draws before invoice void' USING ERRCODE = 'check_violation';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}posting_groups_opening_owner BEFORE INSERT ON mod_billing.{prefix}posting_groups
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_owner();
    CREATE TRIGGER {prefix}document_events_opening_owner BEFORE INSERT ON mod_billing.{prefix}document_events
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_owner();
    CREATE FUNCTION mod_billing.guard_{prefix}opening_allocation() RETURNS trigger AS $$
    DECLARE account record; grp record; original record; parent_group record; settlement record;
            room numeric; roots bigint; depth bigint; chain_count bigint;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      IF NEW.offsets_allocation_id IS NOT NULL THEN
        SELECT * INTO original FROM mod_billing.{prefix}allocation_effects x WHERE x.id = NEW.offsets_allocation_id{scope};
        SELECT * INTO parent_group FROM mod_billing.{prefix}posting_groups x WHERE x.id = original.posting_group_id{scope};
        IF parent_group.group_kind = 'opening_cash_applied' THEN
          SELECT original.amount_delta + coalesce(sum(x.amount_delta), 0) INTO room FROM mod_billing.{prefix}allocation_effects x WHERE x.offsets_allocation_id = original.id{scope};
          IF grp.group_kind <> 'cash_credit_draw_reversed' OR NEW.effect_kind <> 'reversal' OR NEW.amount_delta >= 0
            OR NEW.settlement_id <> original.settlement_id OR NEW.document_id IS DISTINCT FROM original.document_id
            OR NEW.billing_account_id <> original.billing_account_id OR NEW.currency <> original.currency OR NEW.minor_units <> original.minor_units
            OR room < 0 OR room > original.amount_delta OR room + NEW.amount_delta < 0 THEN
              RAISE EXCEPTION 'legacy allocation inverse cannot split a mixed opening draw' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      END IF;
      IF NEW.effect_kind = 'reallocation' AND NEW.amount_delta < 0
        AND mod_billing.has_{prefix}active_opening_draw(NEW.document_id{'' if platform else ', NEW.tenant_id'}) THEN
        RAISE EXCEPTION 'legacy reallocation cannot split active opening-bearing coverage' USING ERRCODE = 'check_violation';
      END IF;
      IF grp.group_kind NOT IN ('opening_cash_applied', 'cash_credit_draw_reversed') THEN RETURN NEW; END IF;
      SELECT * INTO settlement FROM mod_billing.{prefix}confirmed_settlements x WHERE x.id = NEW.settlement_id{scope};
      IF settlement.id IS NULL OR settlement.billing_account_id <> NEW.billing_account_id
        OR settlement.currency <> NEW.currency OR settlement.minor_units <> NEW.minor_units
        OR settlement.funding_lane <> 'available_credit'
        OR NEW.amount_delta::text IN ('NaN', 'Infinity', '-Infinity')
        OR NEW.amount_delta <> round(NEW.amount_delta, NEW.minor_units)
        OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}cash_source_claims x WHERE x.settlement_id = settlement.id{scope}
            AND x.opening_cash_lot_id IS NULL AND x.billing_account_id = settlement.billing_account_id
            AND x.source_system = settlement.source_system AND x.source_settlement_key = settlement.source_settlement_key
            AND x.currency = settlement.currency AND x.minor_units = settlement.minor_units) THEN
          RAISE EXCEPTION 'mixed draw requires exact claimed ordinary cash source' USING ERRCODE = 'check_violation';
      END IF;
      SELECT coalesce(sum(x.amount_delta), 0) INTO room FROM mod_billing.{prefix}allocation_effects x WHERE x.settlement_id = NEW.settlement_id{scope};
      IF room < 0 OR room > settlement.amount OR room + NEW.amount_delta < 0 OR room + NEW.amount_delta > settlement.amount THEN
        RAISE EXCEPTION 'mixed source exceeds exact settlement capacity' USING ERRCODE = 'check_violation';
      END IF;
      IF grp.group_kind = 'opening_cash_applied' THEN
        IF NEW.document_id IS NULL OR NEW.amount_delta <= 0
          OR NOT mod_billing.has_{prefix}live_invoice_issuance(NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{'' if platform else ', NEW.tenant_id'})
          OR NEW.amount_delta > mod_billing.opening_{prefix}invoice_room(NEW.document_id{'' if platform else ', NEW.tenant_id'}) THEN
          RAISE EXCEPTION 'mixed settlement application exceeds exact live invoice room' USING ERRCODE = 'check_violation';
        END IF;
        WITH RECURSIVE chain AS (
          SELECT x.id, 0 AS level FROM mod_billing.{prefix}posting_groups x
            WHERE x.source_ref = 'settlement:' || settlement.id::text AND x.group_kind = 'settlement_accepted' AND x.reverses_group_id IS NULL{scope}
          UNION ALL SELECT x.id, c.level + 1 FROM mod_billing.{prefix}posting_groups x JOIN chain c ON x.reverses_group_id = c.id WHERE true{scope}
        ) SELECT count(*) FILTER (WHERE level = 0), max(level), count(*) INTO roots, depth, chain_count FROM chain;
        IF roots <> 1 OR depth % 2 <> 0 OR chain_count <> depth + 1 THEN
          RAISE EXCEPTION 'mixed draw requires unambiguous active settlement backing' USING ERRCODE = 'check_violation';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}allocation_effects_opening_owner BEFORE INSERT ON mod_billing.{prefix}allocation_effects
      FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}opening_allocation();
    """)  # nosec B608 -- fixed plane tables and exact owner/capacity predicates


_EMPTY_PROBES = {
    "": (
        "SELECT 1 FROM mod_billing.opening_import_receipts LIMIT 1",
        "SELECT 1 FROM mod_billing.opening_cash_lots LIMIT 1",
        "SELECT 1 FROM mod_billing.opening_cash_effects LIMIT 1",
    ),
    "platform_": (
        "SELECT 1 FROM mod_billing.platform_opening_import_receipts LIMIT 1",
        "SELECT 1 FROM mod_billing.platform_opening_cash_lots LIMIT 1",
        "SELECT 1 FROM mod_billing.platform_opening_cash_effects LIMIT 1",
    ),
}


def _drop(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    for table in (
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "accounting_facts",
    ):
        op.execute(
            f"DROP TRIGGER {prefix}{table}_opening_complete ON mod_billing.{prefix}{table};"
        )  # nosec B608 -- fixed qualified plane tables
    for table in ("posting_groups", "document_events", "allocation_effects"):
        op.execute(
            f"DROP TRIGGER {prefix}{table}_opening_owner ON mod_billing.{prefix}{table};"
        )  # nosec B608 -- fixed qualified plane tables
    _invoice_guards(platform, restore=True)
    claim = f"{prefix}cash_source_claims"
    op.drop_constraint(
        f"fk_{prefix}cash_claim_opening_lot", claim, type_="foreignkey", schema=_SCHEMA
    )
    op.drop_constraint(
        f"uq_{prefix}cash_claim_opening_lot", claim, type_="unique", schema=_SCHEMA
    )
    op.drop_constraint(
        f"ck_{prefix}cash_claim_owner", claim, type_="check", schema=_SCHEMA
    )
    op.drop_column(claim, "opening_cash_lot_id", schema=_SCHEMA)
    op.alter_column(
        claim, "settlement_id", nullable=False, existing_type=sa.Uuid(), schema=_SCHEMA
    )
    for table in reversed(_TABLES):
        op.drop_table(f"{prefix}{table}", schema=_SCHEMA)
    op.execute(f"""
        DROP FUNCTION mod_billing.admit_{prefix}opening_receipt();
        DROP FUNCTION mod_billing.admit_{prefix}opening_lot();
        DROP FUNCTION mod_billing.admit_{prefix}opening_effect();
        DROP FUNCTION mod_billing.check_{prefix}opening_lot_claim();
        DROP FUNCTION mod_billing.check_{prefix}opening_posting();
        DROP FUNCTION mod_billing.guard_{prefix}opening_owner();
        DROP FUNCTION mod_billing.guard_{prefix}opening_allocation();
        DROP FUNCTION mod_billing.has_{prefix}active_opening_draw(uuid{'' if platform else ', uuid'});
        DROP FUNCTION mod_billing.opening_{prefix}invoice_room(uuid{'' if platform else ', uuid'});
        DROP FUNCTION mod_billing.opening_{prefix}invoice_room_at(uuid, uuid, bigint{'' if platform else ', uuid'});
    """)  # nosec B608 -- fixed qualified function names


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
    names = (
        "billing_accounts",
        "cash_source_claims",
        "confirmed_settlements",
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "accounting_facts",
        "documents",
        "document_events",
        "withholding_discharges",
        "non_cash_credit_effects",
        "receivable_exposure_facts",
        "receivable_position_facts",
        "receivable_position_facts_v2",
    )
    tables = [
        f"{prefix}{name}"
        for name in (*names, *(() if upgrade else _TABLES))
        for prefix in prefixes
    ]
    op.execute(f"""
        SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '60s';
        LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE;
        SET LOCAL row_security = off;
    """)  # nosec B608 -- closed qualified module table names
    if not upgrade:
        for prefix in prefixes:
            for probe in _EMPTY_PROBES[prefix]:
                if connection.execute(sa.text(probe)).first() is not None:
                    raise RuntimeError(
                        "cannot downgrade Billing while opening cash evidence exists"
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


def upgrade() -> None:
    _apply(upgrade=True)


def downgrade() -> None:
    _apply(upgrade=False)
