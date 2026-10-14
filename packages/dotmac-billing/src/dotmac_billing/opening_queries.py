"""Scoped immutable opening reads, qualified against original JSON and scalars."""

from __future__ import annotations

from collections import Counter
from decimal import DecimalException, localcontext
from typing import Any, TypeVar, cast
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing._document_facts import _decode
from dotmac_billing._opening_values import (
    cash_decimal_context,
    minor_units_of,
    money_from_minor,
)
from dotmac_billing.engine import Effect, EffectLane, rebuild_position
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_authority import (
    OpeningCashRefundCorrectionVerificationV1,
    OpeningCashRefundVerificationV1,
    OpeningImportReversalVerificationV1,
    OpeningImportVerificationV1,
)
from dotmac_billing.opening_cash_contracts import (
    AccountingFactV4,
    OpeningCashEffectV1,
    OpeningCashLotFactV1,
    OpeningCashLotPositionV1,
    OpeningImportReceiptV1,
)
from dotmac_billing.opening_contracts import OpeningTargetV1
from dotmac_billing.opening_receivable_contracts import OpeningImportReceiptV2
from dotmac_billing.opening_validation import opening_manifest_fingerprint

_T = TypeVar("_T")


def _table(scope: Scope, name: str) -> Any:
    if not isinstance(scope, TenantScope | PlatformScope):
        raise BillingRuleViolation(
            "scope_mismatch", "opening reads require explicit scope"
        )
    prefix = "platform_" if isinstance(scope, PlatformScope) else ""
    return models.Base.metadata.tables[f"{models.SCHEMA}.{prefix}{name}"]


def _scoped(statement: Any, scope: Scope, table: Any) -> Any:
    return (
        statement.where(table.c.tenant_id == scope.tenant_id)
        if isinstance(scope, TenantScope)
        else statement
    )


