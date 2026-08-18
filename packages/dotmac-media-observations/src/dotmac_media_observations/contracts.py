"""Typed, provider-neutral contracts for external media observations.

The connector boundary ends at these immutable values. No type here names a
provider, transport protocol, product identity, attribution decision or raw
payload. Persistence services validate the same invariants again at the write
boundary so a caller cannot bypass them by constructing ORM rows directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

JsonScalar = str | int | bool | Decimal | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_FORBIDDEN_AGGREGATE_TOKENS = frozenset(
    {
        "audience",
        "contact",
        "cookie",
        "email",
        "member",
        "person",
        "phone",
        "profile",
        "user",
    }
)


@dataclass(frozen=True, slots=True)
class ObservationRejection:
    """Safe, transport-neutral description of a refused command."""

    code: str
    message: str
    source_observation_id: str | None = None
    observation_id: UUID | None = None


class MediaObservationError(ValueError):
    """Base for a refused normalized media command with a safe report."""

    code = "media_observation_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.report = ObservationRejection(code=self.code, message=message)


class InvalidObservation(MediaObservationError):
    """The command is malformed or violates an aggregate-fact invariant."""

    code = "invalid_observation"


class UnsupportedObservation(MediaObservationError):
    """The command is well formed but V1 does not support its requested shape."""

    code = "unsupported_observation"


class ObservationConflict(MediaObservationError):
    """A replay identity, receipt or period conflicts with stored evidence."""

    def __init__(self, report: ObservationRejection) -> None:
        super().__init__(report.message)
        self.report = report


class RecordStatus(StrEnum):
    RECORDED = "recorded"
    REPLAYED = "replayed"
    RESTATED = "restated"


class ObservationKind(StrEnum):
    ENTITY = "entity"
    HIERARCHY = "hierarchy"
    METRIC = "metric"


class EntityDisposition(StrEnum):
    PRESENT = "present"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MetricValueType(StrEnum):
    COUNT = "count"
    DECIMAL = "decimal"
    MONEY = "money"
    DURATION = "duration"
    RATIO = "ratio"


class MetricSemantic(StrEnum):
    SPEND = "spend"
    IMPRESSIONS = "impressions"
    REACH = "reach"
    CLICKS = "clicks"
    ENGAGEMENTS = "engagements"
    CONVERSION_COUNT = "conversion_count"
    CONVERSION_VALUE = "conversion_value"
    OTHER = "other"


class ClaimStatus(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    DERIVED_PROJECTION = "derived_projection"


@dataclass(frozen=True, slots=True)
class ObservationSource:
    tenant_id: UUID
    installation_ref: str
    source_system: str
    source_observation_id: str
    observed_at: datetime
    received_at: datetime
    transport_receipt_ref: str
    normalization_version: int

    def __post_init__(self) -> None:
        for name in (
            "installation_ref",
            "source_system",
            "source_observation_id",
            "transport_receipt_ref",
        ):
            _require_text(name, getattr(self, name), maximum=255)
        _require_aware("observed_at", self.observed_at)
        _require_aware("received_at", self.received_at)
        if (
            type(self.normalization_version) is not int
            or self.normalization_version < 1
        ):
            raise InvalidObservation("normalization_version must be a positive integer")


@dataclass(frozen=True, slots=True)
class NodeTypeDeclaration:
    tenant_id: UUID
    code: str
    version: int
    label: str
    traits: dict[str, JsonValue]
    declared_by: str
    declared_at: datetime

    def __post_init__(self) -> None:
        _require_code("node code", self.code)
        _require_version(self.version)
        _require_text("label", self.label, maximum=200)
        _require_text("declared_by", self.declared_by, maximum=255)
        _require_aware("declared_at", self.declared_at)
        _validate_aggregate_mapping(self.traits)


@dataclass(frozen=True, slots=True)
class MetricDefinitionDeclaration:
    tenant_id: UUID
    code: str
    version: int
    label: str
    value_type: MetricValueType
    unit: str
    semantic: MetricSemantic
    declared_by: str
    declared_at: datetime

    def __post_init__(self) -> None:
        _require_code("metric code", self.code)
        _require_version(self.version)
        _require_text("label", self.label, maximum=200)
        _require_code("metric unit", self.unit)
        _require_text("declared_by", self.declared_by, maximum=255)
        _require_aware("declared_at", self.declared_at)
        if (
            self.semantic
            in {
                MetricSemantic.IMPRESSIONS,
                MetricSemantic.REACH,
                MetricSemantic.CLICKS,
                MetricSemantic.ENGAGEMENTS,
                MetricSemantic.CONVERSION_COUNT,
            }
            and self.value_type is not MetricValueType.COUNT
        ):
            raise InvalidObservation(
                f"metric semantic {self.semantic} requires count value_type"
            )
        if (
            self.semantic
            in {
                MetricSemantic.SPEND,
                MetricSemantic.CONVERSION_VALUE,
            }
            and self.value_type is not MetricValueType.MONEY
        ):
            raise InvalidObservation(
                f"metric semantic {self.semantic} requires money value_type"
            )


@dataclass(frozen=True, slots=True)
class CountValue:
    value: int
    value_type: MetricValueType = field(
        default=MetricValueType.COUNT, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise InvalidObservation("count value must be an integer, never a float")
        if self.value < 0:
            raise InvalidObservation("count value cannot be negative")
        if self.value > _MAX_SIGNED_64:
            raise InvalidObservation("count value exceeds signed 64-bit storage")


@dataclass(frozen=True, slots=True)
class DecimalValue:
    value: Decimal
    value_type: MetricValueType = field(
        default=MetricValueType.DECIMAL, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_numeric_38_18("decimal value", self.value)


@dataclass(frozen=True, slots=True)
class RatioValue:
    value: Decimal
    value_type: MetricValueType = field(
        default=MetricValueType.RATIO, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_numeric_38_18("ratio value", self.value)


@dataclass(frozen=True, slots=True)
class DurationValue:
    value: int
    value_type: MetricValueType = field(
        default=MetricValueType.DURATION, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise InvalidObservation("duration value must be an integer")
        if self.value < 0:
            raise InvalidObservation("duration value cannot be negative")
        if self.value > _MAX_SIGNED_64:
            raise InvalidObservation("duration value exceeds signed 64-bit storage")


@dataclass(frozen=True, slots=True)
class ExactMoney:
    amount: Decimal
    currency: str
    minor_unit: int
    minor_units: int = field(init=False)
    value_type: MetricValueType = field(
        default=MetricValueType.MONEY, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_decimal("money amount", self.amount)
        if not _CURRENCY.fullmatch(self.currency):
            raise InvalidObservation("money currency must be an uppercase ISO code")
        if type(self.minor_unit) is not int or not 0 <= self.minor_unit <= 9:
            raise InvalidObservation("money minor_unit must be an integer from 0 to 9")
        try:
            scaled = self.amount * (Decimal(10) ** self.minor_unit)
            integral = scaled.to_integral_exact()
        except InvalidOperation as exc:
            raise InvalidObservation(
                "money amount must be exactly representable in its minor unit"
            ) from exc
        if scaled != integral:
            raise InvalidObservation(
                "money amount must be exactly representable in its minor unit"
            )
        minor_units = int(integral)
        if minor_units > _MAX_SIGNED_64:
            raise InvalidObservation("money minor units exceed signed 64-bit storage")
        object.__setattr__(self, "minor_units", minor_units)


MetricValue = CountValue | DecimalValue | ExactMoney | DurationValue | RatioValue


@dataclass(frozen=True, slots=True)
class EntityObservation:
    source: ObservationSource
    external_account_ref: str
    entity_ref: str
    node_code: str
    node_version: int
    name: str | None
    state: str
    disposition: EntityDisposition
    properties: dict[str, JsonValue] = field(default_factory=dict)
    restates_observation_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text("external_account_ref", self.external_account_ref, maximum=255)
        _require_text("entity_ref", self.entity_ref, maximum=255)
        _require_code("node code", self.node_code)
        _require_version(self.node_version)
        if self.name is not None:
            _require_text("name", self.name, maximum=500)
        _require_text("state", self.state, maximum=120)
        _validate_aggregate_mapping(self.properties)


@dataclass(frozen=True, slots=True)
class HierarchyObservation:
    source: ObservationSource
    external_account_ref: str
    child_entity_ref: str
    parent_entity_ref: str
    restates_observation_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text("external_account_ref", self.external_account_ref, maximum=255)
        _require_text("child_entity_ref", self.child_entity_ref, maximum=255)
        _require_text("parent_entity_ref", self.parent_entity_ref, maximum=255)
        if self.child_entity_ref == self.parent_entity_ref:
            raise InvalidObservation("an external entity cannot be its own parent")


@dataclass(frozen=True, slots=True)
class MetricObservation:
    source: ObservationSource
    external_account_ref: str
    entity_ref: str
    metric_code: str
    metric_version: int
    period_start: datetime
    period_end: datetime
    value: MetricValue
    restates_observation_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text("external_account_ref", self.external_account_ref, maximum=255)
        _require_text("entity_ref", self.entity_ref, maximum=255)
        _require_code("metric code", self.metric_code)
        _require_version(self.metric_version)
        _require_aware("period_start", self.period_start)
        _require_aware("period_end", self.period_end)
        if self.period_start >= self.period_end:
            raise InvalidObservation(
                "metric period requires start < end under [start,end)"
            )


ObservationCommand = EntityObservation | HierarchyObservation | MetricObservation


@dataclass(frozen=True, slots=True)
class ProviderRestatement:
    replaces_observation_id: UUID
    replacement: ObservationCommand


@dataclass(frozen=True, slots=True)
class RecordOutcome:
    observation_id: UUID
    fingerprint: str
    status: RecordStatus


@dataclass(frozen=True, slots=True)
class CurrentEntityState:
    observation_id: UUID
    installation_ref: str
    source_system: str
    external_account_ref: str
    entity_ref: str
    node_code: str
    node_version: int
    name: str | None
    state: str
    disposition: EntityDisposition
    properties: dict[str, JsonValue]
    source_observed_at: datetime


@dataclass(frozen=True, slots=True)
class ObservedHierarchyEdge:
    observation_id: UUID
    installation_ref: str
    source_system: str
    external_account_ref: str
    child_entity_ref: str
    parent_entity_ref: str
    drift_code: str | None
    source_observed_at: datetime


@dataclass(frozen=True, slots=True)
class TransportReceiptProvenance:
    transport_receipt_ref: str
    received_at: datetime

    def __post_init__(self) -> None:
        _require_text(
            "transport_receipt_ref", self.transport_receipt_ref, maximum=255
        )
        _require_aware("received_at", self.received_at)


@dataclass(frozen=True, slots=True)
class PeriodMetric:
    observation_id: UUID
    installation_ref: str
    source_system: str
    source_observation_id: str
    external_account_ref: str
    entity_ref: str
    metric_code: str
    metric_version: int
    semantic: MetricSemantic
    unit: str
    period_start: datetime
    period_end: datetime
    value: MetricValue
    claim_status: ClaimStatus
    source_observed_at: datetime
    received_at: datetime
    normalization_version: int
    content_fingerprint: str
    restates_observation_id: UUID | None
    transport_receipts: tuple[TransportReceiptProvenance, ...]

    @property
    def transport_receipt_refs(self) -> tuple[str, ...]:
        """Compatibility view over the timestamped receipt provenance."""

        return tuple(
            receipt.transport_receipt_ref for receipt in self.transport_receipts
        )


@dataclass(frozen=True, slots=True)
class DriftItem:
    projection: str
    identity: str
    code: str


@dataclass(frozen=True, slots=True)
class DriftReport:
    items: tuple[DriftItem, ...]

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    evidence_id: UUID | None
    drift_count: int
    before_digest: str
    expected_digest: str
    applied: bool


@dataclass(frozen=True, slots=True)
class NormalizedEntityPayload:
    external_account_ref: str
    entity_ref: str
    node_code: str
    node_version: int
    name: str | None
    state: str
    disposition: EntityDisposition
    properties: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class NormalizedHierarchyPayload:
    external_account_ref: str
    child_entity_ref: str
    parent_entity_ref: str


@dataclass(frozen=True, slots=True)
class NormalizedMetricPayload:
    external_account_ref: str
    entity_ref: str
    metric_code: str
    metric_version: int
    semantic: MetricSemantic
    unit: str
    period_start: datetime
    period_end: datetime
    value: MetricValue
    claim_status: ClaimStatus


NormalizedMediaPayload = (
    NormalizedEntityPayload | NormalizedHierarchyPayload | NormalizedMetricPayload
)


@dataclass(frozen=True, slots=True)
class NormalizedMediaFact:
    observation_id: UUID
    kind: ObservationKind
    installation_ref: str
    source_system: str
    source_observation_id: str
    source_observed_at: datetime
    received_at: datetime
    normalization_version: int
    content_fingerprint: str
    restates_observation_id: UUID | None
    transport_receipts: tuple[TransportReceiptProvenance, ...]
    payload: NormalizedMediaPayload
    attribution_status: str = field(default="not_attribution", init=False)

    def __post_init__(self) -> None:
        expected = {
            ObservationKind.ENTITY: NormalizedEntityPayload,
            ObservationKind.HIERARCHY: NormalizedHierarchyPayload,
            ObservationKind.METRIC: NormalizedMetricPayload,
        }[self.kind]
        if not isinstance(self.payload, expected):
            raise InvalidObservation(
                f"{self.kind.value} analytics fact has the wrong payload type"
            )

    @property
    def external_account_ref(self) -> str:
        return self.payload.external_account_ref

    @property
    def entity_ref(self) -> str:
        if isinstance(self.payload, NormalizedHierarchyPayload):
            return self.payload.child_entity_ref
        return self.payload.entity_ref

    @property
    def claim_status(self) -> ClaimStatus | None:
        if isinstance(self.payload, NormalizedMetricPayload):
            return self.payload.claim_status
        return None


@dataclass(frozen=True, slots=True)
class DerivedRatio:
    value: RatioValue
    unit: str
    claim_status: ClaimStatus = ClaimStatus.DERIVED_PROJECTION


def derive_ratio(
    numerator: Decimal, denominator: Decimal, *, unit: str
) -> DerivedRatio:
    _require_decimal("ratio numerator", numerator)
    _require_decimal("ratio denominator", denominator)
    _require_code("ratio unit", unit)
    if denominator == 0:
        raise InvalidObservation("a derived ratio denominator cannot be zero")
    return DerivedRatio(value=RatioValue(numerator / denominator), unit=unit)


def _require_text(name: str, value: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidObservation(f"{name} must be non-empty and trimmed")
    if len(value) > maximum:
        raise InvalidObservation(f"{name} exceeds {maximum} characters")


def _require_code(name: str, value: str) -> None:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise InvalidObservation(
            f"{name} must be a lowercase versionable declaration code"
        )


def _require_version(value: int) -> None:
    if type(value) is not int or value < 1:
        raise InvalidObservation("declaration version must be a positive integer")


def _require_aware(name: str, value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidObservation(f"{name} must be timezone-aware")


def _require_decimal(name: str, value: Decimal) -> None:
    if type(value) is not Decimal:
        raise InvalidObservation(f"{name} must be Decimal, never float")
    if not value.is_finite():
        raise InvalidObservation(f"{name} must be finite")
    if value < 0:
        raise InvalidObservation(f"{name} cannot be negative")


def _require_numeric_38_18(name: str, value: Decimal) -> None:
    _require_decimal(name, value)
    if value != 0 and value.adjusted() >= 20:
        raise InvalidObservation(f"{name} exceeds NUMERIC(38,18) storage")
    digits = list(value.as_tuple().digits)
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # narrowed by _require_decimal for runtime
        raise InvalidObservation(f"{name} must be finite")
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if digits and exponent < -18:
        raise InvalidObservation(f"{name} exceeds NUMERIC(38,18) storage")


def _validate_aggregate_mapping(values: dict[str, JsonValue]) -> None:
    if not isinstance(values, dict):
        raise InvalidObservation("normalized properties must be a mapping")
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise InvalidObservation(
                "normalized property names must be non-empty strings"
            )
        tokens = {token for token in re.split(r"[^a-z0-9]+", key.lower()) if token}
        if tokens & _FORBIDDEN_AGGREGATE_TOKENS:
            raise InvalidObservation(
                f"property {key!r} violates the aggregate-only V1 contract"
            )
        _validate_json_value(value)


def _validate_json_value(value: JsonValue) -> None:
    if value is None or type(value) in {str, int, bool, Decimal}:
        if type(value) is Decimal:
            _require_decimal("normalized decimal property", value)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        _validate_aggregate_mapping(value)
        return
    raise InvalidObservation(
        "normalized aggregate properties accept only JSON scalars, lists and mappings"
    )


__all__ = [
    "ClaimStatus",
    "CountValue",
    "CurrentEntityState",
    "DecimalValue",
    "DerivedRatio",
    "DriftItem",
    "DriftReport",
    "DurationValue",
    "EntityDisposition",
    "EntityObservation",
    "ExactMoney",
    "HierarchyObservation",
    "InvalidObservation",
    "JsonValue",
    "MediaObservationError",
    "MetricDefinitionDeclaration",
    "MetricObservation",
    "MetricSemantic",
    "MetricValue",
    "MetricValueType",
    "NodeTypeDeclaration",
    "NormalizedEntityPayload",
    "NormalizedHierarchyPayload",
    "NormalizedMediaFact",
    "NormalizedMediaPayload",
    "NormalizedMetricPayload",
    "ObservationCommand",
    "ObservationConflict",
    "ObservationKind",
    "ObservationRejection",
    "ObservationSource",
    "ObservedHierarchyEdge",
    "PeriodMetric",
    "ProviderRestatement",
    "RatioValue",
    "RecordOutcome",
    "RecordStatus",
    "ReconciliationResult",
    "UnsupportedObservation",
    "TransportReceiptProvenance",
    "derive_ratio",
]
