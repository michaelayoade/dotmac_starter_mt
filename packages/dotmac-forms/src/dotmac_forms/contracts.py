"""Typed public contracts for the reusable Forms owner."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

Scalar = str | int | float | bool | None
AnswerValue = Scalar | tuple[str, ...]


class ContractError(ValueError):
    """A Forms command is malformed before persistence is consulted."""


class FieldType(StrEnum):
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    DATE = "date"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    YES_NO = "yes_no"
    FILE = "file"
    IMAGE = "image"
    PDF = "pdf"
    CONSENT = "consent"
    RATING = "rating"


def required(name: str, value: str, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def optional(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > limit:
        raise ContractError(f"value must be at most {limit} chars")
    return normalized or None


def key(name: str, value: str) -> str:
    normalized = required(name, value, 80).lower()
    if re.fullmatch(r"[a-z][a-z0-9_]*", normalized) is None:
        raise ContractError(f"{name} must be a lower-case identifier")
    return normalized


def aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class FormDefinition:
    name: str
    form_type: str
    description: str | None = None
    owner_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", required("name", self.name, 200))
        object.__setattr__(self, "form_type", key("form_type", self.form_type))
        object.__setattr__(self, "description", optional(self.description, 4000))
        object.__setattr__(self, "owner_ref", optional(self.owner_ref, 255))


@dataclass(frozen=True, slots=True)
class SectionDefinition:
    key: str
    title: str
    position: int
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", key("section key", self.key))
        object.__setattr__(self, "title", required("title", self.title, 200))
        object.__setattr__(self, "description", optional(self.description, 2000))
        if self.position < 0:
            raise ContractError("section position must be non-negative")


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    section_id: UUID
    key: str
    label: str
    field_type: FieldType
    position: int
    required: bool = False
    help_text: str | None = None
    settings: tuple[tuple[str, Scalar], ...] = ()
    validation: tuple[tuple[str, Scalar], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", key("field key", self.key))
        object.__setattr__(self, "label", required("label", self.label, 240))
        object.__setattr__(self, "help_text", optional(self.help_text, 2000))
        if self.position < 0:
            raise ContractError("field position must be non-negative")


@dataclass(frozen=True, slots=True)
class OptionDefinition:
    value: str
    label: str
    position: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", required("option value", self.value, 160))
        object.__setattr__(self, "label", required("option label", self.label, 240))
        if self.position < 0:
            raise ContractError("option position must be non-negative")


@dataclass(frozen=True, slots=True)
class AnswerInput:
    field_key: str
    value: AnswerValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "field_key", key("field_key", self.field_key))


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    submission_key: str
    form_version_id: UUID
    answers: tuple[AnswerInput, ...]
    submitted_at: datetime
    subject_ref: str | None = None
    submitted_by_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "submission_key", required("submission_key", self.submission_key)
        )
        object.__setattr__(self, "subject_ref", optional(self.subject_ref, 255))
        object.__setattr__(
            self, "submitted_by_ref", optional(self.submitted_by_ref, 255)
        )
        aware("submitted_at", self.submitted_at)
        keys = [answer.field_key for answer in self.answers]
        if len(keys) != len(set(keys)):
            raise ContractError("a submission may answer each field only once")


__all__ = [
    "AnswerInput",
    "AnswerValue",
    "ContractError",
    "FieldDefinition",
    "FieldType",
    "FormDefinition",
    "OptionDefinition",
    "Scalar",
    "SectionDefinition",
    "SubmissionRequest",
    "fingerprint",
]
