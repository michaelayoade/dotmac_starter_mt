"""Commands and vocabulary for the technical service catalogue."""

from __future__ import annotations

import enum
from dataclasses import dataclass
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
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreatePlanFamily:
    code: str
    name: str
    specification_id: UUID
    description: str | None = None


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


__all__ = [
    "CatalogError",
    "CharacteristicKind",
    "Conflict",
    "CreateCharacteristic",
    "CreateEligibilityInput",
    "CreatePlanFamily",
    "CreateServiceSpecification",
    "NotFound",
]
