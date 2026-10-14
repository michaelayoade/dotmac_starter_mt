"""Complete documentary receivable plus cash opening; additive to bi7.

No prior monetary/idempotency evidence is rewritten. Downgrade is refused:
permanent historical identities cannot safely be discarded or reissued.
"""

from __future__ import annotations

# Identifiers below are fixed Billing plane/table literals.
# ruff: noqa: S608
import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes
from sqlalchemy.schema import SchemaItem

from alembic import op

revision = "bi_0008_opening_receivables"
down_revision = "bi_0007_opening_cash"
branch_labels = None
depends_on = None
_SCHEMA = "mod_billing"
_TABLES = (
    "opening_receivable_bases",
    "opening_historical_discharge_claims",
    "receivable_exposure_facts_v2",
)


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
    CREATE OR REPLACE FUNCTION mod_billing.admit_{prefix}opening_receipt() RETURNS trigger AS $$
    DECLARE account record; grp record; original record;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      IF {envelope}
        OR NEW.total_residual::text IN ('NaN', 'Infinity', '-Infinity') OR (NEW.total_residual <= 0 AND NOT ((NEW.evidence::jsonb->'total_receivable'->>'amount')::numeric > 0))
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
        IF NEW.original_receipt_id IS NOT NULL OR grp.group_kind <> (CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 'opening_account_imported' ELSE 'opening_cash_imported' END)
          OR grp.source_version <> 1 OR NEW.expected_target_watermark <> 'none'
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_groups x WHERE x.billing_account_id = NEW.billing_account_id AND x.id <> NEW.posting_group_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}confirmed_settlements x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}rated_obligations x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}documents x WHERE x.billing_account_id = NEW.billing_account_id AND (NOT (NEW.evidence::jsonb ? 'total_receivable') OR x.authoring_mode <> 'imported_open_item'){scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x WHERE x.billing_account_id = NEW.billing_account_id{scope})
          OR (NEW.evidence::jsonb->'verification'->>'operation') IS DISTINCT FROM (CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 'billing.opening.import.v2' ELSE 'billing.opening.import.v1' END)
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
          OR grp.group_kind <> (CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 'opening_account_import_reversed' ELSE 'opening_cash_import_reversed' END)
          OR NEW.evidence::jsonb->'lot_ids' IS DISTINCT FROM original.evidence::jsonb->'lot_ids'
          OR NEW.evidence::jsonb->'manifest' IS DISTINCT FROM original.evidence::jsonb->'manifest'
          OR NEW.evidence::jsonb->'target' IS DISTINCT FROM original.evidence::jsonb->'target'
          OR (NEW.evidence::jsonb->'verification'->>'operation') IS DISTINCT FROM (CASE WHEN NEW.evidence::jsonb ? 'total_receivable' THEN 'billing.opening.import.reverse.v2' ELSE 'billing.opening.import.reverse.v1' END)
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
    CREATE OR REPLACE FUNCTION mod_billing.admit_{prefix}opening_lot() RETURNS trigger AS $$
    DECLARE account record; grp record; receipt record; source jsonb; source_sequence bigint;
    BEGIN
      SELECT * INTO account FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = NEW.receipt_id{scope};
      source := NEW.evidence::jsonb->'source';
      IF {envelope} OR receipt.id IS NULL OR receipt.receipt_kind <> 'import'
        OR receipt.billing_account_id <> NEW.billing_account_id OR receipt.posting_group_id <> NEW.posting_group_id
        OR receipt.currency <> NEW.currency OR receipt.minor_units <> NEW.minor_units
        OR grp.group_kind <> (CASE WHEN receipt.evidence::jsonb ? 'total_receivable' THEN 'opening_account_imported' ELSE 'opening_cash_imported' END) OR NEW.sequence < 1
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
    """)  # nosec B608 -- fixed plane tables and scoped envelope expressions
    _effect_admission(platform, envelope)


def _effect_admission(platform: bool, envelope: str) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    live = f"mod_billing.has_{prefix}live_invoice_issuance(NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{'' if platform else ', NEW.tenant_id'})"
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.admit_{prefix}opening_effect() RETURNS trigger AS $$
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
        IF grp.group_kind NOT IN ('opening_cash_imported', 'opening_account_imported') OR grp.id <> lot.posting_group_id
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
        IF grp.group_kind NOT IN ('opening_cash_import_reversed', 'opening_account_import_reversed') OR NEW.amount_delta >= 0
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
    """)  # nosec B608 -- fixed planes and source/capacity envelope expressions


def _completeness(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    joined = "" if platform else " AND e.tenant_id = x.tenant_id"
    payload_scope = (
        "expected_v4_payload->'scope' IS DISTINCT FROM '{}'::jsonb"
        if platform
        else "(expected_v4_payload->'scope'->>'tenant_id') IS DISTINCT FROM NEW.tenant_id::text"
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.check_{prefix}opening_lot_claim() RETURNS trigger AS $$
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
    CREATE OR REPLACE FUNCTION mod_billing.check_{prefix}opening_posting() RETURNS trigger AS $$
    DECLARE group_id uuid; grp record; fact record; receipt record; original record;
            expected_v4_payload jsonb; cash numeric; ar numeric; ordinary_count bigint; opening_count bigint; n bigint;
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
      expected_v4_payload := fact.fact_payload::jsonb;
      IF grp.id IS NULL OR grp.reverses_group_id IS NOT NULL OR n <> 1 OR fact.id IS NULL
        OR fact.source_authority <> 'internal' OR fact.source_system <> 'dotmac-billing' OR fact.effect_kind <> grp.group_kind
        OR fact.occurred_at <> grp.occurred_at OR fact.fact_digest !~ '^[0-9a-f]{{64}}$'
        OR {payload_scope}
        OR (expected_v4_payload->>'fact_id') IS DISTINCT FROM fact.id::text OR (expected_v4_payload->>'fact_version')::integer IS DISTINCT FROM 4
        OR (expected_v4_payload->>'billing_account_id') IS DISTINCT FROM grp.billing_account_id::text
        OR (expected_v4_payload->>'posting_group_id') IS DISTINCT FROM grp.id::text
        OR (expected_v4_payload->>'effect_kind') IS DISTINCT FROM grp.group_kind
        OR (expected_v4_payload->>'source_system') IS DISTINCT FROM 'dotmac-billing'
        OR (expected_v4_payload->>'source_authority') IS DISTINCT FROM 'internal'
        OR (expected_v4_payload->>'source_ref') IS DISTINCT FROM grp.source_ref
        OR (expected_v4_payload->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
        OR jsonb_typeof(expected_v4_payload->'effects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(expected_v4_payload->'settlement_allocations') IS DISTINCT FROM 'array'
        OR jsonb_typeof(expected_v4_payload->'opening_cash_effects') IS DISTINCT FROM 'array'
        OR jsonb_typeof(expected_v4_payload->'cash_source_order') IS DISTINCT FROM 'array'
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
        OR jsonb_array_length(expected_v4_payload->'effects') <> (CASE WHEN ar = 0 THEN 1 ELSE 2 END)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'effects') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = group_id{scope}
              AND x.lane = p->>'lane' AND x.amount_delta = (p->'amount_delta'->>'amount')::numeric
              AND x.currency = p->'amount_delta'->>'currency' AND x.minor_units = (p->'amount_delta'->>'minor_units')::integer))
        OR jsonb_array_length(expected_v4_payload->'opening_cash_effects') <> opening_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'opening_cash_effects') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = (p->>'effect_id')::uuid AND x.posting_group_id = group_id{scope} AND x.evidence::jsonb = p))
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = group_id{scope}
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'opening_cash_effects') p WHERE (p->>'effect_id')::uuid = x.id))
        OR jsonb_array_length(expected_v4_payload->'settlement_allocations') <> ordinary_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'settlement_allocations') p WHERE NOT EXISTS (
            SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.id = (p->>'allocation_effect_id')::uuid AND x.posting_group_id = group_id{scope}
              AND (p->'allocation'->>'settlement_id') IS NOT DISTINCT FROM x.settlement_id::text
              AND (p->'allocation'->>'document_id') IS NOT DISTINCT FROM x.document_id::text
              AND (p->'allocation'->>'effect_kind') IS NOT DISTINCT FROM x.effect_kind
              AND (p->'allocation'->>'offsets_allocation_id') IS NOT DISTINCT FROM x.offsets_allocation_id::text
              AND (p->'allocation'->'amount_delta'->>'amount')::numeric = x.amount_delta
              AND (p->'allocation'->'amount_delta'->>'currency') = x.currency
              AND (p->'allocation'->'amount_delta'->>'minor_units')::integer = x.minor_units))
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = group_id{scope}
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'settlement_allocations') p WHERE (p->>'allocation_effect_id')::uuid = x.id))
      THEN RAISE EXCEPTION 'opening V4 must exactly represent every posting and concrete source effect' USING ERRCODE = 'check_violation'; END IF;
      IF jsonb_array_length(expected_v4_payload->'cash_source_order') <> ordinary_count + opening_count
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'cash_source_order') WITH ORDINALITY p(value, ordinality)
            WHERE (p.value->>'sequence')::bigint IS DISTINCT FROM p.ordinality
              OR ((p.value->>'allocation_effect_id') IS NULL) = ((p.value->>'opening_effect_id') IS NULL)
              OR (p.value->>'allocation_effect_id' IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.id = (p.value->>'allocation_effect_id')::uuid AND x.posting_group_id = group_id{scope}))
              OR (p.value->>'opening_effect_id' IS NOT NULL AND NOT EXISTS (
                  SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.id = (p.value->>'opening_effect_id')::uuid AND x.posting_group_id = group_id{scope})))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_v4_payload->'cash_source_order') p
            GROUP BY p->>'allocation_effect_id', p->>'opening_effect_id' HAVING count(*) <> 1)
      THEN RAISE EXCEPTION 'V4 source ordering must bind each actual source effect once' USING ERRCODE = 'check_violation'; END IF;
      IF grp.group_kind IN ('opening_cash_imported', 'opening_cash_import_reversed') THEN
        SELECT count(*) INTO n FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = group_id{scope};
        SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = group_id{scope};
        IF n <> 1 OR expected_v4_payload->'import_receipt' IS DISTINCT FROM receipt.evidence::jsonb OR ordinary_count <> 0
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
          IF receipt.receipt_kind <> 'unwind' OR (expected_v4_payload->>'corrects_posting_group_id') IS DISTINCT FROM (receipt.evidence::jsonb->'verification'->>'original_posting_group_id')
            OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.original_receipt_id{scope} AND x.residual_amount > 0
                AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects e WHERE e.lot_id = x.id AND e.posting_group_id = group_id{joined} AND e.effect_kind = 'import_unwind' AND e.amount_delta = -x.residual_amount))
            OR opening_count <> (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.original_receipt_id AND x.residual_amount > 0{scope}) THEN
              RAISE EXCEPTION 'opening unwind must reverse each nonzero principal exactly' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
      ELSE
        IF expected_v4_payload->>'import_receipt' IS NOT NULL
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
          SELECT * INTO original FROM mod_billing.{prefix}posting_groups x WHERE x.id = (expected_v4_payload->>'corrects_posting_group_id')::uuid{scope};
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
      IF (SELECT count(*) FROM mod_billing.{prefix}receivable_exposure_facts x WHERE x.posting_group_watermark = group_id{scope})
          + (SELECT count(*) FROM mod_billing.{prefix}receivable_exposure_facts_v2 x WHERE x.posting_group_id = group_id{scope}) <> affected_count THEN
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
          UNION ALL SELECT 1 FROM mod_billing.{prefix}receivable_exposure_facts_v2 x
            WHERE x.document_id = invoice_id AND x.posting_group_id = group_id{scope}
              AND x.billing_account_id = grp.billing_account_id AND x.source_version = grp.source_version
              AND x.currency = grp.currency AND x.minor_units = grp.minor_units
              AND x.collectible_receivable = expected_room
        ) THEN RAISE EXCEPTION 'opening invoice exposure must match exact resolved source room' USING ERRCODE = 'check_violation'; END IF;
      END LOOP;
      IF NOT EXISTS (SELECT 1 FROM public.{prefix}outbox_events x WHERE x.event_type = 'billing.accounting.fact.v4' AND x.payload::jsonb = expected_v4_payload{scope}) THEN
        RAISE EXCEPTION 'opening V4 requires its atomic outbox evidence' USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane/table names and scoped completeness expressions