def _row(db: Session, scope: Scope, name: str, row_id: UUID) -> Any:
    table = _table(scope, name)
    with db.no_autoflush:
        row = (
            db.execute(_scoped(select(table).where(table.c.id == row_id), scope, table))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise BillingRuleViolation(
            "not_found", "immutable opening evidence was not found"
        )
    return row


def _decode_row(
    row: Any,
    schema: type[_T],
    *,
    payload: str = "evidence",
    digest: str = "evidence_digest",
) -> _T:
    try:
        if fingerprint_of(row[payload]) != row[digest]:
            raise ValueError("original opening digest disagrees")
        with localcontext(cash_decimal_context()):
            return cast(_T, _decode(row[payload], schema))
    except (ValueError, TypeError, DecimalException) as exc:
        raise BillingRuleViolation(
            "opening_evidence_integrity", "stored opening evidence is invalid"
        ) from exc


def _require(condition: bool) -> None:
    if not condition:
        raise BillingRuleViolation(
            "opening_evidence_integrity",
            "stored opening envelope disagrees with immutable evidence",
        )


def _group(
    db: Session,
    scope: Scope,
    group_id: UUID,
    account_id: UUID,
    currency: str,
    precision: int,
) -> Any:
    row = _row(db, scope, "posting_groups", group_id)
    _require(
        (row["billing_account_id"], row["currency"], row["minor_units"])
        == (account_id, currency, precision)
    )
    return row


def get_opening_import_receipt(
    db: Session, *, scope: Scope, receipt_id: UUID
) -> OpeningImportReceiptV1:
    row = _row(db, scope, "opening_import_receipts", receipt_id)
    if isinstance(row["evidence"], dict) and "total_receivable" in row["evidence"]:
        raise BillingRuleViolation(
            "opening_receipt_version_unsupported",
            "complete account receipt requires the explicit V2 query",
        )
    fact = _decode_row(row, OpeningImportReceiptV1)
    _require(
        fact.scope == scope
        and fact.receipt_id == row["id"]
        and fact.billing_account_id == row["billing_account_id"]
        and fact.receipt_kind == row["receipt_kind"]
        and fact.original_receipt_id == row["original_receipt_id"]
        and fact.manifest_fingerprint == row["manifest_fingerprint"]
        and opening_manifest_fingerprint(fact.manifest) == fact.manifest_fingerprint
        and fact.posting_group_id == row["posting_group_id"]
        and fact.request_identity == row["request_identity"]
        and fact.request_digest == row["request_digest"]
        and fact.verification.command_digest == fact.request_digest
        and fact.total_residual.amount == row["total_residual"]
        and fact.total_residual.currency.code == row["currency"]
        and fact.total_residual.currency.minor_units == row["minor_units"]
        and fact.target.expected_position_fingerprint
        == row["expected_target_fingerprint"]
        and fact.target.posting_watermark == row["expected_target_watermark"]
        and fact.verification.migration_authority_ref == row["migration_authority_ref"]
        and fact.recorded_at == row["recorded_at"]
    )
    group = _group(
        db,
        scope,
        fact.posting_group_id,
        fact.billing_account_id,
        row["currency"],
        row["minor_units"],
    )
    _require(
        group["source_ref"] == fact.request_identity
        and group["occurred_at"] == fact.occurred_at
    )
    _require(
        fact.manifest.scope == scope
        and fact.target.scope == scope
        and fact.target.account_id == fact.billing_account_id
        and (fact.target.currency, fact.target.precision)
        == (row["currency"], row["minor_units"])
        and fact.partition.account_id == fact.billing_account_id
        and (fact.partition.currency, fact.partition.precision)
        == (row["currency"], row["minor_units"])
    )
    verification = fact.verification
    if fact.receipt_kind == "import":
        if type(verification) is not OpeningImportVerificationV1:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "import proof has wrong operation type"
            )
        _require(
            type(verification) is OpeningImportVerificationV1
            and verification.scope == scope
            and verification.manifest_fingerprint == fact.manifest_fingerprint
            and verification.partition == fact.partition
            and verification.target == fact.target
            and verification.installation_id == fact.manifest.capture.installation_id
            and verification.source_authority_ref
            == fact.manifest.capture.source_authority_ref
            and fact.original_receipt_id is None
            and group["group_kind"] == "opening_cash_imported"
        )
    else:
        if type(verification) is not OpeningImportReversalVerificationV1:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "unwind proof has wrong operation type"
            )
        _require(
            type(verification) is OpeningImportReversalVerificationV1
            and verification.scope == scope
            and fact.original_receipt_id is not None
            and fact.original_receipt_id != fact.receipt_id
            and verification.receipt_id == fact.original_receipt_id
            and verification.billing_account_id == fact.billing_account_id
            and (verification.currency, verification.precision)
            == (row["currency"], row["minor_units"])
            and group["group_kind"] == "opening_cash_import_reversed"
        )
        original_row = _row(
            db, scope, "opening_import_receipts", cast(UUID, fact.original_receipt_id)
        )
        original = _decode_row(original_row, OpeningImportReceiptV1)
        _require(
            original.receipt_kind == "import"
            and original.original_receipt_id is None
            and verification.receipt_evidence_digest == original_row["evidence_digest"]
            and verification.original_posting_group_id == original.posting_group_id
            and original.billing_account_id == fact.billing_account_id
            and original.manifest == fact.manifest
            and original.partition == fact.partition
            and original.target == fact.target
            and original.total_residual == fact.total_residual
        )
    lots = _table(scope, "opening_cash_lots")
    with db.no_autoflush:
        ids = tuple(
            db.execute(
                _scoped(
                    select(lots.c.id)
                    .where(
                        lots.c.receipt_id
                        == (fact.original_receipt_id or fact.receipt_id)
                    )
                    .order_by(lots.c.sequence),
                    scope,
                    lots,
                )
            ).scalars()
        )
    _require(ids == fact.lot_ids)
    return fact


def _opening_cash_receipt(
    db: Session, *, scope: Scope, receipt_id: UUID
) -> OpeningImportReceiptV1 | OpeningImportReceiptV2:
    row = _row(db, scope, "opening_import_receipts", receipt_id)
    if isinstance(row["evidence"], dict) and "total_receivable" in row["evidence"]:
        from dotmac_billing.opening_receivable_queries import (
            get_opening_import_receipt_v2,
        )

        return get_opening_import_receipt_v2(db, scope=scope, receipt_id=receipt_id)
    return get_opening_import_receipt(db, scope=scope, receipt_id=receipt_id)


