"""Technical catalogue decisions; transaction ownership stays with the caller."""

from __future__ import annotations

from typing import TypeVar, cast
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_service_catalog.contracts import (
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    NotFound,
)
from dotmac_service_catalog.models import (
    CharacteristicDefinition,
    EligibilityInputDefinition,
    PlanFamily,
    ServiceSpecification,
)

_Model = TypeVar("_Model")


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-service-catalog requires TenantScope")
    return scope.tenant_id


def _text(value: str, field: str, *, upper: bool = False) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value.upper() if upper else value


def _specification(db: Session, tenant_id: UUID, row_id: UUID) -> ServiceSpecification:
    row = db.scalar(
        select(ServiceSpecification).where(
            ServiceSpecification.tenant_id == tenant_id,
            ServiceSpecification.id == row_id,
        )
    )
    if row is None:
        raise NotFound(f"service specification {row_id} was not found")
    return row


def _flush_new(db: Session, row: _Model, detail: str) -> _Model:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc
    return row


def create_specification(
    db: Session, *, scope: TenantScope, command: CreateServiceSpecification
) -> ServiceSpecification:
    tenant_id = _tenant(scope)
    code = _text(command.code, "specification code", upper=True)
    if db.scalar(
        select(ServiceSpecification.id).where(
            ServiceSpecification.tenant_id == tenant_id,
            ServiceSpecification.code == code,
        )
    ):
        raise Conflict(f"specification code {code!r} already exists")
    return _flush_new(
        db,
        ServiceSpecification(
            tenant_id=tenant_id,
            code=code,
            name=_text(command.name, "specification name"),
            description=command.description,
        ),
        f"specification code {code!r} conflicts",
    )


def create_plan_family(
    db: Session, *, scope: TenantScope, command: CreatePlanFamily
) -> PlanFamily:
    tenant_id = _tenant(scope)
    _specification(db, tenant_id, command.specification_id)
    code = _text(command.code, "plan-family code", upper=True)
    return _flush_new(
        db,
        PlanFamily(
            tenant_id=tenant_id,
            specification_id=command.specification_id,
            code=code,
            name=_text(command.name, "plan-family name"),
            description=command.description,
        ),
        f"plan-family code {code!r} conflicts",
    )


def add_characteristic(
    db: Session, *, scope: TenantScope, command: CreateCharacteristic
) -> CharacteristicDefinition:
    tenant_id = _tenant(scope)
    _specification(db, tenant_id, command.specification_id)
    code = _text(command.code, "characteristic code", upper=True)
    return _flush_new(
        db,
        CharacteristicDefinition(
            tenant_id=tenant_id,
            specification_id=command.specification_id,
            code=code,
            name=_text(command.name, "characteristic name"),
            kind=command.kind,
            required=command.required,
            unit=command.unit,
        ),
        f"characteristic code {code!r} conflicts",
    )


def add_eligibility_input(
    db: Session, *, scope: TenantScope, command: CreateEligibilityInput
) -> EligibilityInputDefinition:
    tenant_id = _tenant(scope)
    _specification(db, tenant_id, command.specification_id)
    code = _text(command.code, "eligibility-input code", upper=True)
    return _flush_new(
        db,
        EligibilityInputDefinition(
            tenant_id=tenant_id,
            specification_id=command.specification_id,
            code=code,
            name=_text(command.name, "eligibility-input name"),
            required=command.required,
        ),
        f"eligibility-input code {code!r} conflicts",
    )


def set_specification_active(
    db: Session, *, scope: TenantScope, specification_id: UUID, active: bool
) -> ServiceSpecification:
    row = _specification(db, _tenant(scope), specification_id)
    row.is_active = active
    db.flush()
    return cast(ServiceSpecification, row)


__all__ = [
    "add_characteristic",
    "add_eligibility_input",
    "create_plan_family",
    "create_specification",
    "set_specification_active",
]