def _historical_invoice_room(platform: bool) -> None:
    """Resolve actual source contributions at one immutable account watermark."""
    prefix = "platform_" if platform else ""
    tenant = "" if platform else " AND x.tenant_id = tenant"
    effects_scope = "" if platform else " AND e.tenant_id = doc.tenant_id"
    op.execute(f"""
    CREATE OR REPLACE FUNCTION mod_billing.opening_{prefix}invoice_room_at(
        invoice_id uuid, account_id uuid, target_version bigint{'' if platform else ', tenant uuid'}
    ) RETURNS numeric AS $$
    DECLARE invoice record; doc record; issuance record; inverse record; void_event record;
            n bigint; room numeric; credit_total numeric := 0; expected_issue numeric;
            invoice_void boolean := false; backing record; opening_base numeric; void_at_target boolean;
    BEGIN
      SELECT * INTO invoice FROM mod_billing.{prefix}documents x WHERE x.id = invoice_id{tenant};
      IF invoice.id IS NULL OR invoice.document_kind <> 'invoice' OR invoice.issued_at IS NULL
        OR invoice.billing_account_id <> account_id OR target_version < 1 THEN
        RAISE EXCEPTION 'historical invoice room requires its exact issued account' USING ERRCODE = 'check_violation';
      END IF;
      opening_base := invoice.grand_total;
      IF invoice.authoring_mode = 'imported_open_item' THEN
        SELECT * INTO backing FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.document_id = invoice_id{tenant};
        IF backing.id IS NULL OR backing.billing_account_id <> account_id OR backing.currency <> invoice.currency
          OR backing.minor_units <> invoice.minor_units OR backing.source_version > target_version THEN
          RAISE EXCEPTION 'imported invoice requires exact source-version backing' USING ERRCODE = 'check_violation';
        END IF;
        opening_base := backing.initial_receivable;
      END IF;
      -- Every issued document needs a unique actual canonical issuance and
      -- exact AR effect. Current lifecycle flags never supply chronology.
      FOR doc IN SELECT * FROM mod_billing.{prefix}documents x
          WHERE (x.id = invoice_id OR (x.credits_document_id = invoice_id AND x.document_kind = 'credit_note' AND x.issued_at IS NOT NULL)){tenant}
      LOOP
        IF doc.id = invoice_id AND invoice.authoring_mode = 'imported_open_item' THEN
          IF EXISTS (SELECT 1 FROM mod_billing.{prefix}document_events x WHERE x.document_id = invoice_id AND x.event_kind = 'voided'{tenant}) THEN
            RAISE EXCEPTION 'imported invoice cannot be generically voided' USING ERRCODE = 'check_violation';
          END IF;
          CONTINUE;
        END IF;
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
        expected_issue := (CASE WHEN doc.document_kind = 'invoice' THEN doc.grand_total ELSE -doc.grand_total END);
        IF issuance.billing_account_id <> account_id OR issuance.currency <> invoice.currency OR issuance.minor_units <> invoice.minor_units
          OR issuance.reverses_group_id IS NOT NULL
          OR issuance.group_kind <> (CASE WHEN doc.document_kind = 'invoice' THEN 'invoice_issued' ELSE 'credit_note_issued' END)
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
      ) SELECT opening_base - credit_total + coalesce(sum(x.delta) FILTER (WHERE g.source_version <= target_version), 0),
          count(*) FILTER (WHERE g.id IS NULL OR g.billing_account_id <> account_id OR x.billing_account_id <> account_id
            OR g.currency <> invoice.currency OR x.currency <> invoice.currency
            OR g.minor_units <> invoice.minor_units OR x.minor_units <> invoice.minor_units
            OR x.delta::text IN ('NaN', 'Infinity', '-Infinity')) INTO room, n
        FROM contributions x LEFT JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id{'' if platform else ' AND g.tenant_id = tenant'};
      IF n <> 0 THEN
        RAISE EXCEPTION 'historical contribution has no exact scoped monetary group' USING ERRCODE = 'check_violation';
      END IF;
      IF room IS NULL OR room::text IN ('NaN', 'Infinity', '-Infinity') OR room < 0 OR room > opening_base THEN
        RAISE EXCEPTION 'historical invoice resolution exceeds exact room' USING ERRCODE = 'check_violation';
      END IF;
      IF invoice.authoring_mode = 'imported_open_item' THEN
        IF EXISTS (
            SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x
              JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id {'' if platform else 'AND g.tenant_id = x.tenant_id'}
            WHERE x.original_receipt_id = backing.receipt_id AND g.source_version <= target_version{tenant}
        ) THEN
          IF room <> opening_base THEN RAISE EXCEPTION 'opening unwind retains active discharges' USING ERRCODE = 'check_violation'; END IF;
          RETURN 0;
        END IF;
      END IF;
      IF invoice_void THEN RETURN 0; END IF;
      RETURN room;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and exact version-bound source predicates


def _create_tables(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = [] if platform else ["tenant_id"]
    for kind in _TABLES:
        table = f"{prefix}{kind}"
        columns: list[SchemaItem] = [sa.Column("id", sa.Uuid(), primary_key=True)]
        if not platform:
            columns.append(
                sa.Column(
                    "tenant_id",
                    sa.Uuid(),
                    sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
                    nullable=False,
                )
            )
        columns.extend(
            (
                sa.Column("billing_account_id", sa.Uuid(), nullable=False),
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                ),
            )
        )
        if kind == "opening_historical_discharge_claims":
            columns.extend(
                (
                    sa.Column("basis_id", sa.Uuid(), nullable=False),
                    sa.Column("operation", sa.String(120), nullable=False),
                    sa.Column("source_owner", sa.String(120), nullable=False),
                    sa.Column("source_ref", sa.String(512), nullable=False),
                    sa.Column("determination_ref", sa.String(255)),
                    sa.Column("component_ref", sa.String(255)),
                    sa.Column("evidence_digest", sa.String(64), nullable=False),
                    sa.UniqueConstraint(
                        *scope,
                        "operation",
                        "source_owner",
                        "determination_ref",
                        "component_ref",
                        name=f"uq_{table}_taxcomponent",
                    ),
                    sa.CheckConstraint(
                        "(operation = 'billing.withholding.tax_component' AND determination_ref IS NOT NULL AND component_ref IS NOT NULL) OR (operation <> 'billing.withholding.tax_component' AND determination_ref IS NULL AND component_ref IS NULL)",
                        name=f"ck_{table}_taxcomponent",
                    ),
                    sa.ForeignKeyConstraint(
                        [*scope, "basis_id", "billing_account_id"],
                        [
                            f"{_SCHEMA}.{prefix}opening_receivable_bases.{key}"
                            for key in (*scope, "id", "billing_account_id")
                        ],
                        name=f"fk_{table}_basis",
                        ondelete="RESTRICT",
                    ),
                )
            )
        else:
            columns.extend(
                (
                    sa.Column("document_id", sa.Uuid(), nullable=False),
                    sa.Column("posting_group_id", sa.Uuid(), nullable=False),
                    sa.Column("source_version", sa.BigInteger(), nullable=False),
                    sa.Column("currency", sa.String(3), nullable=False),
                    sa.Column("minor_units", sa.Integer(), nullable=False),
                    sa.Column("evidence", sa.JSON(), nullable=False),
                    sa.Column("evidence_digest", sa.String(64), nullable=False),
                    sa.Column(
                        "recorded_at", sa.DateTime(timezone=True), nullable=False
                    ),
                )
            )
            if kind == "opening_receivable_bases":
                columns.extend(
                    (
                        sa.Column("receipt_id", sa.Uuid(), nullable=False),
                        sa.Column("sequence", sa.Integer(), nullable=False),
                        sa.Column("installation_id", sa.String(255), nullable=False),
                        sa.Column("source_system", sa.String(120), nullable=False),
                        sa.Column("source_invoice_key", sa.String(255), nullable=False),
                        sa.Column(
                            "initial_receivable", sa.Numeric(20, 6), nullable=False
                        ),
                        sa.UniqueConstraint(
                            *scope,
                            "id",
                            "billing_account_id",
                            name=f"uq_{table}_account",
                        ),
                        sa.UniqueConstraint(
                            *scope, "document_id", name=f"uq_{table}_document"
                        ),
                        sa.UniqueConstraint(
                            *scope,
                            "receipt_id",
                            "sequence",
                            name=f"uq_{table}_sequence",
                        ),
                        sa.UniqueConstraint(
                            *scope,
                            "installation_id",
                            "source_system",
                            "source_invoice_key",
                            name=f"uq_{table}_source",
                        ),
                        sa.CheckConstraint(
                            "initial_receivable > 0 AND minor_units BETWEEN 0 AND 6",
                            name=f"ck_{table}_amount",
                        ),
                    )
                )
                links = (("receipt_id", "opening_import_receipts"),)
            else:
                columns.extend(
                    (
                        sa.Column("basis_id", sa.Uuid(), nullable=False),
                        sa.Column(
                            "collectible_receivable", sa.Numeric(20, 6), nullable=False
                        ),
                        sa.UniqueConstraint(
                            *scope,
                            "basis_id",
                            "source_version",
                            name=f"uq_{table}_version",
                        ),
                    )
                )
                links = (("basis_id", "opening_receivable_bases"),)
            for column, parent in (
                *links,
                ("document_id", "documents"),
                ("posting_group_id", "posting_groups"),
            ):
                columns.append(
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
        if platform:
            if kind == "opening_receivable_bases":
                op.create_table(
                    "platform_opening_receivable_bases", *columns, schema="mod_billing"
                )
            elif kind == "opening_historical_discharge_claims":
                op.create_table(
                    "platform_opening_historical_discharge_claims",
                    *columns,
                    schema="mod_billing",
                )
            else:
                op.create_table(
                    "platform_receivable_exposure_facts_v2",
                    *columns,
                    schema="mod_billing",
                )
        elif kind == "opening_receivable_bases":
            op.create_table("opening_receivable_bases", *columns, schema="mod_billing")
        elif kind == "opening_historical_discharge_claims":
            op.create_table(
                "opening_historical_discharge_claims", *columns, schema="mod_billing"
            )
        else:
            op.create_table(
                "receivable_exposure_facts_v2", *columns, schema="mod_billing"
            )
        if kind == "opening_historical_discharge_claims":
            op.create_index(
                f"uq_{table}_source",
                table,
                [*scope, "operation", "source_owner", "source_ref"],
                unique=True,
                postgresql_where=sa.text(
                    "operation <> 'billing.withholding.tax_component'"
                ),
                schema=_SCHEMA,
            )
        if platform:
            op.execute(
                f"REVOKE ALL ON mod_billing.{table} FROM app_user; GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;"
            )
        else:
            op.execute(f"""
                ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
                CREATE POLICY {table}_tenant_isolation ON mod_billing.{table}
                  USING (tenant_id = public.app_current_tenant_id())
                  WITH CHECK (tenant_id = public.app_current_tenant_id());
                GRANT SELECT, INSERT ON mod_billing.{table} TO app_user;
                REVOKE ALL ON mod_billing.{table} FROM platform_api;
            """)
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table}
              FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
        """)
    op.drop_constraint(
        f"ck_{prefix}documents_authoring_mode",
        f"{prefix}documents",
        schema=_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        f"ck_{prefix}documents_authoring_mode",
        f"{prefix}documents",
        "authoring_mode IN ('legacy_single', 'explicit_multiline', 'imported_open_item')",
        schema=_SCHEMA,
    )


