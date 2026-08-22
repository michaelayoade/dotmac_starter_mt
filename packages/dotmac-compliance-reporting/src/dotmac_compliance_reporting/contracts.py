"""Typed evidence-section and regulator acknowledgement values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SectionState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class EvidenceSectionInput:
    section_code: str
    source_owner: str
    state: SectionState
    evidence_ref: str | None
    evidence_digest: str | None
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class AcknowledgementInput:
    acknowledgement_key: str
    outcome: str
    acknowledged_at: datetime
    evidence_ref: str