def get_opening_cash_lot(
    db: Session, *, scope: Scope, lot_id: UUID
) -> OpeningCashLotFactV1:
    row = _row(db, scope, "opening_cash_lots", lot_id)
    fact = _decode_row(row, OpeningCashLotFactV1)
    source = fact.source
    _require(
        fact.scope == scope
        and fact.lot_id == row["id"]
        and fact.receipt_id == row["receipt_id"]
        and fact.billing_account_id == row["billing_account_id"]
        and fact.posting_group_id == row["posting_group_id"]
        and fact.sequence == row["sequence"]
        and source.source_system == row["source_system"]
        and source.source_settlement_key == row["source_settlement_key"]
        and fact.initial_amount.amount == row["residual_amount"]
        and minor_units_of(fact.initial_amount) == source.residual_minor
        and fact.initial_amount.currency.code == row["currency"]
        and fact.initial_amount.currency.minor_units == row["minor_units"]
        and source.partition.account_id == fact.billing_account_id
        and (source.partition.currency, source.partition.precision)
        == (row["currency"], row["minor_units"])
        and fact.recorded_at == row["recorded_at"]
    )
    _require(
        money_from_minor(source.gross_minor, row["currency"], row["minor_units"]).amount
        == row["gross_amount"]
        and money_from_minor(
            sum(item.amount_minor for item in source.consumptions),
            row["currency"],
            row["minor_units"],
        ).amount
        == row["historical_consumed"]
    )
    receipt = _opening_cash_receipt(db, scope=scope, receipt_id=fact.receipt_id)
    _require(
        receipt.receipt_kind == "import"
        and fact.lot_id in receipt.lot_ids
        and receipt.posting_group_id == fact.posting_group_id
        and source in receipt.manifest.cash_lots
    )
    claims = _table(scope, "cash_source_claims")
    with db.no_autoflush:
        claim = (
            db.execute(
                _scoped(
                    select(claims).where(claims.c.opening_cash_lot_id == lot_id),
                    scope,
                    claims,
                )
            )
            .mappings()
            .one_or_none()
        )
    _require(
        claim is not None
        and claim["settlement_id"] is None
        and claim["billing_account_id"] == fact.billing_account_id
        and claim["source_system"] == source.source_system
        and claim["source_settlement_key"] == source.source_settlement_key
        and (claim["currency"], claim["minor_units"])
        == (row["currency"], row["minor_units"])
    )
    return fact


