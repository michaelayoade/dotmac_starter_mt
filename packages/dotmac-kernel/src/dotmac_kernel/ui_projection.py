"""Transport-neutral values whose meaning is decided before presentation.

Domain services own status meaning, KPI cohorts and action eligibility. These
types let their read/context services return that meaning without tying a
product to HTML, Jinja, CSS or ``dotmac-ui``. A presentation client owns only
how the already-decided label, tone, icon, freshness and action are rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class StatusTone(StrEnum):
    """Semantic status meaning; clients map it to their native visual tokens."""

    positive = "positive"
    info = "info"
    warning = "warning"
    negative = "negative"
    neutral = "neutral"


class StatusIcon(StrEnum):
    """Small icon vocabulary that keeps status from relying on colour alone."""

    check = "check"
    info = "info"
    clock = "clock"
    alert = "alert"
    x = "x"
    minus = "minus"
    archive = "archive"


class StatusPresentation(BaseModel):
    """Owner-supplied label and semantics for one authoritative status value."""

    value: str
    label: str
    tone: StatusTone
    icon: StatusIcon


class StateKind(StrEnum):
    """Why a value is or is not present."""

    present = "present"
    unknown = "unknown"
    stale = "stale"
    unavailable = "unavailable"
    not_applicable = "not_applicable"


@dataclass(frozen=True, slots=True)
class StateValue:
    """A value carrying state and optional observation freshness."""

    kind: StateKind
    value: object | None = None
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        renderable = self.kind in (StateKind.present, StateKind.stale)
        if renderable and self.value is None:
            raise ValueError("Present and stale UI state requires a value")
        if not renderable and (self.value is not None or self.as_of is not None):
            raise ValueError("Absent UI state cannot carry a value or freshness")
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise ValueError("UI state freshness must be timezone-aware")

    @classmethod
    def present(cls, value: object, *, as_of: datetime | None = None) -> StateValue:
        return cls(StateKind.present, value, as_of)

    @classmethod
    def stale(cls, value: object, *, as_of: datetime | None = None) -> StateValue:
        return cls(StateKind.stale, value, as_of)

    @classmethod
    def unknown(cls) -> StateValue:
        return cls(StateKind.unknown)

    @classmethod
    def unavailable(cls) -> StateValue:
        return cls(StateKind.unavailable)

    @classmethod
    def not_applicable(cls) -> StateValue:
        return cls(StateKind.not_applicable)

    @property
    def is_present(self) -> bool:
        return self.kind in (StateKind.present, StateKind.stale)

    @property
    def is_stale(self) -> bool:
        return self.kind is StateKind.stale

    @property
    def placeholder(self) -> str:
        return {
            StateKind.present: "",
            StateKind.stale: "",
            StateKind.unknown: "Unknown",
            StateKind.unavailable: "Unavailable",
            StateKind.not_applicable: "—",
        }[self.kind]


@dataclass(frozen=True, slots=True)
class Kpi:
    """A headline value and the exact cohort that produced it."""

    label: str
    value: StateValue
    cohort_url: str
    tone: StatusTone = StatusTone.neutral
    icon: StatusIcon | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("KPI label is required")
        if not self.cohort_url.startswith("/"):
            raise ValueError("KPI cohort URL must be an application-relative URL")


@dataclass(frozen=True, slots=True)
class Action:
    """One backend-decided action, including blocked reason and impact preview."""

    key: str
    label: str
    allowed: bool
    reason: str | None = None
    permission: str | None = None
    preview_url: str | None = None
    affected: int | None = None
    tone: StatusTone = StatusTone.neutral
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("Action key and label are required")
        if self.allowed and self.reason:
            raise ValueError("Allowed action cannot carry a blocked reason")
        if not self.allowed and not str(self.reason or "").strip():
            raise ValueError("Blocked action requires a reason")
        if self.affected is not None and self.affected < 0:
            raise ValueError("Action affected count cannot be negative")
        if self.requires_confirmation != bool(self.preview_url):
            raise ValueError(
                "Confirmation requirement and preview URL must be declared together"
            )
        if self.preview_url and not self.preview_url.startswith("/"):
            raise ValueError("Action preview URL must be application-relative")


__all__ = [
    "Action",
    "Kpi",
    "StateKind",
    "StateValue",
    "StatusIcon",
    "StatusPresentation",
    "StatusTone",
]
