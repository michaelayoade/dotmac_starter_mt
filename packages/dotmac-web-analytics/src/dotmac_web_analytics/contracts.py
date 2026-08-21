"""Pure public contracts for privacy-first first-party web analytics.

No type in this module imports persistence, a web framework, a product or a
connector. Commands are immutable values. Website-specific policy arrives as
typed data and registries supplied by an assembly; it is never a package
branch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_BATCH_SIZE: Final[int] = 100
MAX_EVENT_CODE_LENGTH: Final[int] = 96
MAX_EVENT_ATTRIBUTES: Final[int] = 24
MAX_ATTRIBUTE_NAME_LENGTH: Final[int] = 64
MAX_ATTRIBUTE_STRING_LENGTH: Final[int] = 256
MAX_URL_INPUT_LENGTH: Final[int] = 4096
MAX_ORIGIN_LENGTH: Final[int] = 255
MAX_SOURCE_REFERENCE_LENGTH: Final[int] = 255
PAGE_VIEW_EVENT_CODE: Final[str] = "web.page_view"

_CODE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z][a-z0-9_.-]{{0,{MAX_EVENT_CODE_LENGTH - 1}}}$"
)
_ATTRIBUTE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z][a-z0-9_]{{0,{MAX_ATTRIBUTE_NAME_LENGTH - 1}}}$"
)
_SENSITIVE_ATTRIBUTE_TERMS: Final[tuple[str, ...]] = (
    "address",
    "authorization",
    "customer",
    "email",
    "form_value",
    "invoice",
    "name",
    "password",
    "phone",
    "revenue",
    "secret",
    "subscriber",
    "token",
)

AttributeScalar = str | int | bool


class WebAnalyticsError(Exception):
    """Base for every typed refusal made by this package."""


class InvalidContract(WebAnalyticsError):
    """A command or declaration is malformed before persistence."""


class UnknownEventDeclaration(WebAnalyticsError):
    """No installed declaration owns the requested code/version."""


class AttributeRejected(WebAnalyticsError):
    """An event attribute is unknown, mistyped, sensitive or oversized."""


class CollectionRefused(WebAnalyticsError):
    """Origin, rate-limit or privacy admission did not authorize collection."""


class EventIdentityConflict(WebAnalyticsError):
    """An event identity was reused with different canonical content."""


class ProjectionDrift(WebAnalyticsError):
    """The active projection differs from retained authoritative observations."""


def _require_code(value: str, *, field_name: str) -> None:
    if not _CODE_RE.fullmatch(value):
        raise InvalidContract(f"{field_name} {value!r} must match {_CODE_RE.pattern}")


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidContract(f"{field_name} must be timezone-aware")


def _normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InvalidContract("origin is malformed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidContract("origin must use HTTP(S) and contain a host")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise InvalidContract("origin must contain only scheme, host and port")
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    suffix = "" if port is None or default_port else f":{port}"
    return f"{scheme}://{parsed.hostname.lower()}{suffix}"


class AttributeKind(StrEnum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    STRING = "string"
    ENUM = "enum"


class ConsentState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


class CollectionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class TransportKind(StrEnum):
    LOCAL = "local"
    INTEGRATOR = "integrator"


class DeviceClass(StrEnum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"
    TV = "tv"
    OTHER = "other"
    UNKNOWN = "unknown"


class IngestStatus(StrEnum):
    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class DeletionKind(StrEnum):
    RETENTION = "retention"
    PRIVACY = "privacy"


@dataclass(frozen=True, slots=True)
class EventAttributeSpec:
    name: str
    kind: AttributeKind
    required: bool = False
    max_length: int | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        if not _ATTRIBUTE_RE.fullmatch(self.name):
            raise InvalidContract(
                f"attribute name {self.name!r} must match {_ATTRIBUTE_RE.pattern}"
            )
        lowered = self.name.lower()
        if any(term in lowered for term in _SENSITIVE_ATTRIBUTE_TERMS):
            raise InvalidContract(
                f"attribute {self.name!r} uses reserved identity/secret vocabulary"
            )
        if self.kind in {AttributeKind.STRING, AttributeKind.ENUM}:
            if self.max_length is None or not (
                1 <= self.max_length <= MAX_ATTRIBUTE_STRING_LENGTH
            ):
                raise InvalidContract(
                    f"string attribute {self.name!r} requires max_length between "
                    f"1 and {MAX_ATTRIBUTE_STRING_LENGTH}"
                )
        elif self.max_length is not None:
            raise InvalidContract(
                f"non-string attribute {self.name!r} cannot declare max_length"
            )
        if self.kind is AttributeKind.ENUM:
            if not self.allowed_values or len(set(self.allowed_values)) != len(
                self.allowed_values
            ):
                raise InvalidContract(
                    f"enum attribute {self.name!r} needs unique allowed_values"
                )
            if any(
                not value or len(value) > (self.max_length or 0)
                for value in self.allowed_values
            ):
                raise InvalidContract(
                    f"enum attribute {self.name!r} has a blank or oversized value"
                )
        elif self.allowed_values:
            raise InvalidContract(
                f"non-enum attribute {self.name!r} cannot declare allowed_values"
            )
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise InvalidContract(
                    f"attribute {self.name!r} minimum exceeds maximum"
                )

    def validate(self, value: AttributeScalar) -> None:
        if self.kind is AttributeKind.BOOLEAN:
            valid = isinstance(value, bool)
        elif self.kind is AttributeKind.INTEGER:
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise AttributeRejected(
                f"attribute {self.name!r} must be {self.kind.value}"
            )
        if isinstance(value, str):
            if len(value) > (self.max_length or 0):
                raise AttributeRejected(f"attribute {self.name!r} is oversized")
            if self.kind is AttributeKind.ENUM and value not in self.allowed_values:
                raise AttributeRejected(
                    f"attribute {self.name!r} is not an allowed enum value"
                )
        if isinstance(value, int) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                raise AttributeRejected(f"attribute {self.name!r} is below minimum")
            if self.maximum is not None and value > self.maximum:
                raise AttributeRejected(f"attribute {self.name!r} exceeds maximum")


@dataclass(frozen=True, slots=True)
class EventDeclaration:
    code: str
    schema_version: int
    attributes: tuple[EventAttributeSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_code(self.code, field_name="event code")
        if self.schema_version < 1:
            raise InvalidContract("event schema_version must be positive")
        if len(self.attributes) > MAX_EVENT_ATTRIBUTES:
            raise InvalidContract(
                f"event {self.code!r} exceeds {MAX_EVENT_ATTRIBUTES} attributes"
            )
        names = [attribute.name for attribute in self.attributes]
        if len(set(names)) != len(names):
            raise InvalidContract(f"event {self.code!r} declares duplicate attributes")

    def validate_attributes(
        self, attributes: tuple[tuple[str, AttributeScalar], ...]
    ) -> tuple[tuple[str, AttributeScalar], ...]:
        if len(attributes) > MAX_EVENT_ATTRIBUTES:
            raise AttributeRejected("event contains too many attributes")
        supplied = [name for name, _ in attributes]
        if len(set(supplied)) != len(supplied):
            raise AttributeRejected("event contains a duplicate attribute")
        specifications = {spec.name: spec for spec in self.attributes}
        unknown = set(supplied) - set(specifications)
        if unknown:
            raise AttributeRejected(f"unknown attributes: {sorted(unknown)}")
        missing = {
            spec.name
            for spec in self.attributes
            if spec.required and spec.name not in supplied
        }
        if missing:
            raise AttributeRejected(f"missing required attributes: {sorted(missing)}")
        for name, value in attributes:
            specifications[name].validate(value)
        return tuple(sorted(attributes, key=lambda item: item[0]))


class EventDeclarationRegistry:
    """Immutable owner of the adopter's open event vocabulary."""

    __slots__ = ("_declarations",)

    def __init__(self, declarations: Iterable[EventDeclaration]) -> None:
        indexed: dict[tuple[str, int], EventDeclaration] = {}
        for declaration in declarations:
            key = (declaration.code, declaration.schema_version)
            if key in indexed:
                raise InvalidContract(f"duplicate event declaration {key!r}")
            indexed[key] = declaration
        self._declarations = MappingProxyType(indexed)

    def require(self, code: str, schema_version: int) -> EventDeclaration:
        try:
            return self._declarations[(code, schema_version)]
        except KeyError as exc:
            raise UnknownEventDeclaration(
                f"event {code!r} schema v{schema_version} is not declared"
            ) from exc

    def declarations(self) -> tuple[EventDeclaration, ...]:
        return tuple(self._declarations[key] for key in sorted(self._declarations))