def _backing_guards(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    tenant_arg = "" if platform else ", tenant uuid"
    tenant_call = "" if platform else ", tenant"
    scope = "" if platform else " AND x.tenant_id = tenant"
    scoped = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    # Rename the existing genuine-issuance predicate rather than weakening it.
    op.execute(
        f"ALTER FUNCTION mod_billing.has_{prefix}live_invoice_issuance(uuid, uuid, text, integer{'' if platform else ', uuid'}) RENAME TO has_{prefix}native_invoice_issuance;"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.has_{prefix}live_invoice_issuance(
      invoice_id uuid, account_id uuid, currency_code text, scale integer{tenant_arg}
    ) RETURNS boolean AS $$
      SELECT CASE WHEN d.authoring_mode = 'imported_open_item' THEN
        EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_receivable_bases x
          JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id {'' if platform else 'AND g.tenant_id = x.tenant_id'}
          JOIN mod_billing.{prefix}opening_import_receipts r ON r.id = x.receipt_id {'' if platform else 'AND r.tenant_id = x.tenant_id'}
          WHERE x.document_id = invoice_id AND x.billing_account_id = account_id AND x.currency = currency_code AND x.minor_units = scale{scope}
            AND x.source_version = g.source_version AND g.group_kind = 'opening_account_imported'
            AND r.receipt_kind = 'import' AND r.posting_group_id = g.id AND r.billing_account_id = account_id
            AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts inv WHERE inv.original_receipt_id = r.id {'' if platform else 'AND inv.tenant_id = x.tenant_id'}))
        ELSE mod_billing.has_{prefix}native_invoice_issuance(invoice_id, account_id, currency_code, scale{tenant_call}) END
      FROM mod_billing.{prefix}documents d WHERE d.id = invoice_id AND d.billing_account_id = account_id
        AND d.currency = currency_code AND d.minor_units = scale AND d.document_kind = 'invoice' AND d.issued_at IS NOT NULL
        {'' if platform else 'AND d.tenant_id = tenant'};
    $$ LANGUAGE sql;
    CREATE OR REPLACE FUNCTION mod_billing.opening_{prefix}invoice_room(invoice_id uuid{tenant_arg}) RETURNS numeric AS $$
      SELECT mod_billing.opening_{prefix}invoice_room_at(d.id, d.billing_account_id,
        (SELECT max(x.source_version) FROM mod_billing.{prefix}posting_groups x WHERE x.billing_account_id = d.billing_account_id{scope}){tenant_call})
      FROM mod_billing.{prefix}documents d WHERE d.id = invoice_id {'' if platform else 'AND d.tenant_id = tenant'};
    $$ LANGUAGE sql;
    CREATE OR REPLACE FUNCTION mod_billing.check_{prefix}noncash_invoice_room() RETURNS trigger AS $$
    DECLARE v_invoice_id uuid; v_doc record; v_room numeric;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}documents' THEN
        v_invoice_id := (CASE WHEN NEW.document_kind = 'credit_note' THEN NEW.credits_document_id ELSE NEW.id END);
      ELSE v_invoice_id := NEW.document_id; END IF;
      IF v_invoice_id IS NULL THEN RETURN NEW; END IF;
      SELECT * INTO v_doc FROM mod_billing.{prefix}documents x WHERE x.id = v_invoice_id{scoped};
      IF v_doc.id IS NULL OR v_doc.document_kind <> 'invoice' OR v_doc.issued_at IS NULL THEN RETURN NEW; END IF;
      PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = v_doc.billing_account_id{scoped} FOR UPDATE;
      -- At commit this checks the current state, while immutable exposure checks
      -- below separately qualify EACH historical source-version watermark.
      v_room := mod_billing.opening_{prefix}invoice_room(v_invoice_id{'' if platform else ', NEW.tenant_id'});
      IF v_room IS NULL OR v_room < 0 THEN RAISE EXCEPTION 'invoice exceeds exact native/imported backing' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates


