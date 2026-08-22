"""Typed commands, immutable values, ports and refusals for publishing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_publishing.lifecycle import DeliveryState, PublicationState


class PublishingError(Exception):
    """Base for publication-owner refusals."""


class ContractError(PublishingError, ValueError):
    """A public contract value is malformed."""


class NotFound(PublishingError):
    """A row does not exist in the declared tenant scope."""


class Conflict(PublishingError):
    """A request or receipt identity conflicts with prior content."""


class StaleTimer(PublishingError):
    """A superseded timer generation attempted to dispatch."""


def _required(name: str, value: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def _optional(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > limit:
        raise ContractError(f"value must be at most {limit} chars")
    return normalized or None


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicationSnapshotV1:
    source_ref: str
    title: str
    body: str
    variant_key: str | None
    creative_refs: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_ref", _required("source_ref", self.source_ref, 255)
        )
        object.__setattr__(self, "title", _required("title", self.title, 300))
        object.__setattr__(self, "body", _required("body", self.body, 100_000))
        object.__setattr__(self, "variant_key", _optional(self.variant_key, 120))
        if self.schema_version != 1:
            raise ContractError("PublicationSnapshotV1 schema_version must be 1")
        normalized = tuple(
            _required("creative_ref", value, 255) for value in self.creative_refs
        )
        if len(set(normalized)) != len(normalized):
            raise ContractError("creative_refs cannot contain duplicates")
        object.__setattr__(self, "creative_refs", normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "title": self.title,
            "body": self.body,
            "variant_key": self.variant_key,
            "creative_refs": list(self.creative_refs),
        }

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())


@dataclass(frozen=True, slots=True)
class PublicationTarget:
    target_ref: str
    variant_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "target_ref", _required("target_ref", self.target_ref, 255)
        )
        object.__setattr__(self, "variant_key", _optional(self.variant_key, 120))

    def as_dict(self) -> dict[str, object]:
        return {"target_ref": self.target_ref, "variant_key": self.variant_key}


@dataclass(frozen=True, slots=True)
class RequestPublication:
    request_key: str
    requested_for: datetime
    snapshot: PublicationSnapshotV1
    targets: tuple[PublicationTarget, ...]
    actor_ref: str = "system"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_key", _required("request_key", self.request_key, 200)
        )
        object.__setattr__(
            self, "actor_ref", _required("actor_ref", self.actor_ref, 255)
        )
        _aware("requested_for", self.requested_for)
        if not self.targets:
            raise ContractError("a publication requires at least one target")
        refs = [target.target_ref for target in self.targets]
        if len(set(refs)) != len(refs):
            raise ContractError("duplicate publication target_ref")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "requested_for": self.requested_for,
            "snapshot": self.snapshot.as_dict(),
            "targets": [target.as_dict() for target in self.targets],
            "actor_ref": self.actor_ref,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.fingerprint_payload())


class DeliveryOutcome(StrEnum):
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DeliveryObservationV1:
    receipt_ref: str
    attempt_ref: str
    outcome: DeliveryOutcome
    observed_at: datetime
    remote_ref: str | None
    error_detail: str | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "receipt_ref", _required("receipt_ref", self.receipt_ref, 255)
        )
        object.__setattr__(
            self, "attempt_ref", _required("attempt_ref", self.attempt_ref, 255)
        )
        object.__setattr__(self, "remote_ref", _optional(self.remote_ref, 500))
        object.__setattr__(self, "error_detail", _optional(self.error_detail, 2_000))
        _aware("observed_at", self.observed_at)
        if self.schema_version != 1:
            raise ContractError("DeliveryObservationV1 schema_version must be 1")
        if self.outcome == DeliveryOutcome.PUBLISHED and self.remote_ref is None:
            raise ContractError("remote_ref is required for a published observation")
        if self.outcome == DeliveryOutcome.FAILED and self.error_detail is None:
            raise ContractError("error_detail is required for a failed observation")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_ref": self.attempt_ref,
            "outcome": self.outcome.value,
            "observed_at": self.observed_at,
            "remote_ref": self.remote_ref,
            "error_detail": self.error_detail,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class DispatchPublicationV1:
    publication_release_id: UUID
    publication_delivery_id: UUID
    publication_attempt_id: UUID
    attempt_number: int
    target_ref: str
    requested_for: datetime
    snapshot: PublicationSnapshotV1
    schema_version: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "publication_release_id": str(self.publication_release_id),
            "publication_delivery_id": str(self.publication_delivery_id),
            "publication_attempt_id": str(self.publication_attempt_id),
            "attempt_number": self.attempt_number,
            "target_ref": self.target_ref,
            "requested_for": self.requested_for.isoformat(),
            "snapshot": self.snapshot.as_dict(),
            "snapshot_digest": self.snapshot.digest,
        }


@dataclass(frozen=True, slots=True)
class PublicationTimerTrigger:
    timer_ref: UUID
    publication_release_id: UUID
    generation: int
    due_at: datetime

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ContractError("timer generation must be positive")
        _aware("due_at", self.due_at)


@dataclass(frozen=True, slots=True)
class ScheduledPublicationTimer:
    timer_ref: UUID
    publication_release_id: UUID
    generation: int
    due_at: datetime

    def trigger(self) -> PublicationTimerTrigger:
        return PublicationTimerTrigger(
            timer_ref=self.timer_ref,
            publication_release_id=self.publication_release_id,
            generation=self.generation,
            due_at=self.due_at,
        )


@dataclass(frozen=True, slots=True)
class TimerAcceptance:
    current: bool
    replayed: bool = False
    reason: str = "current"


@dataclass(frozen=True, slots=True)
class TimerCancellation:
    cancelled: bool
    reason: str = "cancelled"


class PublicationTimerPort(Protocol):
    def schedule(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        publication_release_id: UUID,
        due_at: datetime,
        recorded_at: datetime,
    ) -> ScheduledPublicationTimer: ...

    def accept(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        trigger: PublicationTimerTrigger,
        accepted_at: datetime,
    ) -> TimerAcceptance: ...

    def cancel(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        publication_release_id: UUID,
        recorded_at: datetime,
    ) -> TimerCancellation: ...


@dataclass(frozen=True, slots=True)
class ObservationResult:
    observation_id: UUID
    publication_state: PublicationState


__all__ = [
    "Conflict",
    "ContractError",
    "DeliveryObservationV1",
    "DeliveryOutcome",
    "DeliveryState",
    "DispatchPublicationV1",
    "NotFound",
    "ObservationResult",
    "PublicationSnapshotV1",
    "PublicationState",
    "PublicationTarget",
    "PublicationTimerPort",
    "PublicationTimerTrigger",
    "PublishingError",
    "RequestPublication",
    "ScheduledPublicationTimer",
    "StaleTimer",
    "TimerAcceptance",
    "TimerCancellation",
]
