"""Versioned technical-catalogue decisions; callers own transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar, cast
from uuid import UUID, uuid4

from dotmac_kernel.cache import TenantScope
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_service_catalog.contracts import (
    CharacteristicKind,
    CharacteristicValue,
    Conflict,
    CreateCharacteristic,
    CreateEligibilityInput,
    CreatePlanFamily,
    CreateServiceSpecification,
    EffectiveServiceSpecification,
    NotFound,
    PublishPlanFamilyVersion,
    PublishServiceSpecificationVersion,
)
from dotmac_service_catalog.models import (
    CharacteristicDefinition,
    EligibilityInputDefinition,
    PlanFamily,
    PlanFamilyVersion,
    ServiceSpecification,
    ServiceSpecificationCharacteristic,
    ServiceSpecificationVersion,
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


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    """Restore SQLite's dropped UTC marker without weakening command ingress."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _family(db: Session, tenant_id: UUID, row_id: UUID) -> PlanFamily:
    row = db.scalar(
        select(PlanFamily).where(
            PlanFamily.tenant_id == tenant_id,
            PlanFamily.id == row_id,
        )
    )
    if row is None:
        raise NotFound(f"plan family {row_id} was not found")
    return row


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


def create_plan_family(
    db: Session, *, scope: TenantScope, command: CreatePlanFamily
) -> PlanFamily:
    tenant_id = _tenant(scope)
    code = _text(command.code, "plan-family code", upper=True)
    if db.scalar(
        select(PlanFamily.id).where(
            PlanFamily.tenant_id == tenant_id,
            PlanFamily.code == code,
        )
    ):
        raise Conflict(f"plan-family code {code!r} already exists")
    return _flush_new(
        db,
        PlanFamily(tenant_id=tenant_id, code=code),
        f"plan-family code {code!r} conflicts",
    )


def create_specification(
    db: Session, *, scope: TenantScope, command: CreateServiceSpecification
) -> ServiceSpecification:
    tenant_id = _tenant(scope)
    _family(db, tenant_id, command.plan_family_id)
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
            plan_family_id=command.plan_family_id,
            code=code,
        ),
        f"specification code {code!r} conflicts",
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


def _validate_version_input(
    *,
    version: int,
    effective_from: datetime,
    effective_until: datetime | None,
    source_code: str,
    source_version: int,
    name: str,
) -> tuple[datetime, datetime | None, str, str]:
    if version < 1 or source_version < 1:
        raise ValueError("version and source_version must be positive")
    starts_at = _aware(effective_from, "effective_from")
    ends_at = (
        _aware(effective_until, "effective_until")
        if effective_until is not None
        else None
    )
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("effective interval must be non-empty")
    return (
        starts_at,
        ends_at,
        _text(source_code, "source code"),
        _text(name, "version name"),
    )


