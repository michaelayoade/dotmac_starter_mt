"""Typed bank/Activity import intent and uncommitted source observations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from pydantic import Field, InstanceOf

from .bank_validation import BankDoc
from .curriculum_contracts import BankDocument, CurriculumInput


@dataclass(frozen=True, slots=True)
class BankActivityTerms:
    """New-activity terms; explicit document policy overrides matching fields.

    Existing activities preserve omitted mode, grading and weight. The copied
    mapping cannot be changed through a caller-owned dictionary after validation.
    """

    pass_threshold_by_kind: Mapping[str, float]
    fallback_pass_threshold: float
    new_activity_assessment_mode: str
    new_activity_grading: str
    new_activity_weight: float
    new_activity_scoring_policy: str
    new_activity_min_attempt_interval_seconds: int

    def __post_init__(self) -> None:
        thresholds = dict(self.pass_threshold_by_kind)
        for key, value in thresholds.items():
            if not isinstance(key, str):
                raise TypeError("pass threshold kinds must be strings")
            self._fraction(value)
        self._fraction(self.fallback_pass_threshold)
        value = self.new_activity_weight
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("new_activity_weight must be a finite positive number")
        if self.new_activity_scoring_policy not in ("best", "latest", "first"):
            raise ValueError(
                "new_activity_scoring_policy must be best, latest or first"
            )
        interval = self.new_activity_min_attempt_interval_seconds
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 0 <= interval <= 2592000
        ):
            raise ValueError(
                "new_activity_min_attempt_interval_seconds must be "
                "between 0 and 2592000"
            )
        for text in (self.new_activity_assessment_mode, self.new_activity_grading):
            if not isinstance(text, str) or not text:
                raise ValueError(
                    "new activity mode and grading must be nonempty strings"
                )
        object.__setattr__(self, "pass_threshold_by_kind", MappingProxyType(thresholds))

    @staticmethod
    def _fraction(value: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("pass thresholds must be finite fractions")


class ImportBankWithActivityCommand(CurriculumInput):
    """Product has parsed and linted the document before invoking this intent."""

    tenant_id: UUID
    terms: InstanceOf[BankActivityTerms]
    missing_question_weight: int = Field(strict=True, gt=0)
    doc: InstanceOf[BankDoc] | BankDocument


@dataclass(frozen=True, slots=True)
class BankActivityImportResult:
    """No Activity identity or defaults are read before the caller's flush."""

    bank_id: UUID
    title: str
    created: bool
    applied_policy: tuple[tuple[str, object], ...]


__all__ = [
    "BankActivityTerms",
    "BankActivityImportResult",
    "ImportBankWithActivityCommand",
]
