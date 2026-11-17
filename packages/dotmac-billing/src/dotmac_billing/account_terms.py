"""Account terms: dated per-account contract terms over a tenant default.

Contract type (prepaid | postpaid), period, postpaid billing timing and postpaid
grace are owned here, on the billing account. Two append-only tables hold them:
dated versions of an account's own terms (every term nullable, NULL = use the
tenant default) and dated versions of the tenant default (every term set).

Billing performs no period arithmetic and reads no clock. Callers read the terms
*at the start of the period they charge* (``get_effective_account_terms``), so a
change dated mid-period only affects periods that start after it. Back-dating is
refused by the database (``effective_from`` may not precede the DB recording
instant). Commands run in the caller's transaction and never commit.

Resolution at instant ``at``: the account's latest version with
``effective_from <= at`` is that account's statement of overrides; each term it
leaves NULL, and every term when the account has no such version, comes from the
latest default version with ``effective_from <= at``. Each resolved term reports
its source and the version id it came from. No default at ``at`` is a refusal.

Existing Billing commands are unchanged; nothing here is called by them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Generic, Literal, TypeVar, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_billing import models
from dotmac_billing.errors import BillingConflict, BillingRuleViolation

ContractType = Literal["prepaid", "postpaid"]
TermsPeriod = Literal["daily", "monthly", "quarterly", "annually"]
PostpaidBillingTiming = Literal["period_start", "period_end"]
TermSource = Literal["customer", "default"]

CONTRACT_TYPES: tuple[str, ...] = ("prepaid", "postpaid")
TERMS_PERIODS: tuple[str, ...] = ("daily", "monthly", "quarterly", "annually")
POSTPAID_BILLING_TIMINGS: tuple[str, ...] = ("period_start", "period_end")

T = TypeVar("T")


def _aware(value: object, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise BillingRuleViolation(
            "terms_instant_invalid", f"{name} must be a timezone-aware datetime"
        )


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise BillingRuleViolation(
            "terms_attribution_invalid", f"{name} must be 1 to 255 non-blank characters"
        )


def _member(
    value: object, allowed: tuple[str, ...], name: str, *, nullable: bool
) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or value not in allowed:
        raise BillingRuleViolation(
            "terms_value_invalid", f"{name} must be one of {', '.join(allowed)}"
        )


def _grace(value: object, *, nullable: bool) -> None:
    if value is None and nullable:
        return
    if (
        not isinstance(value, timedelta)
        or isinstance(value, bool)
        or value < timedelta(0)
    ):
        raise BillingRuleViolation(
            "terms_value_invalid", "postpaid_grace must be a non-negative timedelta"
        )


def _scope(scope: object) -> None:
    if not isinstance(scope, TenantScope | PlatformScope) or (
        isinstance(scope, TenantScope) and not isinstance(scope.tenant_id, UUID)
    ):
        raise BillingRuleViolation(
            "scope_mismatch", "account terms require an explicit scope"
        )


@dataclass(frozen=True, slots=True)
class RecordAccountTermsV1:
    """One dated version of an account's own terms; a NULL term uses the default.

    The version is a complete statement of the account's overrides: a term left
    ``None`` returns to the tenant default from ``effective_from``.
    """

    scope: Scope
    billing_account_id: UUID
    effective_from: datetime
    actor_ref: str
    reason: str
    contract_type: ContractType | None = None
    period: TermsPeriod | None = None
    postpaid_billing_timing: PostpaidBillingTiming | None = None
    postpaid_grace: timedelta | None = None

    def __post_init__(self) -> None:
        _scope(self.scope)
        if not isinstance(self.billing_account_id, UUID):
            raise BillingRuleViolation(
                "invalid_account_id", "billing account identity must be a UUID"
            )
        _aware(self.effective_from, "effective_from")
        _text(self.actor_ref, "actor_ref")
        _text(self.reason, "reason")
        _member(self.contract_type, CONTRACT_TYPES, "contract_type", nullable=True)
        _member(self.period, TERMS_PERIODS, "period", nullable=True)
        _member(
            self.postpaid_billing_timing,
            POSTPAID_BILLING_TIMINGS,
            "postpaid_billing_timing",
            nullable=True,
        )
        _grace(self.postpaid_grace, nullable=True)


@dataclass(frozen=True, slots=True)
class RecordDefaultTermsV1:
    """One dated version of the tenant default terms; every term is required."""

    scope: Scope
    effective_from: datetime
    contract_type: ContractType
    period: TermsPeriod
    postpaid_billing_timing: PostpaidBillingTiming
    postpaid_grace: timedelta
    actor_ref: str
    reason: str

    def __post_init__(self) -> None:
        _scope(self.scope)
        _aware(self.effective_from, "effective_from")
        _text(self.actor_ref, "actor_ref")
        _text(self.reason, "reason")
        _member(self.contract_type, CONTRACT_TYPES, "contract_type", nullable=False)
        _member(self.period, TERMS_PERIODS, "period", nullable=False)
        _member(
            self.postpaid_billing_timing,
            POSTPAID_BILLING_TIMINGS,
            "postpaid_billing_timing",
            nullable=False,
        )
        _grace(self.postpaid_grace, nullable=False)


@dataclass(frozen=True, slots=True)
class TermsVersionRefV1:
    """The recorded version: its identity and the DB recording instant."""

    id: UUID
    effective_from: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class EffectiveTermV1(Generic[T]):
    value: T
    source: TermSource
    version_id: UUID


@dataclass(frozen=True, slots=True)
class AccountTermsV1:
    billing_account_id: UUID
    at: datetime
    contract_type: EffectiveTermV1[ContractType]
    period: EffectiveTermV1[TermsPeriod]
    postpaid_billing_timing: EffectiveTermV1[PostpaidBillingTiming]
    postpaid_grace: EffectiveTermV1[timedelta]


def _plane(scope: Scope) -> tuple[Any, Any, Any]:
    if isinstance(scope, TenantScope):
        return (
            models.BillingAccount,
            models.BillingAccountTermsVersion,
            models.BillingTermsDefaultsVersion,
        )
    return (
        models.PlatformBillingAccount,
        models.PlatformBillingAccountTermsVersion,
        models.PlatformBillingTermsDefaultsVersion,
    )


def _scoped(statement: Any, scope: Scope, model: Any) -> Any:
    if isinstance(scope, TenantScope):
        return statement.where(model.tenant_id == scope.tenant_id)
    return statement


def _require_account(db: Session, scope: Scope, account_id: UUID) -> None:
    account = _plane(scope)[0]
    found = db.execute(
        _scoped(select(account.id).where(account.id == account_id), scope, account)
    ).first()
    if found is None:
        raise BillingRuleViolation(
            "account_not_found", "the billing account does not exist in this scope"
        )


def _append(db: Session, scope: Scope, row: Any) -> TermsVersionRefV1:
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code == "23505":
            raise BillingConflict(
                "terms_version_exists",
                "terms are already recorded for that effective_from",
            ) from exc
        if code == "23514" and "back-dated" in str(exc.orig):
            raise BillingRuleViolation(
                "terms_back_dated",
                "terms cannot take effect before the instant they are recorded",
            ) from exc
        raise
    # recorded_at is stamped by the database trigger; read it back.
    db.refresh(row)
    return TermsVersionRefV1(
        id=row.id, effective_from=row.effective_from, recorded_at=row.recorded_at
    )


def record_account_terms(
    db: Session, *, command: RecordAccountTermsV1
) -> TermsVersionRefV1:
    """Append one dated version of an account's terms. Never commits."""
    if not isinstance(command, RecordAccountTermsV1):
        raise BillingRuleViolation(
            "invalid_command", "record_account_terms needs RecordAccountTermsV1"
        )
    scope = command.scope
    _require_account(db, scope, command.billing_account_id)
    _, version, _ = _plane(scope)
    values: dict[str, object] = {
        "id": uuid4(),
        "billing_account_id": command.billing_account_id,
        "effective_from": command.effective_from,
        "contract_type": command.contract_type,
        "period": command.period,
        "postpaid_billing_timing": command.postpaid_billing_timing,
        "postpaid_grace": command.postpaid_grace,
        "actor_ref": command.actor_ref,
        "reason": command.reason,
    }
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    return _append(db, scope, version(**values))


