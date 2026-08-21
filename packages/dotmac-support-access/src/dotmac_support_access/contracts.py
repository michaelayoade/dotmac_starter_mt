"""Typed request and finite enforcement descriptor contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccessMode(StrEnum):
    CONSENT = "consent"
    BREAK_GLASS = "break_glass"


@dataclass(frozen=True, slots=True)
class SupportRequestInput:
    request_key: str
    case_ref: str
    purpose: str
    target_ref: str
    requester_ref: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FiniteGrantDescriptor:
    grant_id: UUID
    case_ref: str
    purpose: str
    target_ref: str
    requester_ref: str
    capabilities: tuple[str, ...]
    mode: str
    issued_at: datetime
    expires_at: datetime

