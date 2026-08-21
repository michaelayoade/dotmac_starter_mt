"""Pure records-management contracts."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


class RecordsError(ValueError):
    """Base for fail-closed records decisions."""


class RecordNotFound(RecordsError):
    pass


class RecordConflict(RecordsError):
    pass


class TriggerConflict(RecordsError):
    pass


class DispositionBlocked(RecordsError):
    pass


class CutoffRule(enum.StrEnum):
    EXACT_DATE = "exact_date"
    MONTH_END = "month_end"
    CALENDAR_YEAR_END = "calendar_year_end"


class FinalAction(enum.StrEnum):
    DESTROY = "destroy"
    ARCHIVAL_REVIEW = "archival_review"
    TRANSFER = "transfer"
    RETAIN_PERMANENTLY = "retain_permanently"


class DispositionOutcome(enum.StrEnum):
    APPROVED_FOR_DESTRUCTION = "approved_for_destruction"
    TRANSFERRED_TO_ARCHIVE = "transferred_to_archive"
    RETAINED_PERMANENTLY = "retained_permanently"
    DEFERRED = "deferred"
    REFUSED = "refused"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecordsError(f"{name} must be timezone-aware")


def _checksum(value: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise RecordsError("checksum must use sha256:<64 lowercase hex>")
    if any(character not in "0123456789abcdef" for character in value[7:]):
        raise RecordsError("checksum must use sha256:<64 lowercase hex>")


@dataclass(frozen=True, slots=True)
class DefineRetentionScheduleVersion:
    schedule_code: str
    version: int
    trigger_event_type: str
    duration_days: int | None
    permanent: bool
    cutoff_rule: CutoffRule
    final_action: FinalAction
    disposition_approval_policy: str
    review_cadence_days: int
    authority: str
    accountable_owner: str

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise RecordsError("schedule version must be positive")
        if self.permanent != (self.duration_days is None):
            raise RecordsError(
                "permanent schedules have no duration; timed schedules require one"
            )
        if self.duration_days is not None and self.duration_days < 0:
            raise RecordsError("duration_days cannot be negative")
        if self.review_cadence_days <= 0:
            raise RecordsError("review cadence must be positive")


@dataclass(frozen=True, slots=True)
class DefineRecordSeriesVersion:
    series_code: str
    version: int
    name: str
    parent_series_code: str | None
    responsible_owner: str
    custodian: str
    jurisdiction: str
    regulatory_basis: str
    default_schedule_code: str
    default_schedule_version: int
    vital_record: bool
    confidentiality: str
    transfer_destination: str
    required_fields: tuple[str, ...]
    parent_series_version: int | None = None

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise RecordsError("record series version must be positive")
        if self.default_schedule_version <= 0:
            raise RecordsError("default retention schedule version must be positive")
        if (self.parent_series_code is None) != (self.parent_series_version is None):
            raise RecordsError("a parent series reference pins both code and version")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    owner: str
    source_type: str
    source_id: str
    source_version: str
    authority: str
    provenance: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.owner,
                self.source_type,
                self.source_id,
                self.source_version,
                self.authority,
            )
        ):
            raise RecordsError(
                "source owner, type, id, version and authority are required"
            )


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    file_id: UUID
    checksum_sha256: str
    media_type: str
    byte_length: int

    def __post_init__(self) -> None:
        _checksum(self.checksum_sha256)
        if not self.media_type.strip():
            raise RecordsError("media_type is required")
        if self.byte_length < 0:
            raise RecordsError("byte_length cannot be negative")


@dataclass(frozen=True, slots=True)
class DeclareRecord:
    source: SourceSnapshot
    file: FileSnapshot | None
    series_code: str
    series_version: int
    schedule_code: str
    schedule_version: int
    metadata: dict[str, object]
    sensitivity: str
    access_restrictions: tuple[str, ...]
    declared_by: UUID
    declared_at: datetime
    supersedes_record_id: UUID | None = None

    def __post_init__(self) -> None:
        _aware("declared_at", self.declared_at)


@dataclass(frozen=True, slots=True)
class TriggerObservation:
    source_owner: str
    source_event_id: str
    source_fingerprint: str
    event_type: str
    source_version: str
    occurred_at: datetime
    observed_at: datetime
    provenance: dict[str, object]

    def __post_init__(self) -> None:
        _aware("occurred_at", self.occurred_at)
        _aware("observed_at", self.observed_at)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_owner": self.source_owner,
            "source_event_id": self.source_event_id,
            "source_fingerprint": self.source_fingerprint,
            "event_type": self.event_type,
            "source_version": self.source_version,
            "occurred_at": self.occurred_at,
            "observed_at": self.observed_at,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class TimerRequest:
    owner: str
    entity_kind: str
    entity_id: str
    purpose: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class TriggerResult:
    observation_id: UUID
    retention_due_at: datetime | None
    review_at: datetime
    timer: TimerRequest
    replayed: bool


@dataclass(frozen=True, slots=True)
class HoldCaseDefinition:
    case_code: str
    authority: str
    reason: str
    responsible_officer: UUID
    review_at: datetime
    ongoing_capture_rule: dict[str, object] | None = None

    def __post_init__(self) -> None:
        _aware("review_at", self.review_at)


@dataclass(frozen=True, slots=True)
class HoldTargetDefinition:
    record_id: UUID | None = None
    series_code: str | None = None
    series_version: int | None = None
    cohort_fingerprint: str | None = None
    cohort_snapshot: dict[str, object] | None = None

    def __post_init__(self) -> None:
        kinds = sum(
            (
                self.record_id is not None,
                self.series_code is not None,
                self.cohort_fingerprint is not None,
            )
        )
        if kinds != 1:
            raise RecordsError(
                "a hold target is exactly one record, series or cohort snapshot"
            )
        if (self.series_code is None) != (self.series_version is None):
            raise RecordsError("series hold targets pin both code and version")
        if (self.cohort_fingerprint is None) != (self.cohort_snapshot is None):
            raise RecordsError("cohort hold targets pin fingerprint and snapshot")


@dataclass(frozen=True, slots=True)
class DispositionEvaluation:
    record_id: UUID
    eligible: bool
    reason: str
    active_hold_count: int
    final_action: FinalAction
    source_state_fingerprint: str
    eligibility_fingerprint: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalVerdict:
    request_id: UUID
    content_digest: str
    approved: bool
    decided_at: datetime

    def __post_init__(self) -> None:
        _checksum(self.content_digest)
        _aware("decided_at", self.decided_at)


@dataclass(frozen=True, slots=True)
class DeletionAuthorization:
    authorization_id: UUID
    record_id: UUID
    file_id: UUID
    checksum_sha256: str
    outcome: DispositionOutcome
    authorized_at: datetime


@dataclass(frozen=True, slots=True)
class PhysicalDeletionConfirmation:
    authorization_id: UUID
    file_id: UUID
    checksum_sha256: str
    physical_state: str
    confirmed_at: datetime
    provider_evidence_ref: str

    def __post_init__(self) -> None:
        _checksum(self.checksum_sha256)
        _aware("confirmed_at", self.confirmed_at)


__all__ = [
    "ApprovalVerdict",
    "CutoffRule",
    "DeclareRecord",
    "DefineRecordSeriesVersion",
    "DefineRetentionScheduleVersion",
    "DeletionAuthorization",
    "DispositionBlocked",
    "DispositionEvaluation",
    "DispositionOutcome",
    "FileSnapshot",
    "FinalAction",
    "HoldCaseDefinition",
    "HoldTargetDefinition",
    "PhysicalDeletionConfirmation",
    "RecordConflict",
    "RecordNotFound",
    "RecordsError",
    "SourceSnapshot",
    "TimerRequest",
    "TriggerConflict",
    "TriggerObservation",
    "TriggerResult",
]