def record_default_terms(
    db: Session, *, command: RecordDefaultTermsV1
) -> TermsVersionRefV1:
    """Append one dated version of the tenant default terms. Never commits."""
    if not isinstance(command, RecordDefaultTermsV1):
        raise BillingRuleViolation(
            "invalid_command", "record_default_terms needs RecordDefaultTermsV1"
        )
    scope = command.scope
    _, _, default = _plane(scope)
    values: dict[str, object] = {
        "id": uuid4(),
        "effective_from": command.effective_from,
        "contract_type": command.contract_type,
        "period": command.period,
        "postpaid_billing_timing": command.postpaid_billing_timing,
        "postpaid_grace": command.postpaid_grace,
        "actor_ref": command.actor_ref,
        "reason": command.reason,
    }
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    return _append(db, scope, default(**values))


def get_effective_account_terms(
    db: Session, *, scope: Scope, account_id: UUID, at: datetime
) -> AccountTermsV1:
    """Resolve the account's terms at ``at`` (read-only; the caller owns the txn).

    Refuses when the account does not exist in the scope or when no default
    version is effective at ``at``.
    """
    _scope(scope)
    if not isinstance(account_id, UUID):
        raise BillingRuleViolation(
            "invalid_account_id", "billing account identity must be a UUID"
        )
    _aware(at, "at")
    _require_account(db, scope, account_id)
    _, version, default = _plane(scope)
    customer = db.execute(
        _scoped(
            select(version)
            .where(version.billing_account_id == account_id)
            .where(version.effective_from <= at)
            .order_by(version.effective_from.desc())
            .limit(1),
            scope,
            version,
        )
    ).scalar_one_or_none()
    base = db.execute(
        _scoped(
            select(default)
            .where(default.effective_from <= at)
            .order_by(default.effective_from.desc())
            .limit(1),
            scope,
            default,
        )
    ).scalar_one_or_none()
    if base is None:
        raise BillingRuleViolation(
            "terms_default_missing",
            "no tenant default terms are effective at the requested instant",
        )

    def resolve(name: str) -> EffectiveTermV1[Any]:
        if customer is not None and getattr(customer, name) is not None:
            return EffectiveTermV1(getattr(customer, name), "customer", customer.id)
        return EffectiveTermV1(getattr(base, name), "default", base.id)

    return AccountTermsV1(
        billing_account_id=account_id,
        at=at,
        contract_type=cast(EffectiveTermV1[ContractType], resolve("contract_type")),
        period=cast(EffectiveTermV1[TermsPeriod], resolve("period")),
        postpaid_billing_timing=cast(
            EffectiveTermV1[PostpaidBillingTiming], resolve("postpaid_billing_timing")
        ),
        postpaid_grace=cast(EffectiveTermV1[timedelta], resolve("postpaid_grace")),
    )


__all__ = [
    "AccountTermsV1",
    "ContractType",
    "EffectiveTermV1",
    "PostpaidBillingTiming",
    "RecordAccountTermsV1",
    "RecordDefaultTermsV1",
    "TermSource",
    "TermsPeriod",
    "TermsVersionRefV1",
    "get_effective_account_terms",
    "record_account_terms",
    "record_default_terms",
]
