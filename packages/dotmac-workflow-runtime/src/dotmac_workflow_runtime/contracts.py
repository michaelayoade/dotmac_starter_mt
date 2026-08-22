"""Typed commands for provider-neutral resumable workflow execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class ContractError(ValueError):
    """A workflow runtime command is malformed."""


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


@dataclass(frozen=True, slots=True)
class StartExecution:
    definition_version_ref: str
    definition_digest: str
    subject_ref: str
    source_owner: str
    source_event_id: str
    request_fingerprint: str
    checkpoint_codes: tuple[str, ...]
    max_attempts: int = 3

    def __post_init__(self) -> None:
        for name in (
            "definition_version_ref",
            "subject_ref",
            "source_owner",
            "source_event_id",
        ):
            object.__setattr__(self, name, required(name, getattr(self, name)))
        object.__setattr__(
            self,
            "definition_digest",
            digest("definition_digest", self.definition_digest),
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            digest("request_fingerprint", self.request_fingerprint),
        )
        codes = tuple(
            required("checkpoint code", code, 80).lower()
            for code in self.checkpoint_codes
        )
        if not codes or len(codes) != len(set(codes)):
            raise ContractError("checkpoint_codes must be non-empty and unique")
        object.__setattr__(self, "checkpoint_codes", codes)
        if not 1 <= self.max_attempts <= 50:
            raise ContractError("max_attempts must be from 1 to 50")


@dataclass(frozen=True, slots=True)
class SettleCheckpoint:
    checkpoint_id: UUID
    worker_ref: str
    outcome: str
    output_ref: str | None = None
    output_digest: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "worker_ref", required("worker_ref", self.worker_ref))
        outcome = required("outcome", self.outcome, 20).lower()
        if outcome not in {"succeeded", "failed"}:
            raise ContractError("outcome must be succeeded or failed")
        object.__setattr__(self, "outcome", outcome)
        if self.output_ref is not None:
            object.__setattr__(
                self, "output_ref", required("output_ref", self.output_ref, 500)
            )
        if self.output_digest is not None:
            object.__setattr__(
                self, "output_digest", digest("output_digest", self.output_digest)
            )
        if self.error_code is not None:
            object.__setattr__(
                self, "error_code", required("error_code", self.error_code, 120)
            )
        if outcome == "failed" and self.error_code is None:
            raise ContractError("a failed checkpoint requires error_code")


@dataclass(frozen=True, slots=True)
class RepairCommand:
    execution_id: UUID
    checkpoint_code: str
    reason: str
    evidence_digest: str
    repaired_by_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_code",
            required("checkpoint_code", self.checkpoint_code, 80).lower(),
        )
        object.__setattr__(self, "reason", required("reason", self.reason, 2000))
        object.__setattr__(
            self, "evidence_digest", digest("evidence_digest", self.evidence_digest)
        )
        object.__setattr__(
            self, "repaired_by_ref", required("repaired_by_ref", self.repaired_by_ref)
        )


__all__ = ["ContractError", "RepairCommand", "SettleCheckpoint", "StartExecution"]
