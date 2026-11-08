"""Opening variance ledger: writers called by the governed paths, and reads.

Nothing here decides what a variance is: the frozen manifest (one fingerprint
binds transfer and variance) carries every record, produced from a digest-bound
rules document. These functions only persist those records inside the caller's
transaction, append lifecycle events, and read the ledger back. The database
(bi_0014) independently refuses an invalid record, a wrong resolution kind for
the basis, an over-resolution, and a seal that does not close.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_contracts import OpeningPartitionV1
from dotmac_billing.opening_queries import _scoped, _table
from dotmac_billing.opening_receivable_contracts import OpeningManifestV2
from dotmac_billing.opening_variance_contracts import (
    HISTORY_FIELDS,
    LANES,
    LEDGER_BASIS,
    OpeningControlReportV1,
    OpeningControlRowV1,
    OpeningHistoryRowV1,
    OpeningHoldRowV1,
    OpeningReasonTotalV1,
    OpeningVarianceRecordV1,
    OpeningVarianceV1,
    is_customer_adverse,
)
from dotmac_billing.opening_window_queries import (
    get_opening_window,
    get_opening_window_seal,
)

#: Emits one ledger-lane fact: ``(kind, payload)``. The service owns the outbox.
FactEmitter = Callable[[str, dict[str, object]], None]


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    """What Approvals vouched for: stored on both the resolving and resolved event."""

    request_id: UUID
    maker_ref: str
    approver_refs: tuple[str, ...]


_LANE_FIELD = {
    "receivable": "collectible_receivable_minor",
    "customer_credit": "unapplied_customer_credit_minor",
    "prepaid_funding": "prepaid_funding_reserved_minor",
}


def _vals(scope: Scope, values: dict[str, object]) -> dict[str, object]:
    return (
        {"tenant_id": scope.tenant_id, **values}
        if isinstance(scope, TenantScope)
        else values
    )


def variance_fact_payload(
    variance_id: UUID, window_id: UUID, v: OpeningVarianceV1, *, kind: str
) -> dict[str, object]:
    """Ledger-lane facts only: memo bases post nothing (design 3.5)."""
    return {
        "fact_version": 1,
        "kind": kind,
        "variance_id": str(variance_id),
        "window_id": str(window_id),
        "billing_account_id": None if v.account_id is None else str(v.account_id),
        "currency": v.currency,
        "minor_units": v.precision,
        "lane": v.lane,
        "reason_code": v.reason_code,
        "amount_minor": v.amount_minor,
        "customer_adverse": is_customer_adverse(v.lane, v.amount_minor),
        "evidence_digest": v.evidence_digest,
    }


def _latest_kind(db: Session, scope: Scope, variance_id: UUID) -> str | None:
    events = _table(scope, "opening_variance_events")
    row = db.execute(
        _scoped(
            select(events.c.kind)
            .where(events.c.variance_id == variance_id)
            .order_by(events.c.seq.desc())
            .limit(1),
            scope,
            events,
        )
    ).scalar_one_or_none()
    return None if row is None else str(row)


def _append_event(
    db: Session,
    scope: Scope,
    *,
    window_id: UUID,
    variance_id: UUID,
    account_id: UUID | None,
    kind: str,
    actor: str,
    occurred_at: datetime,
    **extra: object,
) -> None:
    db.execute(
        insert(_table(scope, "opening_variance_events")).values(
            **_vals(
                scope,
                {
                    "id": uuid4(),
                    "window_id": window_id,
                    "variance_id": variance_id,
                    "billing_account_id": account_id,
                    "kind": kind,
                    "actor": actor,
                    "occurred_at": occurred_at,
                    **extra,
                },
            )
        )
    )


def _record(
    db: Session,
    scope: Scope,
    *,
    window_id: UUID,
    v: OpeningVarianceV1,
    receipt_id: UUID | None,
    actor: str,
    occurred_at: datetime,
    emit: FactEmitter | None,
) -> bool:
    """Insert one variance and its ``recorded`` event; a replay adds nothing.

    Identity includes the import receipt: a void is terminal, so a re-import
    after an unwind inserts fresh rows bound to its own receipt.
    """
    table = _table(scope, "opening_variances")
    existing = db.execute(
        _scoped(
            select(table.c.id).where(
                table.c.window_id == window_id,
                table.c.billing_account_id.is_(None)
                if v.account_id is None
                else table.c.billing_account_id == v.account_id,
                table.c.import_receipt_id.is_(None)
                if receipt_id is None
                else table.c.import_receipt_id == receipt_id,
                table.c.currency == v.currency,
                table.c.lane == v.lane,
                table.c.basis == v.basis,
                table.c.reason_code == v.reason_code,
                table.c.source_kind == v.source.kind,
                table.c.source_ref == v.source.key,
            ),
            scope,
            table,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    variance_id = uuid4()
    db.execute(
        insert(table).values(
            **_vals(
                scope,
                {
                    "id": variance_id,
                    "window_id": window_id,
                    "scope_level": v.level,
                    "billing_account_id": v.account_id,
                    "currency": v.currency,
                    "minor_units": v.precision,
                    "lane": v.lane,
                    "basis": v.basis,
                    "reason_code": v.reason_code,
                    "source_kind": v.source.kind,
                    "source_ref": v.source.key,
                    "import_receipt_id": receipt_id,
                    "amount_minor": v.amount_minor,
                    "evidence_digest": v.evidence_digest,
                    "rules_digest": v.rules_digest,
                },
            )
        )
    )
    _append_event(
        db,
        scope,
        window_id=window_id,
        variance_id=variance_id,
        account_id=v.account_id,
        kind="recorded",
        actor=actor,
        occurred_at=occurred_at,
    )
    if emit is not None and v.basis == LEDGER_BASIS:
        emit(
            "recorded",
            variance_fact_payload(variance_id, window_id, v, kind="recorded"),
        )
    return True


def record_partition_variances(
    db: Session,
    *,
    scope: Scope,
    window_id: UUID,
    manifest: OpeningManifestV2,
    partition: OpeningPartitionV1,
    receipt_id: UUID,
    actor: str,
    occurred_at: datetime,
    emit: FactEmitter | None,
) -> int:
    """Persist the partition's manifest variances and its control gap (if any).

    Returns the number of newly recorded variances. Called from the shared V2
    import body, so a held-partition admission inherits it unchanged.
    """
    created = 0
    for v in manifest.variances:
        if v.partition == partition:
            created += _record(
                db,
                scope,
                window_id=window_id,
                v=v,
                receipt_id=receipt_id,
                actor=actor,
                occurred_at=occurred_at,
                emit=emit,
            )
    measure = next((m for m in manifest.measures if m.partition == partition), None)
    if measure is not None and measure.rebuild_minor is None:
        gaps = _table(scope, "opening_control_gaps")
        present = db.execute(
            _scoped(
                select(gaps.c.id).where(
                    gaps.c.window_id == window_id,
                    gaps.c.billing_account_id == partition.account_id,
                    gaps.c.import_receipt_id == receipt_id,
                    gaps.c.basis == "rebuild",
                ),
                scope,
                gaps,
            )
        ).scalar_one_or_none()
        if present is None:
            gap_id = uuid4()
            reason = measure.rebuild_gap_reason or ""
            db.execute(
                insert(gaps).values(
                    **_vals(
                        scope,
                        {
                            "id": gap_id,
                            "window_id": window_id,
                            "billing_account_id": partition.account_id,
                            "basis": "rebuild",
                            "reason_code": reason,
                            "import_receipt_id": receipt_id,
                            "evidence_digest": fingerprint_of(
                                {"gap": "rebuild", "reason": reason}
                            ),
                        },
                    )
                )
            )
            db.execute(
                insert(_table(scope, "opening_control_gap_events")).values(
                    **_vals(
                        scope,
                        {
                            "id": uuid4(),
                            "window_id": window_id,
                            "gap_id": gap_id,
                            "billing_account_id": partition.account_id,
                            "kind": "recorded",
                            "actor": actor,
                            "occurred_at": occurred_at,
                        },
                    )
                )
            )
    db.flush()
    return created


def record_batch_variances(
    db: Session,
    *,
    scope: Scope,
    window_id: UUID,
    manifest: OpeningManifestV2,
    actor: str,
    occurred_at: datetime,
    emit: FactEmitter | None,
) -> int:
    """Batch-scope variances are recorded by the seal, before the seal row."""
    created = 0
    for v in manifest.variances:
        if v.level == "batch":
            created += _record(
                db,
                scope,
                window_id=window_id,
                v=v,
                receipt_id=None,
                actor=actor,
                occurred_at=occurred_at,
                emit=emit,
            )
    db.flush()
    return created


def void_partition_variances(
    db: Session,
    *,
    scope: Scope,
    window_id: UUID,
    account_id: UUID,
    actor: str,
    occurred_at: datetime,
) -> int:
    """Pre-seal import reversal: void the account's recorded variances and gaps.

    The void is terminal. The database refuses it unless the row's import
    receipt has an unwind receipt.
    """
    table = _table(scope, "opening_variances")
    rows = (
        db.execute(
            _scoped(
                select(table.c.id).where(
                    table.c.window_id == window_id,
                    table.c.billing_account_id == account_id,
                ),
                scope,
                table,
            )
        )
        .scalars()
        .all()
    )
    voided = 0
    for variance_id in rows:
        if _latest_kind(db, scope, variance_id) == "recorded":
            _append_event(
                db,
                scope,
                window_id=window_id,
                variance_id=variance_id,
                account_id=account_id,
                kind="voided_by_import_reversal",
                actor=actor,
                occurred_at=occurred_at,
            )
            voided += 1
    gaps = _table(scope, "opening_control_gaps")
    gap_events = _table(scope, "opening_control_gap_events")
    for gap_id in (
        db.execute(
            _scoped(
                select(gaps.c.id).where(
                    gaps.c.window_id == window_id,
                    gaps.c.billing_account_id == account_id,
                ),
                scope,
                gaps,
            )
        )
        .scalars()
        .all()
    ):
        latest = db.execute(
            _scoped(
                select(gap_events.c.kind)
                .where(gap_events.c.gap_id == gap_id)
                .order_by(gap_events.c.seq.desc())
                .limit(1),
                scope,
                gap_events,
            )
        ).scalar_one_or_none()
        if latest == "recorded":
            db.execute(
                insert(gap_events).values(
                    **_vals(
                        scope,
                        {
                            "id": uuid4(),
                            "window_id": window_id,
                            "gap_id": gap_id,
                            "billing_account_id": account_id,
                            "kind": "voided_by_import_reversal",
                            "actor": actor,
                            "occurred_at": occurred_at,
                        },
                    )
                )
            )
    db.flush()
    return voided


def require_recognisable(db: Session, *, scope: Scope, variance_id: UUID) -> None:
    """Q6: ``recognised`` never resolves a customer-adverse variance.

    Otherwise the customer stays over-collected, the DV-3 pause lifts and
    Collections pursues money the authoritative lane says is not owed. Adverse
    variances resolve only by ``position_corrected``. The database refuses it too.
    """
    table = _table(scope, "opening_variances")
    row = (
        db.execute(
            _scoped(select(table).where(table.c.id == variance_id), scope, table)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BillingRuleViolation("variance_not_found", "opening variance not found")
    if row["basis"] != LEDGER_BASIS or is_customer_adverse(
        row["lane"], int(row["amount_minor"])
    ):
        raise BillingRuleViolation(
            "variance_recognise_adverse",
            "only a non-adverse ledger-lane variance is recognised; a "
            "customer-adverse one is resolved by position_corrected",
        )


def require_group_not_variance_resolution(
    db: Session, *, scope: Scope, group_id: UUID
) -> None:
    """Finding 8 (v1, fail closed): a posting group a variance resolution
    references is never reversed; a reopen path is a named deferral."""
    events = _table(scope, "opening_variance_events")
    # Historical-revision canaries run current code against older schemas.
    qualified = f"{events.schema}.{events.name}"
    if db.scalar(select(func.to_regclass(qualified))) is None:
        return
    found = db.execute(
        _scoped(
            select(events.c.id).where(events.c.posting_group_id == group_id).limit(1),
            scope,
            events,
        )
    ).first()
    if found is not None:
        raise BillingRuleViolation(
            "variance_resolution_locked",
            "this posting group resolves an opening variance and cannot be reversed",
        )


def require_resolution_primitive(
    db: Session,
    *,
    scope: Scope,
    variance_id: UUID,
    primitive: str,
    apply_on_issue: bool,
) -> None:
    """Q2: a credit note or grant resolves only a receivable-lane, customer-adverse
    variance (and a grant only with ``apply_on_issue``); refuse early otherwise.

    The database refuses the same shapes; this keeps the primitive from posting
    first. B7 (lane-neutral reallocation) and B1/IssueCharge stay unwired.
    """
    table = _table(scope, "opening_variances")
    row = (
        db.execute(
            _scoped(select(table).where(table.c.id == variance_id), scope, table)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BillingRuleViolation("variance_not_found", "opening variance not found")
    if (
        row["basis"] != LEDGER_BASIS
        or row["lane"] != "receivable"
        or int(row["amount_minor"]) >= 0
        or (primitive == "grant" and not apply_on_issue)
    ):
        raise BillingRuleViolation(
            "variance_resolution_lane_effect_missing",
            "only a receivable-lane customer-adverse variance is resolved by a "
            "credit note, or a grant applied on issue",
        )


def resolve_variance(
    db: Session,
    *,
    scope: Scope,
    variance_id: UUID,
    resolution_kind: str,
    amount_minor: int,
    approval: ApprovalEvidence,
    occurred_at: datetime,
    posting_group_id: UUID | None = None,
    gl_target_class: str | None = None,
    evidence_ref: str | None = None,
    emit: FactEmitter | None = None,
) -> str:
    """Append ``resolving`` then ``resolved``/``partially_resolved`` (one unit).

    One record at a time; nothing expires, auto-closes or resolves in bulk. The
    caller has already verified maker-checker. The database enforces the sign,
    the cumulative bound and the kind per basis; a violation rolls the caller's
    unit of work back.
    """
    table = _table(scope, "opening_variances")
    row = (
        db.execute(
            _scoped(select(table).where(table.c.id == variance_id), scope, table)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BillingRuleViolation("variance_not_found", "opening variance not found")
    window = get_opening_window(db, scope=scope)
    if window is None or get_opening_window_seal(db, scope=scope) is None:
        raise BillingRuleViolation(
            "variance_window_not_sealed", "variances resolve only after the seal"
        )
    events = _table(scope, "opening_variance_events")
    done = (
        db.execute(
            _scoped(
                select(events.c.amount_minor).where(
                    events.c.variance_id == variance_id,
                    events.c.kind.in_(("resolved", "partially_resolved")),
                ),
                scope,
                events,
            )
        )
        .scalars()
        .all()
    )
    total = sum(int(x) for x in done) + amount_minor
    kind = "resolved" if total == row["amount_minor"] else "partially_resolved"
    if (
        amount_minor == 0
        or (amount_minor > 0) != (row["amount_minor"] > 0)
        or abs(total) > abs(row["amount_minor"])
    ):
        raise BillingRuleViolation(
            "variance_resolution_bound",
            "resolution must carry the variance sign within its remaining amount",
        )
    approval_fields = {
        "maker_ref": approval.maker_ref,
        "approver_refs": list(approval.approver_refs),
        "approval_request_id": approval.request_id,
    }
    for event_kind, extra in (
        ("resolving", approval_fields),
        (
            kind,
            {
                **approval_fields,
                "resolution_kind": resolution_kind,
                "amount_minor": amount_minor,
                "posting_group_id": posting_group_id,
                "gl_target_class": gl_target_class,
                "evidence_ref": evidence_ref,
            },
        ),
    ):
        _append_event(
            db,
            scope,
            window_id=row["window_id"],
            variance_id=variance_id,
            account_id=row["billing_account_id"],
            kind=event_kind,
            actor=approval.maker_ref,
            occurred_at=occurred_at,
            **extra,
        )
    db.flush()
    if emit is not None and row["basis"] == LEDGER_BASIS:
        emit(
            "recognised" if resolution_kind == "recognised" else "resolved",
            {
                "fact_version": 1,
                "kind": resolution_kind,
                "variance_id": str(variance_id),
                "window_id": str(row["window_id"]),
                "billing_account_id": None
                if row["billing_account_id"] is None
                else str(row["billing_account_id"]),
                "currency": row["currency"],
                "minor_units": row["minor_units"],
                "lane": row["lane"],
                "amount_minor": amount_minor,
                "gl_target_class": gl_target_class,
                "posting_group_id": None
                if posting_group_id is None
                else str(posting_group_id),
                "approval_request_id": str(approval.request_id),
                "customer_adverse": is_customer_adverse(
                    row["lane"], int(row["amount_minor"])
                ),
            },
        )
    return kind


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def _states(db: Session, scope: Scope, window_id: UUID) -> dict[UUID, tuple[str, int]]:
    events = _table(scope, "opening_variance_events")
    state: dict[UUID, tuple[str, int]] = {}
    rows = db.execute(
        _scoped(
            select(events.c.variance_id, events.c.kind, events.c.amount_minor)
            .where(events.c.window_id == window_id)
            .order_by(events.c.seq),
            scope,
            events,
        )
    ).all()
    for variance_id, kind, amount in rows:
        _prev, done = state.get(variance_id, ("", 0))
        if kind in ("resolved", "partially_resolved"):
            done += int(amount)
        state[variance_id] = (kind, done)
    return state


_STATE_NAME = {
    "voided_by_import_reversal": "voided",
}


_OPEN_STATES = frozenset({"recorded", "resolving", "partially_resolved"})


def list_opening_variances(
    db: Session,
    *,
    scope: Scope,
    after: tuple[datetime, UUID] | None = None,
    limit: int = 500,
    reason_code: str | None = None,
    state: str | None = None,
    account_id: UUID | None = None,
    open_customer_adverse: bool = False,
) -> tuple[tuple[OpeningVarianceRecordV1, ...], tuple[datetime, UUID] | None]:
    """Keyset-paged (created_at, id) variances with derived lifecycle state.

    ``open_customer_adverse`` (Q8) keeps only variances that are still open and
    customer-adverse; ST-13 lifts a Collections pause from this query state, not
    by counting events. Filters apply before the page limit, so a full page
    means more may follow.
    """
    if not 1 <= limit <= 5000:
        raise BillingRuleViolation("invalid_limit", "page size must be 1..5000")
    window = get_opening_window(db, scope=scope)
    if window is None:
        return (), None
    table = _table(scope, "opening_variances")
    states = _states(db, scope, window.window_id)
    records: list[OpeningVarianceRecordV1] = []
    cursor = after
    while True:
        query = select(table).where(table.c.window_id == window.window_id)
        if reason_code is not None:
            query = query.where(table.c.reason_code == reason_code)
        if account_id is not None:
            query = query.where(table.c.billing_account_id == account_id)
        if cursor is not None:
            query = query.where(
                (table.c.created_at > cursor[0])
                | ((table.c.created_at == cursor[0]) & (table.c.id > cursor[1]))
            )
        rows = (
            db.execute(
                _scoped(
                    query.order_by(table.c.created_at, table.c.id).limit(limit),
                    scope,
                    table,
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            kind, done = states.get(row["id"], ("recorded", 0))
            name = _STATE_NAME.get(kind, kind)
            adverse = is_customer_adverse(row["lane"], int(row["amount_minor"]))
            if state is not None and name != state:
                cursor = (row["created_at"], row["id"])
                continue
            if open_customer_adverse and not (adverse and name in _OPEN_STATES):
                cursor = (row["created_at"], row["id"])
                continue
            records.append(
                OpeningVarianceRecordV1(
                    row["id"],
                    row["window_id"],
                    row["scope_level"],
                    row["billing_account_id"],
                    row["currency"],
                    row["minor_units"],
                    row["lane"],
                    row["basis"],
                    row["reason_code"],
                    row["source_kind"],
                    row["source_ref"],
                    row["import_receipt_id"],
                    int(row["amount_minor"]),
                    row["evidence_digest"],
                    row["rules_digest"],
                    name,
                    done,
                    adverse,
                )
            )
            cursor = (row["created_at"], row["id"])
            if len(records) == limit:
                return tuple(records), cursor
        if len(rows) < limit:
            return tuple(records), None


def opening_control_report(db: Session, *, scope: Scope) -> OpeningControlReportV1:
    """Window closure: per lane and basis, source = transferred + variance.

    ``closed`` is true only when every row difference is exactly 0. The seal
    refuses unless this holds (bi_0014); this read lets Finance acknowledge the
    digest of a report whose outcome they cannot change.
    """
    window = get_opening_window(db, scope=scope)
    if window is None or not isinstance(window.manifest, OpeningManifestV2):
        raise BillingRuleViolation(
            "opening_window_missing", "a V2 opening window is required"
        )
    manifest = window.manifest
    receipts = _table(scope, "opening_import_receipts")
    unwound = set(
        db.execute(
            _scoped(
                select(receipts.c.original_receipt_id).where(
                    receipts.c.receipt_kind == "unwind"
                ),
                scope,
                receipts,
            )
        )
        .scalars()
        .all()
    )
    transferred: dict[UUID, int] = defaultdict(int)
    lots: dict[UUID, int] = defaultdict(int)
    for receipt_id, account_id, evidence in db.execute(
        _scoped(
            select(
                receipts.c.id, receipts.c.billing_account_id, receipts.c.evidence
            ).where(receipts.c.receipt_kind == "import"),
            scope,
            receipts,
        )
    ).all():
        if receipt_id in unwound:  # an unwound import transfers nothing
            continue
        for name, sink in (("total_receivable", transferred), ("total_residual", lots)):
            money = evidence[name]
            sink[account_id] += int(
                Decimal(money["amount"]).scaleb(int(money["minor_units"]))
            )
    seal = get_opening_window_seal(db, scope=scope)
    held = set() if seal is None else set(seal.command.held_account_ids)
    # Finding 5: every control partition minus the held ones, T = 0 where
    # nothing was imported; none can leave the closure silently.
    admitted = {c.partition.account_id for c in manifest.controls} - held
    table = _table(scope, "opening_variances")
    vrows = (
        db.execute(
            _scoped(
                select(table).where(table.c.window_id == window.window_id), scope, table
            )
        )
        .mappings()
        .all()
    )
    states = _states(db, scope, window.window_id)
    active = [
        r
        for r in vrows
        if states.get(r["id"], ("", 0))[0] != "voided_by_import_reversal"
    ]

    src: dict[tuple[str, str, str], int] = defaultdict(int)
    tra: dict[tuple[str, str, str], int] = defaultdict(int)
    var: dict[tuple[str, str, str], int] = defaultdict(int)
    cnt: dict[tuple[str, str, str], int] = defaultdict(int)
    currency_of = {
        c.partition.account_id: c.partition.currency for c in manifest.controls
    }
    history: dict[tuple[str, str], tuple[int, int]] = defaultdict(lambda: (0, 0))
    for control in manifest.controls:
        acct = control.partition.account_id
        if acct not in admitted:
            continue
        ccy = control.partition.currency
        for lane, field in _LANE_FIELD.items():
            src[(lane, LEDGER_BASIS, ccy)] += getattr(control, field)
        tra[("receivable", LEDGER_BASIS, ccy)] += transferred[acct]
        for field_name in HISTORY_FIELDS:
            value = getattr(control, field_name)
            total, nonzero = history[(field_name, ccy)]
            history[(field_name, ccy)] = (total + value, nonzero + (value != 0))
    for m in manifest.measures:
        acct = m.partition.account_id
        if acct not in admitted:
            continue
        ccy = m.partition.currency
        for basis, figure in (
            ("document_residual", m.document_residual_minor),
            ("stored_balance", m.stored_balance_minor),
            ("rebuild", m.rebuild_minor),
        ):
            if figure is not None:
                src[("receivable", basis, ccy)] += figure
                tra[("receivable", basis, ccy)] += transferred[acct] - (
                    lots[acct] if basis == "rebuild" else 0
                )
    for b in manifest.batch_controls:
        src[(b.lane, b.basis, b.currency)] += b.source_minor
    adverse_count = adverse_minor = 0
    reasons: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in active:
        key = (r["lane"], r["basis"], r["currency"])
        var[key] += int(r["amount_minor"])
        cnt[key] += 1
        reasons[r["reason_code"]][0] += 1
        reasons[r["reason_code"]][1] += int(r["amount_minor"])
        if is_customer_adverse(r["lane"], int(r["amount_minor"])):
            adverse_count += 1
            adverse_minor += int(r["amount_minor"])
    keys = sorted(set(src) | set(var) | set(tra))
    rows = tuple(
        OpeningControlRowV1(
            lane,
            basis,
            ccy,
            src[(lane, basis, ccy)],
            tra[(lane, basis, ccy)],
            var[(lane, basis, ccy)],
            src[(lane, basis, ccy)] - tra[(lane, basis, ccy)] - var[(lane, basis, ccy)],
            cnt[(lane, basis, ccy)],
        )
        for lane, basis, ccy in keys
    )
    operational = (
        ()
        if seal is None
        else tuple(
            OpeningHoldRowV1(str(h.account_id), h.reason_code)
            for h in seal.command.operational_holds
        )
    )
    totals = tuple(
        OpeningReasonTotalV1(k, c, total) for k, (c, total) in sorted(reasons.items())
    )
    closed = all(r.difference_minor == 0 for r in rows)
    history_rows = tuple(
        OpeningHistoryRowV1(name, ccy, total, nonzero)
        for (name, ccy), (total, nonzero) in sorted(history.items())
    )
    del currency_of
    payload: dict[str, Any] = {
        "domain": "billing.opening.control.report.v1",
        "rows": [
            [
                r.lane,
                r.basis,
                r.currency,
                r.source_minor,
                r.transferred_minor,
                r.variance_minor,
                r.difference_minor,
                r.variance_count,
            ]
            for r in rows
        ],
        "reasons": [[t.reason_code, t.count, t.total_minor] for t in totals],
        "adverse": [adverse_count, adverse_minor],
        "history": [
            [h.field, h.currency, h.total_minor, h.nonzero_accounts]
            for h in history_rows
        ],
        "operational_holds": [[h.account_id, h.reason_code] for h in operational],
    }
    return OpeningControlReportV1(
        rows,
        totals,
        adverse_count,
        adverse_minor,
        operational,
        history_rows,
        closed,
        fingerprint_of(payload),
    )


__all__ = [
    "LANES",
    "FactEmitter",
    "list_opening_variances",
    "opening_control_report",
    "record_batch_variances",
    "require_group_not_variance_resolution",
    "require_recognisable",
    "require_resolution_primitive",
    "record_partition_variances",
    "resolve_variance",
    "variance_fact_payload",
    "void_partition_variances",
]
_ = UTC