def _successor_admission(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}receivable_basis() RETURNS trigger AS $$
    DECLARE basis record; doc record; receipt record; grp record; original jsonb; source jsonb; discharged numeric; scale numeric;
    BEGIN
      PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
      SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = NEW.receipt_id{scope};
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      source := NEW.evidence::jsonb->'source'; original := source->'original'; scale := power(10::numeric, NEW.minor_units);
      SELECT coalesce(sum((e->>'amount_minor')::numeric),0) / scale INTO discharged FROM jsonb_array_elements(source->'historical_discharges') e;
      IF doc.id IS NULL OR receipt.id IS NULL OR grp.id IS NULL OR receipt.receipt_kind <> 'import'
        OR grp.group_kind <> 'opening_account_imported' OR grp.source_version <> NEW.source_version
        OR receipt.posting_group_id <> grp.id OR receipt.billing_account_id <> NEW.billing_account_id
        OR NEW.source_version <> 1 OR NEW.sequence < 1 OR NEW.minor_units NOT BETWEEN 0 AND 6
        OR NEW.initial_receivable::text IN ('NaN','Infinity','-Infinity') OR NEW.initial_receivable <= 0
        OR NEW.initial_receivable <> round(NEW.initial_receivable, NEW.minor_units)
        OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$'
        OR NEW.evidence::jsonb->'scope' IS DISTINCT FROM {scope_json}
        OR NEW.evidence::jsonb->>'basis_id' IS DISTINCT FROM NEW.id::text
        OR NEW.evidence::jsonb->>'receipt_id' IS DISTINCT FROM NEW.receipt_id::text
        OR NEW.evidence::jsonb->>'document_id' IS DISTINCT FROM NEW.document_id::text
        OR NEW.evidence::jsonb->>'billing_account_id' IS DISTINCT FROM NEW.billing_account_id::text
        OR NEW.evidence::jsonb->>'posting_group_id' IS DISTINCT FROM NEW.posting_group_id::text
        OR (NEW.evidence::jsonb->>'source_version')::bigint IS DISTINCT FROM NEW.source_version
        OR (NEW.evidence::jsonb->>'sequence')::integer IS DISTINCT FROM NEW.sequence
        OR NEW.evidence::jsonb->>'installation_id' IS DISTINCT FROM NEW.installation_id
        OR NEW.evidence::jsonb->>'source_system' IS DISTINCT FROM NEW.source_system
        OR NEW.evidence::jsonb->>'source_invoice_key' IS DISTINCT FROM NEW.source_invoice_key
        OR (NEW.evidence::jsonb->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.recorded_at
        OR NEW.evidence::jsonb->>'manifest_fingerprint' IS DISTINCT FROM receipt.manifest_fingerprint
        OR (NEW.evidence::jsonb->'initial_receivable'->>'amount')::numeric IS DISTINCT FROM NEW.initial_receivable
        OR NEW.evidence::jsonb->'initial_receivable'->>'currency' IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'initial_receivable'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        OR NEW.installation_id IS DISTINCT FROM receipt.evidence::jsonb->'manifest'->'capture'->>'installation_id'
        OR NEW.source_system IS DISTINCT FROM receipt.evidence::jsonb->'manifest'->'capture'->>'source_system'
        OR source->>'source_invoice_key' IS DISTINCT FROM NEW.source_invoice_key
        OR source->'partition' IS DISTINCT FROM receipt.evidence::jsonb->'partition'
        OR (source->>'residual_minor')::numeric / scale IS DISTINCT FROM NEW.initial_receivable
        OR original IS NULL OR jsonb_typeof(original) <> 'object'
        OR original->>'document_number' IS DISTINCT FROM doc.document_number
        OR (original->>'issued_at')::timestamptz IS DISTINCT FROM doc.issued_at
        OR (original->'grand_total'->>'amount')::numeric IS DISTINCT FROM doc.grand_total
        OR (original->'subtotal'->>'amount')::numeric IS DISTINCT FROM doc.subtotal
        OR (original->'tax_total'->>'amount')::numeric IS DISTINCT FROM doc.tax_total
        OR doc.subtotal + doc.tax_total <> doc.grand_total OR discharged < 0
        OR doc.grand_total - discharged <> NEW.initial_receivable
        OR doc.authoring_mode <> 'imported_open_item' OR doc.obligation_id IS NOT NULL
        OR doc.document_kind <> 'invoice' OR doc.issued_at IS NULL OR doc.credits_document_id IS NOT NULL
        OR doc.billing_account_id <> NEW.billing_account_id OR doc.currency <> NEW.currency OR doc.minor_units <> NEW.minor_units
        OR (receipt.evidence::jsonb->'basis_ids'->>(NEW.sequence-1)) IS DISTINCT FROM NEW.id::text
        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'receivables') e WHERE e = source)
      THEN RAISE EXCEPTION 'opening receivable requires complete immutable original and exact residual basis' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}opening_receivable_bases_admission BEFORE INSERT ON mod_billing.{prefix}opening_receivable_bases
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}receivable_basis();
    CREATE FUNCTION mod_billing.admit_{prefix}historical_claim() RETURNS trigger AS $$
    DECLARE basis record;
    BEGIN
      SELECT * INTO basis FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.id = NEW.basis_id{scope};
      IF basis.id IS NULL OR basis.billing_account_id <> NEW.billing_account_id
        OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$'
        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(basis.evidence::jsonb->'source'->'historical_discharges') d,
            jsonb_array_elements(d->'replay_bindings') b
            WHERE b->>'operation' = NEW.operation AND b->>'source_owner' = NEW.source_owner
              AND b->>'source_ref' = NEW.source_ref AND b->>'evidence_digest' = NEW.evidence_digest
              AND (b->>'determination_ref') IS NOT DISTINCT FROM NEW.determination_ref
              AND (b->>'component_ref') IS NOT DISTINCT FROM NEW.component_ref)
      THEN RAISE EXCEPTION 'historical replay claim requires its exact immutable owner binding' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}opening_historical_claim_admission BEFORE INSERT ON mod_billing.{prefix}opening_historical_discharge_claims
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}historical_claim();
    CREATE FUNCTION mod_billing.guard_{prefix}imported_owner() RETURNS trigger AS $$
    DECLARE doc record; original record; room numeric; delta numeric; v_live_operation text;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}document_events' THEN
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
        IF doc.authoring_mode = 'imported_open_item' AND NEW.event_kind = 'voided' THEN
          RAISE EXCEPTION 'imported document requires complete opening unwind owner' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME = '{prefix}posting_groups' THEN
        IF NEW.reverses_group_id IS NOT NULL THEN
          SELECT * INTO original FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.reverses_group_id{scope};
          IF original.group_kind IN ('opening_account_imported','opening_account_import_reversed') THEN
            RAISE EXCEPTION 'opening principal cannot use generic posting reversal' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
        RETURN NEW;
      ELSIF TG_TABLE_NAME = '{prefix}withholding_discharges' THEN
        v_live_operation := 'billing.withholding.accept'; delta := NEW.amount_delta;
        IF NEW.reverses_discharge_id IS NOT NULL THEN RETURN NEW; END IF;
        IF EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_historical_discharge_claims x
            WHERE x.operation = 'billing.withholding.tax_component' AND x.source_owner = NEW.tax_source_owner
              AND x.determination_ref = NEW.determination_ref AND x.component_ref = NEW.component_ref{scope}) THEN
          RAISE EXCEPTION 'historical Tax component cannot discharge a fresh receivable' USING ERRCODE = 'unique_violation';
        END IF;
      ELSIF TG_TABLE_NAME = '{prefix}non_cash_credit_grants' THEN
        v_live_operation := 'billing.non_cash_credit.grant';
      ELSE
        v_live_operation := 'billing.non_cash_credit.apply'; delta := -NEW.availability_delta;
        IF NEW.effect_kind <> 'application' THEN RETURN NEW; END IF;
      END IF;
      IF EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_historical_discharge_claims x
          WHERE x.operation = v_live_operation AND x.source_owner = NEW.source_owner AND x.source_ref = NEW.source_ref{scope}) THEN
        RAISE EXCEPTION 'historical discharge identity cannot fund a fresh effect' USING ERRCODE = 'unique_violation';
      END IF;
      IF TG_TABLE_NAME = '{prefix}non_cash_credit_grants' THEN RETURN NEW; END IF;
      SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
      IF doc.authoring_mode = 'imported_open_item' THEN
        PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
        room := mod_billing.opening_{prefix}invoice_room(NEW.document_id{'' if platform else ', NEW.tenant_id'});
        IF NOT mod_billing.has_{prefix}live_invoice_issuance(NEW.document_id, NEW.billing_account_id, NEW.currency, NEW.minor_units{'' if platform else ', NEW.tenant_id'})
          OR delta <= 0 OR delta > room THEN RAISE EXCEPTION 'post-import discharge exceeds exact live residual' USING ERRCODE = 'check_violation'; END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates
    for table in (
        "document_events",
        "posting_groups",
        "withholding_discharges",
        "non_cash_credit_grants",
        "non_cash_credit_effects",
    ):
        op.execute(
            f"CREATE TRIGGER {prefix}{table}_imported_owner BEFORE INSERT ON mod_billing.{prefix}{table} FOR EACH ROW EXECUTE FUNCTION mod_billing.guard_{prefix}imported_owner();"
        )


