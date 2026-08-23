"""Explicit, caller-timed grace evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from dotmac_kernel.cache import Scope

from dotmac_collections._validation import require_aware, require_text


@dataclass(frozen=True, slots=True)
class GraceGrantV1:
    grant_id: UUID
    scope: Scope
    case_id: UUID
    anchor_kind: str
    anchor_at: datetime
    duration: timedelta
    actor_ref: str
    reason_code: str
    granted_at: datetime

    def __post_init__(self) -> None:
        require_text("anchor_kind", self.anchor_kind)
        require_aware("anchor_at", self.anchor_at)
        if self.duration < timedelta(0):
            raise ValueError("duration must be non-negative")
        require_text("actor_ref", self.actor_ref)
        require_text("reason_code", self.reason_code)
        require_aware("granted_at", self.granted_at)


@dataclass(frozen=True, slots=True)
class GraceActive:
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class GraceExpired:
    ended_at: datetime


GraceDecision = GraceActive | GraceExpired


def evaluate_grace(grant: GraceGrantV1, *, as_of: datetime) -> GraceDecision:
    require_aware("as_of", as_of)
    end = grant.anchor_at + grant.duration
    if as_of < end:
        return GraceActive(end)
    return GraceExpired(end)


__all__ = [
    "GraceActive",
    "GraceDecision",
    "GraceExpired",
    "GraceGrantV1",
    "evaluate_grace",
]
