"""Complimentary and sponsored treatment over both selected persistence planes.

Ported product-first from `dotmac_sub`'s
`app/services/subscription_billing_treatments.py` and
`app/services/subscription_billing_grants.py` (ADR-0006), with every Sub
identity replaced by a module-owned one: the subscription becomes a stable
subscription contract plus a contract-line lineage key, the catalogue offer
becomes an immutable offer version, and Sub's `BillingCycle` becomes the
module's own service interval.

The one decision this file exists to keep true:

    A complimentary or sponsored service is NEVER a zero price.

The contract line keeps its real, strictly positive `unit_price` — `su_0002`
made that a CHECK constraint — an arrangement records approval not to collect
it, and a grant records the exact non-cash amount actually foregone.  Zeroing
the price would delete the evidence that revenue was given away, which is the
number a sponsor is invoiced from and an internal cost centre is charged.

Sub's product consequences deliberately do NOT come with the port: creating or
repairing a `ServiceEntitlement`, advancing an ISP billing anchor, suppressing
an invoice, and posting a sponsor receivable or internal expense all stay with
their owners.  This module publishes `NonCashGrantOutputV1` and stops.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_subscriptions.cadence import BillingCadence, Interval, service_period
from dotmac_subscriptions.commands import (
    ApprovalPolicySnapshot,
    ApproveBillingArrangementCommand,
    BillingArrangementDecision,
    BillingArrangementPreview,
    BillingArrangementResult,
    NonCashGrantResult,
    PreviewBillingArrangementCommand,
    RecordNonCashGrantCommand,
    RevokeBillingArrangementCommand,
)
from dotmac_subscriptions.contracts import (
    NonCashGrantOutputV1,
    NonCashGrantPublisher,
)
from dotmac_subscriptions.errors import (
    SubscriptionConflictError,
    SubscriptionDataError,
    SubscriptionStateError,
)
from dotmac_subscriptions.lifecycle import (
    BillingArrangementDecisionStatus,
    BillingArrangementStatus,
    BillingTreatment,
    require_arrangement_transition,
)
from dotmac_subscriptions.models import (
    PlatformSubscriptionBillingArrangement,
    PlatformSubscriptionBillingGrant,
    SubscriptionBillingArrangement,
    SubscriptionBillingGrant,
)
from dotmac_subscriptions.service import (
    ContractVersionRow,
    LineRow,
    OccurrenceRow,
    _cadence_from,
    _execute_once,
    _fingerprint,
    _models,
    _require_aware,
    _scope_values,
    _scoped,
    _stored_utc,
)
from dotmac_subscriptions.values import (
    ExactAmount,
    billing_arrangement_fingerprint,
    canonical_decimal,
    non_cash_grant_idempotency_key,
)
from dotmac_subscriptions.vocabulary import SubscriptionVocabularyRegistry

if TYPE_CHECKING:
    from dotmac_kernel.idempotency import IdempotentOutcome

_APPROVE_SCOPE = "subscriptions.approve_billing_arrangement"
_REVOKE_SCOPE = "subscriptions.revoke_billing_arrangement"
_GRANT_SCOPE = "subscriptions.record_non_cash_grant"

#: Sub's tolerance for a start that is "now" by the time the operator confirms.
#: Ported unchanged: without it an approval that takes a minute to review is
#: refused as retroactive, which teaches operators to backdate.
_START_TOLERANCE = timedelta(minutes=5)

#: Bound on the period walk that proves an interval is a whole number of
#: service periods. Fails closed rather than looping on a bad anchor.
_MAX_PERIOD_WALK = 4096

ArrangementModel = (
    type[SubscriptionBillingArrangement] | type[PlatformSubscriptionBillingArrangement]
)
GrantModel = type[SubscriptionBillingGrant] | type[PlatformSubscriptionBillingGrant]
ArrangementRow = SubscriptionBillingArrangement | PlatformSubscriptionBillingArrangement
GrantRow = SubscriptionBillingGrant | PlatformSubscriptionBillingGrant


@dataclass(frozen=True, slots=True)
class _TreatmentModels:
    arrangement: ArrangementModel
    grant: GrantModel


_TENANT_TREATMENT_MODELS = _TreatmentModels(
    SubscriptionBillingArrangement, SubscriptionBillingGrant
)
_PLATFORM_TREATMENT_MODELS = _TreatmentModels(
    PlatformSubscriptionBillingArrangement, PlatformSubscriptionBillingGrant
)


def _treatment_models(scope: Scope) -> _TreatmentModels:
    return (
        _TENANT_TREATMENT_MODELS
        if isinstance(scope, TenantScope)
        else _PLATFORM_TREATMENT_MODELS
    )


def _text(value: str, *, field: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise SubscriptionDataError(
            "treatments.invalid_command",
            f"{field} is required and at most {limit} characters.",
            {"field": field},
        )
    return cleaned


def _optional_text(value: str | None, *, limit: int) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _exact(amount: Decimal, currency: str, scale: int) -> ExactAmount:
    return ExactAmount(amount=amount, currency=currency, scale=scale)


def _line_contracted_amount(line: LineRow) -> ExactAmount:
    """The strictly positive recurring value the customer really contracted for.

    Derived from the immutable line, never from a product setting, and refused
    at zero: a zero here is exactly the concealment this module exists to make
    impossible, so it fails loudly even though `ck_contract_lines_amounts`
    should already have made it unreachable.
    """
    quantum = Decimal(1).scaleb(-line.scale)
    # ROUND_HALF_UP matches `engine.rate_recurring_line`, so the approved
    # ceiling can never round BELOW the amount the same line will later rate —
    # which would turn a correct grant into an over-cap refusal.
    amount = (line.unit_price * line.quantity).quantize(quantum, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise SubscriptionDataError(
            "treatments.non_positive_contract_price",
            "A treatment requires a strictly positive contracted line value; "
            "a zero price hides foregone revenue instead of recording it.",
        )
    return _exact(amount, line.currency, line.scale)


def _effective_line(
    db: Session, *, scope: Scope, contract_line_key: UUID, moment: datetime
) -> tuple[ContractVersionRow, LineRow]:
    plane = _models(scope)
    statement: Select[Any] = (
        select(plane.contract_version, plane.line)
        .join(
            plane.line,
            plane.line.contract_version_id == plane.contract_version.id,
        )
        .where(
            plane.line.contract_line_key == contract_line_key,
            plane.contract_version.starts_at <= moment,
            (
                plane.contract_version.ends_at.is_(None)
                | (plane.contract_version.ends_at > moment)
            ),
            plane.contract_version.state.in_(("effective", "superseded", "ended")),
        )
    )
    statement = _scoped(statement, scope, plane.contract_version)
    statement = _scoped(statement, scope, plane.line)
    row = db.execute(statement).first()
    if row is None:
        raise SubscriptionDataError(
            "treatments.line_not_effective",
            "No effective contract line was found for that lineage key.",
            {"contract_line_key": str(contract_line_key)},
        )
    return cast(ContractVersionRow, row[0]), cast(LineRow, row[1])


def _whole_service_periods(
    *,
    cadence: BillingCadence,
    contract_start: datetime,
    starts_at: datetime,
    ends_at: datetime,
) -> None:
    """Refuse an interval that is not aligned whole service periods.

    Ported from Sub's `_aligned_end`, expressed through the module's own
    calendar rather than a `BillingCycle` preset. Without it an approval can
    waive part of a cycle nobody reviewed.
    """
    boundaries: list[datetime] = []
    for index in range(_MAX_PERIOD_WALK):
        interval: Interval = service_period(
            cadence=cadence, contract_start=contract_start, index=index
        )
        if not boundaries:
            boundaries.append(interval.starts_at)
        boundaries.append(interval.ends_at)
        if interval.ends_at >= ends_at:
            break
    else:  # pragma: no cover - guarded by the bound above
        raise SubscriptionDataError(
            "treatments.period_walk_exhausted",
            "Treatment interval exceeded the service-period walk bound.",
        )
    if starts_at not in boundaries:
        raise SubscriptionDataError(
            "treatments.unaligned_start",
            "Treatment must start on a contract service-period boundary.",
        )
    if ends_at not in boundaries:
        raise SubscriptionDataError(
            "treatments.unaligned_period",
            "Treatment must end on a complete contract service period.",
        )


def _overlap_statement(
    *,
    scope: Scope,
    model: ArrangementModel,
    contract_id: UUID,
    contract_line_key: UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> Select[tuple[UUID]]:
    statement = select(model.id).where(
        model.contract_id == contract_id,
        model.contract_line_key == contract_line_key,
        model.status == BillingArrangementStatus.active.value,
        model.ends_at > starts_at,
        model.starts_at < ends_at,
    )
    return _scoped(statement, scope, model)


def preview_billing_arrangement(
    db: Session,
    command: PreviewBillingArrangementCommand,
    *,
    registry: SubscriptionVocabularyRegistry,
) -> BillingArrangementPreview:
    """Validate the current commercial evidence and fingerprint it.

    Approval confirms against this fingerprint, so an offer version, price,
    cadence or overlapping approval that moved in between cannot be waved
    through on evidence nobody reviewed.
    """
    _require_aware(command.starts_at, "starts_at")
    _require_aware(command.evaluated_at, "evaluated_at")
    if command.ends_at is None:
        raise SubscriptionDataError(
            "treatments.finite_period_required",
            "Billing treatment requires a finite end and periodic reapproval; "
            "a permanent exemption cannot be recorded.",
        )
    _require_aware(command.ends_at, "ends_at")
    start = command.starts_at.astimezone(UTC)
    end = command.ends_at.astimezone(UTC)
    observed_at = command.evaluated_at.astimezone(UTC)
    if end <= start:
        raise SubscriptionDataError(
            "treatments.invalid_period", "Treatment end must be after its start."
        )
    if start < observed_at - _START_TOLERANCE:
        raise SubscriptionDataError(
            "treatments.retroactive_treatment",
            "Billing treatment must start prospectively.",
        )
    policy = command.approval_policy
    policy_ref = _text(policy.policy_ref, field="approval_policy_ref", limit=255)
    policy_version = _text(
        policy.policy_version, field="approval_policy_version", limit=80
    )
    if policy.maximum_days < 1:
        raise SubscriptionDataError(
            "treatments.invalid_approval_policy",
            "The supplied approval horizon must be at least one day.",
        )
    # Normalise ONCE, here: the stored row, the fingerprint and the returned
    # snapshot must all carry the same text, or a caller's stray whitespace
    # makes an approval disagree with the preview it confirmed.
    policy = ApprovalPolicySnapshot(
        policy_ref=policy_ref,
        policy_version=policy_version,
        maximum_days=policy.maximum_days,
    )
    if end - start > timedelta(days=policy.maximum_days):
        raise SubscriptionDataError(
            "treatments.approval_horizon_exceeded",
            "Billing treatment exceeds the approved maximum horizon.",
            {"maximum_days": policy.maximum_days},
        )
    reason = _text(command.reason, field="reason", limit=2000)
    registry.require_billing_treatment_reason(command.reason_code)
    sponsor = _optional_text(command.sponsor_reference, limit=255)
    cost_center = _optional_text(command.cost_center, limit=120)
    if command.treatment is BillingTreatment.sponsored and not (sponsor or cost_center):
        raise SubscriptionDataError(
            "treatments.missing_sponsor_evidence",
            "Sponsored treatment requires a sponsor reference or cost centre.",
        )
    version, line = _effective_line(
        db,
        scope=command.scope,
        contract_line_key=command.contract_line_key,
        moment=start,
    )
    contracted = _line_contracted_amount(line)
    cadence = _cadence_from(version)
    _whole_service_periods(
        cadence=cadence,
        contract_start=_stored_utc(version.starts_at),
        starts_at=start,
        ends_at=end,
    )
    treatment_models = _treatment_models(command.scope)
    overlapping = db.execute(
        _overlap_statement(
            scope=command.scope,
            model=treatment_models.arrangement,
            contract_id=version.contract_id,
            contract_line_key=command.contract_line_key,
            starts_at=start,
            ends_at=end,
        ).limit(1)
    ).scalar_one_or_none()
    if overlapping is not None:
        raise SubscriptionConflictError(
            "treatments.overlapping_treatment",
            "The contract line already has an overlapping billing treatment.",
        )
    fingerprint = billing_arrangement_fingerprint(
        scope=command.scope,
        contract_id=version.contract_id,
        contract_line_key=command.contract_line_key,
        authorized_contract_version_id=version.id,
        authorized_offer_version_id=line.offer_version_id,
        treatment=command.treatment.value,
        reason_code=command.reason_code,
        reason=reason,
        starts_at=start,
        ends_at=end,
        approval_policy_ref=policy_ref,
        approval_policy_version=policy_version,
        approval_policy_max_days=policy.maximum_days,
        maximum_recurring_amount=contracted,
        service_interval_unit=cadence.service_interval_unit,
        service_interval_count=cadence.service_interval_count,
        sponsor_reference=sponsor,
        cost_center=cost_center,
    )
    return BillingArrangementPreview(
        scope=command.scope,
        contract_id=version.contract_id,
        contract_line_key=command.contract_line_key,
        authorized_contract_version_id=version.id,
        authorized_offer_version_id=line.offer_version_id,
        treatment=command.treatment,
        reason_code=command.reason_code,
        reason=reason,
        starts_at=start,
        ends_at=end,
        approval_policy=policy,
        maximum_recurring_amount=contracted,
        service_interval_unit=cadence.service_interval_unit.value,
        service_interval_count=cadence.service_interval_count,
        sponsor_reference=sponsor,
        cost_center=cost_center,
        evaluated_at=observed_at,
        fingerprint=fingerprint,
    )


def _arrangement_result(
    row: ArrangementRow, *, replayed: bool
) -> BillingArrangementResult:
    return BillingArrangementResult(
        arrangement_id=row.id,
        contract_id=row.contract_id,
        contract_line_key=row.contract_line_key,
        treatment=BillingTreatment(row.treatment),
        status=BillingArrangementStatus(row.status),
        starts_at=_stored_utc(row.starts_at),
        ends_at=_stored_utc(row.ends_at),
        maximum_recurring_amount=_exact(
            row.maximum_recurring_amount, row.currency, row.scale
        ),
        replayed=replayed,
    )


def _arrangement(db: Session, *, scope: Scope, arrangement_id: UUID) -> ArrangementRow:
    model = _treatment_models(scope).arrangement
    row = db.execute(
        _scoped(select(model).where(model.id == arrangement_id), scope, model)
    ).scalar_one_or_none()
    if row is None:
        raise SubscriptionDataError(
            "treatments.arrangement_not_found",
            "The billing arrangement was not found.",
        )
    return cast(ArrangementRow, row)


def approve_billing_arrangement(
    db: Session,
    command: ApproveBillingArrangementCommand,
    *,
    registry: SubscriptionVocabularyRegistry,
) -> BillingArrangementResult:
    """Record one non-overlapping, finite, fingerprint-bound approval."""
    _require_aware(command.approved_at, "approved_at")
    _require_aware(command.preview_evaluated_at, "preview_evaluated_at")
    approved_by = _text(command.approved_by, field="approved_by", limit=160)
    key = _text(command.idempotency_key, field="idempotency_key", limit=255)
    if not command.preview_fingerprint:
        raise SubscriptionDataError(
            "treatments.missing_preview",
            "Approval must confirm a previewed fingerprint.",
        )
    models = _treatment_models(command.scope)
    # Replay is resolved BEFORE the preview runs, exactly as Sub does. Previewing
    # first would make a retry of an already-approved command fail its own
    # overlap check against the arrangement it created, turning an idempotent
    # retry into a permanent error.
    replay = cast(
        ArrangementRow | None,
        db.execute(
            _scoped(
                select(models.arrangement).where(
                    models.arrangement.idempotency_key == key
                ),
                command.scope,
                models.arrangement,
            )
        ).scalar_one_or_none(),
    )
    if replay is not None:
        if replay.command_fingerprint != command.preview_fingerprint:
            raise SubscriptionConflictError(
                "treatments.idempotency_conflict",
                "The idempotency key belongs to another billing treatment.",
            )
        return _arrangement_result(replay, replayed=True)
    preview = preview_billing_arrangement(
        db,
        PreviewBillingArrangementCommand(
            scope=command.scope,
            contract_line_key=command.contract_line_key,
            treatment=command.treatment,
            reason_code=command.reason_code,
            reason=command.reason,
            starts_at=command.starts_at,
            ends_at=command.ends_at,
            approval_policy=command.approval_policy,
            sponsor_reference=command.sponsor_reference,
            cost_center=command.cost_center,
            evaluated_at=command.preview_evaluated_at,
        ),
        registry=registry,
    )
    if preview.fingerprint != command.preview_fingerprint:
        raise SubscriptionConflictError(
            "treatments.stale_preview",
            "The commercial evidence changed since the preview; preview again.",
            {"current_fingerprint": preview.fingerprint},
        )
    arrangement_id = uuid4()

    def operation(session: Session) -> Mapping[str, object]:
        session.add(
            models.arrangement(
                **_scope_values(command.scope),
                id=arrangement_id,
                contract_id=preview.contract_id,
                contract_line_key=preview.contract_line_key,
                authorized_contract_version_id=preview.authorized_contract_version_id,
                authorized_offer_version_id=preview.authorized_offer_version_id,
                treatment=preview.treatment.value,
                reason_code=preview.reason_code,
                reason=preview.reason,
                status=BillingArrangementStatus.active.value,
                starts_at=preview.starts_at,
                ends_at=preview.ends_at,
                approval_policy_ref=preview.approval_policy.policy_ref,
                approval_policy_version=preview.approval_policy.policy_version,
                approval_policy_max_days=preview.approval_policy.maximum_days,
                maximum_recurring_amount=preview.maximum_recurring_amount.amount,
                currency=preview.maximum_recurring_amount.currency,
                scale=preview.maximum_recurring_amount.scale,
                service_interval_unit=preview.service_interval_unit,
                service_interval_count=preview.service_interval_count,
                sponsor_reference=preview.sponsor_reference,
                cost_center=preview.cost_center,
                approved_by=approved_by,
                approved_at=command.approved_at.astimezone(UTC),
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                idempotency_key=key,
                command_fingerprint=preview.fingerprint,
                revoked_by=None,
                revoked_at=None,
                revocation_reason=None,
                revocation_command_id=None,
                revocation_correlation_id=None,
                revocation_idempotency_key=None,
            )
        )
        session.flush()
        return {"arrangement_id": str(arrangement_id)}

    outcome = _run_once(
        db,
        scope=command.scope,
        operation_scope=_APPROVE_SCOPE,
        key=key,
        fingerprint=preview.fingerprint,
        correlation_id=command.correlation_id,
        operation=operation,
        conflict_code="treatments.database_conflict",
        conflict_message="The billing arrangement conflicts with stored state.",
    )
    stored_id = UUID(str(outcome.result["arrangement_id"]))
    return _arrangement_result(
        _arrangement(db, scope=command.scope, arrangement_id=stored_id),
        replayed=outcome.replayed,
    )


def revoke_billing_arrangement(
    db: Session, command: RevokeBillingArrangementCommand
) -> BillingArrangementResult:
    """Restore standard billing prospectively without erasing granted periods."""
    _require_aware(command.revoked_at, "revoked_at")
    reason = _text(command.reason, field="reason", limit=2000)
    revoked_by = _text(command.revoked_by, field="revoked_by", limit=160)
    key = _text(command.idempotency_key, field="idempotency_key", limit=255)
    digest = _fingerprint(
        {
            "arrangement_id": str(command.arrangement_id),
            "reason": reason,
            "revoked_by": revoked_by,
        }
    )

    def operation(session: Session) -> Mapping[str, object]:
        row = _arrangement(
            session, scope=command.scope, arrangement_id=command.arrangement_id
        )
        require_arrangement_transition(
            BillingArrangementStatus(row.status), BillingArrangementStatus.revoked
        )
        row.status = BillingArrangementStatus.revoked.value
        row.revoked_by = revoked_by
        row.revoked_at = command.revoked_at.astimezone(UTC)
        row.revocation_reason = reason
        row.revocation_command_id = command.command_id
        row.revocation_correlation_id = command.correlation_id
        row.revocation_idempotency_key = key
        session.flush()
        return {"arrangement_id": str(row.id)}

    outcome = _run_once(
        db,
        scope=command.scope,
        operation_scope=_REVOKE_SCOPE,
        key=key,
        fingerprint=digest,
        correlation_id=command.correlation_id,
        operation=operation,
        conflict_code="treatments.revocation_conflict",
        conflict_message="The revocation conflicts with stored arrangement state.",
    )
    return _arrangement_result(
        _arrangement(
            db,
            scope=command.scope,
            arrangement_id=UUID(str(outcome.result["arrangement_id"])),
        ),
        replayed=outcome.replayed,
    )


def resolve_billing_arrangement(
    db: Session, *, scope: Scope, contract_line_key: UUID, as_of: datetime
) -> BillingArrangementDecision:
    """Answer `standard`, `effective` or `protected_drift` for one line.

    `protected_drift` never fabricates coverage: it suppresses customer
    charging while refusing a grant, which is the only safe answer when the
    approved evidence and the current contract disagree.
    """
    _require_aware(as_of, "as_of")
    observed_at = as_of.astimezone(UTC)
    model = _treatment_models(scope).arrangement
    rows = [
        cast(ArrangementRow, row)
        for row in db.execute(
            _scoped(
                select(model)
                .where(
                    model.contract_line_key == contract_line_key,
                    model.status == BillingArrangementStatus.active.value,
                    model.starts_at <= observed_at,
                    model.ends_at > observed_at,
                )
                .order_by(model.starts_at.desc(), model.id.desc()),
                scope,
                model,
            )
        )
        .scalars()
        .all()
    ]
    if not rows:
        # Standard billing is the ABSENCE of an arrangement, so an unknown or
        # ended line answers `standard` too — never an error, and never a
        # silent suppression.
        contract_id: UUID | None = None
        try:
            version, _ = _effective_line(
                db, scope=scope, contract_line_key=contract_line_key, moment=observed_at
            )
        except SubscriptionDataError:
            contract_id = None
        else:
            contract_id = version.contract_id
        return BillingArrangementDecision(
            scope=scope,
            contract_id=contract_id,
            contract_line_key=contract_line_key,
            status=BillingArrangementDecisionStatus.standard,
        )
    arrangement = rows[0]
    drift_reason: str | None = None
    contracted: ExactAmount | None = None
    if len(rows) > 1:
        drift_reason = "overlapping_effective_arrangements"
    else:
        try:
            version, line = _effective_line(
                db, scope=scope, contract_line_key=contract_line_key, moment=observed_at
            )
            contracted = _line_contracted_amount(line)
        except SubscriptionDataError as exc:
            drift_reason = exc.code.rsplit(".", maxsplit=1)[-1]
        else:
            if version.id != arrangement.authorized_contract_version_id:
                drift_reason = "unauthorized_contract_version_change"
            elif line.offer_version_id != arrangement.authorized_offer_version_id:
                drift_reason = "unauthorized_offer_change"
            elif contracted.currency != arrangement.currency:
                drift_reason = "currency_mismatch"
            elif contracted.amount > arrangement.maximum_recurring_amount:
                drift_reason = "approved_value_exceeded"
            else:
                cadence = _cadence_from(version)
                if (
                    cadence.service_interval_unit.value
                    != arrangement.service_interval_unit
                    or cadence.service_interval_count
                    != arrangement.service_interval_count
                ):
                    drift_reason = "service_interval_mismatch"
    return BillingArrangementDecision(
        scope=scope,
        contract_id=arrangement.contract_id,
        contract_line_key=contract_line_key,
        status=(
            BillingArrangementDecisionStatus.protected_drift
            if drift_reason
            else BillingArrangementDecisionStatus.effective
        ),
        treatment=BillingTreatment(arrangement.treatment),
        arrangement_id=arrangement.id,
        authorized_contract_version_id=arrangement.authorized_contract_version_id,
        reason_code=arrangement.reason_code,
        reason=arrangement.reason,
        starts_at=_stored_utc(arrangement.starts_at),
        ends_at=_stored_utc(arrangement.ends_at),
        maximum_recurring_amount=_exact(
            arrangement.maximum_recurring_amount,
            arrangement.currency,
            arrangement.scale,
        ),
        contracted_amount=contracted,
        drift_reason=drift_reason,
    )


def record_non_cash_grant(
    db: Session,
    command: RecordNonCashGrantCommand,
    *,
    publisher: NonCashGrantPublisher | None = None,
) -> NonCashGrantResult:
    """Record one bounded, append-only, exact non-cash grant.

    The grant is measured against the occurrence's own strictly positive
    `pre_tax_amount` — the real charge the customer would otherwise owe — so
    the foregone amount is evidence rather than an assertion, and it can never
    exceed either that amount or the approved ceiling.
    """
    _require_aware(command.recorded_at, "recorded_at")
    actor = _text(command.actor, field="actor", limit=160)
    arrangement = _arrangement(
        db, scope=command.scope, arrangement_id=command.arrangement_id
    )
    plane = _models(command.scope)
    occurrence = cast(
        OccurrenceRow | None,
        db.execute(
            _scoped(
                select(plane.occurrence).where(
                    plane.occurrence.id == command.recurring_occurrence_id
                ),
                command.scope,
                plane.occurrence,
            )
        ).scalar_one_or_none(),
    )
    if occurrence is None:
        raise SubscriptionDataError(
            "treatments.occurrence_not_found",
            "The recurring charge occurrence was not found.",
        )
    if (
        occurrence.contract_line_key != arrangement.contract_line_key
        or occurrence.contract_id != arrangement.contract_id
    ):
        raise SubscriptionDataError(
            "treatments.grant_line_mismatch",
            "A grant covers the arrangement's own contract line only.",
        )
    period_start = _stored_utc(occurrence.period_start)
    period_end = _stored_utc(occurrence.period_end)
    if period_start < _stored_utc(arrangement.starts_at) or period_end > _stored_utc(
        arrangement.ends_at
    ):
        raise SubscriptionDataError(
            "treatments.grant_outside_arrangement",
            "The service period falls outside the approved treatment.",
        )
    decision = resolve_billing_arrangement(
        db,
        scope=command.scope,
        contract_line_key=arrangement.contract_line_key,
        as_of=period_start,
    )
    if not decision.grantable or decision.arrangement_id != arrangement.id:
        raise SubscriptionStateError(
            "treatments.grant_blocked",
            "The billing treatment is not effective for this service period.",
            {"drift_reason": decision.drift_reason},
        )
    contracted = _exact(
        occurrence.pre_tax_amount, occurrence.currency, occurrence.amount_scale
    )
    if contracted.amount <= 0:
        raise SubscriptionDataError(
            "treatments.non_positive_contract_price",
            "A grant requires a strictly positive rated charge; a zero-valued "
            "occurrence conceals foregone revenue instead of recording it.",
        )
    approved_maximum = _exact(
        arrangement.maximum_recurring_amount, arrangement.currency, arrangement.scale
    )
    foregone = (
        contracted if command.foregone_amount is None else command.foregone_amount
    )
    if foregone.currency != contracted.currency:
        raise SubscriptionDataError(
            "treatments.currency_mismatch",
            "The grant currency must match the rated occurrence currency.",
        )
    if foregone.amount <= 0:
        raise SubscriptionDataError(
            "treatments.non_positive_grant",
            "A non-cash grant records a strictly positive foregone amount.",
        )
    if foregone.amount > contracted.amount or foregone.amount > approved_maximum.amount:
        raise SubscriptionDataError(
            "treatments.grant_exceeds_approval",
            "A grant never exceeds the contracted amount or the approved cap.",
            {
                "contracted_amount": canonical_decimal(contracted.amount),
                "approved_maximum_amount": canonical_decimal(approved_maximum.amount),
            },
        )
    idempotency_key = non_cash_grant_idempotency_key(
        scope=command.scope,
        arrangement_id=arrangement.id,
        recurring_occurrence_id=occurrence.id,
        contract_line_key=arrangement.contract_line_key,
        period_start=period_start,
        period_end=period_end,
        currency=contracted.currency,
    )
    digest = _fingerprint(
        {
            "idempotency_key": idempotency_key,
            "contracted_amount": contracted.as_wire(),
            "approved_maximum_amount": approved_maximum.as_wire(),
            "foregone_amount": foregone.as_wire(),
            "treatment": arrangement.treatment,
            "reason_code": arrangement.reason_code,
        }
    )
    models = _treatment_models(command.scope)
    grant_id = uuid4()

    def operation(session: Session) -> Mapping[str, object]:
        session.add(
            models.grant(
                **_scope_values(command.scope),
                id=grant_id,
                arrangement_id=arrangement.id,
                recurring_occurrence_id=occurrence.id,
                contract_line_key=arrangement.contract_line_key,
                treatment=arrangement.treatment,
                reason_code=arrangement.reason_code,
                starts_at=period_start,
                ends_at=period_end,
                contracted_amount=contracted.amount,
                approved_maximum_amount=approved_maximum.amount,
                foregone_amount=foregone.amount,
                currency=contracted.currency,
                scale=contracted.scale,
                actor=actor,
                reason=arrangement.reason,
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                idempotency_key=idempotency_key,
                recorded_at=command.recorded_at.astimezone(UTC),
            )
        )
        session.flush()
        return {"grant_id": str(grant_id)}

    outcome = _run_once(
        db,
        scope=command.scope,
        operation_scope=_GRANT_SCOPE,
        key=idempotency_key,
        fingerprint=digest,
        correlation_id=command.correlation_id,
        operation=operation,
        conflict_code="treatments.grant_conflict",
        conflict_message="The grant identity conflicts with stored evidence.",
    )
    stored_id = UUID(str(outcome.result["grant_id"]))
    output = NonCashGrantOutputV1(
        grant_id=stored_id,
        recorded_at=command.recorded_at.astimezone(UTC),
        scope=command.scope,
        arrangement_id=arrangement.id,
        subscription_contract_id=arrangement.contract_id,
        authorized_contract_version_id=arrangement.authorized_contract_version_id,
        contract_line_key=arrangement.contract_line_key,
        recurring_occurrence_id=occurrence.id,
        treatment=arrangement.treatment,
        reason_code=arrangement.reason_code,
        period_start=period_start,
        period_end=period_end,
        currency=contracted.currency,
        contracted_amount=contracted,
        approved_maximum_amount=approved_maximum,
        foregone_amount=foregone,
        actor=actor,
        command_id=command.command_id,
        correlation_id=command.correlation_id,
        idempotency_key=idempotency_key,
    )
    if publisher is not None:
        publisher.stage(output)
    return NonCashGrantResult(
        grant_id=stored_id,
        arrangement_id=arrangement.id,
        contract_line_key=arrangement.contract_line_key,
        recurring_occurrence_id=occurrence.id,
        replayed=outcome.replayed,
        staged_output=output,
    )


def billing_arrangements_for_line(
    db: Session, *, scope: Scope, contract_line_key: UUID
) -> tuple[ArrangementRow, ...]:
    """Every arrangement ever recorded for one contract-line lineage."""
    model = _treatment_models(scope).arrangement
    return tuple(
        cast(ArrangementRow, row)
        for row in db.execute(
            _scoped(
                select(model)
                .where(model.contract_line_key == contract_line_key)
                .order_by(model.starts_at.desc(), model.id.desc()),
                scope,
                model,
            )
        )
        .scalars()
        .all()
    )


def non_cash_grants_for_line(
    db: Session, *, scope: Scope, contract_line_key: UUID
) -> tuple[GrantRow, ...]:
    """Append-only foregone-revenue evidence for one contract-line lineage."""
    model = _treatment_models(scope).grant
    return tuple(
        cast(GrantRow, row)
        for row in db.execute(
            _scoped(
                select(model)
                .where(model.contract_line_key == contract_line_key)
                .order_by(model.starts_at, model.id),
                scope,
                model,
            )
        )
        .scalars()
        .all()
    )


def _run_once(
    db: Session,
    *,
    scope: Scope,
    operation_scope: str,
    key: str,
    fingerprint: str,
    correlation_id: UUID,
    operation: Callable[[Session], Mapping[str, object] | None],
    conflict_code: str,
    conflict_message: str,
) -> IdempotentOutcome:
    try:
        return _execute_once(
            db,
            scope=scope,
            operation_scope=operation_scope,
            key=key,
            fingerprint=fingerprint,
            correlation_id=correlation_id,
            operation=operation,
        )
    except IntegrityError as exc:
        raise SubscriptionConflictError(conflict_code, conflict_message) from exc


__all__ = [
    "approve_billing_arrangement",
    "billing_arrangements_for_line",
    "non_cash_grants_for_line",
    "preview_billing_arrangement",
    "record_non_cash_grant",
    "resolve_billing_arrangement",
    "revoke_billing_arrangement",
]