def _exposure_admission(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    op.execute(f"""
    CREATE FUNCTION mod_billing.admit_{prefix}opening_exposure() RETURNS trigger AS $$
    DECLARE basis record; grp record; v_room numeric; v_state text;
    BEGIN
      SELECT * INTO basis FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.id = NEW.basis_id{scope};
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = NEW.posting_group_id{scope};
      PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = NEW.billing_account_id{scope} FOR UPDATE;
      IF basis.id IS NULL OR grp.id IS NULL OR basis.document_id <> NEW.document_id
        OR basis.billing_account_id <> NEW.billing_account_id OR grp.billing_account_id <> NEW.billing_account_id
        OR basis.currency <> NEW.currency OR basis.minor_units <> NEW.minor_units
        OR grp.currency <> NEW.currency OR grp.minor_units <> NEW.minor_units OR grp.source_version <> NEW.source_version
        OR NEW.evidence_digest !~ '^[0-9a-f]{{64}}$'
        OR NEW.evidence::jsonb->'scope' IS DISTINCT FROM {scope_json}
        OR NEW.evidence::jsonb->>'basis_id' IS DISTINCT FROM basis.id::text
        OR NEW.evidence::jsonb->>'document_id' IS DISTINCT FROM NEW.document_id::text
        OR NEW.evidence::jsonb->>'billing_account_id' IS DISTINCT FROM NEW.billing_account_id::text
        OR NEW.evidence::jsonb->>'posting_group_watermark' IS DISTINCT FROM grp.id::text
        OR (NEW.evidence::jsonb->>'source_version')::bigint IS DISTINCT FROM grp.source_version
        OR (NEW.evidence::jsonb->>'observed_at')::timestamptz IS DISTINCT FROM NEW.recorded_at
        OR NEW.evidence::jsonb->>'exposure_ref' IS DISTINCT FROM 'document\\:' || NEW.document_id::text
        OR NEW.evidence::jsonb->>'source_owner' IS DISTINCT FROM 'dotmac-billing'
        OR NEW.evidence::jsonb->>'source_authority' IS DISTINCT FROM 'internal'
        OR NEW.evidence::jsonb->>'projection_mode' IS DISTINCT FROM 'authoritative'
        OR NEW.evidence::jsonb->>'derived_from' IS DISTINCT FROM 'posting_groups'
        OR NEW.evidence::jsonb->>'completeness' IS DISTINCT FROM 'complete'
        OR NEW.evidence::jsonb->>'completeness_reason_code' IS NOT NULL
        OR NEW.evidence::jsonb->>'state_fingerprint' !~ '^[0-9a-f]{{64}}$'
        OR NEW.evidence::jsonb->'collection' IS DISTINCT FROM basis.evidence::jsonb->'source'->'collection'
      THEN RAISE EXCEPTION 'opening exposure requires exact scoped basis/document/group/version evidence' USING ERRCODE = 'check_violation'; END IF;
      IF NOT (
        basis.posting_group_id = grp.id
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = grp.id AND x.document_id = NEW.document_id{scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}withholding_discharges x WHERE x.posting_group_id = grp.id AND x.document_id = NEW.document_id{scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.posting_group_id = grp.id AND x.document_id = NEW.document_id{scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = grp.id AND x.document_id = NEW.document_id{scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}documents x WHERE x.credits_document_id = NEW.document_id AND x.document_kind = 'credit_note'
            AND (grp.source_ref = 'credit_note\\:' || x.id::text OR EXISTS (
              SELECT 1 FROM mod_billing.{prefix}posting_groups g WHERE g.id = grp.reverses_group_id AND g.source_ref = 'credit_note\\:' || x.id::text {'' if platform else 'AND g.tenant_id = x.tenant_id'})){scope})
        OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = grp.id AND x.original_receipt_id = basis.receipt_id{scope})
      ) THEN RAISE EXCEPTION 'opening exposure group must actually affect this invoice' USING ERRCODE = 'check_violation'; END IF;
      v_room := mod_billing.opening_{prefix}invoice_room_at(NEW.document_id, NEW.billing_account_id, NEW.source_version{'' if platform else ', NEW.tenant_id'});
      v_state := (CASE WHEN EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_import_receipts x
          JOIN mod_billing.{prefix}posting_groups g ON g.id = x.posting_group_id {'' if platform else 'AND g.tenant_id = x.tenant_id'}
          WHERE x.original_receipt_id = basis.receipt_id AND g.source_version <= NEW.source_version{scope}) THEN 'cancelled'
        WHEN v_room = 0 THEN 'resolved' WHEN v_room < basis.initial_receivable THEN 'partially_resolved' ELSE 'open' END);
      IF NEW.collectible_receivable IS DISTINCT FROM v_room
        OR (NEW.evidence::jsonb->'collectible_receivable'->>'amount')::numeric IS DISTINCT FROM v_room
        OR NEW.evidence::jsonb->'collectible_receivable'->>'currency' IS DISTINCT FROM NEW.currency
        OR (NEW.evidence::jsonb->'collectible_receivable'->>'minor_units')::integer IS DISTINCT FROM NEW.minor_units
        OR NEW.evidence::jsonb->>'financial_state' IS DISTINCT FROM v_state
      THEN RAISE EXCEPTION 'opening exposure must resolve exact source-version residual and state' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {prefix}receivable_exposure_v2_admission BEFORE INSERT ON mod_billing.{prefix}receivable_exposure_facts_v2
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}opening_exposure();
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates


def _successor_completeness(platform: bool) -> None:
    same = "" if platform else " AND x.tenant_id = basis.tenant_id"
    prefix = "platform_" if platform else ""
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    joined = "" if platform else " AND e.tenant_id = x.tenant_id"
    scope_json = (
        "'{}'::jsonb"
        if platform
        else "jsonb_build_object('tenant_id', NEW.tenant_id::text)"
    )
    op.execute(rf"""
    CREATE FUNCTION mod_billing.check_{prefix}opening_account() RETURNS trigger AS $$
    DECLARE v_group_id uuid; grp record; fact record; receipt record; original record; basis record; exposure record;
            v_payload jsonb; v_ar numeric; v_cash numeric; v_expected numeric; v_invoice_id uuid; v_n bigint;
            v_principal_id uuid; v_owner text; v_correlation text;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}posting_groups' THEN v_group_id := NEW.id;
      ELSIF TG_TABLE_NAME = '{prefix}opening_historical_discharge_claims' THEN
        SELECT x.posting_group_id INTO v_group_id FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.id = NEW.basis_id{scope};
      ELSIF TG_TABLE_NAME = '{prefix}documents' THEN
        IF NEW.authoring_mode = 'imported_open_item' THEN
          SELECT x.posting_group_id INTO v_group_id FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.document_id = NEW.id{scope};
          IF v_group_id IS NULL THEN RAISE EXCEPTION 'imported document has no immutable opening basis' USING ERRCODE = 'check_violation'; END IF;
        ELSIF NEW.document_kind = 'credit_note' THEN
          SELECT x.id INTO v_group_id FROM mod_billing.{prefix}posting_groups x WHERE x.source_ref = 'credit_note\:' || NEW.id::text{scope};
        ELSE RETURN NEW; END IF;
      ELSIF TG_TABLE_NAME = '{prefix}invoice_document_facts' THEN
        RETURN NEW;
      ELSE v_group_id := NEW.posting_group_id; END IF;
      IF v_group_id IS NULL THEN RETURN NEW; END IF;
      SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x WHERE x.id = v_group_id{scope};
      PERFORM 1 FROM mod_billing.{prefix}billing_accounts x WHERE x.id = grp.billing_account_id{scope} FOR UPDATE;
      IF grp.group_kind IN ('opening_account_imported','opening_account_import_reversed') THEN
        SELECT count(*) INTO v_n FROM mod_billing.{prefix}accounting_facts x WHERE x.posting_group_id = v_group_id{scope};
        SELECT * INTO fact FROM mod_billing.{prefix}accounting_facts x WHERE x.posting_group_id = v_group_id AND x.fact_version = 5{scope};
        SELECT * INTO receipt FROM mod_billing.{prefix}opening_import_receipts x WHERE x.posting_group_id = v_group_id{scope};
        v_payload := fact.fact_payload::jsonb;
        IF v_n <> 1 OR fact.id IS NULL OR receipt.id IS NULL OR grp.reverses_group_id IS NOT NULL
          OR fact.effect_kind <> grp.group_kind OR fact.source_system <> 'dotmac-billing' OR fact.source_authority <> 'internal'
          OR fact.fact_digest !~ '^[0-9a-f]{{64}}$' OR fact.occurred_at <> grp.occurred_at
          OR v_payload->'scope' IS DISTINCT FROM {scope_json}
          OR v_payload->>'fact_id' IS DISTINCT FROM fact.id::text OR (v_payload->>'fact_version')::integer IS DISTINCT FROM 5
          OR v_payload->>'billing_account_id' IS DISTINCT FROM grp.billing_account_id::text
          OR v_payload->>'posting_group_id' IS DISTINCT FROM grp.id::text
          OR v_payload->>'effect_kind' IS DISTINCT FROM grp.group_kind
          OR v_payload->>'source_ref' IS DISTINCT FROM grp.source_ref
          OR v_payload->>'source_system' IS DISTINCT FROM fact.source_system
          OR v_payload->>'source_authority' IS DISTINCT FROM fact.source_authority
          OR (v_payload->>'occurred_at')::timestamptz IS DISTINCT FROM grp.occurred_at
          OR v_payload->'import_receipt' IS DISTINCT FROM receipt.evidence::jsonb
          OR jsonb_typeof(v_payload->'effects') IS DISTINCT FROM 'array'
          OR jsonb_typeof(v_payload->'receivable_bases') IS DISTINCT FROM 'array'
          OR jsonb_typeof(v_payload->'opening_cash_effects') IS DISTINCT FROM 'array'
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = v_group_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}withholding_discharges x WHERE x.posting_group_id = v_group_id{scope})
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.posting_group_id = v_group_id{scope})
          OR (receipt.receipt_kind = 'import' AND EXISTS (SELECT 1 FROM mod_billing.{prefix}confirmed_settlements x WHERE x.billing_account_id = grp.billing_account_id{scope}
            AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_groups g
              WHERE g.source_ref = 'settlement\:' || x.id::text AND g.group_kind = 'settlement_accepted' AND g.reverses_group_id IS NULL
                AND g.billing_account_id = x.billing_account_id AND g.currency = x.currency AND g.minor_units = x.minor_units
                AND g.source_version > grp.source_version {'' if platform else 'AND g.tenant_id = x.tenant_id'})))
        THEN RAISE EXCEPTION 'opening account recognition needs exact V5 documentary receipt and no native issuance or settlement' USING ERRCODE = 'check_violation'; END IF;
        SELECT coalesce(sum(x.amount_delta) FILTER (WHERE x.lane = 'receivable'),0),
          coalesce(sum(x.amount_delta) FILTER (WHERE x.lane = 'available_credit'),0) INTO v_ar,v_cash
          FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = v_group_id{scope};
        v_expected := (receipt.evidence::jsonb->'total_receivable'->>'amount')::numeric;
        IF v_expected IS NULL OR v_expected < 0 OR v_expected::text IN ('NaN','Infinity','-Infinity')
          OR receipt.total_residual < 0 OR v_expected + receipt.total_residual <= 0
          OR v_ar <> (CASE WHEN receipt.receipt_kind = 'import' THEN v_expected ELSE -v_expected END)
          OR v_cash <> (CASE WHEN receipt.receipt_kind = 'import' THEN receipt.total_residual ELSE -receipt.total_residual END)
          OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = v_group_id{scope}) <> 2
          OR jsonb_array_length(v_payload->'effects') <> 2
          OR (SELECT count(DISTINCT p->>'lane') FROM jsonb_array_elements(v_payload->'effects') p) <> 2
          OR (SELECT count(DISTINCT x.lane) FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = v_group_id{scope}) <> 2
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = v_group_id{scope}
              AND (x.billing_account_id <> grp.billing_account_id OR x.currency <> grp.currency OR x.minor_units <> grp.minor_units OR x.lane NOT IN ('receivable','available_credit')))
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_payload->'effects') p WHERE NOT EXISTS (
              SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = v_group_id{scope}
                AND x.lane = p->>'lane' AND x.amount_delta = (p->'amount_delta'->>'amount')::numeric
                AND x.currency = p->'amount_delta'->>'currency' AND x.minor_units = (p->'amount_delta'->>'minor_units')::integer))
        THEN RAISE EXCEPTION 'opening account recognizes exact residual AR and cash availability only' USING ERRCODE = 'check_violation'; END IF;
        v_principal_id := (CASE WHEN receipt.receipt_kind = 'import' THEN receipt.id ELSE receipt.original_receipt_id END);
        IF receipt.receipt_kind = 'import' THEN
          IF v_payload->>'corrects_posting_group_id' IS NOT NULL
            OR nullif(trim(receipt.evidence::jsonb->'verification'->>'receivable_lifecycle_proof_ref'),'') IS NULL
            OR nullif(trim(receipt.evidence::jsonb->'verification'->>'documentary_tax_mapping_proof_ref'),'') IS NULL
            OR nullif(trim(receipt.evidence::jsonb->'verification'->>'historical_discharge_mapping_proof_ref'),'') IS NULL
          THEN RAISE EXCEPTION 'opening account requires exact documentary, tax and replay mappings' USING ERRCODE = 'check_violation'; END IF;
        ELSE
          SELECT * INTO original FROM mod_billing.{prefix}opening_import_receipts x WHERE x.id = v_principal_id{scope};
          IF original.receipt_kind <> 'import' OR original.evidence::jsonb->'basis_ids' IS DISTINCT FROM receipt.evidence::jsonb->'basis_ids'
            OR original.evidence::jsonb->'total_receivable' IS DISTINCT FROM receipt.evidence::jsonb->'total_receivable'
            OR v_payload->>'corrects_posting_group_id' IS DISTINCT FROM original.posting_group_id::text
          THEN RAISE EXCEPTION 'complete opening unwind requires exact original basis and amount' USING ERRCODE = 'check_violation'; END IF;
        END IF;
        IF (SELECT coalesce(sum(x.initial_receivable),0) FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope}) <> v_expected
          OR (SELECT count(*) FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope}) <> jsonb_array_length(receipt.evidence::jsonb->'basis_ids')
          OR (SELECT count(*) FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope}) <> jsonb_array_length(v_payload->'receivable_bases')
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_payload->'receivable_bases') p WHERE NOT EXISTS (
              SELECT 1 FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope} AND x.evidence::jsonb = p))
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope}
              AND (v_payload->'receivable_bases'->(x.sequence-1)) IS DISTINCT FROM x.evidence::jsonb)
          OR (SELECT coalesce(sum(x.residual_amount),0) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = v_principal_id{scope}) <> receipt.total_residual
          OR (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = v_principal_id{scope}) <> jsonb_array_length(receipt.evidence::jsonb->'lot_ids')
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = v_principal_id{scope}
            AND (receipt.evidence::jsonb->'basis_ids'->>(x.sequence-1)) IS DISTINCT FROM x.id::text)
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = v_principal_id{scope}
            AND (receipt.evidence::jsonb->'lot_ids'->>(x.sequence-1)) IS DISTINCT FROM x.id::text)
        THEN RAISE EXCEPTION 'opening account receipt must contain every actual ordered source' USING ERRCODE = 'check_violation'; END IF;
        IF receipt.receipt_kind = 'import' AND (
            (SELECT count(*) FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.receipt_id = receipt.id{scope}) <>
              (SELECT count(*) FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'receivables') p WHERE p->'partition' = receipt.evidence::jsonb->'partition')
            OR (SELECT count(*) FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = receipt.id{scope}) <>
              (SELECT count(*) FROM jsonb_array_elements(receipt.evidence::jsonb->'manifest'->'cash_lots') p WHERE p->'partition' = receipt.evidence::jsonb->'partition')
        ) THEN RAISE EXCEPTION 'opening principal cannot stage a partial partition' USING ERRCODE = 'check_violation'; END IF;
        IF jsonb_array_length(v_payload->'opening_cash_effects') <>
            (SELECT count(*) FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = v_group_id{scope})
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(v_payload->'opening_cash_effects') p WHERE NOT EXISTS (
              SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = v_group_id{scope} AND x.evidence::jsonb = p))
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = v_group_id{scope}
              AND (v_payload->'opening_cash_effects'->(x.sequence-1)) IS DISTINCT FROM x.evidence::jsonb)
          OR EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_lots x WHERE x.receipt_id = v_principal_id{scope}
            AND (receipt.receipt_kind = 'import' OR x.residual_amount > 0)
            AND NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}opening_cash_effects e WHERE e.lot_id = x.id AND e.posting_group_id = v_group_id{joined}
                AND e.effect_kind = (CASE WHEN receipt.receipt_kind = 'import' THEN 'recognition' ELSE 'import_unwind' END)
                AND e.amount_delta = (CASE WHEN receipt.receipt_kind = 'import' THEN x.residual_amount ELSE -x.residual_amount END)))
        THEN RAISE EXCEPTION 'opening cash recognition/inverse must cover each actual source exactly' USING ERRCODE = 'check_violation'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.{prefix}outbox_events x WHERE x.event_type = 'billing.accounting.fact.v5' AND x.payload::jsonb = v_payload{scope}) THEN
          RAISE EXCEPTION 'opening V5 requires its atomic outbox evidence' USING ERRCODE = 'check_violation'; END IF;
      END IF;
      -- All imported discharges use real current sources. Historical components
      -- are documentary basis evidence and never contribute a second time.
      FOR v_invoice_id IN (
        SELECT x.document_id FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.posting_group_id = v_group_id{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}allocation_effects x WHERE x.posting_group_id = v_group_id AND x.document_id IS NOT NULL{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}withholding_discharges x WHERE x.posting_group_id = v_group_id{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.posting_group_id = v_group_id AND x.document_id IS NOT NULL{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}opening_cash_effects x WHERE x.posting_group_id = v_group_id AND x.document_id IS NOT NULL{scope}
        UNION SELECT x.credits_document_id FROM mod_billing.{prefix}documents x WHERE x.document_kind = 'credit_note' AND grp.source_ref = 'credit_note\:' || x.id::text{scope}
        UNION SELECT x.credits_document_id FROM mod_billing.{prefix}documents x
          JOIN mod_billing.{prefix}posting_groups g ON g.id = grp.reverses_group_id AND g.source_ref = 'credit_note\:' || x.id::text {'' if platform else 'AND g.tenant_id = x.tenant_id'}
          WHERE x.document_kind = 'credit_note'{scope}
        UNION SELECT x.document_id FROM mod_billing.{prefix}opening_receivable_bases x
          JOIN mod_billing.{prefix}opening_import_receipts r ON r.original_receipt_id = x.receipt_id {'' if platform else 'AND r.tenant_id = x.tenant_id'}
          WHERE r.posting_group_id = v_group_id{scope}
      ) LOOP
        SELECT * INTO basis FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.document_id = v_invoice_id{scope};
        IF basis.id IS NULL THEN CONTINUE; END IF;
        v_expected := mod_billing.opening_{prefix}invoice_room_at(v_invoice_id, grp.billing_account_id, grp.source_version{'' if platform else ', NEW.tenant_id'});
        SELECT count(*) INTO v_n FROM mod_billing.{prefix}receivable_exposure_facts_v2 x WHERE x.document_id = v_invoice_id AND x.posting_group_id = v_group_id{scope};
        SELECT * INTO exposure FROM mod_billing.{prefix}receivable_exposure_facts_v2 x WHERE x.document_id = v_invoice_id AND x.posting_group_id = v_group_id{scope};
        IF v_n <> 1 OR exposure.basis_id <> basis.id OR exposure.billing_account_id <> grp.billing_account_id
          OR exposure.source_version <> grp.source_version OR exposure.currency <> grp.currency OR exposure.minor_units <> grp.minor_units
          OR exposure.collectible_receivable <> v_expected
          OR exposure.evidence_digest !~ '^[0-9a-f]{{64}}$'
          OR exposure.evidence::jsonb->'scope' IS DISTINCT FROM {scope_json}
          OR exposure.evidence::jsonb->>'basis_id' IS DISTINCT FROM basis.id::text
          OR exposure.evidence::jsonb->>'document_id' IS DISTINCT FROM v_invoice_id::text
          OR exposure.evidence::jsonb->>'billing_account_id' IS DISTINCT FROM grp.billing_account_id::text
          OR exposure.evidence::jsonb->>'posting_group_watermark' IS DISTINCT FROM grp.id::text
          OR (exposure.evidence::jsonb->>'source_version')::bigint IS DISTINCT FROM grp.source_version
          OR (exposure.evidence::jsonb->'collectible_receivable'->>'amount')::numeric IS DISTINCT FROM v_expected
          OR exposure.evidence::jsonb->'collectible_receivable'->>'currency' IS DISTINCT FROM grp.currency
          OR (exposure.evidence::jsonb->'collectible_receivable'->>'minor_units')::integer IS DISTINCT FROM grp.minor_units
          OR exposure.evidence::jsonb->>'source_owner' IS DISTINCT FROM 'dotmac-billing'
          OR exposure.evidence::jsonb->>'source_authority' IS DISTINCT FROM 'internal'
          OR exposure.evidence::jsonb->>'projection_mode' IS DISTINCT FROM 'authoritative'
          OR exposure.evidence::jsonb->>'derived_from' IS DISTINCT FROM 'posting_groups'
          OR exposure.evidence::jsonb->>'completeness' IS DISTINCT FROM 'complete'
          OR exposure.evidence::jsonb->'collection' IS DISTINCT FROM basis.evidence::jsonb->'source'->'collection'
          OR NOT EXISTS (SELECT 1 FROM public.{prefix}outbox_events x WHERE x.event_type = 'billing.receivable.exposure.v2' AND x.payload::jsonb = exposure.evidence::jsonb{scope})
        THEN RAISE EXCEPTION 'imported exposure must match exact immutable source-version backing' USING ERRCODE = 'check_violation'; END IF;
      END LOOP;
      FOR basis IN SELECT * FROM mod_billing.{prefix}opening_receivable_bases x WHERE x.posting_group_id = v_group_id{scope} LOOP
        IF EXISTS (SELECT 1 FROM jsonb_array_elements(basis.evidence::jsonb->'source'->'historical_discharges') d,
            jsonb_array_elements(d->'replay_bindings') b WHERE NOT EXISTS (
              SELECT 1 FROM mod_billing.{prefix}opening_historical_discharge_claims x WHERE x.operation = b->>'operation'
                AND x.source_owner = b->>'source_owner' AND x.source_ref = b->>'source_ref'
                AND x.evidence_digest = b->>'evidence_digest'{same})) THEN
          RAISE EXCEPTION 'opening history requires every permanent replay binding' USING ERRCODE = 'check_violation';
        END IF;
      END LOOP;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates
    for table in (
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "accounting_facts",
        "opening_import_receipts",
        "opening_cash_lots",
        "opening_cash_effects",
        *_TABLES,
        "withholding_discharges",
        "non_cash_credit_effects",
        "documents",
        "invoice_document_facts",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {prefix}{table}_opening_account_complete AFTER INSERT ON mod_billing.{prefix}{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}opening_account();"
        )


def _source_registry(platform: bool) -> None:
    prefix = "platform_" if platform else ""
    scope_columns = [] if platform else ["tenant_id"]
    scope = "" if platform else " AND x.tenant_id = NEW.tenant_id"
    scope_insert = "" if platform else "tenant_id, "
    scope_values = "" if platform else "NEW.tenant_id, "
    scope_select = "" if platform else "x.tenant_id, "
    table = f"{prefix}receivable_source_claims"
    owners = (
        ("historical_claim_id", "opening_historical_discharge_claims"),
        ("withholding_discharge_id", "withholding_discharges"),
        ("non_cash_grant_id", "non_cash_credit_grants"),
        ("non_cash_effect_id", "non_cash_credit_effects"),
        ("credit_document_fact_id", "invoice_document_facts"),
    )
    op.create_unique_constraint(
        f"uq_{prefix}opening_historical_discharge_claims_account",
        f"{prefix}opening_historical_discharge_claims",
        [*scope_columns, "id", "billing_account_id"],
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        f"uq_{prefix}withholding_discharges_account",
        f"{prefix}withholding_discharges",
        [*scope_columns, "id", "billing_account_id"],
        schema=_SCHEMA,
    )
    columns: list[SchemaItem] = [
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(
            sa.Column(
                key,
                sa.Uuid(),
                sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
                nullable=False,
            )
            for key in scope_columns
        ),
        sa.Column("billing_account_id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(80), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("key1", sa.String(512), nullable=False),
        sa.Column("key2", sa.String(255), nullable=False),
        *(sa.Column(column, sa.Uuid()) for column, _ in owners),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            *scope_columns,
            "namespace",
            "source_owner",
            "key1",
            "key2",
            name=f"uq_{table}_identity",
            deferrable=False,
        ),
        sa.CheckConstraint(
            " + ".join(
                f"(CASE WHEN {column} IS NULL THEN 0 ELSE 1 END)"
                for column, _ in owners
            )
            + " = 1",
            name=f"ck_{table}_owner",
        ),
        sa.ForeignKeyConstraint(
            [*scope_columns, "billing_account_id"],
            [
                f"{_SCHEMA}.{prefix}billing_accounts.{key}"
                for key in (*scope_columns, "id")
            ],
            name=f"fk_{table}_account",
            ondelete="RESTRICT",
        ),
        *(
            sa.ForeignKeyConstraint(
                [
                    *scope_columns,
                    column,
                    *(
                        ()
                        if parent == "invoice_document_facts"
                        else ("billing_account_id",)
                    ),
                ],
                [
                    f"{_SCHEMA}.{prefix}{parent}.{key}"
                    for key in (
                        *scope_columns,
                        "id",
                        *(
                            ()
                            if parent == "invoice_document_facts"
                            else ("billing_account_id",)
                        ),
                    )
                ],
                name=f"fk_{table}_{column}",
                ondelete="RESTRICT",
            )
            for column, parent in owners
        ),
    ]
    if platform:
        op.create_table(
            "platform_receivable_source_claims", *columns, schema="mod_billing"
        )
    else:
        op.create_table("receivable_source_claims", *columns, schema="mod_billing")
    if platform:
        op.execute(
            f"REVOKE ALL ON mod_billing.{table} FROM app_user; GRANT SELECT, INSERT ON mod_billing.{table} TO platform_api;"
        )
    else:
        op.execute(f"""
          ALTER TABLE mod_billing.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE mod_billing.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY {table}_tenant_isolation ON mod_billing.{table}
            USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
          GRANT SELECT, INSERT ON mod_billing.{table} TO app_user; REVOKE ALL ON mod_billing.{table} FROM platform_api;
        """)
    op.execute(f"""
    CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON mod_billing.{table}
      FOR EACH ROW EXECUTE FUNCTION mod_billing.refuse_mutation();
    CREATE FUNCTION mod_billing.admit_{prefix}receivable_source_claim() RETURNS trigger AS $$
    DECLARE owner_row record; doc record; grp record; v_namespace text; v_owner text; v_key1 text; v_key2 text; v_account uuid; v_n bigint;
    BEGIN
      IF NEW.historical_claim_id IS NOT NULL THEN
        SELECT * INTO owner_row FROM mod_billing.{prefix}opening_historical_discharge_claims x WHERE x.id = NEW.historical_claim_id{scope};
        v_namespace := (CASE owner_row.operation
          WHEN 'billing.withholding.accept' THEN 'withholding.source'
          WHEN 'billing.withholding.tax_component' THEN 'withholding.tax_component'
          WHEN 'billing.non_cash_credit.grant' THEN 'non_cash.grant'
          WHEN 'billing.non_cash_credit.apply' THEN 'non_cash.effect'
          WHEN 'billing.credit_note.issue' THEN 'credit_note.issue' ELSE NULL END);
        IF owner_row.operation = 'billing.credit_note.issue' AND owner_row.source_owner <> 'dotmac-billing' THEN
          RAISE EXCEPTION 'credit issue namespace has its actual fixed Billing owner' USING ERRCODE = 'check_violation';
        END IF;
        v_owner := owner_row.source_owner;
        v_key1 := (CASE WHEN owner_row.operation = 'billing.withholding.tax_component' THEN owner_row.determination_ref ELSE owner_row.source_ref END);
        v_key2 := (CASE WHEN owner_row.operation = 'billing.withholding.tax_component' THEN owner_row.component_ref ELSE '' END);
        v_account := owner_row.billing_account_id;
      ELSIF NEW.withholding_discharge_id IS NOT NULL THEN
        SELECT * INTO owner_row FROM mod_billing.{prefix}withholding_discharges x WHERE x.id = NEW.withholding_discharge_id{scope};
        IF NEW.namespace = 'withholding.source' THEN
          v_namespace := 'withholding.source'; v_owner := owner_row.source_owner; v_key1 := owner_row.source_ref; v_key2 := '';
        ELSIF NEW.namespace = 'withholding.tax_component' AND owner_row.reverses_discharge_id IS NULL THEN
          v_namespace := 'withholding.tax_component'; v_owner := owner_row.tax_source_owner; v_key1 := owner_row.determination_ref; v_key2 := owner_row.component_ref;
        END IF;
        v_account := owner_row.billing_account_id;
      ELSIF NEW.non_cash_grant_id IS NOT NULL THEN
        SELECT * INTO owner_row FROM mod_billing.{prefix}non_cash_credit_grants x WHERE x.id = NEW.non_cash_grant_id{scope};
        v_namespace := 'non_cash.grant'; v_owner := owner_row.source_owner; v_key1 := owner_row.source_ref; v_key2 := ''; v_account := owner_row.billing_account_id;
      ELSIF NEW.non_cash_effect_id IS NOT NULL THEN
        SELECT * INTO owner_row FROM mod_billing.{prefix}non_cash_credit_effects x WHERE x.id = NEW.non_cash_effect_id{scope};
        v_namespace := 'non_cash.effect'; v_owner := owner_row.source_owner; v_key1 := owner_row.source_ref; v_key2 := ''; v_account := owner_row.billing_account_id;
      ELSE
        SELECT * INTO owner_row FROM mod_billing.{prefix}invoice_document_facts x WHERE x.id = NEW.credit_document_fact_id{scope};
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = owner_row.document_id{scope};
        SELECT count(*) INTO v_n FROM mod_billing.{prefix}posting_groups x
          WHERE x.source_ref = 'credit_note\\:' || doc.id::text{scope};
        IF v_n <> 1 THEN RAISE EXCEPTION 'credit issuance needs one genuine canonical root' USING ERRCODE = 'check_violation'; END IF;
        SELECT * INTO grp FROM mod_billing.{prefix}posting_groups x
          WHERE x.source_ref = 'credit_note\\:' || doc.id::text{scope};
        IF owner_row.fact_version <> 1 OR doc.document_kind <> 'credit_note' OR doc.issued_at IS NULL
          OR owner_row.fact_payload::jsonb->>'document_kind' IS DISTINCT FROM 'credit_note'
          OR owner_row.fact_payload::jsonb->>'document_state' IS DISTINCT FROM 'issued'
          OR jsonb_typeof(owner_row.fact_payload::jsonb->'correlation_id') IS DISTINCT FROM 'string'
          OR (owner_row.fact_payload::jsonb->'grand_total'->>'amount')::numeric IS DISTINCT FROM doc.grand_total
          OR owner_row.fact_payload::jsonb->'grand_total'->>'currency' IS DISTINCT FROM doc.currency
          OR (owner_row.fact_payload::jsonb->'grand_total'->>'minor_units')::integer IS DISTINCT FROM doc.minor_units
          OR (owner_row.fact_payload::jsonb->>'issued_at')::timestamptz IS DISTINCT FROM doc.issued_at
          OR owner_row.fact_payload::jsonb->>'invoice_id' IS DISTINCT FROM doc.id::text
          OR grp.id IS NULL OR grp.group_kind <> 'credit_note_issued' OR grp.reverses_group_id IS NOT NULL
          OR grp.billing_account_id <> doc.billing_account_id OR grp.currency <> doc.currency OR grp.minor_units <> doc.minor_units
          OR (SELECT count(*) FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = grp.id{scope}) <> 1
          OR NOT EXISTS (SELECT 1 FROM mod_billing.{prefix}posting_effects x WHERE x.posting_group_id = grp.id{scope}
              AND x.lane = 'receivable' AND x.amount_delta = -doc.grand_total AND x.billing_account_id = doc.billing_account_id
              AND x.currency = doc.currency AND x.minor_units = doc.minor_units)
        THEN RAISE EXCEPTION 'credit issuance source identity requires genuine canonical issued credit evidence' USING ERRCODE = 'check_violation'; END IF;
        v_namespace := 'credit_note.issue'; v_owner := 'dotmac-billing'; v_key1 := owner_row.fact_payload::jsonb->>'correlation_id'; v_key2 := ''; v_account := doc.billing_account_id;
      END IF;
      IF v_namespace IS NULL OR nullif(trim(v_owner),'') IS NULL OR nullif(trim(v_key1),'') IS NULL OR v_key2 IS NULL OR v_account IS NULL
        OR NEW.namespace IS DISTINCT FROM v_namespace OR NEW.source_owner IS DISTINCT FROM v_owner
        OR NEW.key1 IS DISTINCT FROM v_key1 OR NEW.key2 IS DISTINCT FROM v_key2 OR NEW.billing_account_id IS DISTINCT FROM v_account
        OR (v_namespace IN ('withholding.source','non_cash.grant','non_cash.effect') AND length(v_key1) > 255)
        OR (v_namespace = 'withholding.tax_component' AND (length(v_key1) > 255 OR nullif(trim(v_key2),'') IS NULL))
      THEN RAISE EXCEPTION 'receivable source claim requires exact raw identity and genuine scoped owner' USING ERRCODE = 'check_violation'; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER {table}_admission BEFORE INSERT ON mod_billing.{table}
      FOR EACH ROW EXECUTE FUNCTION mod_billing.admit_{prefix}receivable_source_claim();
    CREATE FUNCTION mod_billing.claim_{prefix}receivable_source() RETURNS trigger AS $$
    DECLARE v_namespace text; v_owner text; v_key1 text; v_key2 text; v_account uuid; doc record;
    BEGIN
      IF TG_TABLE_NAME = '{prefix}withholding_discharges' THEN
        INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, withholding_discharge_id)
          VALUES (gen_random_uuid(), {scope_values}NEW.billing_account_id, 'withholding.source', NEW.source_owner, NEW.source_ref, '', NEW.id);
        IF NEW.reverses_discharge_id IS NULL THEN
          INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, withholding_discharge_id)
            VALUES (gen_random_uuid(), {scope_values}NEW.billing_account_id, 'withholding.tax_component', NEW.tax_source_owner, NEW.determination_ref, NEW.component_ref, NEW.id);
        END IF;
      ELSIF TG_TABLE_NAME = '{prefix}non_cash_credit_grants' THEN
        INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, non_cash_grant_id)
          VALUES (gen_random_uuid(), {scope_values}NEW.billing_account_id, 'non_cash.grant', NEW.source_owner, NEW.source_ref, '', NEW.id);
      ELSIF TG_TABLE_NAME = '{prefix}non_cash_credit_effects' THEN
        INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, non_cash_effect_id)
          VALUES (gen_random_uuid(), {scope_values}NEW.billing_account_id, 'non_cash.effect', NEW.source_owner, NEW.source_ref, '', NEW.id);
      ELSIF TG_TABLE_NAME = '{prefix}invoice_document_facts' THEN
        SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
        IF doc.document_kind = 'credit_note' AND NEW.fact_version = 1 AND NEW.fact_payload::jsonb->>'document_state' = 'issued' THEN
          INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, credit_document_fact_id)
            VALUES (gen_random_uuid(), {scope_values}doc.billing_account_id, 'credit_note.issue', 'dotmac-billing', NEW.fact_payload::jsonb->>'correlation_id', '', NEW.id);
        END IF;
      ELSE
        v_namespace := (CASE NEW.operation
          WHEN 'billing.withholding.accept' THEN 'withholding.source'
          WHEN 'billing.withholding.tax_component' THEN 'withholding.tax_component'
          WHEN 'billing.non_cash_credit.grant' THEN 'non_cash.grant'
          WHEN 'billing.non_cash_credit.apply' THEN 'non_cash.effect'
          WHEN 'billing.credit_note.issue' THEN 'credit_note.issue' ELSE NULL END);
        v_key1 := (CASE WHEN NEW.operation = 'billing.withholding.tax_component' THEN NEW.determination_ref ELSE NEW.source_ref END);
        v_key2 := (CASE WHEN NEW.operation = 'billing.withholding.tax_component' THEN NEW.component_ref ELSE '' END);
        INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, historical_claim_id)
          VALUES (gen_random_uuid(), {scope_values}NEW.billing_account_id, v_namespace, NEW.source_owner, v_key1, v_key2, NEW.id);
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates
    for source in (
        "withholding_discharges",
        "non_cash_credit_grants",
        "non_cash_credit_effects",
        "invoice_document_facts",
        "opening_historical_discharge_claims",
    ):
        op.execute(
            f"CREATE TRIGGER {prefix}{source}_source_occupancy AFTER INSERT ON mod_billing.{prefix}{source} FOR EACH ROW EXECUTE FUNCTION mod_billing.claim_{prefix}receivable_source();"
        )
    # Backfill provenance only under the same migration's writer-blocking locks.
    # The immediate unique index refuses ambiguous pre-existing source identity.
    op.execute(f"""
      INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, withholding_discharge_id)
        SELECT gen_random_uuid(), {scope_select}x.billing_account_id, 'withholding.source', x.source_owner, x.source_ref, '', x.id FROM mod_billing.{prefix}withholding_discharges x;
      INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, withholding_discharge_id)
        SELECT gen_random_uuid(), {scope_select}x.billing_account_id, 'withholding.tax_component', x.tax_source_owner, x.determination_ref, x.component_ref, x.id FROM mod_billing.{prefix}withholding_discharges x WHERE x.reverses_discharge_id IS NULL;
      INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, non_cash_grant_id)
        SELECT gen_random_uuid(), {scope_select}x.billing_account_id, 'non_cash.grant', x.source_owner, x.source_ref, '', x.id FROM mod_billing.{prefix}non_cash_credit_grants x;
      INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, non_cash_effect_id)
        SELECT gen_random_uuid(), {scope_select}x.billing_account_id, 'non_cash.effect', x.source_owner, x.source_ref, '', x.id FROM mod_billing.{prefix}non_cash_credit_effects x;
      INSERT INTO mod_billing.{table} (id, {scope_insert}billing_account_id, namespace, source_owner, key1, key2, credit_document_fact_id)
        SELECT gen_random_uuid(), {scope_select}d.billing_account_id, 'credit_note.issue', 'dotmac-billing', x.fact_payload::jsonb->>'correlation_id', '', x.id
        FROM mod_billing.{prefix}invoice_document_facts x JOIN mod_billing.{prefix}documents d ON d.id = x.document_id {'' if platform else 'AND d.tenant_id = x.tenant_id'}
        WHERE d.document_kind = 'credit_note' AND x.fact_version = 1 AND x.fact_payload::jsonb->>'document_state' = 'issued';
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates

    op.execute(f"""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM mod_billing.{prefix}documents d WHERE d.document_kind = 'credit_note' AND d.issued_at IS NOT NULL
            AND (SELECT count(*) FROM mod_billing.{prefix}invoice_document_facts x WHERE x.document_id = d.id AND x.fact_version = 1
              AND x.fact_payload::jsonb->>'document_kind' = 'credit_note' AND x.fact_payload::jsonb->>'document_state' = 'issued'
              {'' if platform else 'AND x.tenant_id = d.tenant_id'}) <> 1) THEN
          RAISE EXCEPTION 'bi8 cannot backfill ambiguous or missing original credit issuance provenance' USING ERRCODE = 'check_violation';
        END IF;
      END $$;
      CREATE FUNCTION mod_billing.check_{prefix}credit_issue_claim() RETURNS trigger AS $$
      DECLARE v_document_id uuid; doc record; v_n bigint;
      BEGIN
        IF TG_TABLE_NAME = '{prefix}documents' THEN
          IF NEW.document_kind <> 'credit_note' OR NEW.issued_at IS NULL THEN RETURN NEW; END IF;
          v_document_id := NEW.id;
        ELSE
          IF NEW.group_kind <> 'credit_note_issued' THEN RETURN NEW; END IF;
          SELECT x.id INTO v_document_id FROM mod_billing.{prefix}documents x WHERE x.document_kind = 'credit_note' AND x.issued_at IS NOT NULL
            AND NEW.source_ref = 'credit_note\\:' || x.id::text{scope};
        END IF;
        IF v_document_id IS NULL THEN RAISE EXCEPTION 'credit issuance requires its actual document and source fact' USING ERRCODE = 'check_violation'; END IF;
        SELECT count(*) INTO v_n FROM mod_billing.{prefix}invoice_document_facts x
          JOIN mod_billing.{table} c ON c.credit_document_fact_id = x.id {'' if platform else 'AND c.tenant_id = x.tenant_id'}
          WHERE x.document_id = v_document_id AND x.fact_version = 1 AND x.fact_payload::jsonb->>'document_state' = 'issued'
            AND c.namespace = 'credit_note.issue' AND c.source_owner = 'dotmac-billing'
            AND c.key1 = x.fact_payload::jsonb->>'correlation_id' AND c.key2 = ''{scope};
        IF v_n <> 1 THEN RAISE EXCEPTION 'issued credit requires its one permanent physical source claim' USING ERRCODE = 'check_violation'; END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
      CREATE CONSTRAINT TRIGGER {prefix}documents_credit_source_complete AFTER INSERT ON mod_billing.{prefix}documents
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}credit_issue_claim();
      CREATE CONSTRAINT TRIGGER {prefix}posting_groups_credit_source_complete AFTER INSERT ON mod_billing.{prefix}posting_groups
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}credit_issue_claim();
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates

    op.execute(f"""
      CREATE FUNCTION mod_billing.check_{prefix}receivable_source_occupancy() RETURNS trigger AS $$
      DECLARE v_n bigint; v_namespace text; v_key1 text; v_key2 text; doc record;
      BEGIN
        IF TG_TABLE_NAME = '{prefix}withholding_discharges' THEN
          SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.withholding_discharge_id = NEW.id
            AND x.billing_account_id = NEW.billing_account_id AND x.namespace = 'withholding.source'
            AND x.source_owner = NEW.source_owner AND x.key1 = NEW.source_ref AND x.key2 = ''{scope};
          IF v_n <> 1 THEN RAISE EXCEPTION 'withholding requires its exact permanent source occupancy' USING ERRCODE = 'check_violation'; END IF;
          IF NEW.reverses_discharge_id IS NULL THEN
            SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.withholding_discharge_id = NEW.id
              AND x.billing_account_id = NEW.billing_account_id AND x.namespace = 'withholding.tax_component'
              AND x.source_owner = NEW.tax_source_owner AND x.key1 = NEW.determination_ref AND x.key2 = NEW.component_ref{scope};
            IF v_n <> 1 THEN RAISE EXCEPTION 'Tax component requires its exact permanent source occupancy' USING ERRCODE = 'check_violation'; END IF;
          END IF;
          RETURN NEW;
        ELSIF TG_TABLE_NAME = '{prefix}non_cash_credit_grants' THEN
          SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.non_cash_grant_id = NEW.id
            AND x.billing_account_id = NEW.billing_account_id AND x.namespace = 'non_cash.grant'
            AND x.source_owner = NEW.source_owner AND x.key1 = NEW.source_ref AND x.key2 = ''{scope};
        ELSIF TG_TABLE_NAME = '{prefix}non_cash_credit_effects' THEN
          SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.non_cash_effect_id = NEW.id
            AND x.billing_account_id = NEW.billing_account_id AND x.namespace = 'non_cash.effect'
            AND x.source_owner = NEW.source_owner AND x.key1 = NEW.source_ref AND x.key2 = ''{scope};
        ELSIF TG_TABLE_NAME = '{prefix}invoice_document_facts' THEN
          SELECT * INTO doc FROM mod_billing.{prefix}documents x WHERE x.id = NEW.document_id{scope};
          IF doc.document_kind <> 'credit_note' OR NEW.fact_version <> 1 OR NEW.fact_payload::jsonb->>'document_state' IS DISTINCT FROM 'issued' THEN RETURN NEW; END IF;
          SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.credit_document_fact_id = NEW.id
            AND x.billing_account_id = doc.billing_account_id AND x.namespace = 'credit_note.issue'
            AND x.source_owner = 'dotmac-billing' AND x.key1 = NEW.fact_payload::jsonb->>'correlation_id' AND x.key2 = ''{scope};
        ELSE
          v_namespace := (CASE NEW.operation
            WHEN 'billing.withholding.accept' THEN 'withholding.source'
            WHEN 'billing.withholding.tax_component' THEN 'withholding.tax_component'
            WHEN 'billing.non_cash_credit.grant' THEN 'non_cash.grant'
            WHEN 'billing.non_cash_credit.apply' THEN 'non_cash.effect'
            WHEN 'billing.credit_note.issue' THEN 'credit_note.issue' ELSE NULL END);
          v_key1 := (CASE WHEN NEW.operation = 'billing.withholding.tax_component' THEN NEW.determination_ref ELSE NEW.source_ref END);
          v_key2 := (CASE WHEN NEW.operation = 'billing.withholding.tax_component' THEN NEW.component_ref ELSE '' END);
          SELECT count(*) INTO v_n FROM mod_billing.{table} x WHERE x.historical_claim_id = NEW.id
            AND x.billing_account_id = NEW.billing_account_id AND x.namespace = v_namespace
            AND x.source_owner = NEW.source_owner AND x.key1 = v_key1 AND x.key2 = v_key2{scope};
        END IF;
        IF v_n <> 1 THEN RAISE EXCEPTION 'financial owner requires its exact permanent physical source occupancy' USING ERRCODE = 'check_violation'; END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
    """)  # nosec B608 -- closed plane identifiers and fixed owner predicates
    for source in (
        "withholding_discharges",
        "non_cash_credit_grants",
        "non_cash_credit_effects",
        "invoice_document_facts",
        "opening_historical_discharge_claims",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {prefix}{source}_source_occupancy_complete AFTER INSERT ON mod_billing.{prefix}{source} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mod_billing.check_{prefix}receivable_source_occupancy();"
        )


def upgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(sa.text("SHOW transaction_isolation")).scalar_one()
        != "read committed"
    ):
        raise RuntimeError(
            "Billing bi8 provenance backfill requires a fresh READ COMMITTED migration runner; no isolation change is performed"
        )
    planes = selected_module_planes("billing")
    prefixes = [
        "platform_" if plane is ModulePlane.PLATFORM else ""
        for plane in (ModulePlane.TENANT, ModulePlane.PLATFORM)
        if plane in planes
    ]
    connection = op.get_bind()
    previous = {
        name: connection.execute(sa.text(f"SHOW {name}")).scalar_one()
        for name in ("lock_timeout", "statement_timeout", "row_security")
    }
    names = (
        "billing_accounts",
        "posting_groups",
        "posting_effects",
        "allocation_effects",
        "accounting_facts",
        "documents",
        "document_events",
        "invoice_document_facts",
        "withholding_discharges",
        "non_cash_credit_grants",
        "non_cash_credit_effects",
        "opening_import_receipts",
        "opening_cash_lots",
        "opening_cash_effects",
        "cash_source_claims",
    )
    tables = [f"{prefix}{name}" for prefix in prefixes for name in names]
    if tables:
        op.execute(
            f"SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '60s'; LOCK TABLE mod_billing.{', mod_billing.'.join(tables)} IN ACCESS EXCLUSIVE MODE; SET LOCAL row_security = off;"
        )
    for platform in (False, True):
        if (ModulePlane.PLATFORM if platform else ModulePlane.TENANT) not in planes:
            continue
        _create_tables(platform)
        _source_registry(platform)
        _admission(platform)
        _completeness(platform)
        _historical_invoice_room(platform)
        _backing_guards(platform)
        _successor_admission(platform)
        _exposure_admission(platform)
        _successor_completeness(platform)
    for name, value in previous.items():
        connection.execute(
            sa.text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def downgrade() -> None:
    raise RuntimeError(
        "opening receivable identities and immutable facts are permanent; downgrade is not supported"
    )