def get_opening_cash_effect(
    db: Session, *, scope: Scope, effect_id: UUID
) -> OpeningCashEffectV1:
    row = _row(db, scope, "opening_cash_effects", effect_id)
    fact = _decode_row(row, OpeningCashEffectV1)
    _require(
        fact.scope == scope
        and fact.effect_id == row["id"]
        and fact.lot_id == row["lot_id"]
        and fact.billing_account_id == row["billing_account_id"]
        and fact.posting_group_id == row["posting_group_id"]
        and fact.sequence == row["sequence"]
        and fact.effect_kind == row["effect_kind"]
        and fact.availability_delta.amount == row["amount_delta"]
        and fact.amount.currency.code == row["currency"]
        and fact.amount.currency.minor_units == row["minor_units"]
        and fact.document_id == row["document_id"]
        and fact.original_effect_id == row["original_effect_id"]
        and fact.source_ref == row["source_ref"]
        and fact.authorization_ref == row["authorization_ref"]
        and fact.request_digest == row["request_digest"]
        and fact.recorded_at == row["recorded_at"]
    )
    lot = get_opening_cash_lot(db, scope=scope, lot_id=fact.lot_id)
    _require(
        lot.billing_account_id == fact.billing_account_id
        and lot.initial_amount.currency == fact.amount.currency
    )
    group = _group(
        db,
        scope,
        fact.posting_group_id,
        fact.billing_account_id,
        row["currency"],
        row["minor_units"],
    )
    _require(
        group["source_ref"] == fact.source_ref
        and group["occurred_at"] == fact.occurred_at
    )
    verification = fact.movement_verification
    if fact.effect_kind in {"refund", "refund_correction"}:
        _require(verification is not None and fact.movement_evidence is not None)
        lot_row = _row(db, scope, "opening_cash_lots", lot.lot_id)
        if verification is None:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "refund proof is missing"
            )
        subject = verification.subject
        _require(
            verification.command_digest == fact.request_digest
            and subject.scope == scope
            and subject.billing_account_id == fact.billing_account_id
            and subject.lot_id == fact.lot_id
            and subject.lot_evidence_digest == lot_row["evidence_digest"]
            and (subject.currency, subject.precision)
            == (row["currency"], row["minor_units"])
            and subject.source_system == lot.source.source_system
            and subject.source_settlement_key == lot.source.source_settlement_key
        )
        if fact.effect_kind == "refund":
            _require(
                type(verification) is OpeningCashRefundVerificationV1
                and group["group_kind"] == "opening_cash_refunded"
            )
        else:
            if type(verification) is not OpeningCashRefundCorrectionVerificationV1:
                raise BillingRuleViolation(
                    "opening_evidence_integrity",
                    "refund correction proof has wrong operation type",
                )
            _require(
                type(verification) is OpeningCashRefundCorrectionVerificationV1
                and fact.original_effect_id is not None
                and fact.original_effect_id != fact.effect_id
                and verification.refund_effect_id == fact.original_effect_id
                and group["group_kind"] == "opening_cash_refund_corrected"
            )
            original_row = _row(
                db, scope, "opening_cash_effects", cast(UUID, fact.original_effect_id)
            )
            original = _decode_row(original_row, OpeningCashEffectV1)
            _require(
                original.effect_kind == "refund"
                and original.lot_id == fact.lot_id
                and original.amount == fact.amount
                and original.movement_evidence is not None
                and type(original.movement_verification)
                is OpeningCashRefundVerificationV1
                and verification.refund_evidence_digest
                == original_row["evidence_digest"]
            )
            if (
                original.movement_evidence is not None
                and type(original.movement_verification)
                is OpeningCashRefundVerificationV1
                and fact.movement_evidence is not None
            ):
                _require(
                    fact.movement_evidence.digest != original.movement_evidence.digest
                    and verification.correction_outcome_proof_ref
                    != original.movement_verification.outcome_proof_ref
                )
    else:
        _require(fact.movement_evidence is None and verification is None)
    return fact


def get_opening_cash_lot_position(
    db: Session, *, scope: Scope, lot_id: UUID
) -> OpeningCashLotPositionV1:
    lot = get_opening_cash_lot(db, scope=scope, lot_id=lot_id)
    effects = _table(scope, "opening_cash_effects")
    with db.no_autoflush:
        ids = tuple(
            db.execute(
                _scoped(
                    select(effects.c.id)
                    .where(effects.c.lot_id == lot_id)
                    .order_by(effects.c.sequence, effects.c.id),
                    scope,
                    effects,
                )
            ).scalars()
        )
    facts = tuple(
        get_opening_cash_effect(db, scope=scope, effect_id=value) for value in ids
    )
    recognition = tuple(item for item in facts if item.effect_kind == "recognition")
    remaining = sum(minor_units_of(item.availability_delta) for item in facts)
    _require(
        len(recognition) == 1
        and recognition[0].amount == lot.initial_amount
        and 0 <= remaining <= minor_units_of(lot.initial_amount)
    )
    groups = _table(scope, "posting_groups")
    with db.no_autoflush:
        latest_posting_group_id = db.execute(
            _scoped(
                select(groups.c.id)
                .where(groups.c.billing_account_id == lot.billing_account_id)
                .order_by(groups.c.source_version.desc())
                .limit(1),
                scope,
                groups,
            )
        ).scalar_one_or_none()
    receipts = _table(scope, "opening_import_receipts")
    with db.no_autoflush:
        unwound = (
            db.execute(
                _scoped(
                    select(receipts.c.id).where(
                        receipts.c.original_receipt_id == lot.receipt_id,
                        receipts.c.receipt_kind == "unwind",
                    ),
                    scope,
                    receipts,
                )
            ).first()
            is not None
        )
    return OpeningCashLotPositionV1(
        lot,
        money_from_minor(
            remaining,
            lot.initial_amount.currency.code,
            lot.initial_amount.currency.minor_units,
        ),
        latest_posting_group_id,
        unwound,
    )


