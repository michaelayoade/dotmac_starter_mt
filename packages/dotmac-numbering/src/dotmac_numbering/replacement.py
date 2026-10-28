"""Immutable series identities and exact rendered claims inside numbering.

Domain and partition identities are trusted product policy. This contract uses
PostgreSQL C collation without normalization; adopters must verify that this
matches their business uniqueness constraints before enabling replacement.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_numbering.models import (
    NumberClaim,
    NumberDomain,
    PlatformNumberClaim,
    PlatformNumberDomain,
)
from dotmac_numbering.service import (
    Allocation,
    NumberingError,
    SeriesConfiguration,
    SeriesRow,
    _error,
    _find_series,
    _models,
    _require_series,
    _tenant_filter,
    period_for,
)

RESERVE_SCOPE = "numbering.reserve"


class NumberCollision(NumberingError):
    """A rendered number or document identity already has a different claim."""

    def __init__(self) -> None:
        super().__init__(
            "number_collision",
            "This number or document already has a different immutable claim.",
        )


@dataclass(frozen=True, slots=True)
class CollisionDomain:
    code: str
    partitioned: bool = False

    def validate(self) -> None:
        _bounded(self.code, 80, "domain_code")
        if not isinstance(self.partitioned, bool):
            raise _error("invalid_domain_policy", "partitioned must be a boolean.")

    def partition_key(self, value: str | None) -> str:
        self.validate()
        if self.partitioned:
            _bounded(value, 128, "partition_key")
            return cast(str, value)
        if value is not None and value != "":
            raise _error("unexpected_partition", "This domain has no partition.")
        return ""


@dataclass(frozen=True, slots=True)
class SeriesDefinition:
    configuration: SeriesConfiguration
    domain: CollisionDomain | None
    strict: bool


@dataclass(frozen=True, slots=True)
class PeriodState:
    series_code: str
    period_key: str
    next_value: int
    counter_exists: bool
    last_allocation: Allocation | None


@dataclass(frozen=True, slots=True)
class NumberReservation:
    claim_id: UUID
    domain_code: str
    partition_key: str
    formatted_number: str
    document_reference: str
    series_code: str
    origin: str
    provenance: str
    replayed: bool


def _bounded(value: object, maximum: int, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise _error(
            f"invalid_{name}",
            f"{name} must be a nonempty string of at most {maximum} characters.",
        )


def _claim_models(
    scope: Scope,
) -> tuple[
    type[NumberDomain] | type[PlatformNumberDomain],
    type[NumberClaim] | type[PlatformNumberClaim],
]:
    _models(scope)
    if isinstance(scope, TenantScope):
        return NumberDomain, NumberClaim
    return PlatformNumberDomain, PlatformNumberClaim


def _configuration(series: SeriesRow) -> SeriesConfiguration:
    return SeriesConfiguration(
        series_code=series.series_code,
        prefix=series.prefix,
        suffix=series.suffix,
        separator=series.separator,
        min_digits=series.min_digits,
        include_year=bool(series.include_year),
        year_digits=series.year_digits,
        include_month=bool(series.include_month),
        reset_policy=series.reset_policy,
        start_value=series.start_value,
    )


def _domain(db: Session, scope: Scope, series: SeriesRow) -> CollisionDomain | None:
    if series.domain_code is None:
        return None
    model, _ = _claim_models(scope)
    stmt = select(model).where(model.domain_code == series.domain_code)
    row = cast(
        NumberDomain | PlatformNumberDomain,
        db.execute(_tenant_filter(stmt, scope, model)).scalar_one(),
    )
    return CollisionDomain(code=row.domain_code, partitioned=row.partitioned)


def _ensure_domain(db: Session, scope: Scope, domain: CollisionDomain) -> None:
    domain.validate()
    model, _ = _claim_models(scope)
    values: dict[str, object] = {
        "id": uuid4(),
        "domain_code": domain.code,
        "partitioned": domain.partitioned,
    }
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    # A concurrent first creator establishes policy; the loser reads and
    # compares that immutable winner rather than overwriting it.
    db.execute(pg_insert(model).values(**values).on_conflict_do_nothing())
    stmt = select(model).where(model.domain_code == domain.code)
    row = cast(
        NumberDomain | PlatformNumberDomain,
        db.execute(_tenant_filter(stmt, scope, model)).scalar_one(),
    )
    if row.partitioned != domain.partitioned:
        raise _error(
            "domain_policy_conflict",
            "This domain already has a different partition policy.",
        )


def read_series(db: Session, *, scope: Scope, series_code: str) -> SeriesDefinition:
    """Read persisted configuration without autoflush or identity-map contamination."""
    model, *_ = _models(scope)
    names = [field.name for field in fields(SeriesConfiguration)]
    stmt = select(
        *(getattr(model, name) for name in names),
        model.strict_identity,
        model.domain_code,
    ).where(model.series_code == series_code)
    with db.no_autoflush:
        row = db.execute(_tenant_filter(stmt, scope, model)).mappings().one_or_none()
        if row is None:
            raise _error(
                "series_not_configured",
                "No configured series for this code.",
                series_code=series_code,
            )
        configuration = SeriesConfiguration(
            series_code=row["series_code"],
            prefix=row["prefix"],
            suffix=row["suffix"],
            separator=row["separator"],
            min_digits=row["min_digits"],
            include_year=bool(row["include_year"]),
            year_digits=row["year_digits"],
            include_month=bool(row["include_month"]),
            reset_policy=row["reset_policy"],
            start_value=row["start_value"],
        )
        domain = None
        if row["domain_code"] is not None:
            domain_model, _ = _claim_models(scope)
            domain_stmt = select(
                domain_model.domain_code, domain_model.partitioned
            ).where(domain_model.domain_code == row["domain_code"])
            domain_row = db.execute(
                _tenant_filter(domain_stmt, scope, domain_model)
            ).one()
            domain = CollisionDomain(domain_row.domain_code, domain_row.partitioned)
        return SeriesDefinition(configuration, domain, row["strict_identity"])


def create_series(
    db: Session,
    *,
    scope: Scope,
    configuration: SeriesConfiguration,
    domain: CollisionDomain,
) -> SeriesDefinition:
    """Create a strictly immutable series, atomically inside the caller transaction."""
    from dotmac_kernel.transactions import conflict_savepoint

    with conflict_savepoint(db):
        return _create_series(
            db, scope=scope, configuration=configuration, domain=domain
        )


def enroll_series(
    db: Session,
    *,
    scope: Scope,
    expected_configuration: SeriesConfiguration,
    domain: CollisionDomain,
) -> SeriesDefinition:
    """Freeze the exact observed legacy identity, preserving its counters and format."""
    from dotmac_kernel.transactions import conflict_savepoint

    with conflict_savepoint(db):
        return _enroll_series(
            db,
            scope=scope,
            expected_configuration=expected_configuration,
            domain=domain,
        )


def _create_series(
    db: Session,
    *,
    scope: Scope,
    configuration: SeriesConfiguration,
    domain: CollisionDomain,
) -> SeriesDefinition:
    """Create an identity frozen from insertion; exact creation retries are safe."""
    configuration.validate()
    domain.validate()
    existing = _find_series(db, scope, configuration.series_code, lock=True)
    if existing is not None:
        stored = read_series(db, scope=scope, series_code=configuration.series_code)
        if (
            not stored.strict
            or stored.configuration != configuration
            or stored.domain != domain
        ):
            raise _error(
                "series_identity_conflict",
                "This series code already identifies a different definition.",
            )
        return read_series(db, scope=scope, series_code=configuration.series_code)
    _ensure_domain(db, scope, domain)
    model, *_ = _models(scope)
    values: dict[str, object] = {
        field.name: getattr(configuration, field.name)
        for field in fields(configuration)
    }
    values.update(id=uuid4(), domain_code=domain.code, strict_identity=True)
    values["include_year"] = int(configuration.include_year)
    values["include_month"] = int(configuration.include_month)
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    db.execute(pg_insert(model).values(**values).on_conflict_do_nothing())
    _require_series(db, scope, configuration.series_code, lock=True)
    stored = read_series(db, scope=scope, series_code=configuration.series_code)
    if (
        not stored.strict
        or stored.configuration != configuration
        or stored.domain != domain
    ):
        raise _error(
            "series_identity_conflict",
            "This series code already identifies a different definition.",
        )
    return read_series(db, scope=scope, series_code=configuration.series_code)


def _enroll_series(
    db: Session,
    *,
    scope: Scope,
    expected_configuration: SeriesConfiguration,
    domain: CollisionDomain,
) -> SeriesDefinition:
    """Freeze an observed legacy identity without changing format or counters.

    This does not attest historical census completeness. The product seals its
    enrollment census and writer inventory before enabling replacement.
    """
    expected_configuration.validate()
    domain.validate()
    series = _require_series(db, scope, expected_configuration.series_code, lock=True)
    stored = read_series(
        db, scope=scope, series_code=expected_configuration.series_code
    )
    if stored.configuration != expected_configuration:
        raise _error(
            "stale_series_configuration", "The observed series definition has changed."
        )
    if stored.strict:
        if stored.domain != domain:
            raise _error(
                "series_identity_conflict",
                "An enrolled series cannot change its domain.",
            )
    else:
        _ensure_domain(db, scope, domain)
        series.domain_code = domain.code
        series.strict_identity = True
        db.flush()
    return read_series(db, scope=scope, series_code=series.series_code)


def read_period_state(
    db: Session, *, scope: Scope, series_code: str, reference_date: date
) -> PeriodState:
    """Read persisted period state; pending ORM edits are neither flushed nor shown."""
    from datetime import datetime

    if not isinstance(reference_date, date) or isinstance(reference_date, datetime):
        raise _error(
            "invalid_reference_date", "reference_date must be an explicit date."
        )
    with db.no_autoflush:
        definition = read_series(db, scope=scope, series_code=series_code)
        period = period_for(reference_date, definition.configuration.reset_policy)
        _, counter_model, receipt_model, _ = _models(scope)
        counter_stmt = select(counter_model.next_value).where(
            counter_model.series_code == series_code, counter_model.period_key == period
        )
        counter_value = db.execute(
            _tenant_filter(counter_stmt, scope, counter_model)
        ).scalar_one_or_none()
        stmt = (
            select(
                receipt_model.formatted_number,
                receipt_model.allocated_value,
                receipt_model.reference_date,
            )
            .where(
                receipt_model.series_code == series_code,
                receipt_model.period_key == period,
            )
            .order_by(receipt_model.allocated_value.desc())
            .limit(1)
        )
        receipt = db.execute(_tenant_filter(stmt, scope, receipt_model)).one_or_none()
        last = (
            None
            if receipt is None
            else Allocation(
                formatted_number=receipt.formatted_number,
                value=receipt.allocated_value,
                series_code=series_code,
                period_key=period,
                reference_date=receipt.reference_date,
                replayed=False,
            )
        )
        return PeriodState(
            series_code,
            period,
            definition.configuration.start_value
            if counter_value is None
            else counter_value,
            counter_value is not None,
            last,
        )


def _claim_values(
    *,
    scope: Scope,
    series: SeriesRow,
    domain: CollisionDomain,
    partition_key: str | None,
    formatted_number: str,
    document_reference: str | None,
    origin: str,
    provenance: str,
) -> dict[str, object]:
    partition = domain.partition_key(partition_key)
    _bounded(formatted_number, 255, "formatted_number")
    _bounded(document_reference, 255, "document_reference")
    _bounded(provenance, 4096, "provenance")
    values: dict[str, object] = {
        "id": uuid4(),
        "domain_code": domain.code,
        "partition_key": partition,
        "formatted_number": formatted_number,
        "document_reference": document_reference,
        "series_code": series.series_code,
        "origin": origin,
        "provenance": provenance,
    }
    if isinstance(scope, TenantScope):
        values["tenant_id"] = scope.tenant_id
    return values


def _allocation_claim(
    db: Session,
    *,
    scope: Scope,
    series: SeriesRow,
    formatted_number: str,
    partition_key: str | None,
    document_reference: str | None,
    idempotency_key: str,
) -> None:
    domain = _domain(db, scope, series)
    if domain is None:
        if partition_key is not None or document_reference is not None:
            raise _error(
                "series_not_enrolled", "Claim arguments require an enrolled series."
            )
        return
    _, model = _claim_models(scope)
    values = _claim_values(
        scope=scope,
        series=series,
        domain=domain,
        partition_key=partition_key,
        formatted_number=formatted_number,
        document_reference=document_reference,
        origin="allocation",
        provenance=idempotency_key,
    )
    # Plain insert is essential: let kernel execute_once resolve a concurrent
    # identical-key winner before converting a real domain collision outside
    # its rolled-back operation savepoint.
    db.execute(pg_insert(model).values(**values))


def reserve_number(
    db: Session,
    *,
    scope: Scope,
    series_code: str,
    formatted_number: str,
    document_reference: str,
    provenance: str,
    origin: str,
    idempotency_key: str,
    partition_key: str | None = None,
) -> NumberReservation:
    """Enroll a historical/external exact string; never allocate or seed."""
    from dotmac_kernel.idempotency import (
        execute_once,
        execute_once_platform,
        fingerprint_of,
    )

    if origin not in ("historical", "external"):
        raise _error(
            "invalid_claim_origin", "Enrollment origin must be historical or external."
        )
    _bounded(idempotency_key, 255, "idempotency_key")
    series = _require_series(db, scope, series_code)
    domain = read_series(db, scope=scope, series_code=series_code).domain
    if domain is None:
        raise _error(
            "series_not_enrolled", "Enroll the series before reserving numbers."
        )
    values = _claim_values(
        scope=scope,
        series=series,
        domain=domain,
        partition_key=partition_key,
        formatted_number=formatted_number,
        document_reference=document_reference,
        origin=origin,
        provenance=provenance,
    )
    fingerprint = fingerprint_of(
        {key: value for key, value in values.items() if key not in ("id", "tenant_id")}
    )

    def operation(session: Session) -> dict[str, object]:
        _require_series(session, scope, series_code, lock=True)
        _, model = _claim_models(scope)
        stmt = select(model).where(
            model.domain_code == domain.code,
            model.partition_key == values["partition_key"],
            model.formatted_number == formatted_number,
        )
        row = cast(
            NumberClaim | PlatformNumberClaim | None,
            session.execute(_tenant_filter(stmt, scope, model)).scalar_one_or_none(),
        )
        if row is not None and all(
            getattr(row, key) == value
            for key, value in values.items()
            if key not in ("id", "tenant_id")
        ):
            return {"claim_id": str(row.id)}
        session.execute(pg_insert(model).values(**values))
        return {"claim_id": str(values["id"])}

    try:
        if isinstance(scope, TenantScope):
            outcome = execute_once(
                db,
                tenant_id=scope.tenant_id,
                scope=RESERVE_SCOPE,
                key=idempotency_key,
                fingerprint=fingerprint,
                operation=operation,
            )
        elif isinstance(scope, PlatformScope):
            outcome = execute_once_platform(
                db,
                scope=RESERVE_SCOPE,
                key=idempotency_key,
                fingerprint=fingerprint,
                operation=operation,
            )
        else:
            _models(scope)
            raise AssertionError("unreachable")
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in (
            "uq_number_claims_rendered",
            "uq_number_claims_document",
            "uq_platform_number_claims_rendered",
            "uq_platform_number_claims_document",
        ):
            raise NumberCollision() from exc
        raise
    return NumberReservation(
        UUID(str(outcome.result["claim_id"])),
        domain.code,
        str(values["partition_key"]),
        formatted_number,
        document_reference,
        series_code,
        origin,
        provenance,
        outcome.replayed,
    )


__all__ = [
    "RESERVE_SCOPE",
    "CollisionDomain",
    "NumberCollision",
    "NumberReservation",
    "PeriodState",
    "SeriesDefinition",
    "create_series",
    "enroll_series",
    "read_period_state",
    "read_series",
    "reserve_number",
]
