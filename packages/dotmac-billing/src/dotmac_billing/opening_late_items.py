"""R8: post-seal opening late items (tenant plane, Billing alone).

After the opening window is sealed, Finance can find a source open invoice the
opening did not carry (a *debit*) or a source credit note never applied (a
*credit* against one open invoice). ``record_opening_late_debit``,
``record_opening_late_credit`` and ``reverse_opening_late_item`` append one typed
``opening_late_items`` row, one posting group with a single receivable effect, one
``AccountingFactV7`` (``billing.accounting.fact.v7``) and the usual position and
exposure facts. Billing books no revenue and no tax: the debit document carries
the source totals and per-document tax snapshots as evidence only; an
accounting consumer maps the fact (receivable against its clearing account).

Permission ``billing.opening_late_item.record`` (default role ``admin``, like
``billing.withholding.release``) is checked through the wired
``ActorPermissionReader`` by the actor that holds it; the database independently bounds
the amount, the sealed window and namespace, ``dual_control = 'off'``, the room
after every insert and the withholding room (bi_0021). Commands run in the
caller's transaction and never commit.

Current limitation: a window that binds ``dual_control = 'on'`` refuses every
item (``opening_late_item_dual_control_unsupported``); two-person approval for
these items is not built yet. This is a gap to close when a tenant needs it, not a
design choice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing import models, service
from dotmac_billing._document_facts import _decode
from dotmac_billing._opening_values import (
    OpeningCashValue,
    cash_decimal_context,
    minor_units_of,
)
from dotmac_billing.commands import OPENING_LATE_ITEM_PERMISSION, ActorPermissionReader
from dotmac_billing.contracts import (
    AccountingEffectV1,
    AppliedTaxSnapshotV1,
    CollectionTiming,
    DueDateBasisStatus,
    DueDateBasisV1,
    ServicePeriodEvidenceV1,
)
from dotmac_billing.engine import EffectLane
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_queries import _scoped, _table
from dotmac_billing.opening_receivable_contracts import (
    OpeningCollectionEvidenceV1,
    OpeningOriginalDocumentV1,
)
from dotmac_billing.opening_receivable_queries import get_opening_receivable_basis
from dotmac_billing.opening_window_queries import (
    get_opening_hold_list,
    get_opening_window,
    get_opening_window_seal,
)

ACCOUNTING_FACT_V7_CONTRACT = "billing.accounting.fact.v7"
DEBIT_KIND: Literal["opening_late_debit_recorded"] = "opening_late_debit_recorded"
CREDIT_KIND: Literal["opening_late_credit_applied"] = "opening_late_credit_applied"
REVERSAL_KIND: Literal["opening_late_item_reversed"] = "opening_late_item_reversed"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_KIND_OF_ITEM = {
    "debit": DEBIT_KIND,
    "credit": CREDIT_KIND,
    "reversal": REVERSAL_KIND,
}
ItemKind = Literal["debit", "credit", "reversal"]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpeningLateSourceV1(OpeningCashValue):
    """The typed source identity inside the sealed source namespace.

    ``key`` is the source invoice key (debit) or source credit-note key
    (credit). ``number`` is the credit note's number; a debit takes its number
    from ``original.document_number``.
    """

    installation_id: str
    source_system: str
    key: str
    number: str | None = None


@dataclass(frozen=True, slots=True)
class RecordOpeningLateDebitV1(OpeningCashValue):
    """Bring in the residual of a source open invoice the opening did not carry."""

    scope: Scope
    billing_account_id: UUID
    source: OpeningLateSourceV1
    original: OpeningOriginalDocumentV1
    amount: Money
    collection: OpeningCollectionEvidenceV1
    evidence_ref: str
    evidence_digest: str
    actor_ref: str
    command_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RecordOpeningLateCreditV1(OpeningCashValue):
    """Apply part of a source credit note to one open invoice (no VAT is posted)."""

    scope: Scope
    source: OpeningLateSourceV1
    source_total: Money
    target_document_id: UUID
    amount: Money
    evidence_ref: str
    evidence_digest: str
    actor_ref: str
    command_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReverseOpeningLateItemV1(OpeningCashValue):
    """Full reversal of one debit (while untouched) or credit."""

    scope: Scope
    item_id: UUID
    actor_ref: str
    command_id: str
    reason: str


# --------------------------------------------------------------------------
# Fact V7 and reads
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpeningLateItemFactBlockV1(OpeningCashValue):
    item_id: UUID
    window_id: UUID
    kind: Literal["debit", "credit", "reversal"]
    document_id: UUID
    source_identity: OpeningLateSourceV1
    reverses_item_id: UUID | None
    actor_ref: str
    evidence_digest: str | None


@dataclass(frozen=True, slots=True)
class AccountingFactV7(OpeningCashValue):
    """``billing.accounting.fact.v7``: one opening late item on its own posting group.

    ``effects`` holds exactly one receivable effect: positive for a debit,
    negative for a credit, and for a reversal the exact negation of the item it
    reverses (``reverses_fact_id`` names that item's V7 fact). No tax or revenue
    field exists; the per-document tax evidence is read with
    ``get_open_item_tax_snapshots``.
    """

    scope: Scope
    source_system: str
    fact_id: UUID
    fact_version: Literal[7]
    billing_account_id: UUID
    posting_group_id: UUID
    source_ref: str
    source_authority: Literal["internal"]
    effect_kind: Literal[
        "opening_late_debit_recorded",
        "opening_late_credit_applied",
        "opening_late_item_reversed",
    ]
    occurred_at: datetime
    committed_at: datetime
    effects: tuple[AccountingEffectV1, ...]
    item: OpeningLateItemFactBlockV1
    reverses_fact_id: UUID | None

    def __post_init__(self) -> None:
        OpeningCashValue.__post_init__(self)
        (effect,) = self.effects if len(self.effects) == 1 else (None,)
        amount = None if effect is None else effect.amount_delta.amount
        sign_ok = (
            effect is not None
            and effect.lane == "receivable"
            and (
                (self.item.kind == "debit" and amount is not None and amount > 0)
                or (self.item.kind == "credit" and amount is not None and amount < 0)
                or (self.item.kind == "reversal" and amount is not None and amount != 0)
            )
        )
        if (
            self.source_system != "dotmac-billing"
            or self.source_ref != f"opening-late-item:{self.item.item_id}"
            or _KIND_OF_ITEM[self.item.kind] != self.effect_kind
            or not sign_ok
            or (self.reverses_fact_id is not None) != (self.item.kind == "reversal")
            or (self.item.reverses_item_id is not None)
            != (self.item.kind == "reversal")
        ):
            raise OpeningEvidenceError("opening late fact is internally inconsistent")


@dataclass(frozen=True, slots=True)
class OpeningLateItemV1:
    """A stored item as read back (typed; ``receivable_delta`` is signed)."""

    item_id: UUID
    window_id: UUID
    billing_account_id: UUID
    kind: ItemKind
    document_id: UUID
    posting_group_id: UUID
    receivable_delta: Money
    source_identity: OpeningLateSourceV1
    generation: int | None
    source_total: Money | None
    reverses_item_id: UUID | None
    evidence_ref: str | None
    evidence_digest: str | None
    reason: str | None
    actor_ref: str
    command_id: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class OpenItemTaxSnapshotsV1:
    """Per-document tax evidence of an imported open item or an opening late debit.

    Evidence only: Billing derives and posts nothing from it. ``snapshots`` are
    the source as-recorded rate and basis per component; their tax sums to
    ``tax_total``.
    """

    scope: Scope
    document_id: UUID
    authoring_mode: Literal["imported_open_item", "opening_late_item"]
    tax_total: Money
    snapshots: tuple[AppliedTaxSnapshotV1, ...]
    contract_version: Literal["billing.open.item.tax.snapshots.v1"] = (
        "billing.open.item.tax.snapshots.v1"
    )


@dataclass(frozen=True, slots=True)
class OpeningLatePosting:
    """What ``_post_group`` needs to persist one item row and build its V7 fact."""

    row: dict[str, object]
    fact: OpeningLateItemFactBlockV1


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _tenant_only(scope: Scope) -> TenantScope:
    if not isinstance(scope, TenantScope):
        raise BillingRuleViolation(
            "opening_late_item_platform_unsupported",
            "opening late items are tenant-plane only; the platform plane stays "
            "fail-closed",
        )
    return scope


def _authorize(
    *,
    scope: Scope,
    declared: Scope,
    permissions: ActorPermissionReader | None,
    actor_ref: str,
    texts: tuple[tuple[str, int], ...],
) -> TenantScope:
    tenant = _tenant_only(scope)
    if permissions is None:
        raise BillingRuleViolation(
            "opening_late_item_permission_unavailable",
            "no permission reader is wired; the item cannot be authorized",
        )
    service._require_scope_matches(routed=scope, declared=declared)
    for text, maximum in texts:
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise BillingRuleViolation(
                "opening_late_item_evidence_required",
                "an opening late item requires bounded command, actor and evidence "
                "text",
            )
    if not permissions.holds(
        scope=scope, actor_ref=actor_ref, code=OPENING_LATE_ITEM_PERMISSION
    ):
        raise BillingRuleViolation(
            "opening_late_item_permission_denied",
            "the actor does not hold the opening late item permission",
        )
    return tenant


def _sealed_window(db: Session, *, scope: Scope, source: OpeningLateSourceV1) -> Any:
    window = get_opening_window(db, scope=scope)
    if window is None:
        raise BillingRuleViolation(
            "opening_late_item_window_missing", "no opening window exists"
        )
    if get_opening_window_seal(db, scope=scope) is None:
        raise BillingRuleViolation(
            "opening_late_item_window_not_sealed",
            "opening late items exist only after the opening window is sealed",
        )
    if window.dual_control != "off":
        raise BillingRuleViolation(
            "opening_late_item_dual_control_unsupported",
            "this window binds two-person approval, which opening late items do not "
            "support yet",
        )
    capture = window.manifest.capture
    if (source.installation_id, source.source_system) != (
        capture.installation_id,
        capture.source_system,
    ):
        raise BillingRuleViolation(
            "opening_late_item_namespace_mismatch",
            "the source identity is outside the sealed capture namespace",
        )
    return window


def _require_not_held(db: Session, *, scope: Scope, account_id: UUID) -> None:
    """A held partition takes no opening late item until its governed admission."""

    tenant = _tenant_only(scope)
    if account_id not in get_opening_hold_list(db, scope=scope):
        return
    admitted = db.execute(
        select(models.OpeningHoldAdmission.id).where(
            models.OpeningHoldAdmission.tenant_id == tenant.tenant_id,
            models.OpeningHoldAdmission.billing_account_id == account_id,
        )
    ).first()
    if admitted is None:
        raise BillingRuleViolation(
            "opening_late_item_account_held",
            "the account is a held opening partition that is not yet admitted",
        )


def _digest(value: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise BillingRuleViolation(
            "opening_late_item_evidence_required",
            "the evidence digest must be a lowercase sha-256 hex digest",
        )


def _tenant_row_sum(db: Session, column: Any, *where: Any) -> Decimal:
    return Decimal(
        db.execute(
            select(func.coalesce(func.sum(column), 0)).where(*where)
        ).scalar_one()
    )


def _view(row: models.OpeningLateItem) -> OpeningLateItemV1:
    return OpeningLateItemV1(
        item_id=row.id,
        window_id=row.window_id,
        billing_account_id=row.billing_account_id,
        kind=cast(ItemKind, row.kind),
        document_id=row.document_id,
        posting_group_id=row.posting_group_id,
        receivable_delta=service._money(
            Decimal(row.receivable_delta), row.currency, row.minor_units
        ),
        source_identity=OpeningLateSourceV1(
            row.source_installation_id,
            row.source_system,
            row.source_key,
            row.source_number,
        ),
        generation=row.generation,
        source_total=(
            None
            if row.source_total is None
            else service._money(
                Decimal(row.source_total), row.currency, row.minor_units
            )
        ),
        reverses_item_id=row.reverses_item_id,
        evidence_ref=row.evidence_ref,
        evidence_digest=row.evidence_digest,
        reason=row.reason,
        actor_ref=row.actor_ref,
        command_id=row.command_id,
        recorded_at=row.recorded_at,
    )


def get_opening_late_item(
    db: Session, *, scope: Scope, item_id: UUID
) -> OpeningLateItemV1:
    """Read one stored item (its ``recorded_at`` is the database's)."""

    tenant = _tenant_only(scope)
    row = db.execute(
        select(models.OpeningLateItem).where(
            models.OpeningLateItem.tenant_id == tenant.tenant_id,
            models.OpeningLateItem.id == item_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise BillingRuleViolation(
            "not_found", "OpeningLateItem was not found", row_id=str(item_id)
        )
    db.refresh(row)
    return _view(row)


def opening_late_collection_evidence(
    db: Session, *, scope: Scope, document_id: UUID
) -> OpeningCollectionEvidenceV1:
    """The collection evidence an opening late debit document was recorded with."""

    tenant = _tenant_only(scope)
    found = db.execute(
        select(
            models.OpeningLateItem.collection, models.OpeningLateItem.collection_digest
        ).where(
            models.OpeningLateItem.tenant_id == tenant.tenant_id,
            models.OpeningLateItem.document_id == document_id,
            models.OpeningLateItem.kind == "debit",
        )
    ).one_or_none()
    if found is None or found[0] is None:
        raise BillingRuleViolation(
            "opening_late_item_integrity", "opening late document has no debit evidence"
        )
    stored, collection_digest = found
    if fingerprint_of(stored) != collection_digest:
        raise BillingRuleViolation(
            "opening_late_item_integrity", "stored collection evidence disagrees"
        )
    with localcontext(cash_decimal_context()):
        return cast(
            OpeningCollectionEvidenceV1, _decode(stored, OpeningCollectionEvidenceV1)
        )


def opening_late_debit_initial(
    db: Session, *, scope: Scope, document_id: UUID
) -> Decimal:
    """The debit row's amount: the base of an opening late document's room."""

    tenant = _tenant_only(scope)
    value = db.execute(
        select(models.OpeningLateItem.receivable_delta).where(
            models.OpeningLateItem.tenant_id == tenant.tenant_id,
            models.OpeningLateItem.document_id == document_id,
            models.OpeningLateItem.kind == "debit",
        )
    ).scalar_one_or_none()
    if value is None:
        raise BillingRuleViolation(
            "receivable_backing_integrity", "opening late document has no debit row"
        )
    return Decimal(value)


def opening_late_net_credit(db: Session, *, scope: Scope, document_id: UUID) -> Decimal:
    """Signed net of the document's non-debit items (negative = credited)."""

    if not isinstance(scope, TenantScope):
        return Decimal(0)
    m = models.OpeningLateItem
    return _tenant_row_sum(
        db,
        m.receivable_delta,
        m.tenant_id == scope.tenant_id,
        m.document_id == document_id,
        m.kind != "debit",
    )


def _validate_original(
    original: OpeningOriginalDocumentV1,
    *,
    collection: OpeningCollectionEvidenceV1,
    amount: Money,
    currency: tuple[str, int],
    cutoff: datetime,
) -> None:
    """The opening validator's checks on one source item, applied standalone.

    Mirrors ``opening_receivable_validation`` (document totals, tax evidence,
    FX, length bounds, cutoff, and verified-but-incomplete collection evidence);
    a parity unit test keeps the two in step.
    """

    def refuse(reason: str) -> BillingRuleViolation:
        return BillingRuleViolation("opening_late_debit_original_invalid", reason)

    amounts = (original.subtotal, original.tax_total, original.grand_total, amount)
    if any((x.currency.code, x.currency.minor_units) != currency for x in amounts):
        raise refuse("the source document and amount must use the account currency")
    subtotal, tax, total, residual = (minor_units_of(x) for x in amounts)
    if min(subtotal, tax, total) < 0 or subtotal + tax != total:
        raise refuse("the source totals are inconsistent")
    if not 0 < residual <= total:
        raise refuse("the residual must be positive and within the source total")
    if (
        len(original.document_number) > 255
        or len(original.document_profile_code) > 120
        or len(original.document_profile_version) > 120
        or len(original.locale) > 40
        or len(original.timezone) > 80
    ):
        raise refuse("a source document text field is too long")
    if collection.status == "verified" and not collection.automated_collection_allowed:
        raise BillingRuleViolation(
            "opening_late_debit_collection_incomplete",
            "verified collection evidence must allow automated collection",
        )
    if sum(minor_units_of(x.tax_amount) for x in original.tax_snapshots) != tax:
        raise refuse("the source tax snapshots do not sum to the source tax")
    for snapshot in original.tax_snapshots:
        if (
            not snapshot.rate.is_finite()
            or snapshot.rate < 0
            or (
                snapshot.tax_amount.currency.code,
                snapshot.tax_amount.currency.minor_units,
            )
            != currency
            or snapshot.tax_amount.currency != snapshot.taxable_basis.currency
        ):
            raise refuse("a source tax snapshot is invalid")
    if original.fx_snapshot is not None and (
        not original.fx_snapshot.rate.is_finite() or original.fx_snapshot.rate <= 0
    ):
        raise refuse("the source FX evidence is invalid")
    if original.issued_at > cutoff:
        raise BillingRuleViolation(
            "opening_late_debit_after_cutoff",
            "a source invoice issued after the capture cutoff is not an opening miss",
        )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@service._exact_cash_operation
def record_opening_late_debit(
    db: Session,
    *,
    scope: Scope,
    command: RecordOpeningLateDebitV1,
    permissions: ActorPermissionReader | None = None,
) -> OpeningLateItemV1:
    """Record a late source open invoice as a receivable (Dr AR; no revenue, no tax).

    Refusals, in order: platform scope; no reader wired or permission denied;
    blank or unbounded text; no sealed window, a window binding two-person
    approval, or a source identity outside the sealed capture namespace; an
    invalid source document or residual; a key carried by the opening or by an
    unreversed debit. Idempotent on ``command_id``: the same body returns the
    stored item, a different body is a conflict.
    """

    if type(command) is not RecordOpeningLateDebitV1:
        raise BillingRuleViolation(
            "opening_late_item_command_invalid", "expected RecordOpeningLateDebitV1"
        )
    tenant = _authorize(
        scope=scope,
        declared=command.scope,
        permissions=permissions,
        actor_ref=command.actor_ref,
        texts=(
            (command.command_id, 255),
            (command.actor_ref, 255),
            (command.evidence_ref, 255),
            (command.source.installation_id, 255),
            (command.source.source_system, 120),
            (command.source.key, 255),
        ),
    )
    _digest(command.evidence_digest)
    window = _sealed_window(db, scope=scope, source=command.source)
    plane = service._models(scope)
    account = service._one(
        db,
        scope=scope,
        model=plane.account,
        row_id=command.billing_account_id,
        lock=True,
    )
    service._require_account_money(account, command.amount)
    _require_not_held(db, scope=scope, account_id=account.id)
    _validate_original(
        command.original,
        collection=command.collection,
        amount=command.amount,
        currency=(account.currency, account.minor_units),
        cutoff=window.manifest.capture.economic_cutoff,
    )
    if command.source.number is not None:
        raise BillingRuleViolation(
            "opening_late_item_command_invalid",
            "a debit takes its number from the source document",
        )

    def operation(session: Session) -> dict[str, object]:
        original = command.original
        source = command.source
        m = models.OpeningLateItem
        carried = session.execute(
            select(models.OpeningReceivableBasis.id).where(
                models.OpeningReceivableBasis.tenant_id == tenant.tenant_id,
                models.OpeningReceivableBasis.installation_id == source.installation_id,
                models.OpeningReceivableBasis.source_system == source.source_system,
                models.OpeningReceivableBasis.source_invoice_key == source.key,
            )
        ).first()
        if carried is not None:
            raise BillingConflict(
                "opening_late_debit_key_carried",
                "the source invoice is carried by the opening",
            )
        if any(
            item.source_invoice_key == source.key
            for item in getattr(window.manifest, "receivables", ())
        ):
            raise BillingConflict(
                "opening_late_debit_key_in_opening_population",
                "the source invoice is in the sealed opening population",
            )
        prior = (
            session.execute(
                select(m.id).where(
                    m.tenant_id == tenant.tenant_id,
                    m.kind == "debit",
                    m.source_installation_id == source.installation_id,
                    m.source_system == source.source_system,
                    m.source_key == source.key,
                )
            )
            .scalars()
            .all()
        )
        reversed_ids = (
            set(
                session.execute(
                    select(m.reverses_item_id).where(
                        m.tenant_id == tenant.tenant_id, m.reverses_item_id.in_(prior)
                    )
                ).scalars()
            )
            if prior
            else set()
        )
        if any(item not in reversed_ids for item in prior):
            raise BillingConflict(
                "opening_late_debit_key_claimed",
                "the source invoice already has an unreversed opening late debit",
            )
        generation = len(prior) + 1
        document_id, item_id, group_id = uuid4(), uuid4(), uuid4()
        collection_json = service._jsonable(command.collection)
        session.add(
            plane.document(
                **service._values(
                    scope,
                    {
                        "id": document_id,
                        "billing_account_id": account.id,
                        "obligation_id": None,
                        "authoring_mode": "opening_late_item",
                        "document_kind": "invoice",
                        "lifecycle": "issued",
                        "series_code": None,
                        "document_number": original.document_number,
                        "currency": account.currency,
                        "minor_units": account.minor_units,
                        "subtotal": original.subtotal.amount,
                        "tax_total": original.tax_total.amount,
                        "grand_total": original.grand_total.amount,
                        "due_at": command.collection.due_at,
                        "due_date_basis": service._jsonable(
                            command.collection.due_date_basis
                        ),
                        "document_profile_code": original.document_profile_code,
                        "document_profile_version": original.document_profile_version,
                        "seller_snapshot": service._jsonable(original.seller),
                        "customer_snapshot": service._jsonable(original.customer),
                        "payment_instructions": service._jsonable(
                            original.payment_instructions
                        ),
                        "brand_asset": service._jsonable(original.brand_asset),
                        "locale": original.locale,
                        "timezone": original.timezone,
                        "issued_at": original.issued_at,
                    },
                )
            )
        )
        session.flush()
        write_tax_snapshots(
            session, scope=scope, document_id=document_id, original=original
        )
        session.flush()
        identity = OpeningLateSourceV1(
            source.installation_id,
            source.source_system,
            source.key,
            original.document_number,
        )
        service._post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind=DEBIT_KIND,
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=f"opening-late-item:{item_id}",
            occurred_at=command.occurred_at,
            effects=((EffectLane.RECEIVABLE, command.amount.amount),),
            exposure_document_ids=(document_id,),
            opening_group_id=group_id,
            opening_late=OpeningLatePosting(
                row={
                    "id": item_id,
                    "window_id": window.window_id,
                    "billing_account_id": account.id,
                    "kind": "debit",
                    "source_installation_id": source.installation_id,
                    "source_system": source.source_system,
                    "source_key": source.key,
                    "source_number": None,
                    "generation": generation,
                    "source_total": None,
                    "document_id": document_id,
                    "receivable_delta": command.amount.amount,
                    "currency": account.currency,
                    "minor_units": account.minor_units,
                    "reverses_item_id": None,
                    "collection": collection_json,
                    "collection_digest": fingerprint_of(collection_json),
                    "evidence_ref": command.evidence_ref,
                    "evidence_digest": command.evidence_digest,
                    "reason": None,
                    "actor_ref": command.actor_ref,
                    "command_id": command.command_id,
                },
                fact=OpeningLateItemFactBlockV1(
                    item_id,
                    window.window_id,
                    "debit",
                    document_id,
                    identity,
                    None,
                    command.actor_ref,
                    command.evidence_digest,
                ),
            ),
        )
        return {"item_id": str(item_id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening_late_item.debit",
        key=command.command_id,
        fingerprint=fingerprint_of(service._jsonable(command)),
        operation=operation,
    )
    return get_opening_late_item(
        db, scope=scope, item_id=UUID(str(outcome.result["item_id"]))
    )


@service._exact_cash_operation
def record_opening_late_credit(
    db: Session,
    *,
    scope: Scope,
    command: RecordOpeningLateCreditV1,
    permissions: ActorPermissionReader | None = None,
) -> OpeningLateItemV1:
    """Apply part of a source credit note to one open invoice (Cr AR; no VAT).

    The target is an issued, live invoice (native, imported or an opening late
    debit). The amount may not exceed the target's outstanding less its
    undischarged frozen withholding
    (``opening_late_credit_exceeds_non_withholding_room``);
    the credits of one source credit note stay on one account and within its
    total. Finance can credit into a withholding expectation only after a
    governed release.
    """

    if type(command) is not RecordOpeningLateCreditV1:
        raise BillingRuleViolation(
            "opening_late_item_command_invalid", "expected RecordOpeningLateCreditV1"
        )
    tenant = _authorize(
        scope=scope,
        declared=command.scope,
        permissions=permissions,
        actor_ref=command.actor_ref,
        texts=(
            (command.command_id, 255),
            (command.actor_ref, 255),
            (command.evidence_ref, 255),
            (command.source.installation_id, 255),
            (command.source.source_system, 120),
            (command.source.key, 255),
            (command.source.number or "", 255),
        ),
    )
    _digest(command.evidence_digest)
    window = _sealed_window(db, scope=scope, source=command.source)
    plane = service._models(scope)
    target_view = service._one(
        db, scope=scope, model=plane.document, row_id=command.target_document_id
    )
    account = service._one(
        db,
        scope=scope,
        model=plane.account,
        row_id=target_view.billing_account_id,
        lock=True,
    )
    service._require_same_money(command.amount, command.source_total)
    service._require_account_money(account, command.amount)
    _require_not_held(db, scope=scope, account_id=account.id)
    if (
        not command.amount.is_positive
        or command.amount.amount > command.source_total.amount
    ):
        raise BillingRuleViolation(
            "opening_late_credit_amount_invalid",
            "the credit must be positive and within the source credit note total",
        )

    def operation(session: Session) -> dict[str, object]:
        source = command.source
        target = service._one(
            session,
            scope=scope,
            model=plane.document,
            row_id=command.target_document_id,
            lock=True,
        )
        service._require_credit_target(
            session, scope=scope, document=target, account_id=account.id
        )
        service._require_live_invoice_issuance(session, scope=scope, document=target)
        room = service._document_outstanding(
            session, scope=scope, document=target
        ) - service._undischarged_frozen_withholding(
            session, scope=scope, document_id=target.id
        )
        if command.amount.amount > room:
            raise BillingRuleViolation(
                "opening_late_credit_exceeds_non_withholding_room",
                "the credit exceeds the invoice's room outside its expected "
                "withholding; release the expectation first",
            )
        m = models.OpeningLateItem
        siblings = (
            session.execute(
                select(m).where(
                    m.tenant_id == tenant.tenant_id,
                    m.generation.is_(None),
                    m.source_installation_id == source.installation_id,
                    m.source_system == source.source_system,
                    m.source_key == source.key,
                )
            )
            .scalars()
            .all()
        )
        if any(row.billing_account_id != account.id for row in siblings):
            raise BillingRuleViolation(
                "opening_late_credit_source_spread",
                "one source credit note stays on one account",
            )
        if any(
            row.source_total is None
            or Decimal(row.source_total) != command.source_total.amount
            or row.source_number != source.number
            for row in siblings
        ):
            raise BillingRuleViolation(
                "opening_late_credit_source_total_differs",
                "every credit of one source note carries the same total and number",
            )
        applied = -sum((Decimal(row.receivable_delta) for row in siblings), Decimal(0))
        if applied + command.amount.amount > command.source_total.amount:
            raise BillingRuleViolation(
                "opening_late_credit_exceeds_source_total",
                "the credits of this source note would exceed its total",
            )
        item_id, group_id = uuid4(), uuid4()
        service._post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind=CREDIT_KIND,
            currency=account.currency,
            minor_units=account.minor_units,
            source_ref=f"opening-late-item:{item_id}",
            occurred_at=command.occurred_at,
            effects=((EffectLane.RECEIVABLE, -command.amount.amount),),
            exposure_document_ids=(target.id,),
            opening_group_id=group_id,
            opening_late=OpeningLatePosting(
                row={
                    "id": item_id,
                    "window_id": window.window_id,
                    "billing_account_id": account.id,
                    "kind": "credit",
                    "source_installation_id": source.installation_id,
                    "source_system": source.source_system,
                    "source_key": source.key,
                    "source_number": source.number,
                    "generation": None,
                    "source_total": command.source_total.amount,
                    "document_id": target.id,
                    "receivable_delta": -command.amount.amount,
                    "currency": account.currency,
                    "minor_units": account.minor_units,
                    "reverses_item_id": None,
                    "collection": None,
                    "collection_digest": None,
                    "evidence_ref": command.evidence_ref,
                    "evidence_digest": command.evidence_digest,
                    "reason": None,
                    "actor_ref": command.actor_ref,
                    "command_id": command.command_id,
                },
                fact=OpeningLateItemFactBlockV1(
                    item_id,
                    window.window_id,
                    "credit",
                    target.id,
                    source,
                    None,
                    command.actor_ref,
                    command.evidence_digest,
                ),
            ),
        )
        return {"item_id": str(item_id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening_late_item.credit",
        key=command.command_id,
        fingerprint=fingerprint_of(service._jsonable(command)),
        operation=operation,
    )
    return get_opening_late_item(
        db, scope=scope, item_id=UUID(str(outcome.result["item_id"]))
    )


@service._exact_cash_operation
def reverse_opening_late_item(
    db: Session,
    *,
    scope: Scope,
    command: ReverseOpeningLateItemV1,
    permissions: ActorPermissionReader | None = None,
) -> OpeningLateItemV1:
    """Reverse one debit (only while its invoice is untouched) or credit in full.

    Appends the exact negation and a reversing posting group linked to the
    original group and V7 fact. A corrected amount is a new item (a debit for
    the same source key takes the next generation). The generic void and posting
    reversal refuse these documents and groups.
    """

    if type(command) is not ReverseOpeningLateItemV1:
        raise BillingRuleViolation(
            "opening_late_item_command_invalid", "expected ReverseOpeningLateItemV1"
        )
    tenant = _authorize(
        scope=scope,
        declared=command.scope,
        permissions=permissions,
        actor_ref=command.actor_ref,
        texts=(
            (command.command_id, 255),
            (command.actor_ref, 255),
            (command.reason, 2000),
        ),
    )
    plane = service._models(scope)
    original = db.execute(
        select(models.OpeningLateItem).where(
            models.OpeningLateItem.tenant_id == tenant.tenant_id,
            models.OpeningLateItem.id == command.item_id,
        )
    ).scalar_one_or_none()
    if original is None:
        raise BillingRuleViolation(
            "not_found", "OpeningLateItem was not found", row_id=str(command.item_id)
        )
    account = service._one(
        db,
        scope=scope,
        model=plane.account,
        row_id=original.billing_account_id,
        lock=True,
    )
    _require_not_held(db, scope=scope, account_id=account.id)

    def operation(session: Session) -> dict[str, object]:
        document = service._one(
            session,
            scope=scope,
            model=plane.document,
            row_id=original.document_id,
            lock=True,
        )
        if original.kind == "reversal":
            raise BillingRuleViolation(
                "opening_late_item_reversal_terminal", "a reversal cannot be reversed"
            )
        m = models.OpeningLateItem
        if (
            session.execute(
                select(m.id).where(
                    m.tenant_id == tenant.tenant_id, m.reverses_item_id == original.id
                )
            ).first()
            is not None
        ):
            raise BillingRuleViolation(
                "opening_late_item_already_reversed",
                "the item already has its reversal",
            )
        if original.kind == "debit" and service._document_outstanding(
            session, scope=scope, document=document
        ) != Decimal(original.receivable_delta):
            raise BillingRuleViolation(
                "opening_late_debit_touched",
                "a debit is reversible only while its invoice is untouched",
            )
        window = get_opening_window(session, scope=scope)
        if window is None or window.window_id != original.window_id:
            raise BillingRuleViolation(
                "opening_late_item_window_missing", "the item's window is unavailable"
            )
        original_group = service._one(
            session, scope=scope, model=plane.group, row_id=original.posting_group_id
        )
        number = original.source_number or document.document_number
        item_id, group_id = uuid4(), uuid4()
        service._post_group(
            session,
            scope=scope,
            account_id=account.id,
            kind=REVERSAL_KIND,
            currency=original.currency,
            minor_units=original.minor_units,
            source_ref=f"opening-late-item:{item_id}",
            occurred_at=session.execute(select(func.clock_timestamp())).scalar_one(),
            effects=((EffectLane.RECEIVABLE, -Decimal(original.receivable_delta)),),
            exposure_document_ids=(document.id,),
            reverses_group_id=original_group.id,
            opening_group_id=group_id,
            opening_late=OpeningLatePosting(
                row={
                    "id": item_id,
                    "window_id": original.window_id,
                    "billing_account_id": account.id,
                    "kind": "reversal",
                    "source_installation_id": original.source_installation_id,
                    "source_system": original.source_system,
                    "source_key": original.source_key,
                    "source_number": original.source_number,
                    "generation": original.generation,
                    "source_total": original.source_total,
                    "document_id": document.id,
                    "receivable_delta": -Decimal(original.receivable_delta),
                    "currency": original.currency,
                    "minor_units": original.minor_units,
                    "reverses_item_id": original.id,
                    "collection": None,
                    "collection_digest": None,
                    "evidence_ref": None,
                    "evidence_digest": None,
                    "reason": command.reason,
                    "actor_ref": command.actor_ref,
                    "command_id": command.command_id,
                },
                fact=OpeningLateItemFactBlockV1(
                    item_id,
                    original.window_id,
                    "reversal",
                    document.id,
                    OpeningLateSourceV1(
                        original.source_installation_id,
                        original.source_system,
                        original.source_key,
                        number,
                    ),
                    original.id,
                    command.actor_ref,
                    None,
                ),
            ),
        )
        return {"item_id": str(item_id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope="billing.opening_late_item.reverse",
        key=command.command_id,
        fingerprint=fingerprint_of(service._jsonable(command)),
        operation=operation,
    )
    return get_opening_late_item(
        db, scope=scope, item_id=UUID(str(outcome.result["item_id"]))
    )


# --------------------------------------------------------------------------
# Per-document tax evidence (imported open items and opening late debits)
# --------------------------------------------------------------------------


def write_tax_snapshots(
    db: Session,
    *,
    scope: Scope,
    document_id: UUID,
    original: OpeningOriginalDocumentV1,
) -> None:
    """One ``applied_tax_snapshots`` row per source tax component (document parent)."""

    plane = service._models(scope)
    for snapshot in original.tax_snapshots:
        db.add(
            plane.tax(
                **service._values(
                    scope,
                    {
                        "id": uuid4(),
                        "obligation_id": None,
                        "document_id": document_id,
                        "treatment_code": snapshot.treatment_code,
                        "jurisdiction_code": snapshot.jurisdiction_code,
                        "policy_id": snapshot.policy_id,
                        "policy_version": snapshot.policy_version,
                        "rate": snapshot.rate,
                        "taxable_basis": snapshot.taxable_basis.amount,
                        "tax_amount": snapshot.tax_amount.amount,
                        "currency": snapshot.tax_amount.currency.code,
                        "minor_units": snapshot.tax_amount.currency.minor_units,
                    },
                )
            )
        )


def get_open_item_tax_snapshots(
    db: Session, *, scope: Scope, document_id: UUID
) -> OpenItemTaxSnapshotsV1:
    """The source tax evidence of one imported open item or opening late debit.

    An imported item's evidence is the verified original document stored with its
    opening basis (the restricted opening principal writes no other tax rows); a
    opening late debit's is its per-document ``applied_tax_snapshots`` rows. Native
    documents are refused (they have invoice document facts). When the document's
    tax is non-zero and there are no snapshots the read refuses
    ``open_item_tax_snapshots_unavailable``; nothing is backfilled. Read-only,
    lock-free; the caller owns the transaction.
    """

    if not isinstance(document_id, UUID):
        raise BillingRuleViolation(
            "invalid_document_id", "document identity must be a UUID"
        )
    documents = _table(scope, "documents")
    snapshots = _table(scope, "applied_tax_snapshots")
    bases = _table(scope, "opening_receivable_bases")
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
        if document is None:
            raise BillingRuleViolation("not_found", "document was not found")
        mode = document["authoring_mode"]
        if mode not in ("imported_open_item", "opening_late_item"):
            raise BillingRuleViolation(
                "open_item_tax_snapshots_native_document",
                "a native document's tax evidence is in its invoice document facts",
            )
        currency = (document["currency"], document["minor_units"])
        tax_total = service._money(Decimal(document["tax_total"]), *currency)
        found: tuple[AppliedTaxSnapshotV1, ...]
        if mode == "imported_open_item":
            basis_id = db.execute(
                _scoped(
                    select(bases.c.id).where(bases.c.document_id == document_id),
                    scope,
                    bases,
                )
            ).scalar_one_or_none()
            if basis_id is None:
                raise BillingRuleViolation(
                    "open_item_tax_snapshots_unavailable",
                    "the imported document has no opening basis",
                )
            original = get_opening_receivable_basis(
                db, scope=scope, basis_id=basis_id
            ).source.original
            found = () if original is None else tuple(original.tax_snapshots)
        else:
            rows = (
                db.execute(
                    _scoped(
                        select(snapshots).where(snapshots.c.document_id == document_id),
                        scope,
                        snapshots,
                    )
                )
                .mappings()
                .all()
            )
            found = tuple(
                AppliedTaxSnapshotV1(
                    treatment_code=row["treatment_code"],
                    jurisdiction_code=row["jurisdiction_code"],
                    policy_id=row["policy_id"],
                    policy_version=row["policy_version"],
                    rate=Decimal(row["rate"]),
                    taxable_basis=service._money(
                        Decimal(row["taxable_basis"]),
                        row["currency"],
                        row["minor_units"],
                    ),
                    tax_amount=service._money(
                        Decimal(row["tax_amount"]), row["currency"], row["minor_units"]
                    ),
                )
                for row in rows
            )
    items = tuple(
        sorted(
            found,
            key=lambda s: (
                s.treatment_code,
                s.jurisdiction_code,
                s.policy_id,
                s.policy_version,
                str(s.rate),
                str(s.taxable_basis.amount),
                str(s.tax_amount.amount),
            ),
        )
    )
    if tax_total.amount != 0 and not items:
        raise BillingRuleViolation(
            "open_item_tax_snapshots_unavailable",
            "no tax snapshots were recorded for this document",
        )
    if sum((s.tax_amount.amount for s in items), Decimal(0)) != tax_total.amount:
        raise BillingRuleViolation(
            "open_item_tax_snapshots_inconsistent",
            "the stored tax snapshots do not sum to the document tax",
        )
    return OpenItemTaxSnapshotsV1(
        scope=scope,
        document_id=document_id,
        authoring_mode=cast(Any, mode),
        tax_total=tax_total,
        snapshots=items,
    )


def get_accounting_fact_v7(
    db: Session, *, scope: Scope, fact_id: UUID
) -> AccountingFactV7:
    """Read one stored V7 (opening late item) accounting fact by id.

    Digest, envelope, posting group, receivable effect and the item row are
    cross-checked; a fact of another version, tenant or an unknown id is
    ``not_found``. Pure read.
    """

    tenant = _tenant_only(scope)
    if not isinstance(fact_id, UUID):
        raise BillingRuleViolation(
            "invalid_fact_id", "accounting fact identity must be a UUID"
        )
    table = _table(scope, "accounting_facts")
    with db.no_autoflush:
        row = (
            db.execute(
                _scoped(
                    select(table).where(
                        table.c.id == fact_id, table.c.fact_version == 7
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
                "not_found", "exact V7 accounting fact was not found"
            )
        try:
            if fingerprint_of(row["fact_payload"]) != row["fact_digest"]:
                raise ValueError("accounting digest disagrees")
            with localcontext(cash_decimal_context()):
                fact = cast(
                    AccountingFactV7, _decode(row["fact_payload"], AccountingFactV7)
                )
            item = db.execute(
                select(models.OpeningLateItem).where(
                    models.OpeningLateItem.tenant_id == tenant.tenant_id,
                    models.OpeningLateItem.posting_group_id == fact.posting_group_id,
                )
            ).scalar_one_or_none()
            group = db.execute(
                select(models.PostingGroup).where(
                    models.PostingGroup.tenant_id == tenant.tenant_id,
                    models.PostingGroup.id == fact.posting_group_id,
                )
            ).scalar_one_or_none()
            if not (
                fact.scope == scope
                and fact.fact_id == row["id"]
                and fact.posting_group_id == row["posting_group_id"]
                and fact.effect_kind == row["effect_kind"]
                and fact.occurred_at == row["occurred_at"]
                and fact.reverses_fact_id == row["reverses_fact_id"]
                and item is not None
                and group is not None
                and item.id == fact.item.item_id
                and item.kind == fact.item.kind
                and item.document_id == fact.item.document_id
                and item.billing_account_id == fact.billing_account_id
                and Decimal(item.receivable_delta)
                == fact.effects[0].amount_delta.amount
                and group.group_kind == fact.effect_kind
                and group.source_ref == fact.source_ref
                and group.billing_account_id == fact.billing_account_id
            ):
                raise ValueError("accounting envelope disagrees")
        except (ValueError, TypeError) as exc:
            raise BillingRuleViolation(
                "accounting_fact_integrity", "stored accounting evidence is invalid"
            ) from exc
    return fact


def exposure_fields_of(
    evidence: OpeningCollectionEvidenceV1, *, account_id: UUID
) -> tuple[str, str | None, CollectionTiming, ServicePeriodEvidenceV1]:
    """Exposure identity fields an opening late debit takes from its evidence.

    An unverified item has no subject or timing; the account subject convention
    and ``arrears`` stand in (the same default the money-flows reader applies).
    """

    return (
        evidence.subject_ref or f"billing-account:{account_id}",
        evidence.service_ref,
        evidence.collection_timing or "arrears",
        evidence.service_period,
    )


def exposure_due(
    evidence: OpeningCollectionEvidenceV1,
    due_at: datetime | None,
    basis: DueDateBasisV1,
) -> tuple[datetime | None, DueDateBasisV1]:
    """Due instant and basis a V1 exposure may carry for an opening late debit.

    Only evidence that allows automated collection (verified, with subject,
    timing, due date and a verified basis, exactly as the V2 reader requires)
    keeps its due instant and basis. Anything else reads as an unverified basis
    with no due instant, so no access hold can be derived from unverified data.
    """

    if (
        evidence.automated_collection_allowed
        and basis.status is DueDateBasisStatus.VERIFIED
    ):
        return due_at, basis
    return None, DueDateBasisV1.unknown_unverified(
        source_authority=evidence.source_authority, evidence_ref=evidence.evidence_ref
    )


__all__ = [
    "ACCOUNTING_FACT_V7_CONTRACT",
    "AccountingFactV7",
    "OpeningLateItemFactBlockV1",
    "OpeningLateItemV1",
    "OpeningLatePosting",
    "OpeningLateSourceV1",
    "OpenItemTaxSnapshotsV1",
    "RecordOpeningLateCreditV1",
    "RecordOpeningLateDebitV1",
    "ReverseOpeningLateItemV1",
    "exposure_due",
    "exposure_fields_of",
    "get_accounting_fact_v7",
    "get_opening_late_item",
    "get_open_item_tax_snapshots",
    "opening_late_collection_evidence",
    "opening_late_debit_initial",
    "opening_late_net_credit",
    "record_opening_late_credit",
    "record_opening_late_debit",
    "reverse_opening_late_item",
    "write_tax_snapshots",
]