def get_opening_cash_target(
    db: Session, *, scope: Scope, billing_account_id: UUID
) -> OpeningTargetV1:
    account = _row(db, scope, "billing_accounts", billing_account_id)
    effects = _table(scope, "posting_effects")
    groups = _table(scope, "posting_groups")
    with db.no_autoflush:
        rows = (
            db.execute(
                _scoped(
                    select(effects).where(
                        effects.c.billing_account_id == billing_account_id
                    ),
                    scope,
                    effects,
                )
            )
            .mappings()
            .all()
        )
        latest_posting_group_id = db.execute(
            _scoped(
                select(groups.c.id)
                .where(groups.c.billing_account_id == billing_account_id)
                .order_by(groups.c.source_version.desc())
                .limit(1),
                scope,
                groups,
            )
        ).scalar_one_or_none()
    with localcontext(cash_decimal_context()):
        state = rebuild_position(
            (
                Effect(
                    EffectLane(row["lane"]),
                    row["amount_delta"],
                    row["currency"],
                    row["minor_units"],
                )
                for row in rows
            ),
            currency=account["currency"],
            minor_units=account["minor_units"],
        )
    return OpeningTargetV1(
        scope,
        billing_account_id,
        account["currency"],
        account["minor_units"],
        state.state_fingerprint,
        "none" if latest_posting_group_id is None else str(latest_posting_group_id),
    )


