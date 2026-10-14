"""Immutable successor opening reads verified against actual scoped rows."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from decimal import localcontext
from uuid import UUID

from dotmac_kernel.cache import Scope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing._opening_values import (
    cash_decimal_context,
    minor_units_of,
    money_from_minor,
)
from dotmac_billing._receivable_backing import resolve_receivable_backing
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_commands import ReverseOpeningAccountImportV1
from dotmac_billing.opening_queries import (
    _decode_row,
    _group,
    _require,
    _row,
    _scoped,
    _table,
)
from dotmac_billing.opening_receivable_contracts import (
    AccountingFactV5,
    ImportOpeningAccountV2,
    OpeningImportReceiptV2,
    OpeningImportReversalVerificationV2,
    OpeningImportVerificationV2,
    OpeningReceivableBasisV1,
    OpeningReceivablePositionV1,
    ReceivableExposureV2,
)
from dotmac_billing.opening_receivable_validation import (
    _opening_replay_identity,
    opening_account_command_digest_v2,
    opening_manifest_fingerprint_v2,
)


def get_opening_import_receipt_v2(
    db: Session,
    *,
    scope: Scope,
    receipt_id: UUID,
) -> OpeningImportReceiptV2:
    row = _row(db, scope, "opening_import_receipts", receipt_id)
    fact = _decode_row(row, OpeningImportReceiptV2)
    _require(
        fact.scope == scope
        and fact.receipt_id == row["id"]
        and fact.billing_account_id == row["billing_account_id"]
        and fact.receipt_kind == row["receipt_kind"]
        and fact.original_receipt_id == row["original_receipt_id"]
        and fact.manifest_fingerprint == row["manifest_fingerprint"]
        and opening_manifest_fingerprint_v2(fact.manifest) == fact.manifest_fingerprint
        and fact.manifest.scope == scope
        and fact.target.scope == scope
        and fact.partition.account_id == fact.billing_account_id
        and fact.target.account_id == fact.billing_account_id
        and (fact.target.currency, fact.target.precision)
        == (row["currency"], row["minor_units"])
        and (fact.partition.currency, fact.partition.precision)
        == (row["currency"], row["minor_units"])
        and fact.posting_group_id == row["posting_group_id"]
        and fact.request_identity == row["request_identity"]
        and fact.request_digest == row["request_digest"]
        and fact.verification.command_digest == fact.request_digest
        and fact.total_residual.amount == row["total_residual"]
        and fact.total_residual.currency == fact.total_receivable.currency
        and (
            fact.total_residual.currency.code,
            fact.total_residual.currency.minor_units,
        )
        == (row["currency"], row["minor_units"])
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
    verification = fact.verification
    if fact.receipt_kind == "import":
        _require(type(verification) is OpeningImportVerificationV2)
        if isinstance(verification, OpeningImportVerificationV2):
            command = ImportOpeningAccountV2(
                scope=scope,
                manifest=fact.manifest,
                partition=fact.partition,
                target=fact.target,
                migration_authority_ref=verification.migration_authority_ref,
                idempotency_key=fact.request_identity,
                occurred_at=fact.occurred_at,
            )
            _require(opening_account_command_digest_v2(command) == fact.request_digest)
            _require(
                verification.scope == scope
                and verification.manifest_fingerprint == fact.manifest_fingerprint
                and verification.partition == fact.partition
                and verification.target == fact.target
                and verification.installation_id
                == fact.manifest.capture.installation_id
                and verification.source_authority_ref
                == fact.manifest.capture.source_authority_ref
                and fact.original_receipt_id is None
                and group["group_kind"] == "opening_account_imported"
            )
    else:
        _require(
            type(verification) is OpeningImportReversalVerificationV2
            and fact.original_receipt_id is not None
        )
        if fact.original_receipt_id is None:
            raise BillingRuleViolation(
                "opening_evidence_integrity", "unwind original is missing"
            )
        original_row = _row(
            db, scope, "opening_import_receipts", fact.original_receipt_id
        )
        original = _decode_row(original_row, OpeningImportReceiptV2)
        if isinstance(verification, OpeningImportReversalVerificationV2):
            from dotmac_billing.service import _jsonable

            reversal = ReverseOpeningAccountImportV1(
                scope=scope,
                receipt_id=fact.original_receipt_id,
                migration_authority_ref=verification.migration_authority_ref,
                idempotency_key=fact.request_identity,
                occurred_at=fact.occurred_at,
            )
            _require(
                fingerprint_of(
                    {
                        "domain": "billing.opening.account.reverse.v2",
                        "command": _jsonable(reversal),
                    }
                )
                == fact.request_digest
            )
            _require(
                original.receipt_kind == "import"
                and verification.scope == scope
                and verification.receipt_id == original.receipt_id
                and verification.receipt_evidence_digest
                == original_row["evidence_digest"]
                and verification.original_posting_group_id == original.posting_group_id
                and verification.billing_account_id == fact.billing_account_id
                and (verification.currency, verification.precision)
                == (row["currency"], row["minor_units"])
                and original.manifest == fact.manifest
                and original.partition == fact.partition
                and original.target == fact.target
                and original.total_receivable == fact.total_receivable
                and original.total_residual == fact.total_residual
                and original.lot_ids == fact.lot_ids
                and original.basis_ids == fact.basis_ids
                and group["group_kind"] == "opening_account_import_reversed"
            )
    principal_id = (
        fact.receipt_id if fact.receipt_kind == "import" else fact.original_receipt_id
    )
    for name, ids in (
        ("opening_cash_lots", fact.lot_ids),
        ("opening_receivable_bases", fact.basis_ids),
    ):
        table = _table(scope, name)
        with db.no_autoflush:
            actual = tuple(
                db.execute(
                    _scoped(
                        select(table.c.id)
                        .where(
                            table.c.receipt_id == principal_id,
                        )
                        .order_by(table.c.sequence),
                        scope,
                        table,
                    )
                ).scalars()
            )
        _require(actual == ids)
        monetary_column = (
            table.c.residual_amount
            if name == "opening_cash_lots"
            else table.c.initial_receivable
        )
        with db.no_autoflush:
            total = db.execute(
                _scoped(
                    select(func.coalesce(func.sum(monetary_column), 0)).where(
                        table.c.receipt_id == principal_id,
                    ),
                    scope,
                    table,
                )
            ).scalar_one()
        _require(
            total
            == (
                fact.total_residual.amount
                if name == "opening_cash_lots"
                else fact.total_receivable.amount
            )
        )
    return fact


def get_opening_receivable_basis(
    db: Session,
    *,
    scope: Scope,
    basis_id: UUID,
) -> OpeningReceivableBasisV1:
    row = _row(db, scope, "opening_receivable_bases", basis_id)
    fact = _decode_row(row, OpeningReceivableBasisV1)
    _require(
        fact.scope == scope
        and fact.basis_id == row["id"]
        and fact.receipt_id == row["receipt_id"]
        and fact.document_id == row["document_id"]
        and fact.billing_account_id == row["billing_account_id"]
        and fact.posting_group_id == row["posting_group_id"]
        and fact.source_version == row["source_version"]
        and fact.sequence == row["sequence"]
        and fact.installation_id == row["installation_id"]
        and fact.source_system == row["source_system"]
        and fact.source_invoice_key == row["source_invoice_key"]
        and fact.initial_receivable.amount == row["initial_receivable"]
        and minor_units_of(fact.initial_receivable) == fact.source.residual_minor
        and (
            fact.initial_receivable.currency.code,
            fact.initial_receivable.currency.minor_units,
        )
        == (row["currency"], row["minor_units"])
        and fact.recorded_at == row["recorded_at"]
    )
    receipt = get_opening_import_receipt_v2(db, scope=scope, receipt_id=fact.receipt_id)
    _require(
        receipt.receipt_kind == "import"
        and fact.basis_id in receipt.basis_ids
        and receipt.posting_group_id == fact.posting_group_id
        and fact.source in receipt.manifest.receivables
        and fact.manifest_fingerprint == receipt.manifest_fingerprint
        and fact.installation_id == receipt.manifest.capture.installation_id
        and fact.source_system == receipt.manifest.capture.source_system
    )
    document = _row(db, scope, "documents", fact.document_id)
    original = fact.source.original
    _require(original is not None)
    if original is not None:
        from dotmac_billing.service import _jsonable

        _require(
            document["authoring_mode"] == "imported_open_item"
            and document["obligation_id"] is None
            and document["document_kind"] == "invoice"
            and document["billing_account_id"] == fact.billing_account_id
            and (document["currency"], document["minor_units"])
            == (row["currency"], row["minor_units"])
            and original.grand_total.currency == fact.initial_receivable.currency
            and original.subtotal.currency == fact.initial_receivable.currency
            and original.tax_total.currency == fact.initial_receivable.currency
            and document["grand_total"] == original.grand_total.amount
            and document["subtotal"] == original.subtotal.amount
            and document["tax_total"] == original.tax_total.amount
            and document["document_number"] == original.document_number
            and document["issued_at"] == original.issued_at
            and document["seller_snapshot"] == _jsonable(original.seller)
            and document["customer_snapshot"] == _jsonable(original.customer)
            and document["payment_instructions"]
            == _jsonable(original.payment_instructions)
            and document["brand_asset"] == _jsonable(original.brand_asset)
            and document["document_profile_code"] == original.document_profile_code
            and document["document_profile_version"]
            == original.document_profile_version
            and document["locale"] == original.locale
            and document["timezone"] == original.timezone
            and document["due_at"] == fact.source.collection.due_at
            and document["due_date_basis"]
            == _jsonable(fact.source.collection.due_date_basis)
        )
    selected = tuple(
        x for x in receipt.manifest.receivables if x.partition == receipt.partition
    )
    _require(
        0 < fact.sequence <= len(selected)
        and selected[fact.sequence - 1] == fact.source
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
        group["source_version"] == fact.source_version
        and group["group_kind"] == "opening_account_imported"
    )
    history = _table(scope, "opening_historical_discharge_claims")
    keeper = _table(scope, "receivable_source_claims")
    for discharge in fact.source.historical_discharges:
        for binding in discharge.replay_bindings:
            statement = select(history).where(
                history.c.operation == binding.operation,
                history.c.source_owner == binding.source_owner,
            )
            if binding.operation == "billing.withholding.tax_component":
                statement = statement.where(
                    history.c.determination_ref == binding.determination_ref,
                    history.c.component_ref == binding.component_ref,
                )
            else:
                statement = statement.where(history.c.source_ref == binding.source_ref)
            with db.no_autoflush:
                claim = (
                    db.execute(_scoped(statement, scope, history))
                    .mappings()
                    .one_or_none()
                )
            if claim is None:
                raise BillingRuleViolation(
                    "opening_evidence_integrity", "historical replay claim is missing"
                )
            _require(
                claim["billing_account_id"] == fact.billing_account_id
                and claim["basis_id"] in receipt.basis_ids
                and claim["evidence_digest"] == binding.evidence_digest
                and claim["source_ref"] == binding.source_ref
            )
            namespace, owner, key1, key2 = _opening_replay_identity(binding)
            with db.no_autoflush:
                occupied = (
                    db.execute(
                        _scoped(
                            select(keeper).where(
                                keeper.c.namespace == namespace,
                                keeper.c.source_owner == owner,
                                keeper.c.key1 == key1,
                                keeper.c.key2 == key2,
                            ),
                            scope,
                            keeper,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if occupied is None:
                raise BillingRuleViolation(
                    "opening_evidence_integrity", "physical source occupancy is missing"
                )
            _require(
                occupied["historical_claim_id"] == claim["id"]
                and occupied["billing_account_id"] == fact.billing_account_id
                and all(
                    occupied[key] is None
                    for key in (
                        "withholding_discharge_id",
                        "non_cash_grant_id",
                        "non_cash_effect_id",
                        "credit_document_fact_id",
                    )
                )
            )
    return fact


def get_opening_receivable_position(
    db: Session,
    *,
    scope: Scope,
    basis_id: UUID,
) -> OpeningReceivablePositionV1:
    basis = get_opening_receivable_basis(db, scope=scope, basis_id=basis_id)
    backing = resolve_receivable_backing(db, scope=scope, document_id=basis.document_id)
    currency = basis.initial_receivable.currency
    from dotmac_kernel.money import Money

    from dotmac_billing._opening_values import minor_units_of

    with localcontext(cash_decimal_context()):
        remaining = money_from_minor(
            minor_units_of(Money(backing.remaining, currency)),
            currency.code,
            currency.minor_units,
        )
    return OpeningReceivablePositionV1(
        basis,
        remaining,
        backing.posting_watermark,
        backing.source_version,
        not backing.active,
    )


def get_opening_receivable_exposure(
    db: Session,
    *,
    scope: Scope,
    basis_id: UUID,
) -> ReceivableExposureV2:
    basis = get_opening_receivable_basis(db, scope=scope, basis_id=basis_id)
    table = _table(scope, "receivable_exposure_facts_v2")
    with db.no_autoflush:
        row = (
            db.execute(
                _scoped(
                    select(table)
                    .where(table.c.basis_id == basis_id)
                    .order_by(
                        table.c.source_version.desc(),
                    )
                    .limit(1),
                    scope,
                    table,
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise BillingRuleViolation("not_found", "opening exposure is unavailable")
    fact = _decode_row(row, ReceivableExposureV2)
    backing = resolve_receivable_backing(
        db,
        scope=scope,
        document_id=basis.document_id,
        source_version=fact.source_version,
    )
    _require(
        fact.scope == scope
        and fact.basis_id == basis_id
        and fact.document_id == basis.document_id
        and fact.billing_account_id == basis.billing_account_id
        and fact.source_version == row["source_version"]
        and fact.posting_group_watermark == row["posting_group_id"]
        and fact.posting_group_watermark == backing.posting_watermark
        and fact.collectible_receivable.amount == row["collectible_receivable"]
        and fact.collectible_receivable.amount == backing.remaining
        and fact.collection == basis.source.collection
        and fact.observed_at == row["recorded_at"]
        and (
            fact.collectible_receivable.currency.code,
            fact.collectible_receivable.currency.minor_units,
        )
        == (row["currency"], row["minor_units"])
        and fact.financial_state
        == (
            "cancelled"
            if not backing.active
            else "resolved"
            if backing.remaining == 0
            else "partially_resolved"
            if backing.remaining < backing.initial_amount
            else "open"
        )
        and fact.exposure_ref == f"document:{basis.document_id}"
        and fact.source_owner == "dotmac-billing"
        and fact.completeness_reason_code is None
    )
    semantic = replace(fact, state_fingerprint="pending")
    from dotmac_billing.service import _jsonable

    payload = _jsonable(semantic)
    payload.pop("state_fingerprint")
    _require(fingerprint_of(payload) == fact.state_fingerprint)
    return fact


def get_accounting_fact_v5(
    db: Session,
    *,
    scope: Scope,
    posting_group_id: UUID,
) -> AccountingFactV5:
    table = _table(scope, "accounting_facts")
    with db.no_autoflush:
        row = (
            db.execute(
                _scoped(
                    select(table).where(
                        table.c.posting_group_id == posting_group_id,
                        table.c.fact_version == 5,
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
            "not_found", "opening V5 accounting fact is unavailable"
        )
    fact = _decode_row(
        row, AccountingFactV5, payload="fact_payload", digest="fact_digest"
    )
    _require(
        fact.scope == scope
        and fact.posting_group_id == posting_group_id
        and fact.fact_id == row["id"]
    )
    receipt = get_opening_import_receipt_v2(
        db, scope=scope, receipt_id=fact.import_receipt.receipt_id
    )
    _require(receipt == fact.import_receipt)
    group = _group(
        db,
        scope,
        posting_group_id,
        receipt.billing_account_id,
        receipt.partition.currency,
        receipt.partition.precision,
    )
    _require(
        fact.billing_account_id == receipt.billing_account_id
        and fact.source_system == row["source_system"] == "dotmac-billing"
        and fact.source_authority == row["source_authority"] == "internal"
        and fact.effect_kind == row["effect_kind"] == group["group_kind"]
        and fact.source_ref == group["source_ref"] == receipt.request_identity
        and fact.occurred_at == row["occurred_at"] == group["occurred_at"]
    )
    posting = _table(scope, "posting_effects")
    opening = _table(scope, "opening_cash_effects")
    with db.no_autoflush:
        posting_rows = (
            db.execute(
                _scoped(
                    select(posting).where(
                        posting.c.posting_group_id == posting_group_id,
                    ),
                    scope,
                    posting,
                )
            )
            .mappings()
            .all()
        )
        opening_ids = set(
            db.execute(
                _scoped(
                    select(opening.c.id).where(
                        opening.c.posting_group_id == posting_group_id,
                    ),
                    scope,
                    opening,
                )
            ).scalars()
        )
    _require(
        Counter(
            (
                x.lane,
                x.amount_delta.amount,
                x.amount_delta.currency.code,
                x.amount_delta.currency.minor_units,
                fact.billing_account_id,
            )
            for x in fact.effects
        )
        == Counter(
            (
                x["lane"],
                x["amount_delta"],
                x["currency"],
                x["minor_units"],
                x["billing_account_id"],
            )
            for x in posting_rows
        )
    )
    sign = 1 if receipt.receipt_kind == "import" else -1
    _require(
        len(fact.effects) == 2
        and {x.lane for x in fact.effects} == {"receivable", "available_credit"}
        and all(
            minor_units_of(x.amount_delta)
            == sign
            * minor_units_of(
                receipt.total_receivable
                if x.lane == "receivable"
                else receipt.total_residual
            )
            for x in fact.effects
        )
        and opening_ids == {x.effect_id for x in fact.opening_cash_effects}
        and len(opening_ids) == len(fact.opening_cash_effects)
    )
    if receipt.original_receipt_id is None:
        _require(fact.corrects_posting_group_id is None)
    else:
        original = get_opening_import_receipt_v2(
            db, scope=scope, receipt_id=receipt.original_receipt_id
        )
        _require(fact.corrects_posting_group_id == original.posting_group_id)
    _require(
        fact.receivable_bases
        == tuple(
            get_opening_receivable_basis(db, scope=scope, basis_id=value)
            for value in receipt.basis_ids
        )
    )
    from dotmac_billing.opening_queries import get_opening_cash_effect

    _require(
        all(
            get_opening_cash_effect(db, scope=scope, effect_id=x.effect_id) == x
            for x in fact.opening_cash_effects
        )
    )
    return fact
