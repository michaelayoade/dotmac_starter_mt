"""Billing-owned earmark admission, immutable capacity and correction coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from functools import wraps
from typing import Any, NoReturn, ParamSpec, TypeVar, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.money import Money
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing._document_facts import _decode
from dotmac_billing._opening_values import cash_decimal_context
from dotmac_billing.commands import (
    DeallocationCommand,
    ReallocationCommand,
    RefundCommand,
    ReversePostingGroupCommand,
)
from dotmac_billing.contracts import AcceptSettlementV1, SettlementFundingLane
from dotmac_billing.earmark_authority import (
    CorrectSettlementEarmarkV1,
    EarmarkChange,
    EarmarkCorrectionVerificationRequestV1,
    EarmarkCorrectionVerificationV1,
    EarmarkCorrectionVerifier,
    ReleaseEarmarkV1,
    ReverseEarmarkReleaseV1,
)
from dotmac_billing.earmark_contracts import (
    ConfirmedSettlementObservationV1,
    EarmarkSettlementV1,
    FundingAvailabilityV1,
    GetConfirmedSettlementV1,
    GetFundingAvailabilityV1,
    SettlementEarmarkV1,
)
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_billing.opening_cash_contracts import OpeningCashEffectV1
from dotmac_billing.opening_commands import ReverseCashCreditDrawV1
from dotmac_billing.opening_queries import get_opening_cash_lot_position

_VERIFIED = object()
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _exact_capacity(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with localcontext(cash_decimal_context()):
            return function(*args, **kwargs)

    return wrapped


def _fail(code: str, message: str) -> NoReturn:
    raise BillingRuleViolation(code, message)


def _model(scope: Scope, name: str) -> Any:
    return getattr(
        models, name if isinstance(scope, TenantScope) else "Platform" + name
    )


def _rows(db: Session, scope: Scope, model: Any, *conditions: Any) -> tuple[Any, ...]:
    statement = select(model.__table__).where(*conditions)
    if isinstance(scope, TenantScope):
        statement = statement.where(model.tenant_id == scope.tenant_id)
    with db.no_autoflush:
        return tuple(db.execute(statement).mappings())


def _claim(
    db: Session,
    scope: Scope,
    *,
    earmark_id: UUID | None = None,
    settlement_id: UUID | None = None,
) -> Any | None:
    model = _model(scope, "SettlementEarmark")
    condition = (
        model.id == earmark_id
        if earmark_id is not None
        else model.settlement_id == settlement_id
    )
    found = _rows(db, scope, model, condition)
    if len(found) > 1:
        _fail("earmark_corrupt", "ambiguous settlement earmark")
    return found[0] if found else None


def _fact(row: Any) -> SettlementEarmarkV1:
    try:
        fact = cast(SettlementEarmarkV1, _decode(row["evidence"], SettlementEarmarkV1))
        if fingerprint_of(row["evidence"]) != row["evidence_digest"] or any(
            (
                fact.earmark_id != row["id"],
                fact.billing_account_id != row["billing_account_id"],
                fact.settlement_id != row["settlement_id"],
                fact.amount.amount != row["amount"],
                fact.amount.currency.code != row["currency"],
                fact.amount.currency.minor_units != row["minor_units"],
                fact.producer_ref != row["producer_ref"],
                isinstance(fact.scope, TenantScope)
                and fact.scope.tenant_id != row.get("tenant_id"),
                fact.source_ref != row["source_ref"],
            )
        ):
            raise ValueError("claim scalar binding differs")
        EarmarkSettlementV1(
            fact.scope,
            fact.billing_account_id,
            fact.settlement_id,
            fact.amount,
            fact.producer_ref,
            fact.targets,
            fact.source_ref,
            fact.occurred_at,
        )
        expected = fingerprint_of(
            {
                "domain": "billing.settlement.earmark.v1",
                "command": asdict(
                    EarmarkSettlementV1(
                        fact.scope,
                        fact.billing_account_id,
                        fact.settlement_id,
                        fact.amount,
                        fact.producer_ref,
                        fact.targets,
                        fact.source_ref,
                        fact.occurred_at,
                    )
                ),
            }
        )
        if fact.evidence_fingerprint != expected or row["request_digest"] != expected:
            raise ValueError("claim fingerprint differs")
        return fact
    except (ValueError, TypeError) as exc:
        raise BillingRuleViolation(
            "earmark_corrupt", "stored settlement earmark is invalid"
        ) from exc


def _effects(db: Session, scope: Scope, earmark_id: UUID) -> tuple[Any, ...]:
    model = _model(scope, "SettlementEarmarkEffect")
    rows = _rows(db, scope, model, model.earmark_id == earmark_id)
    for row in rows:
        if fingerprint_of(row["evidence"]) != row["evidence_digest"]:
            _fail("earmark_corrupt", "stored capacity evidence differs")
    return rows


def _held(db: Session, scope: Scope, claim: Any) -> Decimal:
    held = Decimal(claim["amount"]) + sum(
        (Decimal(row["held_delta"]) for row in _effects(db, scope, claim["id"])),
        Decimal(0),
    )
    if held < 0 or held > claim["amount"]:
        _fail("earmark_corrupt", "held capacity exceeds original claim")
    return held


def _touch_account(db: Session, scope: Scope, account_id: UUID) -> None:
    """Write serialization rejects stale RR/SERIALIZABLE claim/spend snapshots."""
    model = _model(scope, "BillingAccount")
    statement = (
        update(model.__table__).where(model.id == account_id).values(id=account_id)
    )
    if isinstance(scope, TenantScope):
        statement = statement.where(model.tenant_id == scope.tenant_id)
    result = db.execute(statement)
    if not isinstance(result, CursorResult):
        _fail("earmark_corrupt", "account serialization did not return a DML result")
    if result.rowcount != 1:
        _fail("not_found", "billing account was not found")


@_exact_capacity
def earmark_settlement(
    db: Session, *, scope: Scope, command: EarmarkSettlementV1
) -> SettlementEarmarkV1:
    from dotmac_billing import service

    if type(command) is not EarmarkSettlementV1:
        _fail("earmark_input_invalid", "expected EarmarkSettlementV1")
    command.__post_init__()
    service._require_scope_matches(routed=scope, declared=command.scope)
    _touch_account(db, scope, command.billing_account_id)
    digest = fingerprint_of(
        {"domain": "billing.settlement.earmark.v1", "command": asdict(command)}
    )

    def operation(session: Session) -> dict[str, object]:
        plane = service._models(scope)
        get_confirmed_settlement(
            session,
            scope=scope,
            request=GetConfirmedSettlementV1(
                scope, command.billing_account_id, command.settlement_id
            ),
        )
        settlement = service._one(
            session, scope=scope, model=plane.settlement, row_id=command.settlement_id
        )
        if (
            settlement.billing_account_id != command.billing_account_id
            or service._money(
                settlement.amount, settlement.currency, settlement.minor_units
            )
            != command.amount
        ):
            _fail(
                "earmark_binding_mismatch",
                "claim requires the full original settlement Money and account",
            )
        if not service._settlement_has_active_backing(
            session, scope=scope, settlement=settlement
        ):
            _fail(
                "earmark_backing_inactive",
                "claim requires active confirmed settlement backing",
            )
        # Even a fully reversed earlier consumption is not an UNUSED receipt.
        if _rows(
            session,
            scope,
            plane.allocation,
            plane.allocation.settlement_id == settlement.id,
        ):
            _fail(
                "earmark_previously_consumed",
                "claim requires a never-consumed settlement",
            )
        if _claim(session, scope, settlement_id=settlement.id) is not None:
            raise BillingConflict(
                "earmark_already_claimed", "settlement already has an immutable claim"
            )
        claim_id = uuid4()
        fact = SettlementEarmarkV1(
            claim_id,
            scope,
            command.billing_account_id,
            settlement.id,
            command.amount,
            command.producer_ref,
            command.targets,
            command.source_ref,
            command.occurred_at,
            digest,
        )
        payload = service._jsonable(fact)
        model = _model(scope, "SettlementEarmark")
        session.add(
            model(
                **service._values(
                    scope,
                    {
                        "id": claim_id,
                        "billing_account_id": command.billing_account_id,
                        "settlement_id": settlement.id,
                        "amount": command.amount.amount,
                        "currency": settlement.currency,
                        "minor_units": settlement.minor_units,
                        "producer_ref": command.producer_ref,
                        "source_ref": command.source_ref,
                        "request_digest": digest,
                        "evidence_digest": fingerprint_of(payload),
                        "evidence": payload,
                        "occurred_at": command.occurred_at,
                    },
                )
            )
        )
        session.flush()
        target_model = _model(scope, "SettlementEarmarkTarget")
        for target in command.targets:
            session.add(
                target_model(
                    **service._values(
                        scope,
                        {
                            "id": uuid4(),
                            "billing_account_id": command.billing_account_id,
                            "earmark_id": claim_id,
                            **dict(
                                zip(
                                    (
                                        "source_system",
                                        "source_kind",
                                        "source_fact_id",
                                        "source_fact_version",
                                    ),
                                    target.source_identity,
                                    strict=True,
                                )
                            ),
                            "amount": target.amount.amount,
                            "currency": settlement.currency,
                            "minor_units": settlement.minor_units,
                        },
                    )
                )
            )
        session.flush()
        service._emit(
            session,
            scope=scope,
            event_type="billing.settlement.earmarked.v1",
            payload=payload,
        )
        return {"earmark_id": str(claim_id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope="billing.settlement.earmark.v1",
        key=command.source_ref,
        fingerprint=digest,
        operation=operation,
    )
    row = _claim(db, scope, earmark_id=UUID(str(outcome.result["earmark_id"])))
    if row is None:
        _fail("earmark_corrupt", "idempotent claim evidence is missing")
    return _fact(row)


@_exact_capacity
def get_funding_availability(
    db: Session, *, scope: Scope, request: GetFundingAvailabilityV1
) -> FundingAvailabilityV1:
    from dotmac_billing import service

    if type(request) is not GetFundingAvailabilityV1:
        _fail("earmark_input_invalid", "expected GetFundingAvailabilityV1")
    request.__post_init__()
    service._require_scope_matches(routed=scope, declared=request.scope)
    _touch_account(db, scope, request.billing_account_id)
    plane = service._models(scope)
    account = service._one(
        db, scope=scope, model=plane.account, row_id=request.billing_account_id
    )
    conditions = [plane.settlement.billing_account_id == account.id]
    if request.settlement_id is not None:
        conditions.append(plane.settlement.id == request.settlement_id)
    settlements = _rows(db, scope, plane.settlement, *conditions)
    if request.settlement_id is not None and not settlements:
        _fail("not_found", "settlement was not found in the requested account")
    posted = held = Decimal(0)
    fingerprint_material: list[object] = []
    for row in sorted(settlements, key=lambda item: str(item["id"])):
        settlement = service._one(
            db, scope=scope, model=plane.settlement, row_id=row["id"]
        )
        active = service._settlement_has_active_backing(
            db, scope=scope, settlement=settlement
        )
        used = service._settlement_used(db, scope=scope, settlement_id=settlement.id)
        room = Decimal(settlement.amount) - used if active else Decimal(0)
        claim = _claim(db, scope, settlement_id=settlement.id)
        reserved = _held(db, scope, claim) if claim is not None else Decimal(0)
        if used < 0 or room < reserved or room < 0:
            _fail(
                "earmark_capacity_corrupt",
                "actual receipt capacity does not cover held capacity",
            )
        posted += room
        held += reserved
        fingerprint_material.append(
            (
                str(settlement.id),
                active,
                str(used),
                None if claim is None else claim["evidence_digest"],
                tuple(
                    sorted(
                        (str(item["id"]), item["evidence_digest"])
                        for item in (
                            () if claim is None else _effects(db, scope, claim["id"])
                        )
                    )
                ),
            )
        )
    if request.settlement_id is None:
        # Imported lots retain their canonical supported-cash lifecycle. Never
        # reconstruct opening balances here or count historical gross amounts.
        lots = _rows(
            db,
            scope,
            plane.opening_lot,
            plane.opening_lot.billing_account_id == account.id,
        )
        for lot_row in sorted(lots, key=lambda item: str(item["id"])):
            position = get_opening_cash_lot_position(
                db, scope=scope, lot_id=lot_row["id"]
            )
            if (
                position.remaining.currency.code != account.currency
                or position.remaining.currency.minor_units != account.minor_units
            ):
                _fail(
                    "earmark_currency_mismatch",
                    "opening cash differs from account currency",
                )
            if not position.unwound:
                posted += position.remaining.amount
            fingerprint_material.append(
                (
                    "opening_cash",
                    str(lot_row["id"]),
                    lot_row["evidence_digest"],
                    str(position.remaining.amount),
                    position.unwound,
                )
            )
        opening_effects = _rows(
            db,
            scope,
            plane.opening_effect,
            plane.opening_effect.billing_account_id == account.id,
        )
        fingerprint_material.append(
            tuple(
                sorted(
                    (str(row["id"]), row["evidence_digest"]) for row in opening_effects
                )
            )
        )
    # Cover all monetary groups (including linked inverses) as well as claims.
    groups = _rows(db, scope, plane.group, plane.group.billing_account_id == account.id)
    fingerprint_material.append(
        tuple(sorted((str(row["id"]), row["source_version"]) for row in groups))
    )

    def money(value: Decimal) -> Money:
        return service._money(value, account.currency, account.minor_units)

    return FundingAvailabilityV1(
        scope,
        account.id,
        request.settlement_id,
        money(posted),
        money(held),
        money(posted - held),
        fingerprint_of(fingerprint_material),
    )


@dataclass(frozen=True, slots=True)
class _EarmarkPermit:
    token: object
    scope: Scope
    change_digest: str
    command_digest: str
    operation: str
    claims: tuple[tuple[UUID, str], ...]
    verification: EarmarkCorrectionVerificationV1


def _check_permit(
    permit: _EarmarkPermit | None, scope: Scope, command: EarmarkChange
) -> None:
    if permit is not None and (
        type(permit) is not _EarmarkPermit
        or permit.token is not _VERIFIED
        or permit.scope != scope
        or permit.change_digest != fingerprint_of(asdict(command))
    ):
        _fail(
            "earmark_authority_invalid",
            "private correction permit does not cover this exact command",
        )


def _authorized(permit: _EarmarkPermit | None, scope: Scope, claim: Any) -> bool:
    return (
        permit is not None
        and type(permit) is _EarmarkPermit
        and permit.token is _VERIFIED
        and permit.scope == scope
        and (claim["id"], _fact(claim).evidence_fingerprint) in permit.claims
    )


def _append_effect(
    db: Session,
    scope: Scope,
    claim: Any,
    *,
    kind: str,
    delta: Decimal,
    source_ref: str,
    occurred_at: datetime,
    digest: str,
    posting_group_id: UUID | None = None,
    allocation_id: UUID | None = None,
    original_effect_id: UUID | None = None,
    verification: EarmarkCorrectionVerificationV1 | None = None,
) -> UUID:
    from dotmac_billing import service

    effect_id = uuid4()
    payload = service._jsonable(
        {
            "effect_id": effect_id,
            "scope": scope,
            "earmark_id": claim["id"],
            "claim_fingerprint": _fact(claim).evidence_fingerprint,
            "billing_account_id": claim["billing_account_id"],
            "kind": kind,
            "held_delta": service._money(
                delta, claim["currency"], claim["minor_units"]
            ),
            "source_ref": source_ref,
            "occurred_at": occurred_at,
            "posting_group_id": posting_group_id,
            "allocation_id": allocation_id,
            "original_effect_id": original_effect_id,
            "request_digest": digest,
            "verification": verification,
        }
    )
    model = _model(scope, "SettlementEarmarkEffect")
    db.add(
        model(
            **service._values(
                scope,
                {
                    "id": effect_id,
                    "billing_account_id": claim["billing_account_id"],
                    "earmark_id": claim["id"],
                    "posting_group_id": posting_group_id,
                    "allocation_id": allocation_id,
                    "original_effect_id": original_effect_id,
                    "effect_kind": kind,
                    "held_delta": delta,
                    "currency": claim["currency"],
                    "minor_units": claim["minor_units"],
                    "source_ref": source_ref,
                    "request_digest": digest,
                    "evidence_digest": fingerprint_of(payload),
                    "evidence": payload,
                    "occurred_at": occurred_at,
                },
            )
        )
    )
    return effect_id


def _tree_room(
    effect: Any, rows: tuple[Any, ...], visited: frozenset[UUID] = frozenset()
) -> Decimal:
    if effect["id"] in visited:
        _fail("earmark_corrupt", "capacity correction lineage cycles")
    return Decimal(effect["held_delta"]) + sum(
        (
            _tree_room(child, rows, visited | {effect["id"]})
            for child in rows
            if child["original_effect_id"] == effect["id"]
        ),
        Decimal(0),
    )


def _exact_target(
    db: Session, scope: Scope, claim: Any, document_id: UUID | None, amount: Decimal
) -> bool:
    from dotmac_billing import service

    if document_id is None:
        return False
    fact = _fact(claim)
    targets = {item.source_identity: item.amount.amount for item in fact.targets}
    plane = service._models(scope)
    document = service._one(db, scope=scope, model=plane.document, row_id=document_id)
    service._require_credit_target(
        db, scope=scope, document=document, account_id=claim["billing_account_id"]
    )
    lines = _rows(db, scope, plane.line, plane.line.document_id == document_id)
    if not lines:
        return False
    seen: set[tuple[str, str, str, str]] = set()
    total = Decimal(0)
    for line in lines:
        obligation = service._one(
            db, scope=scope, model=plane.obligation, row_id=line["obligation_id"]
        )
        identity = tuple(
            getattr(obligation, name)
            for name in (
                "source_system",
                "source_kind",
                "source_fact_id",
                "source_fact_version",
            )
        )
        if (
            identity in seen
            or identity not in targets
            or obligation.billing_account_id != claim["billing_account_id"]
            or obligation.currency != claim["currency"]
            or obligation.minor_units != claim["minor_units"]
            or obligation.total_amount != targets[identity]
            or line["total_amount"] != targets[identity]
        ):
            return False
        seen.add(identity)
        total += targets[identity]
    return total == amount and document.grand_total == amount


@dataclass(frozen=True, slots=True)
class _CapacityPlan:
    claim: Any
    kind: str
    delta: Decimal
    allocation_id: UUID | None = None
    original_effect_id: UUID | None = None


@_exact_capacity
def guard_post_group(
    db: Session,
    *,
    scope: Scope,
    account_id: UUID,
    kind: str,
    allocations: tuple[Any, ...],
    opening_effects: tuple[OpeningCashEffectV1, ...],
    reverses_group_id: UUID | None,
    corrects_posting_group_id: UUID | None,
    permit: _EarmarkPermit | None,
) -> tuple[_CapacityPlan, ...]:
    """Validate every source mutation, including no-allocation ancestry."""
    from dotmac_billing import service

    plane = service._models(scope)
    _validate_post_capacity(
        db,
        scope=scope,
        account_id=account_id,
        allocations=allocations,
        opening_effects=opening_effects,
    )
    plans: list[_CapacityPlan] = []
    projected: dict[UUID, Decimal] = {}
    touched: set[UUID] = set()
    rooms: dict[UUID, Decimal] = {}

    def reserve(claim: Any) -> Decimal:
        touched.add(claim["id"])
        if claim["billing_account_id"] != account_id:
            _fail(
                "earmark_account_mismatch", "claim does not belong to posting account"
            )
        return projected.setdefault(claim["id"], _held(db, scope, claim))

    def plan(
        claim: Any,
        effect_kind: str,
        delta: Decimal,
        allocation_id: UUID | None = None,
        original_effect_id: UUID | None = None,
    ) -> None:
        if delta == 0:
            return
        projected[claim["id"]] += delta
        plans.append(
            _CapacityPlan(claim, effect_kind, delta, allocation_id, original_effect_id)
        )

    if corrects_posting_group_id is not None:
        for original in _rows(
            db,
            scope,
            plane.allocation,
            plane.allocation.posting_group_id == corrects_posting_group_id,
        ):
            claim = _claim(db, scope, settlement_id=original["settlement_id"])
            if claim is not None:
                reserve(claim)
                if not _authorized(permit, scope, claim):
                    _fail(
                        "earmark_correction_required",
                        "mixed correction requires trusted source verification",
                    )
    # Validate all restrictions before appending any financial or capacity fact.
    # Negative inputs restore attributed capacity first; reallocation can then
    # spend that same capacity with an independently authorized release.
    for allocation in sorted(allocations, key=lambda item: item.amount_delta >= 0):
        claim = _claim(db, scope, settlement_id=allocation.settlement_id)
        if claim is None:
            continue
        held = reserve(claim)
        authorized = _authorized(permit, scope, claim)
        settlement = service._one(
            db, scope=scope, model=plane.settlement, row_id=allocation.settlement_id
        )
        if not service._settlement_has_active_backing(
            db, scope=scope, settlement=settlement
        ):
            _fail(
                "earmark_backing_inactive", "claimed mutation requires active backing"
            )
        room = Decimal(settlement.amount) - service._settlement_used(
            db, scope=scope, settlement_id=settlement.id
        )
        if room < _held(db, scope, claim) or room < 0:
            _fail(
                "earmark_capacity_corrupt",
                "actual source does not cover its held capacity",
            )
        room = rooms.setdefault(claim["id"], room)
        raw = allocation.amount_delta
        if (
            type(raw) is not Decimal
            or not raw.is_finite()
            or raw.adjusted() >= 14
            or raw != raw.quantize(Decimal(1).scaleb(-claim["minor_units"]))
        ):
            _fail(
                "earmark_money_invalid", "claimed mutation requires exact stored Money"
            )
        if allocation.amount_delta > room:
            _fail(
                "earmark_source_exhausted",
                "mutation exceeds actual unused receipt capacity",
            )
        rooms[claim["id"]] -= allocation.amount_delta
        if allocation.amount_delta < 0:
            if not authorized:
                _fail(
                    "earmark_correction_required",
                    "claimed corrections require trusted verification",
                )
            rows = _effects(db, scope, claim["id"])
            if allocation.offsets_allocation_id is not None:
                candidates = [
                    row
                    for row in rows
                    if row["allocation_id"] == allocation.offsets_allocation_id
                ]
            elif kind == "reallocation" and allocation.document_id is not None:
                source_allocations = _rows(
                    db,
                    scope,
                    plane.allocation,
                    plane.allocation.settlement_id == settlement.id,
                    plane.allocation.document_id == allocation.document_id,
                )
                # Reallocation's legacy input is unlinked. Attribute its inverse
                # to actual original capacity facts, not account/document totals.
                source_ids = {
                    row["id"] for row in source_allocations if row["amount_delta"] > 0
                }
                net_source = sum(
                    (Decimal(row["amount_delta"]) for row in source_allocations),
                    Decimal(0),
                )
                if -allocation.amount_delta > net_source:
                    _fail(
                        "earmark_reallocation_exceeds_source",
                        "reallocation exceeds this settlement's source coverage",
                    )
                candidates = [
                    row
                    for row in rows
                    if row["allocation_id"] in source_ids and row["held_delta"] < 0
                ]
            else:
                _fail(
                    "earmark_inverse_unlinked",
                    "capacity restoration requires original allocation ancestry",
                )
            remaining = -Decimal(allocation.amount_delta)
            for original in sorted(candidates, key=lambda row: str(row["id"])):
                net = _tree_room(original, rows)
                quantity = min(remaining, abs(net))
                if not quantity:
                    continue
                delta = quantity if net < 0 else -quantity
                plan(
                    claim,
                    "restore" if delta > 0 else "consumption",
                    delta,
                    allocation.allocation_id,
                    original["id"],
                )
                remaining -= quantity
            # The remainder reversed released/unrestricted spending, so creates
            # no held capacity. It must never mint a fresh reservation.
        else:
            amount = Decimal(allocation.amount_delta)
            # For a reversal of a capacity-restoring inverse, cancel that exact
            # prior attribution before considering any ordinary target policy.
            inverse_rows = (
                ()
                if allocation.offsets_allocation_id is None
                else tuple(
                    row
                    for row in _effects(db, scope, claim["id"])
                    if row["allocation_id"] == allocation.offsets_allocation_id
                )
            )
            if kind in ("reversal", "cash_credit_draw_reversed"):
                if not authorized:
                    _fail(
                        "earmark_correction_required",
                        "claimed inverse requires trusted verification",
                    )
                remaining = amount
                all_effects = _effects(db, scope, claim["id"])
                for original in inverse_rows:
                    net = _tree_room(original, all_effects)
                    quantity = min(remaining, abs(net))
                    if quantity:
                        plan(
                            claim,
                            "consumption" if net > 0 else "restore",
                            -quantity if net > 0 else quantity,
                            allocation.allocation_id,
                            original["id"],
                        )
                        remaining -= quantity
            elif allocation.document_id is None:
                if not authorized:
                    _fail(
                        "earmark_refund_held",
                        "refund exceeds unrestricted source capacity",
                    )
                if authorized:
                    plan(claim, "refund", -min(amount, held), allocation.allocation_id)
            elif _exact_target(db, scope, claim, allocation.document_id, amount):
                plan(claim, "consumption", -min(amount, held), allocation.allocation_id)
            elif kind == "reallocation" and authorized:
                # The original target remains immutable. Moving attributed
                # funding elsewhere releases the moved held capacity explicitly.
                restored = sum(
                    (
                        item.delta
                        for item in plans
                        if item.claim["id"] == claim["id"] and item.delta > 0
                    ),
                    Decimal(0),
                )
                plan(
                    claim,
                    "release",
                    -min(amount, projected[claim["id"]], restored),
                    allocation.allocation_id,
                )
            elif amount > room - held:
                _fail(
                    "earmark_target_mismatch",
                    "allocation exceeds unrestricted room and mismatches targets",
                )

    if reverses_group_id is not None:
        original = service._one(
            db, scope=scope, model=plane.group, row_id=reverses_group_id
        )
        current = original
        visited: set[UUID] = set()
        while current.reverses_group_id is not None:
            if current.id in visited:
                _fail("earmark_backing_ambiguous", "backing reversal ancestry cycles")
            visited.add(current.id)
            current = service._one(
                db, scope=scope, model=plane.group, row_id=current.reverses_group_id
            )
        if current.group_kind == "settlement_accepted":
            settlement_candidates = _rows(
                db,
                scope,
                plane.settlement,
                plane.settlement.billing_account_id == account_id,
            )
            for settlement_row in settlement_candidates:
                if current.source_ref != f"settlement:{settlement_row['id']}":
                    continue
                settlement = service._one(
                    db, scope=scope, model=plane.settlement, row_id=settlement_row["id"]
                )
                if (
                    service._settlement_used(
                        db, scope=scope, settlement_id=settlement.id
                    )
                    != 0
                ):
                    # Consumption is pooled: other active backing on the
                    # account may cover this receipt's use, but removing
                    # backing must never leave total consumption uncovered.
                    total_used = sum(
                        (
                            service._settlement_used(
                                db, scope=scope, settlement_id=row["id"]
                            )
                            for row in settlement_candidates
                        ),
                        Decimal(0),
                    )
                    other_backing = sum(
                        (
                            Decimal(row["amount"])
                            for row in settlement_candidates
                            if row["id"] != settlement.id
                            and service._settlement_has_active_backing(
                                db,
                                scope=scope,
                                settlement=service._one(
                                    db,
                                    scope=scope,
                                    model=plane.settlement,
                                    row_id=row["id"],
                                ),
                            )
                        ),
                        Decimal(0),
                    )
                    if total_used > other_backing:
                        _fail(
                            "settlement_backing_consumed",
                            "undo receipt consumption before removing backing",
                        )
                claim = _claim(db, scope, settlement_id=settlement_row["id"])
                if claim is None:
                    continue
                held = reserve(claim)
                if not _authorized(permit, scope, claim):
                    _fail(
                        "earmark_correction_required",
                        "claimed backing reversal requires trusted verification",
                    )
                if (
                    service._settlement_used(
                        db, scope=scope, settlement_id=settlement_row["id"]
                    )
                    != 0
                ):
                    _fail(
                        "earmark_backing_consumed",
                        "undo all receipt consumption before correcting its backing",
                    )
                original_effects = [
                    row
                    for row in _effects(db, scope, claim["id"])
                    if row["posting_group_id"] == original.id
                    and row["allocation_id"] is None
                ]
                if original_effects:
                    for effect in original_effects:
                        plan(
                            claim,
                            "backing_inverse"
                            if effect["held_delta"] < 0
                            else "backing",
                            -_tree_room(effect, _effects(db, scope, claim["id"])),
                            original_effect_id=effect["id"],
                        )
                else:
                    # First backing removal consumes only still-held capacity;
                    # previously released capacity remains released on inverse.
                    plan(claim, "backing", -held)
    if permit is not None and touched != {item[0] for item in permit.claims}:
        _fail(
            "earmark_authority_invalid",
            "verified claims do not match actual monetary sources",
        )
    for claim_id, held in projected.items():
        claim = _claim(db, scope, earmark_id=claim_id)
        if claim is None or held < 0 or held > claim["amount"]:
            _fail(
                "earmark_inverse_exceeds_capacity",
                "inverse would exceed retained claim capacity",
            )
    return tuple(plans)


@_exact_capacity
def persist_capacity_plans(
    db: Session,
    *,
    scope: Scope,
    plans: tuple[_CapacityPlan, ...],
    posting_group_id: UUID,
    source_ref: str,
    occurred_at: datetime,
    permit: _EarmarkPermit | None,
) -> None:
    for plan in plans:
        _append_effect(
            db,
            scope,
            plan.claim,
            kind=plan.kind,
            delta=plan.delta,
            source_ref=source_ref,
            occurred_at=occurred_at,
            digest=permit.command_digest
            if permit is not None
            else fingerprint_of({"posting_group_id": str(posting_group_id)}),
            posting_group_id=posting_group_id,
            allocation_id=plan.allocation_id,
            original_effect_id=plan.original_effect_id,
            verification=None if permit is None else permit.verification,
        )
    db.flush()
    # Actual money and capacity attribution must agree after every post. This
    # rejects inverse re-holding of capacity already consumed after a release.
    for claim_id in {plan.claim["id"] for plan in plans}:
        claim = _claim(db, scope, earmark_id=claim_id)
        if claim is not None:
            get_funding_availability(
                db,
                scope=scope,
                request=GetFundingAvailabilityV1(
                    scope, claim["billing_account_id"], claim["settlement_id"]
                ),
            )


@_exact_capacity
def unrestricted_settlement_room(
    db: Session, *, scope: Scope, settlement_id: UUID, actual_room: Decimal
) -> Decimal:
    claim = _claim(db, scope, settlement_id=settlement_id)
    return actual_room if claim is None else actual_room - _held(db, scope, claim)


@_exact_capacity
def correct_settlement_earmark(
    db: Session,
    *,
    scope: Scope,
    command: CorrectSettlementEarmarkV1,
    verifier: EarmarkCorrectionVerifier,
) -> models.PostingGroup | models.PlatformPostingGroup | UUID:
    """Verify fresh authority BEFORE replay or mutable capacity admission checks."""
    from dotmac_billing import service

    if type(command) is not CorrectSettlementEarmarkV1:
        _fail("earmark_input_invalid", "expected CorrectSettlementEarmarkV1")
    command.__post_init__()
    service._require_scope_matches(routed=scope, declared=command.scope)
    claims = []
    for claim_id in command.earmark_ids:
        row = _claim(db, scope, earmark_id=claim_id)
        if row is None:
            _fail("not_found", "correction claim was not found")
        claims.append(_fact(row))
    facts = tuple(sorted(claims, key=lambda item: str(item.earmark_id)))
    if len({item.billing_account_id for item in facts}) != 1:
        _fail(
            "earmark_account_mismatch", "correction claims must belong to one account"
        )
    operation_scope = "billing.earmark.correct." + type(command.change).__name__ + ".v1"
    digest = fingerprint_of({"domain": operation_scope, "command": asdict(command)})
    request = EarmarkCorrectionVerificationRequestV1(
        operation_scope, command, digest, facts
    )
    verdict = verifier.verify_correction(request)
    fingerprints = tuple((item.earmark_id, item.evidence_fingerprint) for item in facts)
    if (
        type(verdict) is not EarmarkCorrectionVerificationV1
        or verdict.operation != operation_scope
        or verdict.command_digest != digest
        or verdict.claim_fingerprints != fingerprints
        or type(verdict.authorization_proof_ref) is not str
        or not verdict.authorization_proof_ref.strip()
        or type(verdict.outcome_proof_ref) is not str
        or not verdict.outcome_proof_ref.strip()
    ):
        _fail(
            "earmark_verification_required",
            "fresh trusted operation-qualified correction verification is required",
        )
    permit = _EarmarkPermit(
        _VERIFIED,
        scope,
        fingerprint_of(asdict(command.change)),
        digest,
        operation_scope,
        fingerprints,
        verdict,
    )
    _touch_account(db, scope, facts[0].billing_account_id)
    change = command.change
    if isinstance(change, ReleaseEarmarkV1 | ReverseEarmarkReleaseV1):
        service._require_scope_matches(routed=scope, declared=change.scope)
        return _release_change(db, scope=scope, command=change, permit=permit)

    def operation(session: Session) -> dict[str, object]:
        if isinstance(change, DeallocationCommand):
            group = service.deallocate_settlement(
                session, scope=scope, command=change, _earmark_permit=permit
            )
        elif isinstance(change, ReallocationCommand):
            group = service.reallocate_settlement(
                session, scope=scope, command=change, _earmark_permit=permit
            )
        elif isinstance(change, RefundCommand):
            group = service.refund_settlement(
                session, scope=scope, command=change, _earmark_permit=permit
            )
        elif isinstance(change, ReversePostingGroupCommand):
            group = service.reverse_posting_group(
                session, scope=scope, command=change, _earmark_permit=permit
            )
        elif isinstance(change, ReverseCashCreditDrawV1):
            group = service.reverse_cash_credit_draw(
                session, scope=scope, command=change, _earmark_permit=permit
            )
        else:
            _fail("earmark_input_invalid", "unsupported correction command")
        return {"posting_group_id": str(group.id)}

    key = (
        change.idempotency_key
        if isinstance(change, ReverseCashCreditDrawV1)
        else change.source_ref
    )
    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope=permit.operation,
        key=key,
        fingerprint=digest,
        operation=operation,
    )
    group = service._one(
        db,
        scope=scope,
        model=service._models(scope).group,
        row_id=UUID(str(outcome.result["posting_group_id"])),
    )
    if not isinstance(group, models.PostingGroup | models.PlatformPostingGroup):
        _fail("earmark_corrupt", "correction result is not a posting group")
    return group


@_exact_capacity
def _release_change(
    db: Session,
    *,
    scope: Scope,
    command: ReleaseEarmarkV1 | ReverseEarmarkReleaseV1,
    permit: _EarmarkPermit,
) -> UUID:
    from dotmac_billing import service

    _check_permit(permit, scope, command)

    def operation(session: Session) -> dict[str, object]:
        original = None
        if isinstance(command, ReleaseEarmarkV1):
            claim = _claim(session, scope, earmark_id=command.earmark_id)
            delta = -command.amount.amount
            kind = "release"
        else:
            model = _model(scope, "SettlementEarmarkEffect")
            rows = _rows(session, scope, model, model.id == command.release_effect_id)
            if not rows or rows[0]["effect_kind"] not in ("release", "release_inverse"):
                _fail(
                    "earmark_release_invalid",
                    "inverse requires original release ancestry",
                )
            original = rows[0]
            claim = _claim(session, scope, earmark_id=original["earmark_id"])
            net = _tree_room(original, _effects(session, scope, original["earmark_id"]))
            if command.amount.amount > abs(net):
                _fail(
                    "earmark_inverse_exceeds_capacity",
                    "release inverse exceeds original remaining release",
                )
            delta = command.amount.amount if net < 0 else -command.amount.amount
            kind = "release_inverse" if delta > 0 else "release"
        if (
            claim is None
            or not _authorized(permit, scope, claim)
            or len(permit.claims) != 1
        ):
            _fail("earmark_authority_invalid", "verified release claim differs")
        if (
            service._money(
                command.amount.amount, claim["currency"], claim["minor_units"]
            )
            != command.amount
        ):
            _fail(
                "earmark_binding_mismatch", "release Money differs from original claim"
            )
        available = get_funding_availability(
            session,
            scope=scope,
            request=GetFundingAvailabilityV1(
                scope, claim["billing_account_id"], claim["settlement_id"]
            ),
        )
        if (
            available.held.amount + delta < 0
            or available.held.amount + delta > available.posted.amount
        ):
            _fail(
                "earmark_inverse_exceeds_capacity",
                "release or inverse exceeds actual held/unspent capacity",
            )
        effect_id = _append_effect(
            session,
            scope,
            claim,
            kind=kind,
            delta=delta,
            source_ref=command.source_ref,
            occurred_at=command.occurred_at,
            digest=permit.command_digest,
            original_effect_id=None if original is None else original["id"],
            verification=permit.verification,
        )
        session.flush()
        service._emit(
            session,
            scope=scope,
            event_type="billing.settlement.earmark.changed.v1",
            payload={
                "earmark_id": str(claim["id"]),
                "capacity_effect_id": str(effect_id),
                "command_digest": permit.command_digest,
            },
        )
        return {"effect_id": str(effect_id)}

    outcome = service._idempotent(
        db,
        scope=scope,
        operation_scope=permit.operation,
        key=command.source_ref,
        fingerprint=permit.command_digest,
        operation=operation,
    )
    return UUID(str(outcome.result["effect_id"]))


def require_claim_correction(
    db: Session, *, scope: Scope, settlement_id: UUID, permit: _EarmarkPermit | None
) -> None:
    """Run before existing owner replay, which intentionally skips its operation."""
    claim = _claim(db, scope, settlement_id=settlement_id)
    if claim is not None and not _authorized(permit, scope, claim):
        _fail(
            "earmark_correction_required",
            "claimed-source correction requires fresh trusted verification",
        )


def require_group_correction(
    db: Session, *, scope: Scope, group_id: UUID, permit: _EarmarkPermit | None
) -> None:
    from dotmac_billing import service

    plane = service._models(scope)
    original = service._one(db, scope=scope, model=plane.group, row_id=group_id)
    for allocation in _rows(
        db, scope, plane.allocation, plane.allocation.posting_group_id == group_id
    ):
        require_claim_correction(
            db, scope=scope, settlement_id=allocation["settlement_id"], permit=permit
        )
    current = original
    visited: set[UUID] = set()
    while current.reverses_group_id is not None:
        if current.id in visited:
            _fail("earmark_backing_ambiguous", "backing ancestry cycles")
        visited.add(current.id)
        current = service._one(
            db, scope=scope, model=plane.group, row_id=current.reverses_group_id
        )
    if current.group_kind == "settlement_accepted":
        for settlement in _rows(
            db,
            scope,
            plane.settlement,
            plane.settlement.billing_account_id == current.billing_account_id,
        ):
            if current.source_ref == f"settlement:{settlement['id']}":
                require_claim_correction(
                    db, scope=scope, settlement_id=settlement["id"], permit=permit
                )


@_exact_capacity
def get_confirmed_settlement(
    db: Session, *, scope: Scope, request: GetConfirmedSettlementV1
) -> ConfirmedSettlementObservationV1:
    from dotmac_billing import service

    if type(request) is not GetConfirmedSettlementV1:
        _fail("earmark_input_invalid", "expected GetConfirmedSettlementV1")
    request.__post_init__()
    service._require_scope_matches(routed=scope, declared=request.scope)
    _touch_account(db, scope, request.billing_account_id)
    plane = service._models(scope)
    rows = _rows(
        db,
        scope,
        plane.settlement,
        plane.settlement.id == request.settlement_id,
        plane.settlement.billing_account_id == request.billing_account_id,
    )
    if len(rows) != 1:
        _fail(
            "not_found", "confirmed settlement was not found in the requested account"
        )
    row = rows[0]
    amount = service._money(row["amount"], row["currency"], row["minor_units"])
    command = AcceptSettlementV1(
        scope,
        row["billing_account_id"],
        row["source_system"],
        row["source_settlement_key"],
        row["source_version"],
        amount,
        row["occurred_at"],
        row["observed_at"],
        row["confirmation_evidence"],
        SettlementFundingLane(row["funding_lane"]),
    )
    service._require_cash_source_claim(
        db, scope=scope, settlement_id=row["id"], command=command
    )
    from dotmac_billing._cash_draw_queries import validate_confirmed_cash_origin

    validate_confirmed_cash_origin(db, scope=scope, source=row)
    settlement = service._one(db, scope=scope, model=plane.settlement, row_id=row["id"])
    active = service._settlement_has_active_backing(
        db, scope=scope, settlement=settlement
    )
    return ConfirmedSettlementObservationV1(
        scope,
        row["billing_account_id"],
        row["id"],
        amount,
        row["source_system"],
        row["source_settlement_key"],
        row["source_version"],
        row["request_fingerprint"],
        row["occurred_at"],
        row["observed_at"],
        row["confirmation_evidence"],
        command.funding_lane,
        active,
    )


@_exact_capacity
def allocation_remaining(db: Session, *, scope: Scope, allocation_id: UUID) -> Decimal:
    """Follow actual linked monetary inverses and current source/document room."""
    from dotmac_billing import service

    plane = service._models(scope)
    original = service._one(
        db, scope=scope, model=plane.allocation, row_id=allocation_id
    )
    rows = _rows(
        db,
        scope,
        plane.allocation,
        plane.allocation.settlement_id == original.settlement_id,
        plane.allocation.document_id == original.document_id,
    )
    tree_rows = tuple(
        {
            "id": row["id"],
            "held_delta": row["amount_delta"],
            "original_effect_id": row["offsets_allocation_id"],
        }
        for row in rows
    )
    root = next(row for row in tree_rows if row["id"] == allocation_id)
    return min(
        _tree_room(root, tree_rows),
        sum((Decimal(row["amount_delta"]) for row in rows), Decimal(0)),
    )


@_exact_capacity
def _validate_post_capacity(
    db: Session,
    *,
    scope: Scope,
    account_id: UUID,
    allocations: tuple[Any, ...],
    opening_effects: tuple[OpeningCashEffectV1, ...],
) -> None:
    """One source/invoice capacity invariant for all financial posting owners.

    Aggregate account balances cannot detect an over-covered invoice when a
    different invoice still has open AR. Generic inverses obey these same caps.
    """
    from dotmac_billing import service

    plane = service._models(scope)
    source_deltas: dict[UUID, Decimal] = {}
    document_deltas: dict[UUID, Decimal] = {}
    for item in allocations:
        source_deltas[item.settlement_id] = (
            source_deltas.get(item.settlement_id, Decimal(0)) + item.amount_delta
        )
        if item.document_id is None:
            continue
        document_deltas[item.document_id] = (
            document_deltas.get(item.document_id, Decimal(0)) + item.amount_delta
        )
        if item.amount_delta < 0:
            sources = _rows(
                db,
                scope,
                plane.allocation,
                plane.allocation.settlement_id == item.settlement_id,
                plane.allocation.document_id == item.document_id,
            )
            covered = sum((Decimal(row["amount_delta"]) for row in sources), Decimal(0))
            if -item.amount_delta > covered:
                _fail(
                    "allocation_inverse_exceeds_source",
                    "inverse exceeds this receipt's document coverage",
                )
    for source_id, delta in source_deltas.items():
        settlement = service._one(
            db, scope=scope, model=plane.settlement, row_id=source_id
        )
        if (
            settlement.billing_account_id != account_id
            or not service._settlement_has_active_backing(
                db, scope=scope, settlement=settlement
            )
        ):
            _fail(
                "settlement_backing_inactive",
                "source mutation requires active same-account receipt backing",
            )
        projected = (
            service._settlement_used(db, scope=scope, settlement_id=source_id) + delta
        )
        if projected < 0 or projected > settlement.amount:
            _fail(
                "settlement_capacity_exceeded",
                "mutation exceeds original receipt capacity",
            )
    for effect in opening_effects:
        if effect.document_id is not None:
            document_deltas[effect.document_id] = (
                document_deltas.get(effect.document_id, Decimal(0))
                - effect.availability_delta.amount
            )
    for document_id, delta in document_deltas.items():
        if delta <= 0:
            continue
        document = service._one(
            db, scope=scope, model=plane.document, row_id=document_id
        )
        service._require_credit_target(
            db, scope=scope, document=document, account_id=account_id
        )
        if delta > service._document_outstanding(db, scope=scope, document=document):
            _fail(
                "allocation_exceeds_receivable",
                "inverse exceeds current invoice receivable room",
            )