def get_accounting_fact_v4(
    db: Session, *, scope: Scope, posting_group_id: UUID
) -> AccountingFactV4:
    table = _table(scope, "accounting_facts")
    with db.no_autoflush:
        row = (
            db.execute(
                _scoped(
                    select(table).where(
                        table.c.posting_group_id == posting_group_id,
                        table.c.fact_version == 4,
                    ),
                    scope,
                    table,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise BillingRuleViolation(
            "not_found", "exact V4 accounting fact was not found"
        )
    fact = _decode_row(
        row, AccountingFactV4, payload="fact_payload", digest="fact_digest"
    )
    _require(
        fact.scope == scope
        and fact.fact_id == row["id"]
        and fact.fact_version == 4
        and fact.posting_group_id == posting_group_id
        and fact.effect_kind == row["effect_kind"]
        and fact.source_system == row["source_system"]
        and fact.source_authority == row["source_authority"]
        and fact.occurred_at == row["occurred_at"]
    )
    group = _row(db, scope, "posting_groups", posting_group_id)
    _require(
        group["billing_account_id"] == fact.billing_account_id
        and group["group_kind"] == fact.effect_kind
        and group["source_ref"] == fact.source_ref
    )
    _require(group["occurred_at"] == fact.occurred_at)
    posting = _table(scope, "posting_effects")
    with db.no_autoflush:
        posting_rows = (
            db.execute(
                _scoped(
                    select(posting).where(
                        posting.c.posting_group_id == posting_group_id
                    ),
                    scope,
                    posting,
                )
            )
            .mappings()
            .all()
        )
    _require(
        Counter(
            (
                item.lane,
                item.amount_delta.amount,
                item.amount_delta.currency.code,
                item.amount_delta.currency.minor_units,
                fact.billing_account_id,
            )
            for item in fact.effects
        )
        == Counter(
            (
                item["lane"],
                item["amount_delta"],
                item["currency"],
                item["minor_units"],
                item["billing_account_id"],
            )
            for item in posting_rows
        )
    )
    actual_opening = _table(scope, "opening_cash_effects")
    actual_allocations = _table(scope, "allocation_effects")
    with db.no_autoflush:
        opening_ids = set(
            db.execute(
                _scoped(
                    select(actual_opening.c.id).where(
                        actual_opening.c.posting_group_id == posting_group_id
                    ),
                    scope,
                    actual_opening,
                )
            ).scalars()
        )
        allocation_rows = (
            db.execute(
                _scoped(
                    select(actual_allocations).where(
                        actual_allocations.c.posting_group_id == posting_group_id
                    ),
                    scope,
                    actual_allocations,
                )
            )
            .mappings()
            .all()
        )
    _require(
        len(fact.opening_cash_effects) == len(opening_ids)
        and opening_ids == {item.effect_id for item in fact.opening_cash_effects}
    )
    for item in fact.opening_cash_effects:
        _require(
            get_opening_cash_effect(db, scope=scope, effect_id=item.effect_id) == item
        )
    by_id = {item["id"]: item for item in allocation_rows}
    _require(
        len(fact.settlement_allocations) == len(by_id)
        and set(by_id)
        == {item.allocation_effect_id for item in fact.settlement_allocations}
    )
    for allocation_fact in fact.settlement_allocations:
        original = by_id[allocation_fact.allocation_effect_id]
        allocation = allocation_fact.allocation
        _require(
            (
                allocation.settlement_id,
                allocation.document_id,
                allocation.effect_kind,
                allocation.amount_delta.amount,
                allocation.amount_delta.currency.code,
                allocation.amount_delta.currency.minor_units,
                allocation.offsets_allocation_id,
            )
            == (
                original["settlement_id"],
                original["document_id"],
                original["effect_kind"],
                original["amount_delta"],
                original["currency"],
                original["minor_units"],
                original["offsets_allocation_id"],
            )
        )
    receipts = _table(scope, "opening_import_receipts")
    with db.no_autoflush:
        receipt_ids = tuple(
            db.execute(
                _scoped(
                    select(receipts.c.id).where(
                        receipts.c.posting_group_id == posting_group_id
                    ),
                    scope,
                    receipts,
                )
            ).scalars()
        )
    original_groups: set[UUID] = set()
    if fact.effect_kind in {"opening_cash_imported", "opening_cash_import_reversed"}:
        _require(len(receipt_ids) == 1 and fact.import_receipt is not None)
        receipt = get_opening_import_receipt(db, scope=scope, receipt_id=receipt_ids[0])
        _require(fact.import_receipt == receipt)
        if receipt.original_receipt_id is not None:
            original_receipt = get_opening_import_receipt(
                db, scope=scope, receipt_id=receipt.original_receipt_id
            )
            original_groups.add(original_receipt.posting_group_id)
    else:
        _require(not receipt_ids and fact.import_receipt is None)
    for item in fact.opening_cash_effects:
        if item.original_effect_id is not None:
            original_effect = get_opening_cash_effect(
                db, scope=scope, effect_id=item.original_effect_id
            )
            original_groups.add(original_effect.posting_group_id)
    for allocation_row in allocation_rows:
        if allocation_row["offsets_allocation_id"] is not None:
            original_allocation = _row(
                db, scope, "allocation_effects", allocation_row["offsets_allocation_id"]
            )
            _require(
                original_allocation["billing_account_id"] == fact.billing_account_id
                and (
                    original_allocation["currency"],
                    original_allocation["minor_units"],
                )
                == (group["currency"], group["minor_units"])
            )
            original_groups.add(original_allocation["posting_group_id"])
    if fact.effect_kind in {
        "cash_credit_draw_reversed",
        "opening_cash_refund_corrected",
        "opening_cash_import_reversed",
    }:
        _require(
            len(original_groups) == 1
            and fact.corrects_posting_group_id in original_groups
        )
    else:
        _require(not original_groups and fact.corrects_posting_group_id is None)
    references = tuple(
        (item.allocation_effect_id, item.opening_effect_id)
        for item in fact.cash_source_order
    )
    _require(len(references) == len(set(references)) == len(by_id) + len(opening_ids))
    _require(
        {
            item.allocation_effect_id
            for item in fact.cash_source_order
            if item.allocation_effect_id is not None
        }
        == set(by_id)
        and {
            item.opening_effect_id
            for item in fact.cash_source_order
            if item.opening_effect_id is not None
        }
        == opening_ids
        and tuple(item.sequence for item in fact.cash_source_order)
        == tuple(range(1, len(fact.cash_source_order) + 1))
    )
    return fact