CORE_EVENT_DECLARATIONS: Final[tuple[EventDeclaration, ...]] = (
    EventDeclaration(code=PAGE_VIEW_EVENT_CODE, schema_version=1),
)


@dataclass(frozen=True, slots=True)
class OpaqueVisitorToken:
    """Input-only opaque token whose representation cannot reveal its value."""

    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not (16 <= len(self._value) <= 256):
            raise InvalidContract("opaque visitor token must be 16..256 characters")

    def reveal_for_pseudonymization(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "OpaqueVisitorToken([redacted])"


@dataclass(frozen=True, slots=True)
class PropertyRegistration:
    tenant_id: UUID
    property_code: str
    display_name: str
    allowed_origins: tuple[str, ...]
    timezone_name: str
    raw_retention_days: int
    replay_evidence_days: int

    def __post_init__(self) -> None:
        _require_code(self.property_code, field_name="property code")
        if not self.display_name.strip() or len(self.display_name) > 120:
            raise InvalidContract("property display_name must be 1..120 characters")
        if not self.allowed_origins:
            raise InvalidContract("property requires unique allowed_origins")
        normalized_origins = tuple(
            _normalize_origin(origin) for origin in self.allowed_origins
        )
        if len(set(normalized_origins)) != len(normalized_origins) or any(
            len(origin) > MAX_ORIGIN_LENGTH for origin in normalized_origins
        ):
            raise InvalidContract("allowed origins must be bounded HTTP(S) origins")
        object.__setattr__(self, "allowed_origins", normalized_origins)
        if not self.timezone_name.strip():
            raise InvalidContract("property timezone_name is required")
        try:
            ZoneInfo(self.timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise InvalidContract(
                f"property timezone_name {self.timezone_name!r} is not IANA data"
            ) from exc
        if self.raw_retention_days < 1:
            raise InvalidContract("raw_retention_days must be explicit and positive")
        if self.replay_evidence_days <= self.raw_retention_days:
            raise InvalidContract(
                "replay_evidence_days must outlive raw_retention_days"
            )


@dataclass(frozen=True, slots=True)
class StreamRegistration:
    tenant_id: UUID
    property_code: str
    stream_code: str
    accepted_protocol_versions: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_code(self.property_code, field_name="property code")
        _require_code(self.stream_code, field_name="stream code")
        if not self.accepted_protocol_versions or any(
            version < 1 for version in self.accepted_protocol_versions
        ):
            raise InvalidContract("a stream requires positive protocol versions")
        if len(set(self.accepted_protocol_versions)) != len(
            self.accepted_protocol_versions
        ):
            raise InvalidContract("stream protocol versions must be unique")


@dataclass(frozen=True, slots=True)
class PrivacyPolicyEvidence:
    policy_version: str
    consent_state: ConsentState
    decision: CollectionDecision
    global_privacy_control: bool
    do_not_track: bool
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.policy_version.strip() or len(self.policy_version) > 80:
            raise InvalidContract("privacy policy_version must be 1..80 characters")
        _require_aware(self.evaluated_at, field_name="privacy evaluated_at")


@dataclass(frozen=True, slots=True)
class CollectionAdmissionEvidence:
    adapter_code: str
    origin: str
    checked_at: datetime
    origin_verified: bool
    rate_limit_permitted: bool

    def __post_init__(self) -> None:
        _require_code(self.adapter_code, field_name="adapter code")
        _require_aware(self.checked_at, field_name="admission checked_at")
        if not self.origin_verified:
            raise CollectionRefused("origin verification did not pass")
        if not self.rate_limit_permitted:
            raise CollectionRefused("rate limit did not admit the command")
        try:
            origin = _normalize_origin(self.origin)
        except InvalidContract as exc:
            raise CollectionRefused(str(exc)) from exc
        if len(origin) > MAX_ORIGIN_LENGTH:
            raise CollectionRefused("origin is oversized")
        object.__setattr__(self, "origin", origin)


@dataclass(frozen=True, slots=True)
class TransportProvenance:
    kind: TransportKind
    source_system: str
    source_reference: str
    delivery_id: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.source_system, field_name="source system")
        for field_name, value in (
            ("source_reference", self.source_reference),
            ("delivery_id", self.delivery_id),
        ):
            if value is not None and (
                not value.strip() or len(value) > MAX_SOURCE_REFERENCE_LENGTH
            ):
                raise InvalidContract(f"{field_name} must be bounded and non-blank")
        if self.kind is TransportKind.INTEGRATOR and self.delivery_id is None:
            raise InvalidContract("Integrator provenance requires delivery_id")


@dataclass(frozen=True, slots=True)
class AcquisitionEvidence:
    source: str | None = None
    medium: str | None = None
    campaign: str | None = None
    term: str | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("source", "medium", "campaign", "term", "content"):
            value = getattr(self, field_name)
            if value is not None and (not value.strip() or len(value) > 128):
                raise InvalidContract(
                    f"acquisition {field_name} must be bounded and non-blank"
                )


@dataclass(frozen=True, slots=True)
class PageEvidence:
    page_url: str
    referrer_url: str | None = None

    def __post_init__(self) -> None:
        if not self.page_url or len(self.page_url) > MAX_URL_INPUT_LENGTH:
            raise InvalidContract("page_url must be bounded and non-blank")
        if (
            self.referrer_url is not None
            and len(self.referrer_url) > MAX_URL_INPUT_LENGTH
        ):
            raise InvalidContract("referrer_url is oversized")


@dataclass(frozen=True, slots=True)
class RecordEventCommand:
    tenant_id: UUID
    property_code: str
    stream_code: str
    protocol_version: int
    event_id: str
    event_code: str
    event_schema_version: int
    occurred_at: datetime
    visitor_token: OpaqueVisitorToken
    privacy: PrivacyPolicyEvidence
    admission: CollectionAdmissionEvidence
    provenance: TransportProvenance
    attributes: tuple[tuple[str, AttributeScalar], ...] = ()
    page: PageEvidence | None = None
    acquisition: AcquisitionEvidence = field(default_factory=AcquisitionEvidence)
    device_class: DeviceClass = DeviceClass.UNKNOWN

    def __post_init__(self) -> None:
        _require_code(self.property_code, field_name="property code")
        _require_code(self.stream_code, field_name="stream code")
        _require_code(self.event_code, field_name="event code")
        if self.protocol_version < 1 or self.event_schema_version < 1:
            raise InvalidContract("protocol and event schema versions must be positive")
        if not self.event_id.strip() or len(self.event_id) > 128:
            raise InvalidContract("event_id must be 1..128 characters")
        _require_aware(self.occurred_at, field_name="occurred_at")
        if self.privacy.decision is not CollectionDecision.ALLOW:
            raise CollectionRefused("effective privacy policy denied collection")


@dataclass(frozen=True, slots=True)
class RecordEventBatchCommand:
    events: tuple[RecordEventCommand, ...]

    def __post_init__(self) -> None:
        if not self.events or len(self.events) > MAX_BATCH_SIZE:
            raise InvalidContract(
                f"event batch must contain 1..{MAX_BATCH_SIZE} events"
            )


@dataclass(frozen=True, slots=True)
class RecordPageViewCommand:
    """Explicit page-view contract over the common collection envelope."""

    event: RecordEventCommand

    def __post_init__(self) -> None:
        if self.event.event_code != PAGE_VIEW_EVENT_CODE:
            raise InvalidContract(
                f"page-view command requires event_code={PAGE_VIEW_EVENT_CODE!r}"
            )
        if self.event.event_schema_version != 1:
            raise InvalidContract("page-view schema version must be 1")
        if self.event.page is None:
            raise InvalidContract("page-view command requires page evidence")


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: str
    status: IngestStatus
    observation_id: UUID | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BatchIngestResult:
    results: tuple[IngestResult, ...]


@dataclass(frozen=True, slots=True)
class ClassificationEvidenceCommand:
    tenant_id: UUID
    observation_id: UUID
    classifier_code: str
    classifier_version: int
    classified_at: datetime
    is_bot: bool
    analytically_included: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_code(self.classifier_code, field_name="classifier code")
        if self.classifier_version < 1:
            raise InvalidContract("classifier_version must be positive")
        _require_aware(self.classified_at, field_name="classified_at")
        if len(set(self.reasons)) != len(self.reasons) or any(
            not _CODE_RE.fullmatch(reason) for reason in self.reasons
        ):
            raise InvalidContract("classification reasons must be unique codes")


@dataclass(frozen=True, slots=True)
class SessionizationRule:
    code: str
    version: int
    inactivity_seconds: int

    def __post_init__(self) -> None:
        _require_code(self.code, field_name="session rule code")
        if self.version < 1 or not (60 <= self.inactivity_seconds <= 86_400):
            raise InvalidContract("session rule version/timeout is invalid")


@dataclass(frozen=True, slots=True)
class VisitorProjection:
    tenant_id: UUID
    property_id: UUID
    visitor_digest: str
    pseudonym_key_version: int
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class SessionProjection:
    tenant_id: UUID
    property_id: UUID
    session_key: str
    visitor_digest: str
    rule_code: str
    rule_version: int
    started_at: datetime
    ended_at: datetime
    event_count: int


class MetricDimension(StrEnum):
    ROUTE = "route"
    SOURCE = "source"
    CAMPAIGN_MARKER = "campaign_marker"
    DEVICE = "device"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class AggregateMetricQuery:
    tenant_id: UUID
    property_id: UUID
    starts_at: datetime
    ends_at: datetime
    timezone_name: str
    dimensions: tuple[MetricDimension, ...]

    def __post_init__(self) -> None:
        _require_aware(self.starts_at, field_name="metric starts_at")
        _require_aware(self.ends_at, field_name="metric ends_at")
        if self.ends_at <= self.starts_at:
            raise InvalidContract("metric interval must be positive")


@dataclass(frozen=True, slots=True)
class AggregateMetricRow:
    bucket_start: datetime
    dimension_values: tuple[tuple[MetricDimension, str], ...]
    events: int
    visitors: int
    sessions: int


@dataclass(frozen=True, slots=True)
class FunnelStep:
    event_code: str
    event_schema_version: int

    def __post_init__(self) -> None:
        _require_code(self.event_code, field_name="funnel event code")
        if self.event_schema_version < 1:
            raise InvalidContract("funnel event schema version must be positive")


@dataclass(frozen=True, slots=True)
class FunnelDefinition:
    code: str
    version: int
    steps: tuple[FunnelStep, ...]
    within_seconds: int

    def __post_init__(self) -> None:
        _require_code(self.code, field_name="funnel code")
        if self.version < 1 or len(self.steps) < 2:
            raise InvalidContract("funnel requires a positive version and two steps")
        if not (1 <= self.within_seconds <= 2_592_000):
            raise InvalidContract("funnel window must be 1 second..30 days")


@dataclass(frozen=True, slots=True)
class FunnelResult:
    definition_code: str
    definition_version: int
    generation_id: UUID
    entrants: int
    completed_by_step: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExpireObservationsCommand:
    tenant_id: UUID
    property_id: UUID
    cutoff: datetime
    requested_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.cutoff, field_name="expiry cutoff")
        _require_aware(self.requested_at, field_name="expiry requested_at")


