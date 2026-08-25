"""Pure contracts for declared cross-domain analytical metric projections.

The contracts contain no ORM, web framework, product or connector imports.
Domain owners calculate their own metrics and publish only aggregate values;
this package validates the declared seam and preserves evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

MAX_BATCH_POINTS: Final[int] = 250
MAX_HISTORY_POINTS: Final[int] = 2_000
MAX_CODE_LENGTH: Final[int] = 120
MAX_REFERENCE_LENGTH: Final[int] = 255
MAX_DIMENSIONS: Final[int] = 12
MAX_DIMENSION_VALUE_LENGTH: Final[int] = 128
MAX_NUMERIC_PRECISION: Final[int] = 38
MAX_NUMERIC_SCALE: Final[int] = 12
MAX_NUMERIC_INTEGER_DIGITS: Final[int] = MAX_NUMERIC_PRECISION - MAX_NUMERIC_SCALE

_CODE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[a-z][a-z0-9_.-]{{0,{MAX_CODE_LENGTH - 1}}}$"
)
_OPAQUE_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[A-Za-z0-9][A-Za-z0-9_.:-]{{0,{MAX_DIMENSION_VALUE_LENGTH - 1}}}$"
)
_SOURCE_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[A-Za-z0-9][A-Za-z0-9_.:-]{{0,{MAX_REFERENCE_LENGTH - 1}}}$"
)
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")


class AnalyticsError(Exception):
    """Base for typed analytics refusals."""


class InvalidAnalyticsContract(AnalyticsError):
    """A declaration or command is malformed."""


class DuplicateMetricDeclaration(AnalyticsError):
    """Two declarations claim the same metric code and version."""


class UnknownMetricDeclaration(AnalyticsError):
    """No installed declaration owns a requested metric code and version."""


class MetricIdentityConflict(AnalyticsError):
    """A source-event identity was reused with different canonical content."""


class MetricValueKind(StrEnum):
    COUNT = "count"
    NUMBER = "number"
    RATIO = "ratio"
    MONEY = "money"


class MetricGranularity(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class DimensionKind(StrEnum):
    ENUM = "enum"
    BOOLEAN = "boolean"
    OPAQUE_REFERENCE = "opaque_reference"


def _require_code(value: str, *, field_name: str) -> None:
    if not _CODE_RE.fullmatch(value):
        raise InvalidAnalyticsContract(
            f"{field_name} {value!r} must match {_CODE_RE.pattern}"
        )


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidAnalyticsContract(f"{field_name} must be timezone-aware")


def _require_reference(value: str, *, field_name: str) -> None:
    if not _SOURCE_REFERENCE_RE.fullmatch(value):
        raise InvalidAnalyticsContract(
            f"{field_name} must be a bounded opaque reference containing only "
            "letters, digits, dot, underscore, colon or hyphen"
        )


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    code: str
    kind: DimensionKind
    allowed_values: tuple[str, ...] = ()
    max_length: int = MAX_DIMENSION_VALUE_LENGTH

    def __post_init__(self) -> None:
        _require_code(self.code, field_name="dimension code")
        if not 1 <= self.max_length <= MAX_DIMENSION_VALUE_LENGTH:
            raise InvalidAnalyticsContract(
                f"dimension {self.code!r} max_length must be between 1 and "
                f"{MAX_DIMENSION_VALUE_LENGTH}"
            )
        if self.kind is DimensionKind.ENUM:
            if not self.allowed_values:
                raise InvalidAnalyticsContract(
                    f"enum dimension {self.code!r} requires allowed_values"
                )
            if len(set(self.allowed_values)) != len(self.allowed_values):
                raise InvalidAnalyticsContract(
                    f"enum dimension {self.code!r} has duplicate allowed_values"
                )
            for value in self.allowed_values:
                if not value or len(value) > self.max_length:
                    raise InvalidAnalyticsContract(
                        f"enum dimension {self.code!r} has a blank or oversized value"
                    )
        elif self.allowed_values:
            raise InvalidAnalyticsContract(
                f"non-enum dimension {self.code!r} cannot declare allowed_values"
            )

    def normalize(self, value: str | bool) -> str:
        if self.kind is DimensionKind.BOOLEAN:
            if not isinstance(value, bool):
                raise InvalidAnalyticsContract(
                    f"dimension {self.code!r} must be boolean"
                )
            return "true" if value else "false"
        if not isinstance(value, str):
            raise InvalidAnalyticsContract(f"dimension {self.code!r} must be a string")
        if not value or len(value) > self.max_length:
            raise InvalidAnalyticsContract(
                f"dimension {self.code!r} must contain 1..{self.max_length} characters"
            )
        if self.kind is DimensionKind.ENUM:
            if value not in self.allowed_values:
                raise InvalidAnalyticsContract(
                    f"dimension {self.code!r} value {value!r} is not an allowed value"
                )
            return value
        if not _OPAQUE_REFERENCE_RE.fullmatch(value):
            raise InvalidAnalyticsContract(
                f"dimension {self.code!r} must be a bounded opaque reference"
            )
        return value


@dataclass(frozen=True, slots=True)
class DimensionValue:
    code: str
    value: str | bool

    def __post_init__(self) -> None:
        _require_code(self.code, field_name="dimension code")


@dataclass(frozen=True, slots=True)
class MetricDeclaration:
    owner_code: str
    metric_code: str
    schema_version: int
    display_name: str
    value_kind: MetricValueKind
    unit_code: str
    granularities: tuple[MetricGranularity, ...]
    dimensions: tuple[DimensionSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_code(self.owner_code, field_name="owner code")
        _require_code(self.metric_code, field_name="metric code")
        if not self.metric_code.startswith(f"{self.owner_code}."):
            raise InvalidAnalyticsContract(
                f"metric {self.metric_code!r} must live in its owner namespace "
                f"{self.owner_code!r}"
            )
        if self.schema_version < 1:
            raise InvalidAnalyticsContract("metric schema_version must be positive")
        if not self.display_name or len(self.display_name) > 160:
            raise InvalidAnalyticsContract(
                "metric display_name must contain 1..160 characters"
            )
        _require_code(self.unit_code, field_name="unit code")
        if not self.granularities or len(set(self.granularities)) != len(
            self.granularities
        ):
            raise InvalidAnalyticsContract(
                "metric granularities must be non-empty and unique"
            )
        if len(self.dimensions) > MAX_DIMENSIONS:
            raise InvalidAnalyticsContract(
                f"metric cannot declare more than {MAX_DIMENSIONS} dimensions"
            )
        names = [dimension.code for dimension in self.dimensions]
        if len(set(names)) != len(names):
            raise InvalidAnalyticsContract("metric declares duplicate dimensions")

    @property
    def identity(self) -> tuple[str, int]:
        return self.metric_code, self.schema_version

    @property
    def fingerprint(self) -> str:
        document = {
            "owner_code": self.owner_code,
            "metric_code": self.metric_code,
            "schema_version": self.schema_version,
            "display_name": self.display_name,
            "value_kind": self.value_kind.value,
            "unit_code": self.unit_code,
            "granularities": sorted(item.value for item in self.granularities),
            "dimensions": [
                {
                    "code": item.code,
                    "kind": item.kind.value,
                    "allowed_values": list(item.allowed_values),
                    "max_length": item.max_length,
                }
                for item in sorted(self.dimensions, key=lambda spec: spec.code)
            ],
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()

    def normalize_dimensions(
        self, dimensions: tuple[DimensionValue, ...]
    ) -> tuple[tuple[str, str], ...]:
        declared = {dimension.code: dimension for dimension in self.dimensions}
        supplied: dict[str, str] = {}
        for dimension in dimensions:
            if dimension.code in supplied:
                raise InvalidAnalyticsContract(
                    f"dimension {dimension.code!r} was supplied more than once"
                )
            spec = declared.get(dimension.code)
            if spec is None:
                raise InvalidAnalyticsContract(
                    f"dimension {dimension.code!r} is undeclared for "
                    f"{self.metric_code!r} v{self.schema_version}"
                )
            supplied[dimension.code] = spec.normalize(dimension.value)
        return tuple(sorted(supplied.items()))

    def serialized_dimensions(self) -> list[list[str | int]]:
        return [
            [
                spec.code,
                spec.kind.value,
                spec.max_length,
                *sorted(spec.allowed_values),
            ]
            for spec in sorted(self.dimensions, key=lambda item: item.code)
        ]


@dataclass(frozen=True, slots=True)
class MetricPointInput:
    metric_code: str
    schema_version: int
    period_start: datetime
    period_end: datetime
    granularity: MetricGranularity
    value: Decimal
    dimensions: tuple[DimensionValue, ...] = ()
    currency_code: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.metric_code, field_name="metric code")
        if self.schema_version < 1:
            raise InvalidAnalyticsContract("metric schema_version must be positive")
        _require_aware(self.period_start, field_name="period_start")
        _require_aware(self.period_end, field_name="period_end")
        if self.period_end <= self.period_start:
            raise InvalidAnalyticsContract("period_end must be after period_start")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise InvalidAnalyticsContract("metric value must be a finite Decimal")
        _, digits, raw_exponent = self.value.as_tuple()
        exponent = cast(int, raw_exponent)
        scale = max(-exponent, 0)
        integer_digits = max(len(digits) + exponent, 0)
        if (
            integer_digits > MAX_NUMERIC_INTEGER_DIGITS
            or scale > MAX_NUMERIC_SCALE
        ):
            raise InvalidAnalyticsContract(
                f"metric value exceeds NUMERIC({MAX_NUMERIC_PRECISION},"
                f"{MAX_NUMERIC_SCALE})"
            )
        if self.currency_code is not None and not _CURRENCY_RE.fullmatch(
            self.currency_code
        ):
            raise InvalidAnalyticsContract(
                "currency_code must be a three-letter uppercase ISO code"
            )


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    source_owner: str
    source_event_id: str
    source_schema_version: int
    source_reference: str
    adapter_code: str
    delivery_id: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.source_owner, field_name="source owner")
        _require_reference(self.source_event_id, field_name="source_event_id")
        if self.source_schema_version < 1:
            raise InvalidAnalyticsContract(
                "source_schema_version must be positive"
            )
        _require_reference(self.source_reference, field_name="source_reference")
        _require_code(self.adapter_code, field_name="adapter code")
        if self.delivery_id is not None:
            _require_reference(self.delivery_id, field_name="delivery_id")


@dataclass(frozen=True, slots=True)
class RecordMetricBatchCommand:
    tenant_id: UUID
    provenance: SourceProvenance
    observed_at: datetime
    received_at: datetime
    points: tuple[MetricPointInput, ...]

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, field_name="observed_at")
        _require_aware(self.received_at, field_name="received_at")
        if not self.points or len(self.points) > MAX_BATCH_POINTS:
            raise InvalidAnalyticsContract(
                f"metric batch must contain 1..{MAX_BATCH_POINTS} points"
            )
        coordinates = [
            (
                point.metric_code,
                point.schema_version,
                point.period_start.astimezone(UTC).isoformat(),
                point.period_end.astimezone(UTC).isoformat(),
                point.granularity.value,
                point.currency_code,
                tuple(
                    sorted(
                        (dimension.code, str(dimension.value))
                        for dimension in point.dimensions
                    )
                ),
            )
            for point in self.points
        ]
        if len(set(coordinates)) != len(coordinates):
            raise InvalidAnalyticsContract(
                "metric batch contains a duplicate metric coordinate"
            )


class MetricDeclarationRegistry:
    """Immutable installed set of metric owners and schema versions."""

    __slots__ = ("_declarations",)

    def __init__(self, declarations: tuple[MetricDeclaration, ...]) -> None:
        installed: dict[tuple[str, int], MetricDeclaration] = {}
        for declaration in declarations:
            if declaration.identity in installed:
                raise DuplicateMetricDeclaration(
                    f"metric {declaration.metric_code!r} v"
                    f"{declaration.schema_version} is declared more than once"
                )
            installed[declaration.identity] = declaration
        self._declarations = MappingProxyType(installed)

    def require(self, metric_code: str, schema_version: int) -> MetricDeclaration:
        try:
            return self._declarations[(metric_code, schema_version)]
        except KeyError as exc:
            raise UnknownMetricDeclaration(
                f"metric {metric_code!r} v{schema_version} is not installed"
            ) from exc

    def validate_point(
        self, point: MetricPointInput
    ) -> tuple[MetricDeclaration, tuple[tuple[str, str], ...]]:
        declaration = self.require(point.metric_code, point.schema_version)
        if point.granularity not in declaration.granularities:
            raise InvalidAnalyticsContract(
                f"metric {point.metric_code!r} does not permit "
                f"{point.granularity.value!r} granularity"
            )
        dimensions = declaration.normalize_dimensions(point.dimensions)
        if declaration.value_kind is MetricValueKind.MONEY:
            if point.currency_code is None:
                raise InvalidAnalyticsContract("money metric requires currency_code")
        elif point.currency_code is not None:
            raise InvalidAnalyticsContract("only money metrics may carry currency_code")
        if declaration.value_kind is MetricValueKind.COUNT:
            if point.value != point.value.to_integral_value():
                raise InvalidAnalyticsContract("count metric must be an integer value")
        return declaration, dimensions

    def validate_batch(
        self, command: RecordMetricBatchCommand
    ) -> tuple[
        tuple[MetricDeclaration, tuple[tuple[str, str], ...]], ...
    ]:
        validated = tuple(self.validate_point(point) for point in command.points)
        for declaration, _ in validated:
            if declaration.owner_code != command.provenance.source_owner:
                raise InvalidAnalyticsContract(
                    f"metric {declaration.metric_code!r} is owned by "
                    f"{declaration.owner_code!r}, not source "
                    f"{command.provenance.source_owner!r}"
                )
        return validated

    def validate_selector(
        self, selector: MetricSelector
    ) -> tuple[tuple[str, str], ...]:
        declaration = self.require(selector.metric_code, selector.schema_version)
        if selector.granularity not in declaration.granularities:
            raise InvalidAnalyticsContract(
                f"metric {selector.metric_code!r} does not permit "
                f"{selector.granularity.value!r} granularity"
            )
        dimensions = declaration.normalize_dimensions(selector.dimensions)
        if declaration.value_kind is MetricValueKind.MONEY:
            if selector.currency_code is None:
                raise InvalidAnalyticsContract(
                    "money metric selector requires currency_code"
                )
        elif selector.currency_code is not None:
            raise InvalidAnalyticsContract(
                "only money metric selectors may carry currency_code"
            )
        return dimensions


@dataclass(frozen=True, slots=True)
class MetricSelector:
    metric_code: str
    schema_version: int
    granularity: MetricGranularity
    dimensions: tuple[DimensionValue, ...] = ()
    currency_code: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.metric_code, field_name="metric code")
        if self.schema_version < 1:
            raise InvalidAnalyticsContract("metric schema_version must be positive")
        names = [dimension.code for dimension in self.dimensions]
        if len(set(names)) != len(names):
            raise InvalidAnalyticsContract("selector contains duplicate dimensions")
        if len(self.dimensions) > MAX_DIMENSIONS:
            raise InvalidAnalyticsContract(
                f"selector cannot contain more than {MAX_DIMENSIONS} dimensions"
            )
        for dimension in self.dimensions:
            if isinstance(dimension.value, str) and (
                not dimension.value
                or len(dimension.value) > MAX_DIMENSION_VALUE_LENGTH
            ):
                raise InvalidAnalyticsContract(
                    f"selector dimension {dimension.code!r} must contain 1.."
                    f"{MAX_DIMENSION_VALUE_LENGTH} characters"
                )
        if self.currency_code is not None and not _CURRENCY_RE.fullmatch(
            self.currency_code
        ):
            raise InvalidAnalyticsContract(
                "selector currency_code must be a three-letter uppercase ISO code"
            )


@dataclass(frozen=True, slots=True)
class IngestResult:
    receipt_id: UUID
    accepted_points: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class MetricValue:
    metric_code: str
    schema_version: int
    period_start: datetime
    period_end: datetime
    granularity: MetricGranularity
    dimensions: tuple[tuple[str, str], ...]
    value: Decimal
    currency_code: str | None
    observed_at: datetime
    received_at: datetime
    observation_id: UUID


@dataclass(frozen=True, slots=True)
class MetricComparison:
    metric_code: str
    currency_code: str | None
    current_value: Decimal | None
    prior_value: Decimal | None
    delta: Decimal | None
    percentage_change: Decimal | None


@dataclass(frozen=True, slots=True)
class ProjectionRebuildResult:
    before_digest: str
    after_digest: str
    point_count: int
    rebuild_id: UUID


def selector_digest(
    dimensions: tuple[tuple[str, str], ...], currency_code: str | None
) -> str:
    encoded = json.dumps(
        {"currency_code": currency_code, "dimensions": dimensions},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


__all__ = [
    "MAX_BATCH_POINTS",
    "MAX_HISTORY_POINTS",
    "MAX_NUMERIC_INTEGER_DIGITS",
    "AnalyticsError",
    "DimensionKind",
    "DimensionSpec",
    "DimensionValue",
    "DuplicateMetricDeclaration",
    "IngestResult",
    "InvalidAnalyticsContract",
    "MetricComparison",
    "MetricDeclaration",
    "MetricDeclarationRegistry",
    "MetricGranularity",
    "MetricIdentityConflict",
    "MetricPointInput",
    "MetricSelector",
    "MetricValue",
    "MetricValueKind",
    "ProjectionRebuildResult",
    "RecordMetricBatchCommand",
    "SourceProvenance",
    "UnknownMetricDeclaration",
    "selector_digest",
]
