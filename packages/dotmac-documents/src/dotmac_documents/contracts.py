"""Pure contracts for controlled documents and their exact content versions."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


class DocumentError(ValueError):
    """Base for fail-closed document contract refusals."""


class DocumentNotFound(DocumentError):
    pass


class InvalidLifecycleTransition(DocumentError):
    pass


class MetadataInvalid(DocumentError):
    pass


class VersionConflict(DocumentError):
    pass


class CheckoutConflict(DocumentError):
    pass


class DocumentState(enum.StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class VersionBump(enum.StrEnum):
    MAJOR = "major"
    MINOR = "minor"


class SourceProvenance(enum.StrEnum):
    UPLOAD = "upload"
    SCAN = "scan"
    IMPORT = "import"
    GENERATED = "generated"
    API = "api"
    MIGRATION = "migration"


class RenditionKind(enum.StrEnum):
    PREVIEW_PDF = "preview_pdf"
    THUMBNAIL = "thumbnail"
    PREVIEW_IMAGE = "preview_image"
    OCR_TEXT = "ocr_text"
    ACCESSIBILITY = "accessibility"
    REDACTED = "redacted"


class RelationKind(enum.StrEnum):
    SUPERSEDES = "supersedes"
    ANNEX = "annex"
    ATTACHMENT = "attachment"
    TRANSLATION_OF = "translation_of"
    DERIVED_FROM = "derived_from"
    RELATED_DOCUMENT = "related_document"


class AccessEffect(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"


DOCUMENT_ACTIONS = frozenset(
    {
        "read",
        "download",
        "annotate",
        "version",
        "review",
        "administer",
        "declare_record",
    }
)


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DocumentError(f"{name} must be timezone-aware")


def validate_checksum(value: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise DocumentError("checksum must use sha256:<64 lowercase hex>")
    digest = value[7:]
    if any(character not in "0123456789abcdef" for character in digest):
        raise DocumentError("checksum must use sha256:<64 lowercase hex>")


@dataclass(frozen=True, slots=True)
class FileEvidence:
    file_id: UUID
    checksum_sha256: str
    media_type: str
    byte_length: int

    def __post_init__(self) -> None:
        validate_checksum(self.checksum_sha256)
        if not self.media_type.strip():
            raise DocumentError("media_type is required")
        if self.byte_length < 0:
            raise DocumentError("byte_length cannot be negative")


@dataclass(frozen=True, slots=True)
class CreateLibrary:
    code: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDocumentTypeVersion:
    type_code: str
    version: int
    metadata_schema: dict[str, object]
    required_fields: tuple[str, ...]
    allowed_transitions: dict[str, tuple[str, ...]]
    approval_required_states: tuple[str, ...] = ()
    major_minor: bool = True

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise DocumentError("document type version must be positive")
        known = {state.value for state in DocumentState}
        for source, targets in self.allowed_transitions.items():
            if source not in known or not set(targets) <= known:
                raise DocumentError("lifecycle policy contains an unknown state")
        if not set(self.approval_required_states) <= known:
            raise DocumentError("approval-required policy contains an unknown state")


@dataclass(frozen=True, slots=True)
class CreateDocument:
    library_id: UUID
    type_code: str
    type_version: int
    code: str
    title: str
    metadata: dict[str, object]
    folder_path: str = "/"
    tags: tuple[str, ...] = ()
    sensitivity: str = "internal"
    handling_instructions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AddVersion:
    file: FileEvidence
    provenance: SourceProvenance
    authored_by: UUID
    authored_at: datetime
    change_reason: str
    bump: VersionBump
    metadata: dict[str, object]
    expected_current_version_id: UUID | None = None

    def __post_init__(self) -> None:
        _aware("authored_at", self.authored_at)
        if not self.change_reason.strip():
            raise DocumentError("change_reason is required")


@dataclass(frozen=True, slots=True)
class ApprovalVerdict:
    request_id: UUID
    subject_id: str
    content_digest: str
    approved: bool
    decided_at: datetime

    def __post_init__(self) -> None:
        validate_checksum(self.content_digest)
        _aware("decided_at", self.decided_at)


@dataclass(frozen=True, slots=True)
class AddRendition:
    source_version_id: UUID
    source_checksum_sha256: str
    kind: RenditionKind
    output: FileEvidence
    renderer_code: str
    renderer_version: str

    def __post_init__(self) -> None:
        validate_checksum(self.source_checksum_sha256)
        if not self.renderer_code or not self.renderer_version:
            raise DocumentError("renderer code and version are required")


@dataclass(frozen=True, slots=True)
class AddAnnotation:
    version_id: UUID
    principal_ref: str
    body: str
    anchor: dict[str, object] = field(default_factory=dict)
    finding_code: str | None = None


@dataclass(frozen=True, slots=True)
class GrantDocumentAccess:
    target_kind: str
    target_ref: str
    principal_kind: str
    principal_ref: str
    actions: tuple[str, ...]
    effect: AccessEffect
    inherits: bool = True
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.expires_at is not None:
            _aware("expires_at", self.expires_at)
        if not self.actions or not set(self.actions) <= DOCUMENT_ACTIONS:
            raise DocumentError("access grant contains an unknown action")


@dataclass(frozen=True, slots=True)
class AccessPrincipal:
    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class AccessTarget:
    """One target in least-specific to most-specific inheritance order."""

    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class DocumentAccessDecision:
    allowed: bool
    effect: AccessEffect | None
    matched_grant_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ScheduleDocumentTransition:
    schedule_id: UUID
    target: DocumentState
    due_at: datetime

    def __post_init__(self) -> None:
        _aware("due_at", self.due_at)


@dataclass(frozen=True, slots=True)
class DocumentTimerRequest:
    owner: str
    entity_kind: str
    entity_id: str
    purpose: str
    due_at: datetime
    source_version_id: UUID
    source_checksum_sha256: str


@dataclass(frozen=True, slots=True)
class AcknowledgeVersion:
    version_id: UUID
    principal_ref: str
    attestation_text: str
    evidence: dict[str, object] = field(default_factory=dict)


__all__ = [
    "AccessEffect",
    "AccessPrincipal",
    "AccessTarget",
    "AcknowledgeVersion",
    "AddAnnotation",
    "AddRendition",
    "AddVersion",
    "ApprovalVerdict",
    "CheckoutConflict",
    "CreateDocument",
    "CreateDocumentTypeVersion",
    "CreateLibrary",
    "DocumentError",
    "DocumentAccessDecision",
    "DocumentNotFound",
    "DocumentState",
    "DocumentTimerRequest",
    "FileEvidence",
    "GrantDocumentAccess",
    "InvalidLifecycleTransition",
    "MetadataInvalid",
    "RelationKind",
    "RenditionKind",
    "ScheduleDocumentTransition",
    "SourceProvenance",
    "VersionBump",
    "VersionConflict",
]
