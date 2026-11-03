"""Public read-only per-document and ordered-ledger queries (Billing a11).

These expose persisted, immutable evidence to other owners. They take no lock,
perform no write and never repair: every row is re-verified against its own
digest and its sibling evidence, and any disagreement fails closed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Any, Literal, cast
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Currency, Money, MoneyError
from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from dotmac_billing._document_facts import _decode
from dotmac_billing._receivable_backing import resolve_receivable_backing
from dotmac_billing.contracts import (
    CollectionTiming,
    DueDateBasisV1,
    FinancialState,
    ReceivableExposureV1,
    ServicePeriodEvidenceV1,
    WithholdingDischargeEffectV1,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import _scoped, _table
from dotmac_billing.opening_receivable_contracts import ReceivableExposureV2
from dotmac_billing.queries import get_withholding_discharge

FACT_INDEX_DIGEST_DOMAIN = "billing.accounting.fact.index.v1"
MAX_FACT_INDEX_PAGE = 1000


def _require_scope(scope: Scope) -> None:
    if not isinstance(scope, TenantScope | PlatformScope) or (
        isinstance(scope, TenantScope) and not isinstance(scope.tenant_id, UUID)
    ):
        raise BillingRuleViolation(
            "scope_mismatch", "document queries require explicit scope"
        )


def _require_document_id(document_id: object) -> UUID:
    if not isinstance(document_id, UUID):
        raise BillingRuleViolation(
            "invalid_document_id", "document identity must be a UUID"
        )
    return document_id


# --------------------------------------------------------------------------
# Per-document receivable exposure
# --------------------------------------------------------------------------


def exposure_state_fingerprint(exposure: ReceivableExposureV1) -> str:
    """The fingerprint Billing seals into a native exposure at posting time."""
    from dotmac_billing.service import _wire_value

    payload = {
        "scope": exposure.scope,
        "source_owner": exposure.source_owner,
        "exposure_ref": exposure.exposure_ref,
        "billing_account_id": exposure.billing_account_id,
        "subject_ref": exposure.subject_ref,
        "service_ref": exposure.service_ref,
        "collection_timing": exposure.collection_timing,
        "source_version": exposure.source_version,
        "posting_group_watermark": exposure.posting_group_watermark,
        "source_authority": exposure.source_authority,
        "projection_mode": exposure.projection_mode,
        "derived_from": exposure.derived_from,
        "completeness": exposure.completeness,
        "completeness_reason_code": exposure.completeness_reason_code,
        "observed_at": exposure.observed_at,
        "service_period": exposure.service_period,
        "due_at": exposure.due_at,
        "due_date_basis": exposure.due_date_basis,
        "financial_state": exposure.financial_state,
        "collectible_receivable": exposure.collectible_receivable,
    }
    return fingerprint_of(_wire_value(payload))


def _seal_matches(exposure: ReceivableExposureV1) -> bool:
    """Check the stored seal, tolerating only the database's zone rendering.

    The seal hashes ISO instants. The command supplied ``observed_at`` and
    ``due_at`` with their own offset, while PostgreSQL returns them in the
    session zone, so the same instant may print with a different offset. Only
    the as-read and UTC renderings are tried; any other difference is refused.
    """
    from datetime import UTC

    def forms(value: datetime) -> tuple[datetime, ...]:
        utc = value.astimezone(UTC)
        return (value,) if value.utcoffset() == utc.utcoffset() else (value, utc)

    for observed in forms(exposure.observed_at):
        for due in (None,) if exposure.due_at is None else forms(exposure.due_at):
            candidate = replace(exposure, observed_at=observed, due_at=due)
            if exposure_state_fingerprint(candidate) == exposure.state_fingerprint:
                return True
    return False


def get_document_exposure(
    db: Session, *, scope: Scope, document_id: UUID
) -> ReceivableExposureV1 | ReceivableExposureV2:
    """Return the latest sealed exposure of one issued invoice.

    A native invoice yields ``ReceivableExposureV1``; an imported open item
    yields the opening-owned ``ReceivableExposureV2``. The fact is re-derived
    against Billing's own backing at its source version, and refused if it is
    absent, corrupt, or stale against current resolution. The caller owns the
    transaction; no lock or autoflush happens here.
    """
    _require_scope(scope)
    document_id = _require_document_id(document_id)
    documents = _table(scope, "documents")
    imported = _table(scope, "receivable_exposure_facts_v2")
    table = _table(scope, "receivable_exposure_facts")
    with db.no_autoflush:
        document = (
            db.execute(
                _scoped(
                    select(documents).where(documents.c.id == document_id),
                    scope,
                    documents,
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            document is None
            or document["document_kind"] != "invoice"
            or document["issued_at"] is None
        ):
            raise BillingRuleViolation(
                "not_found", "no issued invoice exists for this exposure"
            )
        if document["authoring_mode"] == "imported_open_item":
            basis_id = db.execute(
                _scoped(
                    select(imported.c.basis_id)
                    .where(imported.c.document_id == document_id)
                    .order_by(imported.c.source_version.desc())
                    .limit(1),
                    scope,
                    imported,
                )
            ).scalar_one_or_none()
            if basis_id is None:
                raise BillingRuleViolation(
                    "not_found", "no imported exposure exists for this document"
                )
            from dotmac_billing.opening_receivable_queries import (
                get_opening_receivable_exposure,
            )

            return get_opening_receivable_exposure(db, scope=scope, basis_id=basis_id)
        row = (
            db.execute(
                _scoped(
                    select(table)
                    .where(
                        table.c.document_id == document_id,
                        table.c.exposure_ref == f"document:{document_id}",
                    )
                    .order_by(table.c.source_version.desc())
                    .limit(1),
                    scope,
                    table,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BillingRuleViolation(
                "not_found", "no exposure has been sealed for this document"
            )
        try:
            exposure = ReceivableExposureV1(
                scope=scope,
                source_owner=row["source_owner"],
                exposure_ref=row["exposure_ref"],
                billing_account_id=row["billing_account_id"],
                subject_ref=row["subject_ref"],
                service_ref=row["service_ref"],
                collection_timing=cast(CollectionTiming, row["collection_timing"]),
                source_version=row["source_version"],
                posting_group_watermark=row["posting_group_watermark"],
                source_authority=row["source_authority"],
                projection_mode=row["projection_mode"],
                derived_from=row["derived_from"],
                completeness=row["completeness"],
                completeness_reason_code=row["completeness_reason_code"],
                state_fingerprint=row["state_fingerprint"],
                observed_at=row["observed_at"],
                service_period=_decode(row["service_period"], ServicePeriodEvidenceV1),
                due_at=row["due_at"],
                due_date_basis=_decode(row["due_date_basis"], DueDateBasisV1),
                financial_state=cast(FinancialState, row["financial_state"]),
                collectible_receivable=Money(
                    row["collectible_receivable"],
                    Currency(row["currency"], row["minor_units"]),
                ),
            )
            if (
                exposure.billing_account_id != document["billing_account_id"]
                or exposure.source_owner != "dotmac-billing"
                or not _seal_matches(exposure)
            ):
                raise ValueError("exposure identity or seal disagrees")
        except (ValueError, TypeError, DecimalException, MoneyError) as exc:
            raise BillingRuleViolation(
                "exposure_fact_integrity", "stored exposure evidence is invalid"
            ) from exc
        sealed = resolve_receivable_backing(
            db,
            scope=scope,
            document_id=document_id,
            source_version=exposure.source_version,
        )
        current = resolve_receivable_backing(db, scope=scope, document_id=document_id)
        cancelled = exposure.financial_state == "cancelled"
        if sealed.posting_watermark != exposure.posting_group_watermark or (
            not cancelled
            and (
                exposure.collectible_receivable.amount != sealed.remaining
                or current.remaining != sealed.remaining
            )
        ):
            raise BillingRuleViolation(
                "exposure_fact_stale",
                "sealed exposure disagrees with current receivable resolution",
            )
        return exposure


# --------------------------------------------------------------------------
# Per-document withholding evidence
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocumentWithholdingEvidenceV1:
    """Every immutable withholding discharge effect of one document, in order.

    ``net_accepted`` is the signed sum of accepted deltas (corrections are
    negative). It is evidence, not capacity: current receivable room is read
    from the receivable exposure.
    """

    scope: Scope
    document_id: UUID
    effects: tuple[WithholdingDischargeEffectV1, ...]
    net_accepted: Money


def get_document_withholding_evidence(
    db: Session, *, scope: Scope, document_id: UUID
) -> DocumentWithholdingEvidenceV1:
    """List one document's withholding discharge evidence, oldest first.

    Each effect is re-verified by the single-discharge reader. A document with
    no discharges returns an empty tuple and zero, provided the document exists.
    """
    _require_scope(scope)
    document_id = _require_document_id(document_id)
    documents = _table(scope, "documents")
    discharges = _table(scope, "withholding_discharges")
    with db.no_autoflush:
        document = (
            db.execute(
                _scoped(
                    select(documents.c.currency, documents.c.minor_units).where(
                        documents.c.id == document_id
                    ),
                    scope,
                    documents,
                )
            )
            .mappings()
            .one_or_none()
        )
        if document is None:
            raise BillingRuleViolation("not_found", "document was not found")
        identities = tuple(
            db.execute(
                _scoped(
                    select(discharges.c.id)
                    .where(discharges.c.document_id == document_id)
                    .order_by(discharges.c.created_at, discharges.c.id),
                    scope,
                    discharges,
                )
            ).scalars()
        )
    effects = tuple(
        get_withholding_discharge(db, scope=scope, discharge_id=identity)
        for identity in identities
    )
    currency = Currency(document["currency"], document["minor_units"])
    total = Decimal("0")
    for effect in effects:
        if effect.accepted_delta.currency != currency:
            raise BillingRuleViolation(
                "withholding_evidence_corrupt",
                "withholding evidence currency differs from its document",
            )
        total += effect.accepted_delta.amount
    return DocumentWithholdingEvidenceV1(
        scope, document_id, effects, Money.of(total, currency)
    )


# --------------------------------------------------------------------------
# Ordered accounting-fact query with count and digest
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountingFactCursorV1:
    """Position in the total order; the next page starts strictly after it."""

    occurred_at: datetime
    billing_account_id: UUID
    source_version: int
    fact_version: int
    fact_id: UUID


@dataclass(frozen=True, slots=True)
class AccountingFactIndexEntryV1:
    """Reference to one immutable accounting fact; content is fetched by owner."""

    cursor: AccountingFactCursorV1
    posting_group_id: UUID
    effect_kind: str
    source_system: str
    source_ref: str
    fact_digest: str


@dataclass(frozen=True, slots=True)
class ListAccountingFactsV1:
    """Window ``occurred_from <= occurred_at < occurred_to``, paged after a cursor."""

    scope: Scope
    occurred_from: datetime
    occurred_to: datetime
    after: AccountingFactCursorV1 | None = None
    limit: int = 500


@dataclass(frozen=True, slots=True)
class AccountingFactIndexPageV1:
    """One page of the window, in a total order independent of commit timing.

    The order is ``occurred_at``, account, posting-group source version, fact
    version, fact id. Paging is keyset: the cost of a page does not grow with the
    window. Completeness is proved by ``summarize_accounting_fact_window``, not
    here.
    """

    scope: Scope
    occurred_from: datetime
    occurred_to: datetime
    entries: tuple[AccountingFactIndexEntryV1, ...]
    next_cursor: AccountingFactCursorV1 | None
    contract_version: Literal["billing.accounting.fact.index.v1"] = (
        "billing.accounting.fact.index.v1"
    )


@dataclass(frozen=True, slots=True)
class SummarizeAccountingFactWindowV1:
    scope: Scope
    occurred_from: datetime
    occurred_to: datetime


@dataclass(frozen=True, slots=True)
class AccountingFactWindowSummaryV1:
    """Count and chained digest of every fact in a window, in the page order.

    ``occurred_at`` is a business time, so a fact committed late can still land
    inside a window already paged. A consumer closes a window only after taking
    this summary at least one settle interval after ``occurred_to``, and compares
    it with its own running count and digest (``fact_index_line``); a mismatch
    means re-page, not a partial acceptance.
    """

    scope: Scope
    occurred_from: datetime
    occurred_to: datetime
    count: int
    digest: str
    contract_version: Literal["billing.accounting.fact.window.v1"] = (
        "billing.accounting.fact.window.v1"
    )


def fact_index_line(fact_id: UUID, fact_version: int, fact_digest: str) -> bytes:
    """The exact bytes one fact contributes to the window digest."""
    return f"{fact_id}|{fact_version}|{fact_digest}\n".encode()


def fact_index_digest_seed() -> hashlib._Hash:
    return hashlib.sha256(f"{FACT_INDEX_DIGEST_DOMAIN}\n".encode())


def _window(scope: Scope, request: object) -> tuple[datetime, datetime]:
    _require_scope(scope)
    if (
        not isinstance(request, ListAccountingFactsV1 | SummarizeAccountingFactWindowV1)
        or request.scope != scope
    ):
        raise BillingRuleViolation(
            "scope_mismatch", "the request scope must equal the routed scope"
        )
    start, end = request.occurred_from, request.occurred_to
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or start.tzinfo is None
        or end.tzinfo is None
        or start >= end
    ):
        raise BillingRuleViolation(
            "invalid_window", "an aware, non-empty half-open window is required"
        )
    return start, end


def _window_select(scope: Scope, start: datetime, end: datetime) -> Any:
    facts = _table(scope, "accounting_facts")
    groups = _table(scope, "posting_groups")
    join = facts.c.posting_group_id == groups.c.id
    if isinstance(scope, TenantScope):
        join = join & (facts.c.tenant_id == groups.c.tenant_id)
    order = (
        facts.c.occurred_at,
        groups.c.billing_account_id,
        groups.c.source_version,
        facts.c.fact_version,
        facts.c.id,
    )
    statement = (
        select(
            facts.c.id,
            facts.c.fact_version,
            facts.c.posting_group_id,
            facts.c.effect_kind,
            facts.c.source_system,
            facts.c.fact_digest,
            facts.c.fact_payload,
            facts.c.occurred_at,
            groups.c.billing_account_id,
            groups.c.source_version,
            groups.c.source_ref,
        )
        .select_from(facts.join(groups, join))
        .where(facts.c.occurred_at >= start, facts.c.occurred_at < end)
    )
    return _scoped(statement, scope, facts), order


def _verify(row: Any) -> None:
    try:
        if fingerprint_of(row["fact_payload"]) != row["fact_digest"]:
            raise ValueError("accounting fact digest disagrees")
    except (ValueError, TypeError, DecimalException) as exc:
        raise BillingRuleViolation(
            "accounting_fact_integrity", "stored accounting evidence is invalid"
        ) from exc


def list_accounting_facts(
    db: Session, *, scope: Scope, request: ListAccountingFactsV1
) -> AccountingFactIndexPageV1:
    """Return one keyset page of a window's ordered accounting facts.

    Read-only and lock-free; the caller owns the transaction. Only the rows of
    the page are read and digest-verified, so the cost is bounded by ``limit``.
    """
    start, end = _window(scope, request)
    if (
        not isinstance(request.limit, int)
        or isinstance(request.limit, bool)
        or not 1 <= request.limit <= MAX_FACT_INDEX_PAGE
    ):
        raise BillingRuleViolation(
            "invalid_limit", "page limit is outside the supported range"
        )
    statement, order = _window_select(scope, start, end)
    cursor = request.after
    if cursor is not None:
        if not isinstance(cursor, AccountingFactCursorV1):
            raise BillingRuleViolation(
                "invalid_cursor", "the cursor must be a Billing fact cursor"
            )
        statement = statement.where(
            tuple_(*order)
            > tuple_(
                *(
                    literal(value, column.type)
                    for column, value in zip(
                        order,
                        (
                            cursor.occurred_at,
                            cursor.billing_account_id,
                            cursor.source_version,
                            cursor.fact_version,
                            cursor.fact_id,
                        ),
                        strict=True,
                    )
                )
            )
        )
    with db.no_autoflush:
        rows = (
            db.execute(statement.order_by(*order).limit(request.limit + 1))
            .mappings()
            .all()
        )
    entries: list[AccountingFactIndexEntryV1] = []
    for row in rows[: request.limit]:
        _verify(row)
        entries.append(
            AccountingFactIndexEntryV1(
                AccountingFactCursorV1(
                    row["occurred_at"],
                    row["billing_account_id"],
                    row["source_version"],
                    row["fact_version"],
                    row["id"],
                ),
                row["posting_group_id"],
                row["effect_kind"],
                row["source_system"],
                row["source_ref"],
                row["fact_digest"],
            )
        )
    return AccountingFactIndexPageV1(
        scope,
        start,
        end,
        tuple(entries),
        entries[-1].cursor if len(rows) > request.limit else None,
    )


def summarize_accounting_fact_window(
    db: Session, *, scope: Scope, request: SummarizeAccountingFactWindowV1
) -> AccountingFactWindowSummaryV1:
    """Count and chain every fact of a window, verifying each row's digest.

    Meant to be called once per window close, not per page.
    """
    start, end = _window(scope, request)
    statement, order = _window_select(scope, start, end)
    digest = fact_index_digest_seed()
    count = 0
    with db.no_autoflush:
        for row in db.execute(
            statement.order_by(*order).execution_options(yield_per=1000)
        ).mappings():
            _verify(row)
            digest.update(
                fact_index_line(row["id"], row["fact_version"], row["fact_digest"])
            )
            count += 1
    return AccountingFactWindowSummaryV1(scope, start, end, count, digest.hexdigest())


__all__ = [
    "AccountingFactCursorV1",
    "AccountingFactIndexEntryV1",
    "AccountingFactIndexPageV1",
    "DocumentWithholdingEvidenceV1",
    "AccountingFactWindowSummaryV1",
    "ListAccountingFactsV1",
    "SummarizeAccountingFactWindowV1",
    "exposure_state_fingerprint",
    "get_document_exposure",
    "get_document_withholding_evidence",
    "list_accounting_facts",
    "summarize_accounting_fact_window",
]