def publish_plan_family_version(
    db: Session, *, scope: TenantScope, command: PublishPlanFamilyVersion
) -> PlanFamilyVersion:
    tenant_id = _tenant(scope)
    _family(db, tenant_id, command.plan_family_id)
    starts_at, ends_at, source_code, name = _validate_version_input(
        version=command.version,
        effective_from=command.effective_from,
        effective_until=command.effective_until,
        source_code=command.source_code,
        source_version=command.source_version,
        name=command.name,
    )
    digest = _digest(
        {
            "plan_family_id": command.plan_family_id,
            "version": command.version,
            "name": name,
            "description": command.description,
            "effective_from": starts_at,
            "effective_until": ends_at,
            "source_code": source_code,
            "source_id": command.source_id,
            "source_version": command.source_version,
        }
    )
    replay = db.scalar(
        select(PlanFamilyVersion).where(
            PlanFamilyVersion.tenant_id == tenant_id,
            PlanFamilyVersion.command_id == command.command_id,
        )
    )
    if replay is not None:
        if replay.content_digest != digest:
            raise Conflict("plan-family command was reused with different content")
        return replay

    current = db.scalar(
        select(PlanFamilyVersion)
        .where(
            PlanFamilyVersion.tenant_id == tenant_id,
            PlanFamilyVersion.plan_family_id == command.plan_family_id,
            PlanFamilyVersion.state == "published",
        )
        .order_by(PlanFamilyVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    expected_version = 1 if current is None else current.version + 1
    if command.version != expected_version:
        raise Conflict(f"next plan-family version must be {expected_version}")
    if current is not None:
        if starts_at <= _stored_utc(current.effective_from):
            raise Conflict("plan-family versions must advance in effective time")
        if current.effective_until is not None and starts_at < _stored_utc(
            current.effective_until
        ):
            raise Conflict("plan-family version overlaps a closed interval")

    row = PlanFamilyVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        plan_family_id=command.plan_family_id,
        version=command.version,
        name=name,
        description=command.description,
        state="published",
        effective_from=starts_at,
        effective_until=ends_at,
        source_code=source_code,
        source_id=command.source_id,
        source_version=command.source_version,
        command_id=command.command_id,
        content_digest=digest,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            if current is not None:
                current.state = "superseded"
                if current.effective_until is None:
                    current.effective_until = starts_at
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict("plan-family version conflicts with stored evidence") from exc
    return row


def _value_columns(
    definition: CharacteristicDefinition, value: CharacteristicValue
) -> dict[str, object | None]:
    columns: dict[str, object | None] = {
        "string_value": None,
        "integer_value": None,
        "decimal_value": None,
        "boolean_value": None,
    }
    if definition.kind is CharacteristicKind.STRING:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{definition.code} requires a non-empty string")
        columns["string_value"] = value.strip()
    elif definition.kind is CharacteristicKind.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{definition.code} requires an integer")
        columns["integer_value"] = value
    elif definition.kind is CharacteristicKind.DECIMAL:
        if isinstance(value, float) or not isinstance(value, Decimal):
            raise ValueError(f"{definition.code} requires an exact Decimal")
        columns["decimal_value"] = value
    elif definition.kind is CharacteristicKind.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError(f"{definition.code} requires a boolean")
        columns["boolean_value"] = value
    else:  # pragma: no cover - enum construction is closed
        raise ValueError(f"unsupported characteristic kind {definition.kind!r}")
    return columns


def publish_service_specification_version(
    db: Session,
    *,
    scope: TenantScope,
    command: PublishServiceSpecificationVersion,
) -> ServiceSpecificationVersion:
    tenant_id = _tenant(scope)
    specification = _specification(db, tenant_id, command.specification_id)
    starts_at, ends_at, source_code, name = _validate_version_input(
        version=command.version,
        effective_from=command.effective_from,
        effective_until=command.effective_until,
        source_code=command.source_code,
        source_version=command.source_version,
        name=command.name,
    )
    family_version = db.scalar(
        select(PlanFamilyVersion).where(
            PlanFamilyVersion.tenant_id == tenant_id,
            PlanFamilyVersion.id == command.plan_family_version_id,
            PlanFamilyVersion.plan_family_id == specification.plan_family_id,
            PlanFamilyVersion.state.in_(("published", "superseded")),
            PlanFamilyVersion.effective_from <= starts_at,
            or_(
                PlanFamilyVersion.effective_until.is_(None),
                PlanFamilyVersion.effective_until > starts_at,
            ),
        )
    )
    if family_version is None:
        raise NotFound("an effective version of the specification's family is required")

    definitions = tuple(
        db.scalars(
            select(CharacteristicDefinition)
            .where(
                CharacteristicDefinition.tenant_id == tenant_id,
                CharacteristicDefinition.specification_id == specification.id,
            )
            .order_by(CharacteristicDefinition.code)
        )
    )
    by_id = {definition.id: definition for definition in definitions}
    supplied = {item.definition_id: item for item in command.characteristics}
    if len(supplied) != len(command.characteristics):
        raise ValueError("a characteristic definition may appear only once")
    unknown = set(supplied) - set(by_id)
    if unknown:
        raise NotFound(
            "a characteristic definition does not belong to this specification"
        )
    missing = {definition.code for definition in definitions if definition.required} - {
        by_id[row_id].code for row_id in supplied
    }
    if missing:
        raise ValueError(f"required characteristics are missing: {sorted(missing)}")
    normalized_values = {
        row_id: _value_columns(by_id[row_id], item.value)
        for row_id, item in supplied.items()
    }
    digest = _digest(
        {
            "specification_id": specification.id,
            "plan_family_version_id": family_version.id,
            "version": command.version,
            "name": name,
            "description": command.description,
            "effective_from": starts_at,
            "effective_until": ends_at,
            "source_code": source_code,
            "source_id": command.source_id,
            "source_version": command.source_version,
            "characteristics": [
                {
                    "code": by_id[row_id].code,
                    "kind": by_id[row_id].kind.value,
                    "value": item.value,
                }
                for row_id, item in sorted(
                    supplied.items(), key=lambda entry: by_id[entry[0]].code
                )
            ],
        }
    )
    replay = db.scalar(
        select(ServiceSpecificationVersion).where(
            ServiceSpecificationVersion.tenant_id == tenant_id,
            ServiceSpecificationVersion.command_id == command.command_id,
        )
    )
    if replay is not None:
        if replay.content_digest != digest:
            raise Conflict("specification command was reused with different content")
        return replay

    current = db.scalar(
        select(ServiceSpecificationVersion)
        .where(
            ServiceSpecificationVersion.tenant_id == tenant_id,
            ServiceSpecificationVersion.specification_id == specification.id,
            ServiceSpecificationVersion.state == "published",
        )
        .order_by(ServiceSpecificationVersion.version.desc())
        .limit(1)
        .with_for_update()
    )
    expected_version = 1 if current is None else current.version + 1
    if command.version != expected_version:
        raise Conflict(f"next specification version must be {expected_version}")
    if current is not None:
        if starts_at <= _stored_utc(current.effective_from):
            raise Conflict("specification versions must advance in effective time")
        if current.effective_until is not None and starts_at < _stored_utc(
            current.effective_until
        ):
            raise Conflict("specification version overlaps a closed interval")

    row = ServiceSpecificationVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        specification_id=specification.id,
        plan_family_id=specification.plan_family_id,
        plan_family_version_id=family_version.id,
        version=command.version,
        name=name,
        description=command.description,
        state="published",
        effective_from=starts_at,
        effective_until=ends_at,
        source_code=source_code,
        source_id=command.source_id,
        source_version=command.source_version,
        command_id=command.command_id,
        content_digest=digest,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            if current is not None:
                current.state = "superseded"
                if current.effective_until is None:
                    current.effective_until = starts_at
            db.add(row)
            db.flush()
            for definition_id, columns in normalized_values.items():
                db.add(
                    ServiceSpecificationCharacteristic(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        specification_version_id=row.id,
                        specification_id=specification.id,
                        definition_id=definition_id,
                        **columns,
                    )
                )
            db.flush()
    except IntegrityError as exc:
        raise Conflict("specification version conflicts with stored evidence") from exc
    return row


def effective_service_specification(
    db: Session,
    *,
    scope: TenantScope,
    specification_id: UUID,
    effective_at: datetime,
) -> EffectiveServiceSpecification | None:
    tenant_id = _tenant(scope)
    moment = _aware(effective_at, "effective_at")
    row = db.scalar(
        select(ServiceSpecificationVersion).where(
            ServiceSpecificationVersion.tenant_id == tenant_id,
            ServiceSpecificationVersion.specification_id == specification_id,
            ServiceSpecificationVersion.state.in_(("published", "superseded")),
            ServiceSpecificationVersion.effective_from <= moment,
            or_(
                ServiceSpecificationVersion.effective_until.is_(None),
                ServiceSpecificationVersion.effective_until > moment,
            ),
        )
    )
    if row is None:
        return None
    specification = _specification(db, tenant_id, specification_id)
    values = db.execute(
        select(CharacteristicDefinition, ServiceSpecificationCharacteristic)
        .join(
            ServiceSpecificationCharacteristic,
            ServiceSpecificationCharacteristic.definition_id
            == CharacteristicDefinition.id,
        )
        .where(
            CharacteristicDefinition.tenant_id == tenant_id,
            ServiceSpecificationCharacteristic.tenant_id == tenant_id,
            ServiceSpecificationCharacteristic.specification_version_id == row.id,
        )
        .order_by(CharacteristicDefinition.code)
    ).all()
    characteristics: dict[str, CharacteristicValue] = {}
    for definition, value in values:
        if definition.kind is CharacteristicKind.STRING:
            resolved: CharacteristicValue = cast(str, value.string_value)
        elif definition.kind is CharacteristicKind.INTEGER:
            resolved = cast(int, value.integer_value)
        elif definition.kind is CharacteristicKind.DECIMAL:
            resolved = cast(Decimal, value.decimal_value)
        else:
            resolved = cast(bool, value.boolean_value)
        characteristics[definition.code] = resolved
    return EffectiveServiceSpecification(
        specification_id=specification.id,
        version_id=row.id,
        version=row.version,
        plan_family_id=specification.plan_family_id,
        plan_family_version_id=row.plan_family_version_id,
        code=specification.code,
        name=row.name,
        description=row.description,
        effective_from=row.effective_from,
        effective_until=row.effective_until,
        source_code=row.source_code,
        source_id=row.source_id,
        source_version=row.source_version,
        characteristics=characteristics,
    )


__all__ = [
    "add_characteristic",
    "add_eligibility_input",
    "create_plan_family",
    "create_specification",
    "effective_service_specification",
    "publish_plan_family_version",
    "publish_service_specification_version",
]
