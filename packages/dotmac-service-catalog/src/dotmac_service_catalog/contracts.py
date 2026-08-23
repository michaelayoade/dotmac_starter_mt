"""Commands and vocabulary for the technical service catalogue."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


class CatalogError(Exception):
    """Base refusal."""


class NotFound(CatalogError):
    """A scoped specification was not found."""


class Conflict(CatalogError):
    """A catalogue identity conflicts."""


class CharacteristicKind(enum.StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"


@dataclass(frozen=True, slots=True)
class CreateServiceSpecification:
    code: str
    plan_family_id: UUID


@dataclass(frozen=True, slots=True)
class CreatePlanFamily:
    code: str


@dataclass(frozen=True, slots=True)
class CreateCharacteristic:
    specification_id: UUID
    code: str
    name: str
    kind: CharacteristicKind
    required: bool = False
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class CreateEligibilityInput:
    specification_id: UUID
    code: str
    name: str
    required: bool = False


CharacteristicValue = str | int | Decimal | bool


@dataclass(frozen=True, slots=True)
class CharacteristicValueInput:
    definition_id: UUID
    value: CharacteristicValue


@dataclass(frozen=True, slots=True)
class PublishPlanFamilyVersion:
    plan_family_id: UUID
    version: int
    name: str
    effective_from: datetime
    source_code: str
    source_id: UUID
    source_version: int
    command_id: UUID
    description: str | None = None
    effective_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublishServiceSpecificationVersion:
    specification_id: UUID
    plan_family_version_id: UUID
    version: int
    name: str
    effective_from: datetime
    source_code: str
    source_id: UUID
    source_version: int
    command_id: UUID
    description: str | None = None
    effective_until: datetime | None = None
    characteristics: tuple[CharacteristicValueInput, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectiveServiceSpecification:
    specification_id: UUID
    version_id: UUID
    version: int
    plan_family_id: UUID
    plan_family_version_id: UUID
    code: str
    name: str
    description: str | None
    effective_from: datetime
    effective_until: datetime | None
    source_code: str
    source_id: UUID
    source_version: int
    characteristics: dict[str, CharacteristicValue]


__all__ = [
    "CatalogError",
    "CharacteristicValue",
    "CharacteristicValueInput",
    "CharacteristicKind",
    "Conflict",
    "CreateCharacteristic",
    "CreateEligibilityInput",
    "CreatePlanFamily",
    "CreateServiceSpecification",
    "EffectiveServiceSpecification",
    "NotFound",
    "PublishPlanFamilyVersion",
    "PublishServiceSpecificationVersion",
]
