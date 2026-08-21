"""Typed public commands for the provider-neutral referral owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ContractError(ValueError):
    """A referral command is malformed."""


def required(name: str, value: str, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def digest(name: str, value: str) -> str:
    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ContractError(f"{name} must be a SHA-256 hex digest")
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
class CreateProgramme:
    code: str
    name: str
    qualification_policy_ref: str
    reward_policy_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", required("code", self.code, 80).upper())
        object.__setattr__(self, "name", required("name", self.name, 200))
        object.__setattr__(
            self,
            "qualification_policy_ref",
            required("qualification_policy_ref", self.qualification_policy_ref),
        )
        object.__setattr__(
            self,
            "reward_policy_ref",
            required("reward_policy_ref", self.reward_policy_ref),
        )


@dataclass(frozen=True, slots=True)
class IssueCode:
    programme_id: UUID
    referrer_ref: str
    code: str
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "referrer_ref", required("referrer_ref", self.referrer_ref)
        )
        object.__setattr__(self, "code", required("code", self.code, 80).upper())
        aware("expires_at", self.expires_at)


@dataclass(frozen=True, slots=True)
class CaptureReferral:
    code: str
    referred_subject_ref: str
    source_owner: str
    source_event_id: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", required("code", self.code, 80).upper())
        object.__setattr__(
            self,
            "referred_subject_ref",
            required("referred_subject_ref", self.referred_subject_ref),
        )
        object.__setattr__(
            self, "source_owner", required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self, "source_event_id", required("source_event_id", self.source_event_id)
        )
        object.__setattr__(
            self,
            "source_fingerprint",
            digest("source_fingerprint", self.source_fingerprint),
        )


@dataclass(frozen=True, slots=True)
class RecordConversion:
    referral_id: UUID
    conversion_ref: str
    qualification_evidence_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "conversion_ref", required("conversion_ref", self.conversion_ref)
        )
        object.__setattr__(
            self,
            "qualification_evidence_digest",
            digest("qualification_evidence_digest", self.qualification_evidence_digest),
        )


__all__ = [
    "CaptureReferral",
    "ContractError",
    "CreateProgramme",
    "IssueCode",
    "RecordConversion",
    "fingerprint",
]