@dataclass(frozen=True, slots=True)
class PrivacyDeletionCommand:
    tenant_id: UUID
    property_id: UUID
    request_id: str
    visitor_digest: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if not self.request_id.strip() or len(self.request_id) > 128:
            raise InvalidContract("privacy request_id must be bounded and non-blank")
        if not self.visitor_digest.startswith("sha256:"):
            raise InvalidContract("privacy deletion requires a property digest")
        _require_aware(self.requested_at, field_name="privacy requested_at")


@dataclass(frozen=True, slots=True)
class RebuildProjectionsCommand:
    tenant_id: UUID
    property_id: UUID
    session_rule: SessionizationRule
    projection_version: int
    timezone_name: str
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.projection_version < 1:
            raise InvalidContract("projection_version must be positive")
        if not self.timezone_name.strip():
            raise InvalidContract("projection timezone_name is required")
        try:
            ZoneInfo(self.timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise InvalidContract(
                f"projection timezone_name {self.timezone_name!r} is not IANA data"
            ) from exc
        _require_aware(self.requested_at, field_name="rebuild requested_at")


@dataclass(frozen=True, slots=True)
class ProjectionDriftReport:
    tenant_id: UUID
    property_id: UUID
    active_generation_id: UUID | None
    authoritative_digest: str
    projection_digest: str | None
    observation_count: int
    projected_event_count: int
    drifted: bool


@dataclass(frozen=True, slots=True)
class ProjectionRepairResult:
    previous_generation_id: UUID | None
    active_generation_id: UUID
    verified_digest: str
    deleted_observations: int = 0


@runtime_checkable
class VisitorPseudonymizer(Protocol):
    """Assembly-supplied held-key port; the package never loads a secret."""

    @property
    def key_version(self) -> int: ...

    def digest(
        self,
        *,
        tenant_id: UUID,
        property_id: UUID,
        token: OpaqueVisitorToken,
    ) -> str: ...


__all__ = [
    "CORE_EVENT_DECLARATIONS",
    "MAX_BATCH_SIZE",
    "PAGE_VIEW_EVENT_CODE",
    "AcquisitionEvidence",
    "AggregateMetricQuery",
    "AggregateMetricRow",
    "AttributeKind",
    "AttributeRejected",
    "BatchIngestResult",
    "ClassificationEvidenceCommand",
    "CollectionAdmissionEvidence",
    "CollectionDecision",
    "CollectionRefused",
    "ConsentState",
    "DeletionKind",
    "DeviceClass",
    "EventAttributeSpec",
    "EventDeclaration",
    "EventDeclarationRegistry",
    "EventIdentityConflict",
    "ExpireObservationsCommand",
    "FunnelDefinition",
    "FunnelResult",
    "FunnelStep",
    "IngestResult",
    "IngestStatus",
    "InvalidContract",
    "MetricDimension",
    "OpaqueVisitorToken",
    "PageEvidence",
    "PrivacyDeletionCommand",
    "PrivacyPolicyEvidence",
    "ProjectionDrift",
    "ProjectionDriftReport",
    "ProjectionRepairResult",
    "PropertyRegistration",
    "RebuildProjectionsCommand",
    "RecordEventBatchCommand",
    "RecordEventCommand",
    "RecordPageViewCommand",
    "SessionProjection",
    "SessionizationRule",
    "StreamRegistration",
    "TransportKind",
    "TransportProvenance",
    "UnknownEventDeclaration",
    "VisitorProjection",
    "VisitorPseudonymizer",
    "WebAnalyticsError",
]
