"""Pure provider-neutral contracts for Digital Media."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


class DigitalMediaError(Exception):
    """Base for fail-closed Digital Media refusals."""


class NotFound(DigitalMediaError):
    """A tenant-scoped Digital Media row does not exist."""


class Conflict(DigitalMediaError):
    """The same identity was reused for different evidence."""


class InvalidEvidence(DigitalMediaError):
    """An observation does not bind the exact immutable source."""


class LifecycleError(DigitalMediaError):
    """A lifecycle transition is not permitted."""


class RightsError(DigitalMediaError):
    """Rights input is incomplete or internally inconsistent."""


class AssetKind(enum.StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    RICH_MEDIA = "rich_media"


class SourceKind(enum.StrEnum):
    UPLOAD = "upload"
    SCAN = "scan"
    IMPORT = "import"
    API = "api"
    GENERATED = "generated"
    MIGRATION = "migration"


class AssetLifecycle(enum.StrEnum):
    INGESTING = "ingesting"
    QUARANTINED = "quarantined"
    AVAILABLE = "available"
    RESTRICTED = "restricted"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class RenditionKind(enum.StrEnum):
    THUMBNAIL = "thumbnail"
    PREVIEW = "preview"
    CROP = "crop"
    RESIZE = "resize"
    VIDEO_TRANSCODE = "video_transcode"
    POSTER_FRAME = "poster_frame"
    AUDIO_TRANSCODE = "audio_transcode"
    WAVEFORM = "waveform"
    WATERMARKED = "watermarked"
    REDACTED = "redacted"
    RESPONSIVE = "responsive"


class RenditionState(enum.StrEnum):
    REQUESTED = "requested"
    READY = "ready"
    FAILED = "failed"


class Permission(enum.StrEnum):
    VIEW = "view"
    DOWNLOAD_ORIGINAL = "download_original"
    DOWNLOAD_RENDITION = "download_rendition"
    ANNOTATE = "annotate"
    TRANSFORM = "transform"
    ADMINISTER = "administer"


class AccessScope(enum.StrEnum):
    LIBRARY = "library"
    COLLECTION = "collection"
    ASSET = "asset"


class GrantEffect(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class RightsDeadlinePurpose(enum.StrEnum):
    REVIEW = "review"
    EXPIRY = "expiry"


@dataclass(frozen=True, slots=True)
class CreateLibrary:
    code: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreateAsset:
    library_id: UUID
    kind: AssetKind
    title: str
    description: str | None = None
    default_alt_text: str | None = None
    creator_credit: str | None = None
    photographer_credit: str | None = None
    producer_credit: str | None = None
    contributor_credits: tuple[str, ...] = ()
    capture_date: datetime | None = None
    supplied_location: str | None = None
    sensitivity: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionCommand:
    file_id: UUID
    checksum: str
    media_type: str
    byte_length: int
    source_kind: SourceKind
    source_ref: str | None
    author_ref: str
    created_at: datetime
    change_reason: str
    perceptual_hash: str | None = None
    perceptual_hash_algorithm: str | None = None


@dataclass(frozen=True, slots=True)
class MetadataObservationCommand:
    revision_id: UUID
    source_checksum: str
    extractor_code: str
    extractor_version: str
    observed_at: datetime
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    frame_rate: float | None = None
    bitrate: int | None = None
    codec: str | None = None
    colour_profile: str | None = None
    orientation: str | None = None
    accessibility: dict[str, Any] = field(default_factory=dict)
    exif: dict[str, Any] = field(default_factory=dict)
    iptc: dict[str, Any] = field(default_factory=dict)
    xmp: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RightsCommand:
    rights_holder: str
    copyright_notice: str | None
    licence_id: str
    licence_version: str
    territories: tuple[str, ...]
    channels: tuple[str, ...]
    purposes: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None
    required_credit: str | None
    commercial_use_allowed: bool
    modification_allowed: bool
    release_references: tuple[str, ...]
    release_evidence_ref: str | None
    release_evidence_valid: bool
    sensitivity: str | None
    embargo_until: datetime | None
    review_at: datetime | None


@dataclass(frozen=True, slots=True)
class RightsUse:
    at: datetime
    territory: str
    channel: str
    purpose: str
    commercial: bool
    modifies: bool


@dataclass(frozen=True, slots=True)
class RightsDeadlineObservation:
    rights_version_id: UUID
    purpose: RightsDeadlinePurpose
    due_at: datetime
    source_event_id: str


@dataclass(frozen=True, slots=True)
class RightsDecision:
    allowed: bool
    reasons: tuple[str, ...]
    rights_version_id: UUID | None
    required_credit: str | None
    evidence_ref: str | None


@dataclass(frozen=True, slots=True)
class RenditionCommand:
    source_revision_id: UUID
    source_checksum: str
    kind: RenditionKind
    recipe_code: str
    recipe_version: str
    engine_code: str
    engine_version: str
    output_media_type: str
    requested_width: int | None = None
    requested_height: int | None = None
    focal_point: dict[str, float] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenditionOutput:
    file_id: UUID
    checksum: str
    byte_length: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    codec: str | None = None


@dataclass(frozen=True, slots=True)
class ScanObservation:
    revision_id: UUID
    file_id: UUID
    checksum: str
    decision: str
    scanner_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AccessGrantCommand:
    scope: AccessScope
    scope_id: UUID
    principal_type: str
    principal_ref: str
    permission: Permission
    effect: GrantEffect
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    grant_id: UUID | None


@dataclass(frozen=True, slots=True)
class UsageObservationCommand:
    source_owner: str
    source_type: str
    source_id: str
    source_version: str
    relation: str
    revision_id: UUID
    rendition_id: UUID | None
    source_event_id: str
    source_fingerprint: str
    active: bool
    observed_at: datetime

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    revision_id: UUID
    asset_id: UUID
    match_kind: str
    hamming_distance: int | None


@dataclass(frozen=True, slots=True)
class RecordDispositionObservation:
    declared_as_record: bool
    records_allows_disposition: bool
    evidence_ref: str | None


@dataclass(frozen=True, slots=True)
class DispositionDecision:
    archive_allowed: bool
    dispose_allowed: bool
    reasons: tuple[str, ...]
    records_evidence_ref: str | None


__all__ = [
    "AccessDecision",
    "AccessGrantCommand",
    "AccessScope",
    "AssetKind",
    "AssetLifecycle",
    "Conflict",
    "CreateAsset",
    "CreateLibrary",
    "DigitalMediaError",
    "DispositionDecision",
    "DuplicateCandidate",
    "GrantEffect",
    "InvalidEvidence",
    "LifecycleError",
    "MetadataObservationCommand",
    "NotFound",
    "Permission",
    "RecordDispositionObservation",
    "RenditionCommand",
    "RenditionKind",
    "RenditionOutput",
    "RenditionState",
    "RevisionCommand",
    "RightsCommand",
    "RightsDeadlineObservation",
    "RightsDeadlinePurpose",
    "RightsDecision",
    "RightsError",
    "RightsUse",
    "ScanObservation",
    "SourceKind",
    "UsageObservationCommand",
]
