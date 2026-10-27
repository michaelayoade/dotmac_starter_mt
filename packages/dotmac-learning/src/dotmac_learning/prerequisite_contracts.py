"""Typed curriculum prerequisite intent and source reconciliation observations."""

from dataclasses import dataclass
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .participation_contracts import ParticipationCommand

# Closed code catalog (HM-01): tenant data rows may only name these kinds.
PREREQUISITE_CONDITION_KINDS: tuple[str, ...] = (
    "course_completed",
    "certificate_held",
    "course_grade_at_least",
    "activity_passed",
    "attempt_number_at_least",
)

COURSE_SCOPED_KINDS: tuple[str, ...] = (
    "course_completed",
    "certificate_held",
    "course_grade_at_least",
)
ACTIVITY_SCOPED_KINDS: tuple[str, ...] = (
    "activity_passed",
    "attempt_number_at_least",
)


class PrerequisiteConditionDeclaration(BaseModel):
    """One typed prerequisite condition on the declaring course (HM-01).

    Certificate conditions carry an interim meaning until certificate
    validity exists (S2/HM-04): any issued certificate for the required
    course satisfies ``certificate_held``; there is no expiry to test yet.
    """

    model_config = ConfigDict(extra="forbid")
    kind: str
    requires: str | None = None
    activity_id: UUID | None = None
    min_fraction: float | None = None
    min_attempts: int | None = None
    group: str = "g0"

    @field_validator("group")
    @classmethod
    def normalize_group(cls, value: str) -> str:
        cleaned = value.strip() if isinstance(value, str) else value
        if not cleaned:
            raise ValueError("condition group must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def validate_kind_params(self) -> "PrerequisiteConditionDeclaration":
        if self.kind not in PREREQUISITE_CONDITION_KINDS:
            raise ValueError(f"unknown prerequisite condition kind: {self.kind!r}")
        if self.kind in COURSE_SCOPED_KINDS:
            if not self.requires:
                raise ValueError(f"{self.kind} requires a course slug")
            if self.activity_id is not None:
                raise ValueError(f"{self.kind} does not accept activity_id")
        else:
            if self.activity_id is None:
                raise ValueError(f"{self.kind} requires activity_id")
            if self.requires is not None:
                raise ValueError(f"{self.kind} does not accept requires")
        if self.kind == "course_grade_at_least":
            if (
                self.min_fraction is None
                or not isfinite(self.min_fraction)
                or not 0 < self.min_fraction <= 1
            ):
                raise ValueError(
                    "course_grade_at_least requires min_fraction in (0, 1]"
                )
        elif self.min_fraction is not None:
            raise ValueError(f"{self.kind} does not accept min_fraction")
        if self.kind == "attempt_number_at_least":
            if self.min_attempts is None or self.min_attempts < 1:
                raise ValueError("attempt_number_at_least requires min_attempts >= 1")
        elif self.min_attempts is not None:
            raise ValueError(f"{self.kind} does not accept min_attempts")
        return self


class PrerequisiteDeclaration(BaseModel):
    """An entry preserves raw slugs and normalizes source ``requires or []``.

    Legacy ``requires`` entries become ``course_completed`` conditions in
    group ``g0`` and UNION with any explicit ``conditions``. ``composition``
    when present is written to ``Course.prerequisite_composition``.
    """

    model_config = ConfigDict(extra="forbid")
    course: str
    requires: tuple[str, ...] = ()
    conditions: tuple[PrerequisiteConditionDeclaration, ...] = ()
    composition: Literal["all", "any"] | None = None

    @field_validator("requires", mode="before")
    @classmethod
    def normalize_requires(cls, value: object) -> object:
        return () if value is None else value


class ReconcilePrerequisitesCommand(ParticipationCommand):
    """Product supplies the tenant; discipline retains source ``doc.get``."""

    discipline: str | None
    declarations: tuple[PrerequisiteDeclaration, ...] = ()


class PrerequisiteReconciliationError(ValueError):
    """Invalid wanted graph; CLI adapters may translate to source SystemExit."""


@dataclass(frozen=True, slots=True)
class PrerequisiteReconciliationResult:
    """Staged counts only; no claim of flush, commit or durable application."""

    added: int
    removed: int
    declared: int
